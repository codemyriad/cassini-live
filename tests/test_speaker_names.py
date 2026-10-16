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


@pytest.fixture(autouse=True)
def video_on(monkeypatch):
    monkeypatch.setenv("CASSINI_LIVE_DEBUG_VIDEO", "1")


def room(**fields):
    defaults = {
        "room_token": "tok",
        "name_user": "alice",
        "speaker_names": {},
        "nc_sid_map": {},
        "_names_at": -1e9,
        "_names_task": None,
        "_closing": False,
        "defunct": asyncio.Event(),
    }
    fake = SimpleNamespace(**{**defaults, **fields})
    fake.refresh_names = lambda: SpreedClient.refresh_names(fake)
    fake._refresh_names = lambda: SpreedClient._refresh_names(fake)
    return fake


class FakeApp:
    calls = []
    reply = []

    async def set_user(self, user):
        self.calls.append(("user", user))

    async def ocs(self, method, path, **kwargs):
        self.calls.append((method, path))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


async def test_names_map_nextcloud_sessions_to_signaling_sessions(monkeypatch):
    FakeApp.calls, FakeApp.reply = (
        [],
        [
            {"actorType": "users", "displayName": "Ana Ferreira", "sessionIds": ["nc1"]},
            {"actorType": "guests", "displayName": "Marta", "sessionIds": ["nc2", "nc3"]},
            {"actorType": "users", "displayName": "Away", "sessionIds": []},
        ],
    )
    monkeypatch.setattr(module, "AsyncNextcloudApp", FakeApp)
    fake = room(nc_sid_map={"nc1": "sig1", "nc3": "sig3"})
    await SpreedClient._refresh_names(fake)
    assert FakeApp.calls == [
        ("user", "alice"),
        ("GET", "/ocs/v2.php/apps/spreed/api/v4/room/tok/participants"),
    ]
    assert SpreedClient.speaker_name(fake, "sig1") == "Ana Ferreira"
    assert SpreedClient.speaker_name(fake, "sig3") == "Guest · Marta"
    assert SpreedClient.speaker_name(fake, "sig-unknown") is None


async def test_a_failed_names_request_keeps_generic_labels_and_logs_no_names(monkeypatch, caplog):
    caplog.set_level("DEBUG", logger="lt.spreed_client")
    FakeApp.calls, FakeApp.reply = [], RuntimeError("403 for Ana Ferreira")
    monkeypatch.setattr(module, "AsyncNextcloudApp", FakeApp)
    fake = room(nc_sid_map={"nc1": "sig1"})
    await SpreedClient._refresh_names(fake)
    assert fake.speaker_names == {}
    assert SpreedClient.speaker_name(fake, "sig1") is None
    assert "Ana Ferreira" not in caplog.text
    assert "Could not read participant names (RuntimeError)" in caplog.text


async def test_participant_bursts_coalesce_into_one_request_per_window(monkeypatch):
    FakeApp.calls, FakeApp.reply = [], []
    monkeypatch.setattr(module, "AsyncNextcloudApp", FakeApp)
    monkeypatch.setattr(module, "NAMES_REFRESH_S", 0.05)
    fake = room()
    for _ in range(5):
        SpreedClient.refresh_names(fake)
    await fake._names_task
    assert sum(1 for call in FakeApp.calls if call[0] == "GET") == 1
    SpreedClient.refresh_names(fake)  # within the window: waits it out, then reads once
    await fake._names_task
    assert sum(1 for call in FakeApp.calls if call[0] == "GET") == 2


async def test_no_user_no_request(monkeypatch):
    FakeApp.calls = []
    monkeypatch.setattr(module, "AsyncNextcloudApp", FakeApp)
    fake = room(name_user=None)
    SpreedClient.refresh_names(fake)
    assert fake._names_task is None and FakeApp.calls == []
    SpreedClient.note_user(fake, None)
    assert fake.name_user is None
    SpreedClient.note_user(fake, "bob")
    await fake._names_task
    assert fake.name_user == "bob" and ("user", "bob") in FakeApp.calls
    SpreedClient.note_user(fake, "carol")  # the first room member is kept
    assert fake.name_user == "bob"


async def test_names_are_not_read_without_the_diagnostic_video(monkeypatch):
    monkeypatch.delenv("CASSINI_LIVE_DEBUG_VIDEO")
    FakeApp.calls = []
    monkeypatch.setattr(module, "AsyncNextcloudApp", FakeApp)
    fake = room()
    SpreedClient.refresh_names(fake)
    SpreedClient.note_user(fake, "bob")
    assert fake._names_task is None and FakeApp.calls == []
