import asyncio
from types import SimpleNamespace

import pytest

from cassini_live import admin_form
from cassini_live.engine_switch import SwitchRefused, SwitchStatus
from cassini_live.settings_sync import BACKOFF_S, LICENSE_GRACE_S, LICENSE_NOTE, SettingsSync

ENGINE, LICENSE = admin_form.ENGINE_FIELD, admin_form.LICENSE_FIELD
CLEANUP = admin_form.CLEANUP_FIELD


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


class Nextcloud:
    def __init__(self, values):
        self.values, self.forms, self.fail = values, [], None
        outer = self

        class AppConfig:
            async def get_values(self, keys):
                outer.check()
                return [SimpleNamespace(key=k, value=outer.values[k]) for k in keys
                        if k in outer.values]

            async def set_value(self, key, value):
                outer.check()
                outer.values[key] = value

            async def delete(self, key):
                outer.check()
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
        self.requests, self.refusals, self.cancels, self.cleanups = [], [], [], 0
        self.refuse = None
        self.progress = 0

    @property
    def busy(self):
        return self.status.state in {"downloading", "checking", "rolling_back"}

    @property
    def desired(self):
        return self.status.target if self.busy else self.active

    async def request_switch(self, engine, accept_license, user=""):
        self.requests.append((engine, accept_license, user))
        refuse = self.refuse(engine, accept_license) if self.refuse else None
        if refuse:
            raise refuse
        self.status = SwitchStatus("downloading", engine, self.active)

    async def cancel(self, reason):
        self.cancels.append(reason)

    async def delete_unused_models(self):
        self.cleanups += 1
        return []

    def note_refusal(self, refused, engine):
        self.refusals.append((refused.code, engine))

    async def refresh_network(self):
        pass

    def panel(self):
        return {"active": self.active, "state": self.status.state, "progress": self.progress}


def build(panel, status, notes):
    return Form({**panel, "notes": list(notes)})


def license_refusal(engine, accepted):
    if engine == "nemotron-en" and not accepted:
        return SwitchRefused(400, "license_required", "Accept the license")
    return None


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
    h.nc.values.update({ENGINE: "nemotron-en", LICENSE: "1"})
    await h.sync.poll_once()
    assert h.manager.requests == [("nemotron-en", True, "")]
    assert h.nc.values[ENGINE] == "nemotron-en"


async def test_refusal_is_noted_written_back_and_republished(h):
    await h.sync.poll_once()
    await h.sync.publish()
    h.manager.refuse = lambda e, a: SwitchRefused(400, "not_eligible", "Cannot run here")
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


async def test_license_ticked_later_gives_one_switch(h):
    h.manager.refuse = license_refusal
    await h.sync.poll_once()
    h.nc.values[ENGINE] = "nemotron-en"
    await h.sync.poll_once()
    assert LICENSE_NOTE in h.sync.notes and h.nc.values[ENGINE] == "nemotron-en"
    h.clock.now += 5
    h.nc.values[LICENSE] = "true"
    await h.sync.poll_once()
    assert h.manager.requests[-1] == ("nemotron-en", True, "")
    assert h.manager.status.target == "nemotron-en"
    assert LICENSE_NOTE not in h.sync.notes and h.manager.refusals == []
    await h.sync.poll_once()
    assert len([r for r in h.manager.requests if r[1]]) == 1


async def test_license_never_ticked_is_refused_after_grace(h):
    h.manager.refuse = license_refusal
    await h.sync.poll_once()
    h.nc.values[ENGINE] = "nemotron-en"
    await h.sync.poll_once()
    h.clock.now += LICENSE_GRACE_S + 1
    await h.sync.poll_once()
    assert h.manager.refusals == [("license_required", "nemotron-en")]
    assert h.nc.values[ENGINE] == "whistle" and not h.sync.pending
    assert LICENSE_NOTE not in h.sync.notes


async def test_choices_while_busy(h):
    await h.sync.poll_once()
    h.manager.status = SwitchStatus("downloading", "nemotron-en", "whistle")
    h.nc.values[ENGINE] = "whistle"
    h.sync.seen = "nemotron-en"
    await h.sync.poll_once()
    assert h.manager.cancels == ["Cancelled in the administration settings"]
    h.nc.values[ENGINE] = "nemotron-multilingual"
    await h.sync.poll_once()
    assert h.nc.values[ENGINE] == "nemotron-en"
    assert any("in progress" in note for note in h.sync.notes)
    h.manager.status = SwitchStatus("rolling_back", "nemotron-en", "whistle")
    h.nc.values[ENGINE] = "whistle"
    await h.sync.poll_once()
    assert len(h.manager.cancels) == 1 and h.nc.values[ENGINE] == "nemotron-en"


async def test_reconcile_after_failed_switch(h):
    await h.sync.poll_once()
    h.nc.values[ENGINE] = "nemotron-en"
    await h.sync.poll_once()
    h.manager.status = SwitchStatus("failed", "nemotron-en", "whistle", rolled_back=True)
    await h.sync.poll_once()
    assert h.nc.values[ENGINE] == "whistle"
    assert not any("in progress" in note for note in h.sync.notes)


async def test_cleanup_box(h):
    await h.sync.poll_once()
    h.nc.values[CLEANUP] = "1"
    await h.sync.poll_once()
    assert h.manager.cleanups == 1 and CLEANUP not in h.nc.values


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
    def by_step(panel, status, notes):
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
                        build=lambda panel, st, notes: Form({**panel, **st}), publish_min_s=0.01)
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
