import asyncio
from types import SimpleNamespace

import pytest

from cassini_live import admin_form
from cassini_live.engine_switch import SwitchRefused, SwitchStatus
from cassini_live.settings_sync import BACKOFF_S, LEGACY_KEYS, SettingsSync

ENGINE = admin_form.ENGINE_FIELD


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


class Nextcloud:
    def __init__(self, values):
        self.values, self.forms, self.fail = values, [], None
        self.deletes, self.fail_delete = [], None
        outer = self

        class AppConfig:
            async def get_values(self, keys):
                outer.check()
                return [SimpleNamespace(key=k, value=outer.values[k]) for k in keys
                        if k in outer.values]

            async def set_value(self, key, value):
                outer.check()
                outer.values[key] = value

            async def delete(self, keys):
                outer.check()
                outer.deletes.append(keys)
                if outer.fail_delete:
                    raise outer.fail_delete
                for key in [keys] if isinstance(keys, str) else keys:
                    outer.values.pop(key, None)

        class Settings:
            async def register_form(self, form):
                outer.check()
                outer.forms.append(form)

        self.appconfig_ex = AppConfig()
        self.ui = SimpleNamespace(settings=Settings())

    def check(self):
        if self.fail:
            raise self.fail


class Form:
    def __init__(self, data):
        self.data = data

    def to_dict(self):
        return self.data


class Manager:
    def __init__(self):
        self.active = "whistle"
        self.status = SwitchStatus()
        self.on_change = None
        self.requests, self.refusals, self.cancels = [], [], []
        self.refuse = None
        self.cancel_reason = None
        self.progress = 0

    @property
    def busy(self):
        return self.status.state in {"validating", "downloading", "checking", "rolling_back"}

    @property
    def desired(self):
        return self.status.target if self.busy else self.active

    async def request_switch(self, engine):
        self.requests.append(engine)
        refuse = self.refuse(engine) if self.refuse else None
        if refuse:
            raise refuse
        self.status = SwitchStatus("downloading", engine, self.active)
        self.cancel_reason = None

    async def cancel(self, reason):
        # Like EngineManager: the switch stays busy until the cancel has been carried out.
        self.cancels.append(reason)
        self.cancel_reason = reason

    def note_refusal(self, refused, engine):
        self.refusals.append((refused.code, engine))

    async def refresh_network(self):
        pass

    def panel(self):
        return {"active": self.active, "state": self.status.state, "progress": self.progress}


def build(panel, status, notes, page_url=admin_form.DEFAULT_PAGE_URL):
    return Form({**panel, "notes": list(notes), "page_url": page_url})


@pytest.fixture
def h():
    manager, clock = Manager(), Clock()
    nc = Nextcloud({})
    sync = SettingsSync(manager, lambda: nc, dict, build=build, clock=clock, publish_min_s=3)
    return SimpleNamespace(manager=manager, clock=clock, nc=nc, sync=sync)


async def test_first_poll_writes_back_a_stale_choice_without_switching(h):
    h.nc.values[ENGINE] = "nemotron-en"
    await h.sync.poll_once()
    assert h.nc.values[ENGINE] == "whistle" and h.manager.requests == []
    assert h.manager.refusals == [("not_running", "nemotron-en")]
    await h.sync.poll_once()
    assert h.manager.requests == []


async def test_new_choice_requests_a_switch(h):
    await h.sync.poll_once()
    h.nc.values[ENGINE] = "nemotron-en"
    await h.sync.poll_once()
    assert h.manager.requests == ["nemotron-en"]
    assert h.nc.values[ENGINE] == "nemotron-en"


async def test_refusal_is_noted_written_back_and_republished(h):
    await h.sync.poll_once()
    await h.sync.publish()
    h.manager.refuse = lambda e: SwitchRefused(400, "not_eligible", "Cannot run here")
    h.nc.values[ENGINE] = "nemotron-multilingual"
    await h.sync.poll_once()
    assert h.manager.refusals == [("not_eligible", "nemotron-multilingual")]
    assert h.nc.values[ENGINE] == "whistle"
    assert h.sync.dirty.is_set()


