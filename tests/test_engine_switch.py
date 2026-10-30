import asyncio
import json
import time
from dataclasses import replace
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cassini_live import capacity, catalog, eligibility, transcriber
from cassini_live.capacity import CapacityAssessment
from cassini_live.config import Config
from cassini_live.engine_switch import EngineManager, SwitchRefused
from cassini_live.model_store import ModelError, ModelState, Progress
from cassini_live.recovery import RecoveryPolicy
from cassini_live.resources import MIB
from cassini_live.safety import SafetyController
from cassini_live.settings import EngineSettings, SettingsStore
from tests.fakes_engine import FakeBackend
from tests.test_safety import SAFE

DATA = {**SAFE, "memoryLimitBytes": 3072 * MIB}


class FakeStore:
    def __init__(self, root, ready=()):
        self.root = root
        self.ready, self.partial, self.corrupt = set(ready), set(), set()
        self.busy = None
        self.gate = None
        self.fail = None
        self.cancelled = asyncio.Event()
        self.deleted, self.verified, self.downloads = [], [], []
        self._progress = None

    def model_dir(self, engine_id):
        return self.root / engine_id

    def state(self, engine_id):
        if catalog.get(engine_id).asset is None:
            return ModelState(engine_id, "bundled", None)
        if self.busy == engine_id:
            return ModelState(engine_id, "downloading", None)
        for name in ("corrupt", "ready", "partial"):
            if engine_id in getattr(self, name):
                return ModelState(engine_id, name, self.root / engine_id, 0, None)
        return ModelState(engine_id, "absent", None)

    async def verify(self, engine_id, *, deep=True):
        self.verified.append(engine_id)
        return self.state(engine_id)

    async def download(self, engine_id):
        self.downloads.append(engine_id)
        self.busy, self.cancelled = engine_id, asyncio.Event()
        self._progress = Progress(engine_id, "downloading", 10, 100)
        try:
            while self.gate is not None and not self.gate.is_set():
                if self.cancelled.is_set():
                    raise ModelError("cancelled", "Download cancelled")
                await asyncio.sleep(0.002)
            if self.fail:
                raise self.fail
            self.ready.add(engine_id)
            return self.root / engine_id
        finally:
            self.busy = self._progress = None

    def progress(self, engine_id):
        return self._progress if self.busy == engine_id else None

    def cancel(self, engine_id, *, discard=False):
        self.cancelled.set()
        return True

    async def delete(self, engine_id):
        self.deleted.append(engine_id)
        self.ready.discard(engine_id)
        self.partial.discard(engine_id)


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.002)


def make(tmp_path, monkeypatch, *, active="whistle", ready=(), data=None, settings=None):
    passes = {}

    async def scenario(pool, count):
        return {"passed": passes.get(pool.engine_id, True), "speakers": count}

    monkeypatch.setattr(capacity, "scenario", scenario)
    data = dict(data or DATA)
    pool = FakeBackend(active, 1)
    service = SimpleNamespace(enabled=True, clients={}, closing={}, stop_rooms=AsyncMock())
    guard = SafetyController(
        pool,
        CapacityAssessment(pool),
        service,
        reader=lambda: dict(data),
        policy=replace(RecoveryPolicy(), monitor=0.005, poll=0.005),
        jitter=lambda: 0,
    )
    built, start_errors = [], {}

    def factory(engine_id, *, size, weights=None, model_dir=None, threads=None):
        backend = FakeBackend(engine_id, size, start_error=start_errors.get(engine_id))
        built.append(backend)
        return backend

    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("flags\t: fpu sse avx2\n")
    facts = partial(
        eligibility.host_facts,
        cpuinfo=cpuinfo,
        disk_usage=lambda path: SimpleNamespace(free=100 * 1024 * MIB),
        arch="x86_64",
    )
    store = FakeStore(tmp_path, ready)
    store_settings = SettingsStore(tmp_path / "settings.json")
    if settings:
        store_settings.write(settings)
    manager = EngineManager(
        guard, service, Config(None, 1, "secret", ""), store, store_settings,
        backend_factory=factory, facts=facts, check_timeout=2,
    )
    changes = []
    manager.on_change = lambda: changes.append(manager.status.state)
    return SimpleNamespace(
        manager=manager, safety=guard, service=service, store=store, settings=store_settings,
        built=built, start_errors=start_errors, passes=passes, data=data, changes=changes,
        pool=pool,
    )


