"""Late-joiner catch-up: the per-call finals buffer and the /history route (D-830)."""

import asyncio

from fastapi.testclient import TestClient

from cassini_live.talk.client import HISTORY_MAX, SpreedClient
from cassini_live.talk.types import HPBSettings, Transcript


def make_client():
    settings = HPBSettings(server="ws://unused/spreed", stunservers=[], turnservers=[])
    return SpreedClient("room", settings, "en", None)


async def _send(client, **kw):
    # send_transcript with no targets still buffers finals, and skips the wire.
    await client.send_transcript(Transcript(**kw))


def test_finals_are_buffered_oldest_first_with_seq_and_partials_are_not():
    client = make_client()

    async def run():
        await _send(client, final=True, lang_id="en", message="first", speaker_session_id="a")
        await _send(client, final=False, lang_id="en", message="typing…", speaker_session_id="a")
        await _send(client, final=True, lang_id="en", message="second", speaker_session_id="b")
        # an empty final is skipped
        await _send(client, final=True, lang_id="en", message="", speaker_session_id="b")

    asyncio.run(run())
    snap = client.history_snapshot()
    assert [e["message"] for e in snap] == ["first", "second"]
    assert [e["type"] for e in snap] == ["final", "final"]
    assert [e["seq"] for e in snap] == [1, 2]
    assert client.history_head() == 2
    assert all(isinstance(e["t"], int) for e in snap)


def test_since_returns_only_newer_entries():
    client = make_client()

    async def run():
        await _send(client, final=True, lang_id="en", message="one", speaker_session_id="a")
        await _send(client, final=True, lang_id="en", message="two", speaker_session_id="a")

    asyncio.run(run())
    assert [e["message"] for e in client.history_snapshot(since=1)] == ["two"]
    assert client.history_snapshot(since=2) == []


def test_joins_are_recorded_on_the_timeline():
    client = make_client()
    client.record_join(
        {"sessionId": "sess-alice", "userid": "alice", "user": {"displayname": "Alice"}}
    )
    client.record_join({"sessionId": "sess-bob", "userid": "bob"})  # no display name
    snap = client.history_snapshot()
    assert [e["type"] for e in snap] == ["join", "join"]
    assert snap[0]["speakerSessionId"] == "sess-alice"
    assert snap[0]["displayName"] == "Alice"
    assert snap[0]["userId"] == "alice"
    assert snap[1]["displayName"] is None


def test_history_is_bounded():
    client = make_client()

    async def run():
        for i in range(HISTORY_MAX + 50):
            await _send(client, final=True, lang_id="en", message=str(i), speaker_session_id="a")

    asyncio.run(run())
    snap = client.history_snapshot()
    assert len(snap) == HISTORY_MAX
    assert snap[0]["message"] == "50"  # oldest 50 evicted
    assert snap[-1]["message"] == str(HISTORY_MAX + 49)


# --- the /history route ----------------------------------------------------

class FakeClientWithHistory:
    def __init__(self, entries):
        self._entries = entries
        self.since_seen = None

    def history_snapshot(self, since=0):
        self.since_seen = since
        return [e for e in self._entries if e.get("seq", 0) > since]

    def history_head(self):
        return max((e.get("seq", 0) for e in self._entries), default=0)


CALLS = {"set_user": 0}


class FakeNc:
    """Stands in for AsyncNextcloudApp: membership = the OCS call succeeds."""

    def __init__(self, *, member):
        self._member = member

    async def set_user(self, user):
        CALLS["set_user"] += 1
        self._user = user

    async def ocs(self, method, path):
        if not self._member:
            raise RuntimeError("403 not a participant")
        return [{"displayName": "Alice", "sessionIds": ["s1"]}]


def build_history_app(*, client_for=None, member=True):
    from cassini_live.app import create_app
    from cassini_live.config import Config
    from cassini_live.pool import WorkerPool
    from cassini_live.service import CaptionService
    from tests.test_streaming import FakeWorker  # noqa: F401 (import side effects as elsewhere)

    CALLS["set_user"] = 0
    config = Config(None, 2, "secret", "")
    service = CaptionService(config)
    service.enabled = True
    service.settings = object()
    if client_for:
        service.clients.update(client_for)
    app = create_app(config, WorkerPool(2), service, nc_factory=lambda: FakeNc(member=member))
    app.state.safety.state = "ready"
    return app, service


def test_history_returns_entries_and_head_for_a_member(headers):
    entries = [
        {"seq": 1, "type": "final", "speakerSessionId": "a", "message": "x", "langId": "en", "t": 1}
    ]
    app, _ = build_history_app(client_for={"room1": FakeClientWithHistory(entries)}, member=True)
    res = TestClient(app).get("/history?token=room1", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"entries": entries, "head": 1}


def test_history_passes_since_through(headers):
    client = FakeClientWithHistory([])
    app, _ = build_history_app(client_for={"room1": client}, member=True)
    TestClient(app).get("/history?token=room1&since=7", headers=headers)
    assert client.since_seen == 7


def test_history_refuses_a_non_member(headers):
    app, _ = build_history_app(client_for={"room1": FakeClientWithHistory([])}, member=False)
    res = TestClient(app).get("/history?token=room1", headers=headers)
    assert res.status_code == 403


def test_history_is_empty_when_no_caption_client(headers):
    # No membership check needed (and none performed) when nothing is buffered.
    app, _ = build_history_app(client_for={}, member=False)
    res = TestClient(app).get("/history?token=room-with-no-captions", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"entries": [], "head": 0}


def test_membership_is_checked_once_then_cached(headers):
    # The panel polls, so the OCS membership check is cached per (user, room).
    entries = [
        {"seq": 1, "type": "final", "speakerSessionId": "a", "message": "x", "langId": "en", "t": 1}
    ]
    app, _ = build_history_app(client_for={"room1": FakeClientWithHistory(entries)}, member=True)
    client = TestClient(app)
    for _ in range(3):
        assert client.get("/history?token=room1", headers=headers).status_code == 200
    # one OCS round-trip for three polls
    assert CALLS["set_user"] == 1


def test_history_requires_a_token(headers):
    app, _ = build_history_app(client_for={}, member=True)
    assert TestClient(app).get("/history", headers=headers).status_code == 400


def test_history_requires_the_appapi_gateway():
    app, _ = build_history_app(client_for={"room1": FakeClientWithHistory([])}, member=True)
    # no auth headers → middleware refuses, like every route
    assert TestClient(app).get("/history?token=room1").status_code == 401
