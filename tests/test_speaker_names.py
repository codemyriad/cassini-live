"""Participant names for the diagnostic video: read as a room member, never required."""

import asyncio
from types import SimpleNamespace

import pytest

from cassini_live.talk import client as module
from cassini_live.talk.client import SpreedClient, speaker_label


def test_labels_mark_guests_and_fall_back_without_a_name():
    assert speaker_label({"actorType": "users", "displayName": "Ana Ferreira"}) == "Ana Ferreira"
    assert speaker_label({"actorType": "users", "displayName": ""}) == "Participant"
    assert speaker_label({"actorType": "guests", "displayName": "Marta"}) == "Guest · Marta"
    assert speaker_label({"actorType": "emails"}) == "Guest"


def room(**fields):
    defaults = {
        "room_token": "tok",
        "name_user": "alice",
        "names_shown": True,  # this room owns the running diagnostic video
        "speaker_names": {},
        "nc_sid_map": {},
        "_names_at": -1e9,
        "_names_again": False,
        "_names_app": None,
        "_names_task": None,
        "_closing": False,
        "defunct": asyncio.Event(),
    }
    fake = SimpleNamespace(**{**defaults, **fields})
    methods = ("refresh_names", "_refresh_names", "_read_names", "note_user", "show_names")
    for method in (*methods, "speaker_name"):
        setattr(fake, method, getattr(SpreedClient, method).__get__(fake))
    return fake


class FakeApp:
    calls = []
    reply = []
    created = 0
    gate = None  # an Event the participants request waits on, when set

    def __init__(self):
        FakeApp.created += 1
        self.user = None

    async def set_user(self, user):
        # Like nc_py_api: capabilities are read again only when the user changes.
        if user != self.user:
            self.user = user
            self.calls.append(("user", user))

    async def ocs(self, method, path, **kwargs):
        self.calls.append((method, path))
        if self.gate is not None:
            await self.gate.wait()
        reply = FakeApp.reply(self.user) if callable(FakeApp.reply) else FakeApp.reply
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture(autouse=True)
def fake_app(monkeypatch):
    FakeApp.calls, FakeApp.reply, FakeApp.created, FakeApp.gate = [], [], 0, None
    monkeypatch.setattr(module, "AsyncNextcloudApp", FakeApp)


def gets():
    return sum(1 for call in FakeApp.calls if call[0] == "GET")


async def test_names_map_nextcloud_sessions_to_signaling_sessions(monkeypatch):
    FakeApp.reply = [
        {"actorType": "users", "displayName": "Ana Ferreira", "sessionIds": ["nc1"]},
        {"actorType": "guests", "displayName": "Marta", "sessionIds": ["nc2", "nc3"]},
        {"actorType": "users", "displayName": "Away", "sessionIds": []},
    ]
    fake = room(nc_sid_map={"nc1": "sig1", "nc3": "sig3"})
    await fake._refresh_names()
    assert FakeApp.calls == [
        ("user", "alice"),
        ("GET", "/ocs/v2.php/apps/spreed/api/v4/room/tok/participants"),
    ]
    assert SpreedClient.speaker_name(fake, "sig1") == "Ana Ferreira"
    assert SpreedClient.speaker_name(fake, "sig3") == "Guest · Marta"
    assert SpreedClient.speaker_name(fake, "sig-unknown") is None


async def test_a_failed_names_request_keeps_generic_labels_and_logs_no_names(monkeypatch, caplog):
    caplog.set_level("DEBUG", logger="lt.spreed_client")
    FakeApp.reply = RuntimeError("403 for Ana Ferreira")
    fake = room(nc_sid_map={"nc1": "sig1"})
    await fake._refresh_names()
    assert fake.speaker_names == {}
    assert SpreedClient.speaker_name(fake, "sig1") is None
    assert "Ana Ferreira" not in caplog.text
    assert "Could not read participant names (RuntimeError)" in caplog.text


async def test_participant_bursts_coalesce_into_one_request_per_window(monkeypatch):
    monkeypatch.setattr(module, "NAMES_REFRESH_S", 0.05)
    fake = room()
    for _ in range(5):  # before the request goes out: all read by it
        fake.refresh_names()
    await fake._names_task
    assert gets() == 1
    fake.refresh_names()  # within the window: waits it out, then reads once
    await fake._names_task
    assert gets() == 2


async def test_no_user_no_request(monkeypatch):
    fake = room(name_user=None)
    fake.refresh_names()
    assert fake._names_task is None and FakeApp.calls == []
    fake.note_user(None)
    assert fake.name_user is None
    fake.note_user("bob")
    await fake._names_task
    assert fake.name_user == "bob" and ("user", "bob") in FakeApp.calls


async def test_the_latest_user_to_enable_captions_is_asked(monkeypatch):
    monkeypatch.setattr(module, "NAMES_REFRESH_S", 0.01)
    fake = room(name_user=None)
    fake.note_user("bob")
    await fake._names_task
    fake.note_user("carol")
    assert fake.name_user == "carol"
    await fake._names_task
    assert FakeApp.calls[-2:] == [
        ("user", "carol"),
        ("GET", "/ocs/v2.php/apps/spreed/api/v4/room/tok/participants"),
    ]
    # One app per room: capabilities are read once per user, not once per request.
    assert FakeApp.created == 1


@pytest.mark.parametrize("status, forgotten", [(403, True), (404, True), (500, False)])
async def test_a_refused_user_is_replaced_by_the_next_one(monkeypatch, status, forgotten):
    from nc_py_api import NextcloudException

    monkeypatch.setattr(module, "NAMES_REFRESH_S", 0.01)
    FakeApp.reply = lambda user: (
        NextcloudException(status)
        if user == "bob"
        else [{"displayName": "Ana", "sessionIds": ["nc1"]}]
    )
    fake = room(name_user="bob", nc_sid_map={"nc1": "sig1"})
    fake.refresh_names()
    await fake._names_task
    assert (fake.name_user is None) is forgotten
    fake.note_user("carol")
    await fake._names_task
    assert fake.name_user == "carol" and fake.speaker_name("sig1") == "Ana"


async def test_a_join_during_a_request_is_read_by_one_more_request(monkeypatch):
    monkeypatch.setattr(module, "NAMES_REFRESH_S", 0.05)
    FakeApp.gate = asyncio.Event()
    fake = room()
    fake.refresh_names()
    while gets() == 0:
        await asyncio.sleep(0)
    for _ in range(3):  # joins while the reply is outstanding
        fake.refresh_names()
    started = asyncio.get_running_loop().time()
    FakeApp.gate.set()
    await fake._names_task
    assert gets() == 2
    # The follow-up still waits out the per-room window.
    assert asyncio.get_running_loop().time() - started >= 0.04


async def test_names_are_read_and_kept_only_while_the_rooms_video_runs():
    FakeApp.reply = [{"displayName": "Ana", "sessionIds": ["nc1"]}]
    fake = room(names_shown=False)
    fake.refresh_names()
    fake.note_user("bob")
    assert fake._names_task is None and FakeApp.calls == []
    fake.show_names(True)
    await fake._names_task
    assert fake.speaker_names == {"nc1": "Ana"}
    FakeApp.gate = asyncio.Event()
    fake.refresh_names()
    task = fake._names_task
    fake.show_names(False)  # the video stopped or moved to another room
    assert fake.speaker_names == {}
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled() and fake.speaker_names == {}