@pytest.fixture
async def h(tmp_path, monkeypatch):
    harness = make(tmp_path, monkeypatch)
    await harness.manager.start_safety()
    await until(lambda: harness.safety.state == "ready")
    yield harness
    await harness.manager.stop()
    await harness.safety.close()


async def finished(h):
    await until(lambda: not h.manager.busy)
    return h.manager.status


async def test_switch_downloads_loads_checks_and_persists(h):
    # No license step: the license is shown on the settings page, not accepted.
    states = []
    h.manager.on_change = lambda: states.append(h.manager.status.state)
    status = await h.manager.request_switch("nemotron-en")
    assert status.target == "nemotron-en" and status.previous == "whistle"
    status = await finished(h)
    assert status.state == "succeeded" and not status.error
    assert h.manager.active == "nemotron-en"
    assert transcriber.POOL is h.safety.pool is h.built[-1]
    assert h.safety.state == "ready"
    h.service.stop_rooms.assert_awaited()
    saved = h.settings.read()
    assert saved.engine == "nemotron-en" and saved.pending is None
    assert "acknowledgements" not in json.loads(h.settings.path.read_text())
    order = [s for i, s in enumerate(states) if i == 0 or states[i - 1] != s]
    assert order == [
        "validating", "downloading", "verifying", "stopping", "loading", "succeeded"
    ]
    assert h.manager.languages().keys() == {"auto", "en"}
    assert h.manager.languages()["auto"]["name"] == "Automatic (English)"
    assert h.manager.engine_status()["id"] == "nemotron-en"


async def test_refusals_leave_the_state_unchanged(h, tmp_path):
    assert h.manager.status.state == "idle"
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.request_switch("whistle")
    assert refused.value.code == "active"
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.request_switch("vosk")
    assert refused.value.code == "unknown_engine"
    h.data["memoryLimitBytes"] = 2048 * MIB
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.request_switch("nemotron-multilingual")
    assert refused.value.code == "not_eligible"
    assert [r.code for r in refused.value.reasons] == ["memory_limit"]
    h.manager.note_refusal(refused.value, "nemotron-multilingual")
    assert h.manager.panel()["lastRefusal"]["reasons"][0]["code"] == "memory_limit"
    h.service.enabled = False
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.request_switch("nemotron-multilingual")
    assert refused.value.code == "disabled" and refused.value.status == 409
    assert h.manager.active == "whistle" and h.safety.state == "ready"


async def test_busy_switch_is_refused(h):
    h.store.gate = asyncio.Event()
    await h.manager.request_switch("nemotron-multilingual")
    await until(lambda: h.manager.status.state == "downloading")
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.request_switch("nemotron-en")
    assert refused.value.code == "busy" and refused.value.status == 409
    assert h.manager.desired == "nemotron-multilingual"
    assert h.manager.panel()["switch"]["progress"]["bytesTotal"] == 100
    h.store.gate.set()
    assert (await finished(h)).state == "succeeded"


async def test_download_failure_never_stops_the_old_engine(h):
    h.store.fail = ModelError("network", "Cannot reach github.com")
    await h.manager.request_switch("nemotron-multilingual")
    status = await finished(h)
    assert status.state == "failed" and status.error["code"] == "network"
    assert h.safety.pool is h.pool and h.safety.state == "ready"
    h.service.stop_rooms.assert_not_awaited()


async def test_corrupt_download_fails_verification(h):
    h.store.corrupt.add("nemotron-multilingual")
    h.store.ready.add("nemotron-multilingual")
    await h.manager.request_switch("nemotron-multilingual")
    status = await finished(h)
    assert status.error["code"] == "hash_mismatch" and h.manager.active == "whistle"