async def test_unknown_engine_is_refused(h):
    await h.sync.poll_once()
    h.nc.values[ENGINE] = "vosk"
    await h.sync.poll_once()
    assert h.manager.refusals == [("unknown_engine", "vosk")] and h.nc.values[ENGINE] == "whistle"


async def test_choices_while_busy(h):
    await h.sync.poll_once()
    h.manager.status = SwitchStatus("downloading", "nemotron-en", "whistle")
    h.nc.values[ENGINE] = "whistle"
    h.sync.seen = "nemotron-en"
    await h.sync.poll_once()
    assert h.manager.cancels == ["Cancelled in the administration settings"]
    # The cancel is still being carried out: the admin's choice is not overwritten by the
    # target, neither in this poll nor in the next ones.
    assert h.nc.values[ENGINE] == "whistle"
    await h.sync.poll_once()
    assert h.nc.values[ENGINE] == "whistle" and len(h.manager.cancels) == 1
    h.manager.cancel_reason = None
    h.nc.values[ENGINE] = "nemotron-multilingual"
    await h.sync.poll_once()
    assert h.nc.values[ENGINE] == "nemotron-en"
    assert any("in progress" in note for note in h.sync.notes)
    h.manager.status = SwitchStatus("rolling_back", "nemotron-en", "whistle")
    h.nc.values[ENGINE] = "whistle"
    await h.sync.poll_once()
    assert len(h.manager.cancels) == 1 and h.nc.values[ENGINE] == "nemotron-en"


async def test_previous_engine_while_validating_is_not_a_cancel(h):
    """The form does not offer "select to cancel" while validating, and does not cancel."""
    await h.sync.poll_once()
    h.manager.status = SwitchStatus("validating", "nemotron-en", "whistle")
    h.nc.values[ENGINE] = "whistle"
    h.sync.seen = "nemotron-en"
    await h.sync.poll_once()
    assert h.manager.cancels == [] and h.manager.refusals == []
    assert h.nc.values[ENGINE] == "nemotron-en"
    assert any("in progress" in note for note in h.sync.notes)


async def test_reconcile_after_failed_switch(h):
    await h.sync.poll_once()
    h.nc.values[ENGINE] = "nemotron-en"
    await h.sync.poll_once()
    h.manager.status = SwitchStatus("failed", "nemotron-en", "whistle", rolled_back=True)
    await h.sync.poll_once()
    assert h.nc.values[ENGINE] == "whistle"
    assert not any("in progress" in note for note in h.sync.notes)


async def test_switch_started_on_the_page_is_mirrored_once(h):
    h.nc.values[ENGINE] = "whistle"
    await h.sync.poll_once()
    writes = []
    set_value = h.nc.appconfig_ex.set_value

    async def recording(key, value):
        writes.append((key, value))
        await set_value(key, value)

    h.nc.appconfig_ex.set_value = recording
    h.manager.status = SwitchStatus("downloading", "nemotron-en", "whistle")
    await h.sync.poll_once()
    await h.sync.poll_once()
    assert writes == [(ENGINE, "nemotron-en")]
    assert h.manager.requests == [] and h.manager.cancels == []
    # Finished elsewhere: the form already shows the new engine, nothing more is written.
    h.manager.status = SwitchStatus("succeeded", "nemotron-en", "whistle")
    h.manager.active = "nemotron-en"
    await h.sync.poll_once()
    await h.sync.poll_once()
    assert writes == [(ENGINE, "nemotron-en")]
    assert h.manager.requests == []


async def test_refusal_from_the_form_is_noted_and_reverted(h):
    await h.sync.poll_once()
    h.manager.refuse = lambda e: SwitchRefused(409, "disabled", "Cassini Live is not enabled")
    h.nc.values[ENGINE] = "nemotron-en"
    await h.sync.poll_once()
    assert h.manager.refusals == [("disabled", "nemotron-en")]
    assert h.nc.values[ENGINE] == "whistle" and h.manager.requests == ["nemotron-en"]
    await h.sync.poll_once()
    assert h.manager.requests == ["nemotron-en"]  # never retried


