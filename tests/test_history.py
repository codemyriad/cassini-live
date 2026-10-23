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


def test_finals_are_buffered_oldest_first_and_partials_are_not():
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
    assert [e["speakerSessionId"] for e in snap] == ["a", "b"]
    assert all(isinstance(e["t"], int) for e in snap)
    assert snap[0]["t"] <= snap[1]["t"]


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

    def history_snapshot(self):
        return list(self._entries)


class FakeNc:
    """Stands in for AsyncNextcloudApp: membership = the OCS call succeeds."""

    def __init__(self, *, member):
        self._member = member

    async def set_user(self, user):
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

    config = Config(None, 2, "secret", "")
    service = CaptionService(config)
    service.enabled = True
    service.settings = object()
    if client_for:
        service.clients.update(client_for)
    app = create_app(config, WorkerPool(2), service, nc_factory=lambda: FakeNc(member=member))
    app.state.safety.state = "ready"
    return app, service


def test_history_returns_entries_for_a_member(headers):
    entries = [{"speakerSessionId": "a", "message": "earlier line", "langId": "en", "t": 1}]
    app, _ = build_history_app(client_for={"room1": FakeClientWithHistory(entries)}, member=True)
    client = TestClient(app)
    res = client.get("/history?token=room1", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"entries": entries}


def test_history_refuses_a_non_member(headers):
    app, _ = build_history_app(client_for={"room1": FakeClientWithHistory([])}, member=False)
    res = TestClient(app).get("/history?token=room1", headers=headers)
    assert res.status_code == 403


def test_history_is_empty_when_no_caption_client(headers):
    # No membership check needed (and none performed) when nothing is buffered.
    app, _ = build_history_app(client_for={}, member=False)
    res = TestClient(app).get("/history?token=room-with-no-captions", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"entries": []}


def test_history_requires_a_token(headers):
    app, _ = build_history_app(client_for={}, member=True)
    assert TestClient(app).get("/history", headers=headers).status_code == 400


def test_history_requires_the_appapi_gateway():
    app, _ = build_history_app(client_for={"room1": FakeClientWithHistory([])}, member=True)
    # no auth headers → middleware refuses, like every route
    assert TestClient(app).get("/history?token=room1").status_code == 401
