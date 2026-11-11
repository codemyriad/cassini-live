"""The transcript panel's start/stop route: Talk's /api/v1/call/transcribe body, with the
checks Talk would have made re-done by acting as the caller."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from cassini_live.app import create_app
from cassini_live.call_access import CallSessions, RateLimit
from cassini_live.config import Config
from cassini_live.pool import WorkerPool
from cassini_live.service import CaptionService
from tests.test_streaming import FakeWorker  # noqa: F401 (import side effects as elsewhere)

ALICE_IN_CALL = {"actorType": "users", "actorId": "alice", "sessionIds": ["s-alice"], "inCall": 7}
BOB_IN_CALL = {"actorType": "users", "actorId": "bob", "sessionIds": ["s-bob"], "inCall": 3}


class FakeNc:
    """Stands in for AsyncNextcloudApp: the participants Talk returns to the acting user."""

    def __init__(self, participants, seen):
        self.participants, self.seen = participants, seen

    async def set_user(self, user):
        self.seen.append(("user", user))

    async def ocs(self, method, path):
        self.seen.append((method, path))
        if self.participants is None:
            raise RuntimeError("403 not a participant, or held in the lobby")
        return self.participants


def build(participants=(ALICE_IN_CALL, BOB_IN_CALL)):
    seen = []
    config = Config(None, 2, "secret", "")
    service = CaptionService(config)
    service.enabled = True
    service.settings = object()
    service.request = AsyncMock()
    rows = None if participants is None else list(participants)
    app = create_app(config, WorkerPool(2), service, nc_factory=lambda: FakeNc(rows, seen))
    app.state.safety.state = "ready"
    return TestClient(app), service, seen


@pytest.fixture
def panel_headers(headers):
    return {**headers, "X-Cassini-Request": "1", "Sec-Fetch-Site": "same-origin"}


BODY = {"roomToken": "room1", "ncSessionId": "s-alice", "enable": True, "langId": "auto"}


def post(client, headers, body=BODY, **kw):
    return client.post("/captions/transcribe", headers=headers, json=body, **kw)


def test_starts_captions_for_the_callers_own_session_in_the_call(panel_headers):
    client, service, seen = build()
    res = post(client, panel_headers)
    assert res.status_code == 200
    assert res.json() == {"message": "Captions enabled"}
    req = service.request.call_args.args[0]
    assert (req.roomToken, req.ncSessionId, req.enable, req.langId) == (
        "room1", "s-alice", True, "auto"
    )
    # Asked of Talk as the caller, not as the app.
    assert seen == [
        ("user", "alice"),
        ("GET", "/ocs/v2.php/apps/spreed/api/v4/room/room1/participants"),
    ]


def test_stops_captions_for_the_callers_own_session(panel_headers):
    client, service, _ = build()
    res = post(client, panel_headers, {**BODY, "enable": False})
    assert res.status_code == 200
    assert res.json() == {"message": "Captions disabled"}
    assert service.request.call_args.args[0].enable is False


def test_stopping_does_not_require_being_in_the_call(panel_headers):
    left = {**ALICE_IN_CALL, "inCall": 0}
    client, service, _ = build([left])
    assert post(client, panel_headers, {**BODY, "enable": True}).status_code == 403
    assert post(client, panel_headers, {**BODY, "enable": False}).status_code == 200
    assert service.request.await_count == 1


def test_refuses_someone_elses_session(panel_headers):
    client, service, _ = build()
    for enable in (True, False):
        res = post(client, panel_headers, {**BODY, "ncSessionId": "s-bob", "enable": enable})
        assert res.status_code == 403
    service.request.assert_not_awaited()


def test_refuses_a_non_member_or_a_caller_in_the_lobby(panel_headers):
    client, service, _ = build(participants=None)
    assert post(client, panel_headers).status_code == 403
    service.request.assert_not_awaited()


def test_a_guest_with_the_same_id_is_not_the_user(panel_headers):
    guest = {**ALICE_IN_CALL, "actorType": "guests"}
    client, service, _ = build([guest])
    assert post(client, panel_headers).status_code == 403


@pytest.mark.parametrize(
    "drop, extra, status",
    [
        ("X-Cassini-Request", {}, 403),
        (None, {"Sec-Fetch-Site": "cross-site"}, 403),
        (None, {"Origin": "https://evil.test"}, 403),
    ],
)
def test_csrf_defences(panel_headers, drop, extra, status):
    client, service, seen = build()
    headers = {k: v for k, v in panel_headers.items() if k != drop} | extra
    assert post(client, headers).status_code == status
    service.request.assert_not_awaited()
    assert seen == []


def test_requires_a_json_body(panel_headers):
    client, service, _ = build()
    res = client.post(
        "/captions/transcribe",
        headers={**panel_headers, "Content-Type": "text/plain"},
        content=b"roomToken=room1",
    )
    assert res.status_code == 415
    service.request.assert_not_awaited()


def test_validates_the_body_like_talks_route(panel_headers):
    client, service, _ = build()
    assert post(client, panel_headers, {**BODY, "langId": "xx"}).status_code == 422
    assert post(client, panel_headers, {"roomToken": "room1"}).status_code == 422
    assert (
        post(client, panel_headers, {**BODY, "translationTargetLangId": "de"}).status_code == 400
    )


def test_refuses_a_token_that_is_not_a_talk_token(panel_headers):
    client, _, seen = build()
    for token in ("../../cloud/user", "room1?x=1", "room 1"):
        assert post(client, panel_headers, {**BODY, "roomToken": token}).status_code == 400
    assert seen == []


def test_rate_limited_per_user(panel_headers):
    client, service, _ = build()
    for _ in range(10):
        assert post(client, panel_headers).status_code == 200
    res = post(client, panel_headers)
    assert res.status_code == 429
    assert int(res.headers["Retry-After"]) >= 1
    assert service.request.await_count == 10


def test_refuses_without_a_user(headers):
    client, _, _ = build()
    headers = {
        **headers,
        "X-Cassini-Request": "1",
        "AUTHORIZATION-APP-API": "OnRlc3QtZXhhcHAtc2VjcmV0",  # ":test-exapp-secret"
    }
    assert post(client, headers).status_code == 403


def test_requires_the_appapi_gateway():
    client, _, _ = build()
    assert post(client, {"X-Cassini-Request": "1"}).status_code == 401


def test_rate_limit_slides():
    now = [0.0]
    limit = RateLimit(limit=2, window=10, clock=lambda: now[0])
    assert limit.retry_after("a") == 0
    now[0] = 4
    assert limit.retry_after("a") == 0
    assert limit.retry_after("a") == pytest.approx(6)
    assert limit.retry_after("b") == 0
    now[0] = 10.5
    assert limit.retry_after("a") == 0


def test_call_sessions_refuses_malformed_participants():
    async def run(rows):
        seen = []
        return await CallSessions(lambda: FakeNc(rows, seen)).may_toggle(
            "alice", "room1", "s-alice", True
        )

    assert asyncio.run(run({"not": "a list"})) is False
    assert asyncio.run(run(["string", {**ALICE_IN_CALL, "sessionIds": None}])) is False
    assert asyncio.run(run([ALICE_IN_CALL])) is True
