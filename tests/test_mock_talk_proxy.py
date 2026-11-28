"""The stand-in for Talk: Talk's checks, re-done by acting as the caller, then the request
Talk sends to the native caption API, over HTTP through this app's own authentication."""

import asyncio
import base64
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from cassini_live import mock_talk_proxy
from cassini_live.app import create_app
from cassini_live.call_access import CallSessions, RateLimit
from cassini_live.config import Config
from cassini_live.mock_talk_proxy import NativeApi
from cassini_live.pool import WorkerPool
from cassini_live.service import CaptionService
from tests.test_app import FakeEncryption
from tests.test_streaming import FakeWorker  # noqa: F401 (import side effects as elsewhere)

ALICE_IN_CALL = {"actorType": "users", "actorId": "alice", "sessionIds": ["s-alice"], "inCall": 7}
BOB_IN_CALL = {"actorType": "users", "actorId": "bob", "sessionIds": ["s-bob"], "inCall": 3}
ROUTE = "/mock_talk_proxy/live-transcription/room1"
NATIVE = "/api/v1/call/transcribe"
SENT = {"roomToken": "room1", "ncSessionId": "s-alice", "enable": True}


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


def over_http(app, hops):
    """The hop to the native API as a real request through the app's middleware and
    routing (in place of the loopback socket), recording what was sent."""

    async def send(method, path, headers, body, timeout):
        hops.append({"method": method, "path": path, "headers": headers, "body": body})
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://exapp") as client:
            response = await client.request(method, path, headers=headers, json=body)
        return response.status_code, dict(response.headers), response.content

    return send


def build(participants=(ALICE_IN_CALL, BOB_IN_CALL), encryption=None):
    seen, hops = [], []
    config = Config(None, 2, "secret", "")
    service = CaptionService(config)
    service.enabled = True
    service.settings = object()
    service.request = AsyncMock()
    rows = None if participants is None else list(participants)
    app = create_app(
        config,
        WorkerPool(2),
        service,
        nc_factory=lambda: FakeNc(rows, seen),
        encryption=encryption or FakeEncryption(),
    )
    app.state.safety.state = "ready"
    app.state.native_api = NativeApi("cassini_live", over_http(app, hops))
    client = TestClient(app)
    client.hops = hops
    return client, service, seen


@pytest.fixture
def panel_headers(headers):
    return {**headers, "X-Cassini-Request": "1", "Sec-Fetch-Site": "same-origin"}


BODY = {"ncSessionId": "s-alice"}


def post(client, headers, body=BODY, route=ROUTE):
    return client.post(route, headers=headers, json=body)


def delete(client, headers, body=BODY, route=ROUTE):
    return client.request("DELETE", route, headers=headers, json=body)


def test_starts_captions_for_the_callers_own_session_in_the_call(panel_headers):
    client, service, seen = build()
    res = post(client, panel_headers)
    assert res.status_code == 200 and res.json() == {}
    req = service.request.call_args.args[0]
    # No language was sent, as from Talk for a conversation without one: the default.
    assert (req.roomToken, req.ncSessionId, req.enable, req.langId) == (
        "room1", "s-alice", True, "auto"
    )
    # Asked of Talk as the caller, not as the app.
    assert seen == [
        ("user", "alice"),
        ("GET", "/ocs/v2.php/apps/spreed/api/v4/room/room1/participants"),
    ]


def test_sends_the_native_api_what_talk_sends(panel_headers):
    client, _, _ = build()
    assert post(client, panel_headers).status_code == 200
    assert delete(client, panel_headers).status_code == 200
    enable, disable = client.hops
    for hop in (enable, disable):
        assert (hop["method"], hop["path"]) == ("POST", NATIVE)
        sent = hop["headers"]
        # AppAPI's headers for a request to an ExApp, with the user Talk acts for.
        assert set(sent) == {
            "AA-VERSION", "EX-APP-ID", "EX-APP-VERSION", "AUTHORIZATION-APP-API", "AA-REQUEST-ID"
        }
        assert base64.b64decode(sent["AUTHORIZATION-APP-API"]) == b"alice:test-exapp-secret"
        assert (sent["EX-APP-ID"], sent["AA-VERSION"]) == ("cassini_live", "32.0.0")
        # Nothing of the browser's request travels on.
        assert not {"x-cassini-request", "cookie", "origin"} & {k.lower() for k in sent}
    assert enable["body"] == SENT
    assert disable["body"] == {**SENT, "enable": False}
    assert enable["headers"]["AA-REQUEST-ID"] != disable["headers"]["AA-REQUEST-ID"]