async def test_refused_load_rolls_back(h):
    from cassini_live.pool import CapacityError

    refused = CapacityError("Not enough memory to load Nemotron English")
    h.start_errors["nemotron-en"] = refused
    await h.manager.request_switch("nemotron-en")
    status = await finished(h)
    assert status.state == "failed" and status.rolled_back
    assert status.error == {"code": "load_failed", "message": str(refused)}
    assert h.manager.active == "whistle" and h.safety.engine_id == "whistle"
    await until(lambda: h.safety.state == "ready")
    saved = h.settings.read()
    assert saved.engine == "whistle" and saved.pending is None
    assert saved.last_error["engine"] == "nemotron-en"


async def test_load_failure_with_failed_rollback(h):
    h.start_errors["nemotron-en"] = RuntimeError("boom")
    h.start_errors["whistle"] = RuntimeError("boom too")
    await h.manager.request_switch("nemotron-en")
    status = await finished(h)
    assert status.state == "failed" and not status.rolled_back
    assert h.manager.active == "whistle" and h.settings.read().engine == "whistle"


async def test_cancel_while_downloading_keeps_captions(h):
    h.store.gate = asyncio.Event()
    await h.manager.request_switch("nemotron-multilingual")
    await until(lambda: h.manager.status.state == "downloading")
    await h.manager.cancel("Cancelled in the administration settings")
    status = await finished(h)
    assert status.state == "cancelled" and status.error["code"] == "cancelled"
    assert h.safety.pool is h.pool and h.safety.state == "ready"


async def test_cancel_while_loading_rolls_back(h):
    gate = asyncio.Event()
    h.start_errors.clear()
    original = h.manager.backend_factory

    def gated(engine_id, **kwargs):
        backend = original(engine_id, **kwargs)
        if engine_id != "whistle":
            backend.start_gate = gate
        return backend

    h.manager.backend_factory = gated
    h.store.ready.add("nemotron-multilingual")
    await h.manager.request_switch("nemotron-multilingual")
    await until(lambda: h.manager.status.state == "loading" and h.safety.phase == "loading")
    await h.manager.cancel()
    status = await finished(h)
    assert status.state == "cancelled" and status.rolled_back
    assert h.manager.active == "whistle"
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.cancel()
    assert refused.value.code == "idle"


async def test_switch_and_delete_during_the_critical_section_are_refused_at_once(h):
    """Never queued behind the lock: a queued switch would start, unconfirmed, afterwards."""
    gate = asyncio.Event()
    original = h.manager.backend_factory

    def gated(engine_id, **kwargs):
        backend = original(engine_id, **kwargs)
        if engine_id != "whistle":
            backend.start_gate = gate
        return backend

    h.manager.backend_factory = gated
    h.store.ready.update({"nemotron-multilingual", "nemotron-en"})
    await h.manager.request_switch("nemotron-multilingual")
    await until(lambda: h.manager.status.state == "loading" and h.safety.phase == "loading")
    for call in (h.manager.request_switch("nemotron-en"), h.manager.delete_model("nemotron-en")):
        with pytest.raises(SwitchRefused) as refused:
            await asyncio.wait_for(call, 0.5)
        assert refused.value.code == "busy" and refused.value.status == 409
    gate.set()
    status = await finished(h)
    assert status.state == "succeeded" and status.target == "nemotron-multilingual"
    await asyncio.sleep(0.05)
    assert h.manager.active == "nemotron-multilingual" and not h.manager.busy
    assert "nemotron-en" not in h.store.deleted


async def test_held_lock_refuses_instead_of_waiting(h):
    h.store.ready.add("nemotron-en")
    await h.manager.lock.acquire()  # e.g. a restore watch rolling back
    try:
        for call in (h.manager.request_switch("nemotron-en"),
                     h.manager.delete_model("nemotron-en")):
            with pytest.raises(SwitchRefused) as refused:
                await asyncio.wait_for(call, 0.5)
            assert refused.value.code == "busy"
    finally:
        h.manager.lock.release()
    assert h.manager.status.state == "idle" and h.store.deleted == []


