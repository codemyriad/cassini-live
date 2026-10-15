import asyncio
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
    states = []
    h.manager.on_change = lambda: states.append(h.manager.status.state)
    status = await h.manager.request_switch("nemotron-en", True)
    assert status.target == "nemotron-en" and status.previous == "whistle"
    status = await finished(h)
    assert status.state == "succeeded" and not status.error
    assert h.manager.active == "nemotron-en"
    assert transcriber.POOL is h.safety.pool is h.built[-1]
    assert h.safety.state == "ready"
    h.service.stop_rooms.assert_awaited()
    saved = h.settings.read()
    assert saved.engine == "nemotron-en" and saved.pending is None
    assert saved.acknowledgements["nemotron-en"].version == "2025-10-24"
    assert saved.acknowledgements["nemotron-en"].user == ""
    order = [s for i, s in enumerate(states) if i == 0 or states[i - 1] != s]
    assert order == [
        "validating", "downloading", "verifying", "stopping", "loading", "checking", "succeeded"
    ]
    assert h.manager.languages().keys() == {"auto", "en"}
    assert h.manager.languages()["auto"]["name"] == "Automatic (English)"
    assert h.manager.engine_status()["id"] == "nemotron-en"


async def test_refusals_leave_the_state_unchanged(h, tmp_path):
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.request_switch("nemotron-en", False)
    assert refused.value.code == "license_required" and refused.value.status == 400
    assert h.manager.status.state == "idle"
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.request_switch("whistle", False)
    assert refused.value.code == "active"
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.request_switch("vosk", False)
    assert refused.value.code == "unknown_engine"
    h.data["memoryLimitBytes"] = 2048 * MIB
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.request_switch("nemotron-multilingual", False)
    assert refused.value.code == "not_eligible"
    assert [r.code for r in refused.value.reasons] == ["memory_limit"]
    h.manager.note_refusal(refused.value, "nemotron-multilingual")
    assert h.manager.panel()["lastRefusal"]["reasons"][0]["code"] == "memory_limit"
    h.service.enabled = False
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.request_switch("nemotron-multilingual", False)
    assert refused.value.code == "disabled" and refused.value.status == 409
    assert h.manager.active == "whistle" and h.safety.state == "ready"


async def test_busy_switch_is_refused(h):
    h.store.gate = asyncio.Event()
    await h.manager.request_switch("nemotron-multilingual", False)
    await until(lambda: h.manager.status.state == "downloading")
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.request_switch("nemotron-en", True)
    assert refused.value.code == "busy" and refused.value.status == 409
    assert h.manager.desired == "nemotron-multilingual"
    assert h.manager.panel()["switch"]["progress"]["bytesTotal"] == 100
    h.store.gate.set()
    assert (await finished(h)).state == "succeeded"


async def test_download_failure_never_stops_the_old_engine(h):
    h.store.fail = ModelError("network", "Cannot reach github.com")
    await h.manager.request_switch("nemotron-multilingual", False)
    status = await finished(h)
    assert status.state == "failed" and status.error["code"] == "network"
    assert h.safety.pool is h.pool and h.safety.state == "ready"
    h.service.stop_rooms.assert_not_awaited()


async def test_corrupt_download_fails_verification(h):
    h.store.corrupt.add("nemotron-multilingual")
    h.store.ready.add("nemotron-multilingual")
    await h.manager.request_switch("nemotron-multilingual", False)
    status = await finished(h)
    assert status.error["code"] == "hash_mismatch" and h.manager.active == "whistle"


async def test_capacity_failure_rolls_back(h):
    h.passes["nemotron-en"] = False
    await h.manager.request_switch("nemotron-en", True)
    status = await finished(h)
    assert status.state == "failed" and status.rolled_back
    assert status.error["code"] == "capacity_failed"
    assert h.manager.active == "whistle" and h.safety.engine_id == "whistle"
    await until(lambda: h.safety.state == "ready")
    saved = h.settings.read()
    assert saved.engine == "whistle" and saved.pending is None
    assert saved.last_error["engine"] == "nemotron-en"


async def test_load_failure_with_failed_rollback(h):
    h.start_errors["nemotron-en"] = RuntimeError("boom")
    h.passes["whistle"] = False
    await h.manager.request_switch("nemotron-en", True)
    status = await finished(h)
    assert status.state == "failed" and not status.rolled_back
    assert h.manager.active == "whistle" and h.settings.read().engine == "whistle"


async def test_cancel_while_downloading_keeps_captions(h):
    h.store.gate = asyncio.Event()
    await h.manager.request_switch("nemotron-multilingual", False)
    await until(lambda: h.manager.status.state == "downloading")
    await h.manager.cancel("Cancelled in the administration settings")
    status = await finished(h)
    assert status.state == "cancelled" and status.error["code"] == "cancelled"
    assert h.safety.pool is h.pool and h.safety.state == "ready"