def test_the_browser_cannot_choose_what_is_sent(panel_headers):
    client, service, _ = build()
    body = {**BODY, "langId": "de", "roomToken": "other", "translationTargetLangId": "fr"}
    assert post(client, panel_headers, body).status_code == 200
    assert client.hops[0]["body"] == SENT


def test_stops_captions_for_the_callers_own_session(panel_headers):
    client, service, _ = build()
    res = delete(client, panel_headers)
    assert res.status_code == 200 and res.json() == {}
    assert service.request.call_args.args[0].enable is False


def test_stopping_does_not_require_being_in_the_call(panel_headers):
    left = {**ALICE_IN_CALL, "inCall": 0}
    client, service, _ = build([left])
    assert post(client, panel_headers).status_code == 403
    assert delete(client, panel_headers).status_code == 200
    assert service.request.await_count == 1


def test_refuses_someone_elses_session(panel_headers):
    client, service, _ = build()
    for send in (post, delete):
        assert send(client, panel_headers, {"ncSessionId": "s-bob"}).status_code == 403
    service.request.assert_not_awaited()
    assert client.hops == []


def test_refuses_a_non_member_or_a_caller_in_the_lobby(panel_headers):
    client, service, _ = build(participants=None)
    assert post(client, panel_headers).status_code == 403
    service.request.assert_not_awaited()


def test_a_guest_with_the_same_id_is_not_the_user(panel_headers):
    guest = {**ALICE_IN_CALL, "actorType": "guests"}
    client, service, _ = build([guest])
    assert post(client, panel_headers).status_code == 403


@pytest.mark.parametrize("send", [post, delete])
@pytest.mark.parametrize(
    "drop, extra, status",
    [
        ("X-Cassini-Request", {}, 403),
        (None, {"Sec-Fetch-Site": "cross-site"}, 403),
        (None, {"Origin": "https://evil.test"}, 403),
    ],
)
def test_csrf_defences(panel_headers, send, drop, extra, status):
    client, service, seen = build()
    headers = {k: v for k, v in panel_headers.items() if k != drop} | extra
    assert send(client, headers).status_code == status
    service.request.assert_not_awaited()
    assert seen == [] and client.hops == []


def test_requires_a_json_body(panel_headers):
    client, service, _ = build()
    res = client.post(
        ROUTE, headers={**panel_headers, "Content-Type": "text/plain"}, content=b"ncSessionId=s"
    )
    assert res.status_code == 415
    assert post(client, panel_headers, {}).status_code == 400
    assert delete(client, panel_headers, {"ncSessionId": 7}).status_code == 400
    service.request.assert_not_awaited()


def test_refuses_a_token_that_is_not_a_talk_token(panel_headers):
    client, _, seen = build()
    for token in ("room1%3Fx=1", "room%201", "room.1"):
        route = f"/mock_talk_proxy/live-transcription/{token}"
        assert post(client, panel_headers, route=route).status_code == 400
    assert seen == [] and client.hops == []


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


def test_passes_on_the_native_apis_refusal(panel_headers):
    client, service, _ = build()
    service.enabled = False
    res = post(client, panel_headers)
    assert res.status_code == 503
    assert res.json() == {"detail": "Cassini Live is not enabled or Talk is not ready"}
    service.enabled = True
    client.app.state.safety.state, client.app.state.safety.measuring = "ready", True
    res = post(client, panel_headers)
    assert res.status_code == 503 and int(res.headers["Retry-After"]) >= 1


def test_passes_on_the_encrypted_call_refusal(panel_headers):
    """The panel shows the 409 detail and stops retrying: end-to-end encryption does
    not pass by waiting. Stopping captions stays allowed."""
    client, service, _ = build(encryption=FakeEncryption(True))
    res = post(client, panel_headers)
    assert res.status_code == 409
    assert "end-to-end encrypted" in res.json()["detail"]
    service.request.assert_not_awaited()
    assert delete(client, panel_headers).status_code == 200
    assert service.request.await_count == 1


def test_reads_only_the_error_key_as_talk_does(panel_headers):
    client, _, _ = build()

    async def detail_only(*_):
        return 500, {}, b'{"detail": "not where Talk looks"}'

    client.app.state.native_api = NativeApi("cassini_live", detail_only)
    res = post(client, panel_headers)
    assert res.status_code == 500 and res.json() == {"detail": "Captions could not be changed"}