async def test_refusal_noted_during_a_switch_ends_with_it(h):
    h.store.gate = asyncio.Event()
    await h.manager.request_switch("nemotron-multilingual")
    await until(lambda: h.manager.status.state == "downloading")
    h.manager.note_refusal(SwitchRefused(409, "busy", "Try again"), "whistle")
    assert h.manager.panel()["lastRefusal"]["engine"] == "whistle"
    h.store.gate.set()
    assert (await finished(h)).state == "succeeded"
    assert h.manager.panel()["lastRefusal"] is None
    # A refusal while idle stays until the next change.
    h.manager.note_refusal(SwitchRefused(400, "unknown_engine", "Unknown"), "vosk")
    assert h.manager.panel()["lastRefusal"]["engine"] == "vosk"


async def test_cancel_during_rollback_is_refused(h):
    h.manager.status.state = "rolling_back"
    h.manager.task = asyncio.create_task(asyncio.sleep(0))
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.cancel()
    assert refused.value.code == "busy"
    h.manager.status.state = "idle"


async def test_disable_during_loading_restores_previous_without_restart(h):
    gate = asyncio.Event()
    original = h.manager.backend_factory

    def gated(engine_id, **kwargs):
        backend = original(engine_id, **kwargs)
        backend.start_gate = gate
        return backend

    h.manager.backend_factory = gated
    h.store.ready.add("nemotron-multilingual")
    await h.manager.request_switch("nemotron-multilingual")
    await until(lambda: h.manager.status.state == "loading" and h.safety.phase == "loading")
    await h.manager.stop()
    assert h.manager.status.state == "cancelled"
    assert h.manager.status.error["code"] == "disabled"
    assert h.safety.engine_id == "whistle" and not h.safety.desired_enabled
    saved = h.settings.read()
    assert saved.engine == "whistle" and saved.pending is None


async def test_delete_refusals_and_cleanup(h):
    h.store.ready.add("nemotron-en")
    h.store.partial.add("nemotron-multilingual")
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.delete_model("whistle")
    assert refused.value.code == "active"
    with pytest.raises(SwitchRefused):
        await h.manager.delete_model("vosk")
    card = await h.manager.delete_model("nemotron-en")
    assert card["model"]["state"] == "absent" and h.store.deleted == ["nemotron-en"]
    assert await h.manager.delete_unused_models() == ["nemotron-multilingual"]


def test_panel_shape(tmp_path, monkeypatch):
    h = make(tmp_path, monkeypatch, data={**DATA, "memoryLimitBytes": 2048 * MIB})
    panel = h.manager.panel()
    assert set(panel) >= {
        "active", "activeCalls", "measurements", "switch", "lastError", "lastRefusal",
        "host", "engines",
    }
    cards = {c["id"]: c for c in panel["engines"]}
    assert list(cards) == list(catalog.ENGINES)
    assert cards["whistle"]["active"] and cards["whistle"]["model"]["state"] == "bundled"
    assert cards["whistle"]["requirements"]["downloadBytes"] == 0
    en = cards["nemotron-en"]
    assert not en["eligible"] and en["reasons"][0]["code"] == "memory_limit"
    assert set(en["license"]) == {"id", "name", "url", "version", "notice", "summary"}
    assert en["requirements"]["memoryLimitMiB"] == 2304
    assert en["model"]["bytesTotal"] == catalog.get("nemotron-en").asset.download_bytes
    assert panel["host"]["cpuFlags"] == ["avx2"]
    assert panel["switch"]["state"] == "idle"
    assert panel["measurements"] == {"measuredOn": catalog.MEASURED_ON, "estimates": ["cpu"]}


async def test_restore_with_interrupted_switch(tmp_path, monkeypatch):
    saved = EngineSettings(engine="whistle", pending="nemotron-en")
    h = make(tmp_path, monkeypatch, settings=saved)
    await h.manager.restore()
    stored = h.settings.read()
    assert stored.pending is None and "interrupted" in stored.last_error["message"]
    assert h.manager.active == "whistle"