async def test_switch_started_on_the_page_is_mirrored_on_a_fresh_install(h):
    await h.sync.poll_once()  # no engine value saved yet
    h.manager.status = SwitchStatus("downloading", "nemotron-en", "whistle")
    await h.sync.poll_once()
    assert h.nc.values[ENGINE] == "nemotron-en" and h.manager.cancels == []


async def test_legacy_keys_are_deleted_once(h):
    h.nc.values.update({key: "1" for key in LEGACY_KEYS})
    await h.sync.poll_once()
    await h.sync.poll_once()
    assert h.nc.deletes == [LEGACY_KEYS]
    assert not any(key in h.nc.values for key in LEGACY_KEYS)


async def test_legacy_cleanup_failure_is_ignored_and_bounded(h):
    h.nc.fail_delete = OSError("forbidden")
    for _ in range(6):
        await h.sync.poll_once()
    assert len(h.nc.deletes) == 3
    assert h.sync.delay == h.sync.poll_s  # the poll itself kept working


async def test_form_is_built_with_the_page_url(h):
    sync = SettingsSync(h.manager, lambda: h.nc, dict, build=build,
                        page_url="/nc/index.php/apps/app_api/proxy/x/settings/index.html")
    await sync.publish()
    assert h.nc.forms[-1].to_dict()["page_url"].startswith("/nc/index.php/")
    await sync.stop(final="Stopped.")
    assert h.nc.forms[-1].to_dict()["page_url"].startswith("/nc/index.php/")
    await h.sync.publish()
    assert h.nc.forms[-1].to_dict()["page_url"] == admin_form.DEFAULT_PAGE_URL


async def test_identical_forms_are_not_resent(h):
    await h.sync.publish()
    await h.sync.publish()
    assert len(h.nc.forms) == 1
    await h.sync.publish(force=True)
    assert len(h.nc.forms) == 2
    h.manager.active = "nemotron-en"
    await h.sync.publish()
    assert len(h.nc.forms) == 3


async def test_notifications_are_coalesced_with_a_trailing_publish(h):
    sync = SettingsSync(h.manager, lambda: h.nc, dict, build=build, publish_min_s=0.05)
    task = asyncio.create_task(sync._publish_loop())
    try:
        for value in range(10):
            h.manager.progress = value
            sync.notify()
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.15)
        assert 1 <= len(h.nc.forms) <= 3
        assert h.nc.forms[-1].to_dict()["progress"] == 9
    finally:
        task.cancel()


async def test_progress_below_the_next_step_does_not_publish(h):
    def by_step(panel, status, notes, page_url=None):
        return Form({**panel, "progress": panel["progress"] - panel["progress"] % 5})

    sync = SettingsSync(h.manager, lambda: h.nc, dict, build=by_step)
    h.manager.progress = 41
    await sync.publish()
    h.manager.progress = 44
    await sync.publish()
    assert len(h.nc.forms) == 1
    h.manager.progress = 45
    await sync.publish()
    assert len(h.nc.forms) == 2


async def test_ocs_errors_back_off_and_never_propagate(h):
    h.nc.fail = OSError("Nextcloud is down")
    await h.sync.poll_once()
    assert h.sync.delay == BACKOFF_S
    await h.sync.publish()
    assert h.sync.publish_failed and h.nc.forms == []
    h.nc.fail = None
    await h.sync.poll_once()
    assert h.sync.delay == h.sync.poll_s and h.sync.dirty.is_set()