def test_gives_up_when_the_native_api_does_not_answer(panel_headers):
    client, _, _ = build()

    async def never(*_):
        await asyncio.sleep(5)

    async def down(*_):
        raise ConnectionRefusedError

    client.app.state.native_api = NativeApi("cassini_live", never, timeout=0.05)
    assert post(client, panel_headers).status_code == 504
    client.app.state.native_api = NativeApi("cassini_live", down)
    assert post(client, panel_headers).status_code == 502


def test_the_native_route_does_not_need_what_the_mock_checks(headers):
    """Talk's own call: no CSRF header, no session check, and no user for a guest."""
    client, service, seen = build()
    guest = {**headers, "AUTHORIZATION-APP-API": "OnRlc3QtZXhhcHAtc2VjcmV0"}
    for sent in (headers, guest):
        body = {"roomToken": "room1", "ncSessionId": "s-bob"}
        assert client.post(NATIVE, headers=sent, json=body).status_code == 200
    assert seen == []


@pytest.mark.parametrize(
    "environ, address",
    [
        ({"APP_HOST": "0.0.0.0", "APP_PORT": "23000"}, "http://127.0.0.1:23000"),
        ({"APP_PORT": "23000"}, "http://127.0.0.1:23000"),
        ({"APP_HOST": "::", "APP_PORT": "23000"}, "http://[::1]:23000"),
        ({"APP_HOST": "10.0.0.5", "APP_PORT": "9"}, "http://10.0.0.5:9"),
        ({"HP_SHARED_KEY": "k"}, "http+unix://%2Ftmp%2Fexapp.sock"),
        ({"HP_SHARED_KEY": "k", "HP_EXAPP_SOCK": "/run/a.sock"}, "http+unix://%2Frun%2Fa.sock"),
    ],
)
def test_finds_its_own_address_as_the_server_binds_it(environ, address):
    assert mock_talk_proxy.own_address(environ) == address


# The route panels built before the mock call: kept for one release.
OLD_BODY = {"roomToken": "room1", "ncSessionId": "s-alice", "enable": True, "langId": "auto"}


def test_the_old_panel_route_still_works(panel_headers):
    client, service, _ = build()

    def old(body):
        return client.post("/captions/transcribe", headers=panel_headers, json=body).status_code

    assert old(OLD_BODY) == 200
    assert client.hops[0]["body"] == {**SENT, "langId": "auto"}
    assert old({**OLD_BODY, "enable": False}) == 200
    assert client.hops[1]["body"] == {**SENT, "enable": False}
    assert old({**OLD_BODY, "ncSessionId": "s-bob"}) == 403
    assert old({**OLD_BODY, "langId": "xx"}) == 400
    assert old({}) == 400


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


@pytest.mark.parametrize("behind_harp", [False, True])
def test_reaches_the_native_api_on_the_servers_own_socket(
    panel_headers, monkeypatch, tmp_path, behind_harp
):
    """The real hop: the app served by uvicorn as run_app binds it (a TCP port, or a
    Unix socket behind HaRP), calling its own native route."""
    import socket
    import threading
    import time

    import uvicorn

    client, service, _ = build()
    app = client.app
    app.state.native_api = NativeApi("cassini_live")
    if behind_harp:
        # Short path: Unix socket paths are limited to about 100 bytes.
        import tempfile

        folder = tempfile.mkdtemp(prefix="cl-", dir="/tmp")
        path = f"{folder}/exapp.sock"
        monkeypatch.setenv("HP_SHARED_KEY", "harp")
        monkeypatch.setenv("HP_EXAPP_SOCK", path)
        config = uvicorn.Config(app, uds=path, log_level="error", lifespan="off")
        transport = httpx.HTTPTransport(uds=path)
        base = "http://exapp"
    else:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        monkeypatch.setenv("APP_HOST", "0.0.0.0")
        monkeypatch.setenv("APP_PORT", str(port))
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off")
        transport = httpx.HTTPTransport()
        base = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        with httpx.Client(transport=transport, base_url=base) as browser:
            res = browser.post(ROUTE, headers=panel_headers, json=BODY)
        assert (res.status_code, res.json()) == (200, {})
        req = service.request.call_args.args[0]
        assert (req.roomToken, req.ncSessionId, req.enable) == ("room1", "s-alice", True)
    finally:
        server.should_exit = True
        thread.join(5)