@pytest.mark.parametrize("problem", ["corrupt", "ineligible", "missing"])
async def test_restore_falls_back_to_whistle(tmp_path, monkeypatch, problem):
    data = {**DATA, "memoryLimitBytes": 2048 * MIB} if problem == "ineligible" else None
    h = make(tmp_path, monkeypatch, data=data, settings=EngineSettings(engine="nemotron-en"))
    if problem != "missing":
        h.store.ready.add("nemotron-en")
    if problem == "corrupt":
        h.store.corrupt.add("nemotron-en")
    await h.manager.restore()
    assert h.manager.active == "whistle" and h.safety.engine_id == "whistle"
    stored = h.settings.read()
    assert stored.last_error["engine"] == "nemotron-en"
    if problem == "ineligible":
        # A host-limit problem keeps the choice for the next suitable start.
        assert stored.engine == "nemotron-en" and stored.last_error["code"] == "restore_deferred"
    else:
        assert stored.engine == "whistle" and stored.last_error["code"] == "restore_failed"


@pytest.mark.parametrize("host_memory", [5120 * MIB, None])
async def test_installation_advice_never_defers_a_restore(tmp_path, monkeypatch, host_memory):
    # Review 0.8.0: host RAM under twice the container (or unreadable) is advice since
    # Decision 5; treating it as blocking fell back to Whistle at every restart.
    data = {**DATA, "memoryLimitBytes": 3072 * MIB, "hostMemoryBytes": host_memory}
    h = make(tmp_path, monkeypatch, data=data, ready=("nemotron-en",),
             settings=EngineSettings(engine="nemotron-en"))
    advice = h.manager._assess("nemotron-en", h.manager._host_facts(fresh=True))
    assert advice.eligible and advice.advisory
    await h.manager.restore()
    assert h.manager.active == "nemotron-en" and h.settings.read().last_error is None


async def test_deferred_engine_returns_once_the_limits_allow_it(tmp_path, monkeypatch):
    unhardened = {**DATA, "memoryLimitBytes": 2048 * MIB}
    h = make(tmp_path, monkeypatch, data=unhardened, ready=("nemotron-en",),
             settings=EngineSettings(engine="nemotron-en"))
    await h.manager.restore()
    assert h.manager.active == "whistle" and h.manager.desired == "nemotron-en"
    hardened = make(tmp_path, monkeypatch, ready=("nemotron-en",))
    await hardened.manager.restore()
    assert hardened.manager.active == "nemotron-en"
    assert hardened.safety.engine_id == "nemotron-en"
    assert hardened.manager.panel()["lastError"] is None


async def test_choosing_the_fallback_drops_a_deferred_choice(tmp_path, monkeypatch):
    unhardened = {**DATA, "memoryLimitBytes": 2048 * MIB}
    h = make(tmp_path, monkeypatch, data=unhardened, ready=("nemotron-en",),
             settings=EngineSettings(engine="nemotron-en"))
    await h.manager.restore()
    await h.manager.request_switch("whistle")
    stored = h.settings.read()
    assert stored.engine == "whistle" and stored.last_error is None
    with pytest.raises(SwitchRefused):
        await h.manager.request_switch("whistle")


def test_unreadable_settings_fall_back(tmp_path, monkeypatch):
    h = make(tmp_path, monkeypatch)
    (tmp_path / "settings.json").write_text("{not json")
    asyncio.run(h.manager.restore())
    assert h.settings.read().last_error["code"] == "settings"


async def test_restored_engine_rolls_back_when_its_first_load_fails(tmp_path, monkeypatch):
    h = make(tmp_path, monkeypatch, ready=("nemotron-en",),
             settings=EngineSettings(engine="nemotron-en"))
    h.start_errors["nemotron-en"] = RuntimeError("boom")
    await h.manager.restore()
    assert h.safety.engine_id == "nemotron-en"
    try:
        await h.manager.start_safety()
        await until(lambda: h.manager.status.state == "failed")
        assert h.manager.status.rolled_back and h.manager.active == "whistle"
        assert h.settings.read().engine == "whistle"
    finally:
        await h.manager.stop()
        await h.safety.close()