async def test_start_and_stop_with_final_note(h):
    await h.sync.start()
    await h.sync.start()
    assert h.manager.on_change == h.sync.notify
    await asyncio.sleep(0.01)
    await h.sync.stop(final="Cassini Live is disabled.")
    assert h.manager.on_change is None
    assert h.nc.forms[-1].to_dict()["notes"] == ["Cassini Live is disabled."]
    count = len(h.nc.forms)
    await asyncio.sleep(0.01)
    assert len(h.nc.forms) == count


async def test_status_changes_outside_the_manager_are_republished(h):
    status = {"captions": "checking"}
    sync = SettingsSync(h.manager, lambda: h.nc, lambda: dict(status),
                        build=lambda panel, st, notes, page_url: Form({**panel, **st}),
                        publish_min_s=0.01)
    task = asyncio.create_task(sync._publish_loop())
    try:
        await sync.poll_once()
        await asyncio.sleep(0.05)
        assert h.nc.forms[-1].to_dict()["captions"] == "checking"
        published = len(h.nc.forms)
        await sync.poll_once()  # nothing changed: no new registration
        await asyncio.sleep(0.05)
        assert len(h.nc.forms) == published
        status["captions"] = "ready"  # the capacity check finished
        await sync.poll_once()
        await asyncio.sleep(0.05)
        assert h.nc.forms[-1].to_dict()["captions"] == "ready"
    finally:
        task.cancel()


async def test_a_deferred_choice_is_neither_refused_nor_overwritten(h, monkeypatch):
    # The saved choice waits for a restart with suitable limits; Whistle runs meanwhile.
    monkeypatch.setattr(type(h.manager), "desired", property(lambda self: "nemotron-en"))
    h.nc.values[ENGINE] = "nemotron-en"
    await h.sync.poll_once()
    await h.sync.poll_once()
    assert h.nc.values[ENGINE] == "nemotron-en"
    assert h.manager.refusals == [] and h.manager.requests == []


async def deferred_sync(tmp_path, monkeypatch, value="nemotron-en"):
    """A real EngineManager whose Nemotron English waits for suitable limits."""
    from cassini_live.resources import MIB
    from cassini_live.settings import EngineSettings
    from tests.test_engine_switch import DATA, make

    e = make(tmp_path, monkeypatch, data={**DATA, "memoryLimitBytes": 2048 * MIB},
             ready=("nemotron-en",), settings=EngineSettings(engine="nemotron-en"))
    await e.manager.restore()
    nc = Nextcloud({ENGINE: value})
    sync = SettingsSync(e.manager, lambda: nc, dict)
    return e, nc, sync


async def test_choosing_the_running_engine_drops_a_deferred_choice(tmp_path, monkeypatch):
    e, nc, sync = await deferred_sync(tmp_path, monkeypatch)
    await sync.poll_once()
    assert nc.values[ENGINE] == "nemotron-en"
    nc.values[ENGINE] = "whistle"
    await sync.poll_once()
    assert nc.values[ENGINE] == "whistle" and e.settings.read().engine == "whistle"
    assert e.manager.last_refusal is None and e.manager.deferred is None


async def test_deferred_form_text_and_labels(tmp_path, monkeypatch):
    e, nc, sync = await deferred_sync(tmp_path, monkeypatch)
    await sync.poll_once()
    await sync.publish(force=True)
    form = nc.forms[-1].to_dict()
    assert form["description"].count("Using Whistle.") == 1
    assert "stays selected" in form["description"]
    field = form["fields"][0]
    labels = {o["value"]: o["name"] for o in field["options"]}
    assert labels["nemotron-en"].startswith("Nemotron English — selected;")
    assert labels["whistle"].startswith("Whistle — in use meanwhile (select to keep it)")
    assert field["default"] == "nemotron-en"


async def test_a_restore_failure_is_not_also_reported_as_chosen_while_down(h):
    failed = {"code": "restore_failed", "engine": "nemotron-en"}
    h.manager.saved = SimpleNamespace(last_error=failed)
    h.nc.values[ENGINE] = "nemotron-en"
    await h.sync.poll_once()
    assert h.manager.refusals == [] and h.nc.values[ENGINE] == "whistle"