async def test_cancel_while_checking_rolls_back(h):
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
    await h.manager.request_switch("nemotron-multilingual", False)
    await until(lambda: h.manager.status.state == "checking")
    await h.manager.cancel()
    status = await finished(h)
    assert status.state == "cancelled" and status.rolled_back
    assert h.manager.active == "whistle"
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.cancel()
    assert refused.value.code == "idle"


async def test_cancel_during_rollback_is_refused(h):
    h.manager.status.state = "rolling_back"
    h.manager.task = asyncio.create_task(asyncio.sleep(0))
    with pytest.raises(SwitchRefused) as refused:
        await h.manager.cancel()
    assert refused.value.code == "busy"
    h.manager.status.state = "idle"


async def test_disable_during_checking_restores_previous_without_restart(h):
    gate = asyncio.Event()
    original = h.manager.backend_factory

    def gated(engine_id, **kwargs):
        backend = original(engine_id, **kwargs)
        backend.start_gate = gate
        return backend

    h.manager.backend_factory = gated
    h.store.ready.add("nemotron-multilingual")
    await h.manager.request_switch("nemotron-multilingual", False)
    await until(lambda: h.manager.status.state == "checking")
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
    assert en["license"]["requiresAcknowledgement"] and not en["license"]["acknowledged"]
    assert en["requirements"]["memoryLimitMiB"] == 2304
    assert en["model"]["bytesTotal"] == catalog.get("nemotron-en").asset.archive_bytes
    assert panel["host"]["cpuFlags"] == ["avx2"]
    assert panel["switch"]["state"] == "idle"


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
    assert stored.engine == "whistle" and stored.last_error["engine"] == "nemotron-en"


def test_unreadable_settings_fall_back(tmp_path, monkeypatch):
    h = make(tmp_path, monkeypatch)
    (tmp_path / "settings.json").write_text("{not json")
    asyncio.run(h.manager.restore())
    assert h.settings.read().last_error["code"] == "settings"


async def test_restored_engine_rolls_back_when_its_first_check_fails(tmp_path, monkeypatch):
    h = make(tmp_path, monkeypatch, ready=("nemotron-en",),
             settings=EngineSettings(engine="nemotron-en"))
    await h.manager.restore()
    assert h.safety.engine_id == "nemotron-en"
    h.passes["nemotron-en"] = False
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
        h.data["memoryFraction"] = 0.9
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
    await h.manager.restore()
    h.passes["nemotron-en"] = False
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
    await h.manager.request_switch("nemotron-en", True)
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
    t = asyncio.create_task(h.manager.request_switch("nemotron-en", True))
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


def slow_calibration(monkeypatch, seconds):
    async def scenario(pool, count):
        await asyncio.sleep(seconds)
        return {"passed": True, "speakers": count}

    monkeypatch.setattr(capacity, "scenario", scenario)


async def test_restore_times_the_check_from_the_attempt_not_the_cooldown(tmp_path, monkeypatch):
    """A cooldown ending late in a wait must not roll back the load that follows it."""
    h = make(tmp_path, monkeypatch, ready=("nemotron-en",),
             settings=EngineSettings(engine="nemotron-en"))
    h.safety.policy = replace(h.safety.policy, headroom_window=0.2)
    h.safety.journal.write(1, time.time() + 1.5, False, "previous run")
    slow_calibration(monkeypatch, 0.3)
    await h.manager.restore()
    try:
        await h.manager.start_safety()
        await until(lambda: h.safety.state == "checking")
        async with asyncio.timeout(4):
            while h.safety.state != "ready":
                await asyncio.sleep(0.01)
        # The check ran past check_timeout=2 measured from start_safety, but not from its start.
        assert h.safety.attempt_started + 0.4 < h.safety.clock()
        assert h.manager.active == "nemotron-en" and "rolling_back" not in h.changes
    finally:
        await h.manager.stop()
        await h.safety.close()


async def test_switch_times_the_check_from_the_attempt_not_the_headroom_wait(h, monkeypatch):
    h.safety.policy = replace(h.safety.policy, headroom_window=1.2)
    h.data["cpuPressureRecent"] = 30  # Pressure left by the engine that just stopped.
    slow_calibration(monkeypatch, 0.3)
    await h.manager.request_switch("nemotron-en", True)
    await until(lambda: h.manager.status.state == "checking")
    await asyncio.sleep(0.1)
    h.data["cpuPressureRecent"] = 0
    async with asyncio.timeout(5):
        status = await finished(h)
    assert status.state == "succeeded" and h.manager.active == "nemotron-en"