async def test_later_trip_does_not_roll_back(tmp_path, monkeypatch):
    h = make(tmp_path, monkeypatch, ready=("nemotron-en",),
             settings=EngineSettings(engine="nemotron-en"))
    await h.manager.restore()
    try:
        await h.manager.start_safety()
        await until(lambda: h.safety.state == "ready")
        await asyncio.sleep(0.01)
        h.data["oomKills"] += 1
        await until(lambda: h.safety.state != "ready")
        await asyncio.sleep(0.05)
        assert h.manager.active == "nemotron-en" and h.manager.status.state == "idle"
    finally:
        await h.manager.stop()
        await h.safety.close()


async def test_disable_during_restore_rollback_does_not_stick(tmp_path, monkeypatch):
    """Disabling during the restore rollback ends the change; re-enabling starts captions."""
    h = make(tmp_path, monkeypatch, ready=("nemotron-en",),
             settings=EngineSettings(engine="nemotron-en"))
    h.start_errors["nemotron-en"] = RuntimeError("boom")
    await h.manager.restore()
    gate = asyncio.Event()
    original = h.manager.backend_factory
    def gated(engine_id, **kw):
        b = original(engine_id, **kw)
        if engine_id == "whistle":
            b.start_gate = gate  # whistle rollback check hangs (slow load)
        return b
    h.manager.backend_factory = gated
    await h.manager.start_safety()
    await until(lambda: h.manager.status.state == "rolling_back")
    await asyncio.sleep(0.05)
    # Admin disables the app: app.py calls manager.stop() then safety.close()
    await h.manager.stop()
    await h.safety.close()
    # Re-enable
    await h.manager.start_safety()
    assert not h.manager.busy and h.manager.active == "whistle"
    assert h.safety.desired_enabled
    await h.manager.stop()
    await h.safety.close()


async def test_cancel_while_waiting_for_the_lock_is_honoured(tmp_path, monkeypatch):
    """A cancel while _run waits for the lock after verify is honoured."""
    h = make(tmp_path, monkeypatch, ready=("nemotron-en",))
    await h.manager.start_safety()
    await until(lambda: h.safety.state == "ready")
    await h.manager.request_switch("nemotron-en")
    await h.manager.lock.acquire()  # e.g. delete_model / restore-watch holding the lock
    await until(lambda: h.manager.status.state == "verifying" and h.manager.step is None)
    await h.manager.cancel("admin cancel")
    h.manager.lock.release()
    status = await finished(h)
    assert status.state == "cancelled" and h.manager.active == "whistle"
    await h.manager.stop()
    await h.safety.close()


async def test_cancel_during_validating_is_refused(tmp_path, monkeypatch):
    h = make(tmp_path, monkeypatch)
    await h.manager.start_safety()
    await until(lambda: h.safety.state == "ready")
    gate = asyncio.Event()
    class Probe:
        async def refresh(self, ids):
            await gate.wait()
        def status(self, engine_id):
            return "ok"
    h.manager.probe = Probe()
    h.manager.task = asyncio.create_task(asyncio.sleep(0))  # a previous, finished switch
    await asyncio.sleep(0)
    t = asyncio.create_task(h.manager.request_switch("nemotron-en"))
    await until(lambda: h.manager.status.state == "validating")
    with pytest.raises(SwitchRefused):
        await h.manager.cancel("admin cancel")
    gate.set()
    await t
    status = await finished(h)
    assert status.state == "succeeded"
    await h.manager.stop()
    await h.safety.close()


async def test_restore_waits_out_the_recovery_cooldown(tmp_path, monkeypatch):
    """A cooldown longer than the check timeout is not an engine failure."""
    h = make(tmp_path, monkeypatch, ready=("nemotron-en",),
             settings=EngineSettings(engine="nemotron-en"))
    # Journal from the previous container run: one earlier attempt, retry deadline in future.
    h.safety.journal.write(2, time.time() + 10, False, "previous run")
    await h.manager.restore()
    try:
        await h.manager.start_safety()
        await asyncio.sleep(2.5)  # check_timeout=2 in harness
        await until(lambda: not h.manager.busy)
        assert h.manager.active == "nemotron-en" and h.settings.read().engine == "nemotron-en"
    finally:
        await h.manager.stop()
        await h.safety.close()


def slow_load(h, seconds):
    """The next backends the manager builds take `seconds` to load once started."""
    original = h.manager.backend_factory

    def factory(engine_id, **kwargs):
        backend = original(engine_id, **kwargs)
        gate = backend.start_gate = asyncio.Event()
        start = backend.start

        async def delayed():
            asyncio.get_running_loop().call_later(seconds, gate.set)
            await start()

        backend.start = delayed
        return backend

    h.manager.backend_factory = factory


async def test_restore_times_the_load_from_the_attempt_not_the_cooldown(tmp_path, monkeypatch):
    """A cooldown ending late in a wait must not roll back the load that follows it."""
    h = make(tmp_path, monkeypatch, ready=("nemotron-en",),
             settings=EngineSettings(engine="nemotron-en"))
    h.safety.journal.write(1, time.time() + 1.5, False, "previous run")
    slow_load(h, 0.8)
    await h.manager.restore()
    try:
        await h.manager.start_safety()
        await until(lambda: h.safety.phase == "loading")
        async with asyncio.timeout(4):
            while h.safety.state != "ready":
                await asyncio.sleep(0.01)
        # The load ran past check_timeout=2 measured from start_safety, but not from its start.
        assert h.safety.attempt_started + 0.7 < h.safety.clock()
        assert h.manager.active == "nemotron-en" and "rolling_back" not in h.changes
    finally:
        await h.manager.stop()
        await h.safety.close()


async def test_a_deferred_engine_counts_as_in_use(tmp_path, monkeypatch):
    unhardened = {**DATA, "memoryLimitBytes": 2048 * MIB}
    h = make(tmp_path, monkeypatch, data=unhardened, ready=("nemotron-en",),
             settings=EngineSettings(engine="nemotron-en"))
    await h.manager.restore()
    panel = h.manager.panel()
    cards = {c["id"]: c for c in panel["engines"]}
    assert panel["active"] == "whistle" and panel["selected"] == "nemotron-en"
    assert cards["nemotron-en"]["deferred"] and not cards["whistle"]["deferred"]
    assert "Using Whistle" not in panel["lastError"]["message"]
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.delete_model("nemotron-en")
    assert refused.value.code == "selected" and "Keep using Whistle" in str(refused.value)
    assert await h.manager.delete_unused_models() == [] and h.store.deleted == []
    # Keeping Whistle drops the choice; then the files are unused like any others.
    await h.manager.request_switch("whistle")
    assert h.manager.panel()["selected"] == "whistle" and h.manager.deferred is None
    assert await h.manager.delete_unused_models() == ["nemotron-en"]


async def test_restore_failed_message_names_the_fallback_once(tmp_path, monkeypatch):
    h = make(tmp_path, monkeypatch, settings=EngineSettings(engine="nemotron-en"))
    await h.manager.restore()
    message = h.settings.read().last_error["message"]
    assert message == ("The Nemotron English model files are missing or damaged. "
                       "Choose it again to download it.")


async def test_restore_clears_a_07x_capacity_verdict(tmp_path, monkeypatch):
    error = {"code": "capacity_failed", "message": "Capacity check did not establish a safe "
             "speaking budget", "engine": "nemotron-en", "at": 1}
    h = make(tmp_path, monkeypatch, settings=EngineSettings(engine="whistle", last_error=error))
    await h.manager.restore()
    assert h.settings.read().last_error is None and h.manager.panel()["lastError"] is None


def test_the_load_bound_is_the_engine_start_timeout_plus_room_teardown(tmp_path, monkeypatch):
    from cassini_live.engine_switch import STOP_ROOMS_S, _failure_code

    h = make(tmp_path, monkeypatch)
    h.manager.check_timeout = None
    assert h.manager._timeout() == catalog.get("whistle").requirements.start_timeout_s + 30
    assert STOP_ROOMS_S == 30
    h.safety.replace_pool(FakeBackend("nemotron-en", 1))
    assert h.manager._timeout() == 150
    assert _failure_code("The speech engine did not load in time") == "timeout"
    assert _failure_code("Capacity check did not establish a safe speaking budget") == "load_failed"


async def test_nemotron_is_sized_from_the_cpu_limit_alone(tmp_path, monkeypatch):
    # 0.8.2: the speaker ceiling always follows the CPU limit (Nemotron on 2 CPUs seats 2),
    # whatever Whistle's default count in the configuration, with no workers advice.
    data = {**DATA, "cpuQuotaCpus": 2.0, "effectiveCpus": 2.0}
    h = make(tmp_path, monkeypatch, data=data, ready=("nemotron-en",),
             settings=EngineSettings(engine="nemotron-en"))
    h.manager.config = Config(None, 3, "secret", "")
    await h.manager.restore()
    assert h.manager.active == "nemotron-en" and h.safety.pool.size == 2
    try:
        await h.manager.start_safety()
        await until(lambda: h.safety.state == "ready")
        card = next(c for c in h.manager.panel()["engines"] if c["id"] == "nemotron-en")
        assert card["eligible"] and h.safety.governor.ceiling == 2
        assert not any(r["code"] == "workers" for r in card["reasons"])
    finally:
        await h.manager.stop()
        await h.safety.close()


# Nemotron needs about 1.5 GiB free to load; Whistle's two workers need 384 MiB.
CROWDED = {**DATA, "memoryCurrentBytes": 1672 * MIB}  # 1400 MiB free of 3 GiB


async def test_restore_defers_an_engine_that_does_not_fit_while_whistle_does(
    tmp_path, monkeypatch
):
    h = make(tmp_path, monkeypatch, data=CROWDED, ready=("nemotron-en",),
             settings=EngineSettings(engine="nemotron-en"))
    await h.manager.restore()
    stored = h.settings.read()
    assert h.manager.active == "whistle" and h.manager.deferred == "nemotron-en"
    assert stored.engine == "nemotron-en" and stored.last_error["code"] == "restore_deferred"
    assert "Not enough memory to load" in stored.last_error["message"]
    roomy = make(tmp_path, monkeypatch, ready=("nemotron-en",))  # the next restart fits
    await roomy.manager.restore()
    assert roomy.manager.active == "nemotron-en" and roomy.manager.panel()["lastError"] is None


async def test_a_fit_refusal_after_restore_falls_back_to_whistle(tmp_path, monkeypatch):
    # Review 0.8.0: the refusal is phase "configuration", which _watch_restore took for a
    # host problem blocking Whistle too, so no call had captions although Whistle fits.
    h = make(tmp_path, monkeypatch, ready=("nemotron-en",),
             settings=EngineSettings(engine="nemotron-en"))
    await h.manager.restore()
    assert h.manager.active == "nemotron-en"
    h.data.update(CROWDED)  # memory filled between the restore check and the load
    try:
        await h.manager.start_safety()
        await until(lambda: h.manager.active == "whistle" and h.safety.state == "ready")
        stored = h.settings.read()
        assert stored.engine == "nemotron-en" and stored.last_error["code"] == "restore_deferred"
        assert h.safety.engine_id == "whistle" and h.manager.deferred == "nemotron-en"
    finally:
        await h.manager.stop()
        await h.safety.close()


async def test_a_fit_refusal_with_no_room_for_whistle_either_stays_blocked(tmp_path, monkeypatch):
    h = make(tmp_path, monkeypatch, ready=("nemotron-en",),
             settings=EngineSettings(engine="nemotron-en"))
    await h.manager.restore()
    h.data["memoryCurrentBytes"] = 2900 * MIB
    try:
        await h.manager.start_safety()
        await until(lambda: h.safety.phase == "configuration")
        await h.manager.watch
        assert h.manager.active == "nemotron-en" and h.settings.read().last_error is None
    finally:
        await h.manager.stop()
        await h.safety.close()
