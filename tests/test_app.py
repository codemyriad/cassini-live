import asyncio
import base64
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from nc_py_api.ex_app.integration_fastapi import anc_app

from cassini_live import __version__
from cassini_live.app import create_app
from cassini_live.config import Config, websocket_url
from cassini_live.pool import WorkerPool
from cassini_live.service import CaptionService
from cassini_live.talk.types import SpreedClientException
from tests.test_streaming import FakeWorker


def make_app(app_id=None):
    config = Config(None, 2, "secret", "")
    service = CaptionService(config)
    service.enabled = True
    service.settings = object()
    service.request = AsyncMock()
    app = create_app(config, WorkerPool(2), service, app_id=app_id)
    app.state.safety.state = "ready"
    return app, service


def test_auth_rejects_wrong_secret_without_logging_it(headers, capsys):
    app, _ = make_app()
    client = TestClient(app)
    assert client.get("/status").status_code == 401
    headers["AUTHORIZATION-APP-API"] = base64.b64encode(b"alice:incorrect").decode()
    assert client.get("/status", headers=headers).status_code == 401
    headers["AUTHORIZATION-APP-API"] = "malformed==!"
    assert client.get("/status", headers=headers).status_code == 401
    assert "test-exapp-secret" not in capsys.readouterr().out
    assert client.get("/heartbeat").json() == {"status": "ok"}


def test_native_talk_contract(headers):
    app, service = make_app()
    client = TestClient(app)
    supported = client.get("/api/v1/languages", headers=headers).json()
    assert set(supported) == {"auto", "en", "de", "fr", "es", "it", "nl", "pl"}
    assert supported["de"] == {"name": "German", "metadata": {"separator": " ", "rtl": False}}
    caps = client.get("/capabilities", headers=headers).json()["live_transcription"]
    assert caps["features"] == ["live_transcription"] and "live_translation" not in caps
    assert caps["live_transcription"]["supported_languages"] == supported
    req = {"roomToken": "room1", "ncSessionId": "session1"}
    assert client.post("/api/v1/call/transcribe", headers=headers, json=req).status_code == 200
    assert service.request.call_args.args[0].langId == "auto"
    service.enabled = False
    assert client.post("/api/v1/call/transcribe", headers=headers, json=req).status_code == 503


class Room:
    """A captioned room, as far as the native routes look at one."""

    def __init__(self, language="de", targets=("session1",)):
        self.room_lang_id, self.targets, self.defunct = language, set(targets), asyncio.Event()

    async def add_target(self, session):
        self.targets.add(session)

    async def is_target(self, session):
        return session in self.targets

    async def set_language(self, language):
        self.room_lang_id = language


def test_native_routes_answer_as_the_upstream_provider_does(headers):
    """Status codes, the body's one key and the wording of nextcloud/live_transcription
    2.1.3 (ex_app/lib/main.py and service.py); "auto" is the one addition."""
    app, service = make_app()
    client = TestClient(app)

    def call(route, body):
        res = client.post(f"/api/v1/{route}", headers=headers, json=body)
        return res.status_code, res.json()

    req = {"roomToken": "room1", "ncSessionId": "session1"}
    done = {"message": "Transcription or translation request processed successfully."}
    assert call("call/transcribe", req) == (200, done)
    assert call("call/transcribe", {**req, "enable": False}) == (200, done)
    assert call("call/transcribe", {**req, "langId": "auto"}) == (200, done)
    bad_language = (400, {"error": "Invalid or unsupported language ID provided."})
    assert call("call/transcribe", {**req, "langId": "xx"}) == bad_language
    assert call("call/set-language", {"roomToken": "room1", "langId": "xx"}) == bad_language
    assert call("call/set-language", {"roomToken": "room1", "langId": "de"}) == (
        404,
        {
            "error": "No SpreedClient for room token room1, cannot set language."
            " Start a call and add at least one participant first."
        },
    )
    assert call("call/leave", {"roomToken": "room1"}) == (
        200, {"message": "Leave call request processed."}
    )
    # A malformed body is FastAPI's own 422 on both sides.
    for route in ("call/transcribe", "call/set-language", "call/leave"):
        status, body = call(route, {})
        assert status == 422 and isinstance(body["detail"], list)

    service.clients["room1"] = Room()
    assert call("call/set-language", {"roomToken": "room1", "langId": "fr"}) == (
        200, {"message": "Language set successfully for the call"}
    )
    service.request.side_effect = RuntimeError("boom")
    assert call("call/transcribe", req) == (
        500, {"error": "Failed to process transcription or translation request."}
    )
    service.request.side_effect = SpreedClientException("no signalling")
    status, body = call("call/transcribe", req)
    assert status == 503 and set(body) == {"error"}


def test_translation_is_answered_as_not_available(headers):
    app, service = make_app()
    client = TestClient(app)

    def target(body):
        res = client.post("/api/v1/translation/set-target-language", headers=headers, json=body)
        return res.status_code, res.json()

    listed = client.get("/api/v1/translation/languages?roomToken=room1", headers=headers)
    assert (listed.status_code, listed.json()) == (
        550, {"error": "A fatal error occurred while fetching translation languages."}
    )
    assert client.get("/api/v1/translation/languages", headers=headers).status_code == 422
    req = {"roomToken": "room1", "ncSessionId": "session1", "langId": "en"}
    assert target(req) == (
        404, {"error": "No SpreedClient for room token room1, cannot set target language"}
    )
    room = service.clients["room1"] = Room()
    # The status Talk reads as "translation is not supported".
    assert target(req) == (550, {"error": "Translation is not supported"})
    assert target({**req, "langId": "de"}) == (
        400, {"error": "Target language 'de' is the same as the original language 'de'"}
    )
    status, body = target({**req, "ncSessionId": "session2"})
    assert status == 412 and "session2" in body["error"]
    # Turning translation off puts the session back on captions in the spoken language.
    assert target({"roomToken": "room1", "ncSessionId": "session2"}) == (
        200, {"message": "Target translation language set successfully for the participant."}
    )
    assert "session2" in room.targets

    transcribe = {"roomToken": "room1", "ncSessionId": "session1", "translationTargetLangId": "en"}
    res = client.post("/api/v1/call/transcribe", headers=headers, json=transcribe)
    assert (res.status_code, res.json()) == (550, {"error": "Translation is not supported"})
    res = client.post(
        "/api/v1/call/transcribe",
        headers=headers,
        json={**transcribe, "translationTargetLangId": "de"},
    )
    assert res.status_code == 400 and "same as the original language" in res.json()["error"]
    service.request.assert_not_awaited()
    # Turning captions off ignores it, as upstream does.
    transcribe["enable"] = False
    off = client.post("/api/v1/call/transcribe", headers=headers, json=transcribe)
    assert off.status_code == 200


def test_disable_closes_all_rooms(headers):
    app, service = make_app()
    nc = AsyncMock()
    app.dependency_overrides[anc_app] = lambda: nc
    room = AsyncMock()
    service.clients["room"] = room
    response = TestClient(app).put("/enabled?enabled=false", headers=headers)
    assert response.json() == {"error": ""}
    assert not service.enabled
    room.close.assert_awaited_once()


def test_url_discovery_and_override():
    assert websocket_url("https://cloud.test/signaling/") == "wss://cloud.test/signaling/spreed"
    assert websocket_url("ws://hpb:8080/spreed") == "ws://hpb:8080/spreed"
    for url in ("not-a-url", "file:///secret", "https://a:b@cloud.test", "https://a/?token=x"):
        with pytest.raises(ValueError):
            websocket_url(url)


async def test_hpb_discovered_from_talk():
    nc = AsyncMock()
    nc.ocs.return_value = {
        "server": "https://cloud.test/signaling/",
        "stunservers": [],
        "turnservers": [],
    }
    service = CaptionService(Config(None, 2, "secret", ""))
    await service.discover(nc)
    assert service.settings.server == "wss://cloud.test/signaling/spreed"


async def test_restart_restores_enabled_state_and_preloads_workers():
    class Nextcloud:
        @property
        def enabled_state(self):
            # The SDK exposes an awaitable property, not an async method.
            return self.get_enabled()

        async def get_enabled(self):
            return True

    config = Config(None, 1, "secret", "")
    pool = WorkerPool(1, worker_factory=FakeWorker)
    service = CaptionService(config)
    service.discover = AsyncMock()
    from tests.test_safety import safe_factory

    app = create_app(config, pool, service, nc_factory=Nextcloud, safety_factory=safe_factory)
    async with app.router.lifespan_context(app):
        assert service.enabled
        assert pool.idle.qsize() == pool.size == 2  # the CPU ceiling for SAFE limits
        service.discover.assert_awaited_once()
    assert pool.closed and not service.enabled


async def test_disabled_startup_never_loads_workers():
    class Nextcloud:
        @property
        def enabled_state(self):
            async def disabled():
                return False

            return disabled()

    pool = WorkerPool(1, worker_factory=FakeWorker)
    pool.start = AsyncMock()
    from tests.test_safety import safe_factory

    app = create_app(
        Config(None, 1, "secret", ""), pool, nc_factory=Nextcloud, safety_factory=safe_factory
    )
    async with app.router.lifespan_context(app):
        pool.start.assert_not_awaited()
        assert not app.state.safety.desired_enabled


async def test_startup_connection_failure_recovers_without_admin(monkeypatch):
    import asyncio

    from cassini_live import capacity
    from tests.test_safety import safe_factory

    class Nextcloud:
        attempts = 0

        @property
        def enabled_state(self):
            async def enabled():
                self.attempts += 1
                if self.attempts == 1:
                    raise OSError("Nextcloud is still starting")
                return True

            return enabled()

    async def scenario(pool, count):
        return {"passed": True, "speakers": count}

    monkeypatch.setattr(capacity, "scenario", scenario)
    nc = Nextcloud()
    pool = WorkerPool(1, worker_factory=FakeWorker)
    config = Config(None, 1, "secret", "")
    service = CaptionService(config)
    service.discover = AsyncMock(side_effect=[OSError("Talk is still starting"), None])
    app = create_app(
        config,
        pool,
        service,
        nc_factory=lambda: nc,
        safety_factory=safe_factory,
        discovery_delay=0.005,
    )
    async with app.router.lifespan_context(app):
        async with asyncio.timeout(2):
            while app.state.safety.state != "ready":
                await asyncio.sleep(0.005)
        assert service.enabled and len(pool.workers) == pool.size
        assert service.discover.await_count == 2
    assert pool.closed


def make_engine_app(engine_id):
    from tests.fakes_engine import FakeBackend

    config = Config(None, 2, "secret", "")
    service = CaptionService(config)
    service.enabled = True
    service.settings = object()
    service.request = AsyncMock()
    app = create_app(config, FakeBackend(engine_id, 2), service)
    app.state.safety.state = "ready"
    return app, service


@pytest.mark.parametrize(
    "engine, languages",
    [
        ("whistle", {"auto", "en", "de", "fr", "es", "it", "nl", "pl"}),
        ("nemotron-multilingual", {"auto", "en", "de", "fr", "es", "it", "nl", "pl"}),
        ("nemotron-en", {"auto", "en"}),
    ],
)
def test_languages_follow_the_active_engine(headers, engine, languages):
    app, _ = make_engine_app(engine)
    client = TestClient(app)
    assert set(client.get("/api/v1/languages", headers=headers).json()) == languages
    caps = client.get("/capabilities", headers=headers).json()["live_transcription"]
    assert set(caps["live_transcription"]["supported_languages"]) == languages
    status = client.get("/status", headers=headers).json()
    assert status["engine"]["id"] == engine and status["engine"]["switchState"] == "idle"
    assert status["languages"] == status["engine"]["languages"]
    assert status["engine"]["name"] == status["model"]


def test_english_engine_refuses_other_languages(headers):
    app, service = make_engine_app("nemotron-en")
    client = TestClient(app)
    req = {"roomToken": "room1", "ncSessionId": "session1", "langId": "de"}
    response = client.post("/api/v1/call/transcribe", headers=headers, json=req)
    assert response.status_code == 400
    assert response.json() == {"error": "Invalid or unsupported language ID provided."}
    response = client.post(
        "/api/v1/call/set-language", headers=headers, json={"roomToken": "room1", "langId": "de"}
    )
    assert response.status_code == 400
    req["langId"] = "auto"
    assert client.post("/api/v1/call/transcribe", headers=headers, json=req).status_code == 200


def test_capacity_check_waits_for_an_engine_switch(headers):
    app, _ = make_app()
    app.state.manager.status.state = "downloading"
    headers["AUTHORIZATION-APP-API"] = base64.b64encode(b":test-exapp-secret").decode()
    response = TestClient(app).post("/capacity/check", headers=headers)
    assert response.status_code == 409


def test_enable_removes_the_legacy_page_and_registers_the_home_entry(headers):
    app, service = make_app()
    service.discover = AsyncMock()
    nc = AsyncMock()
    nc.ui.top_menu.unregister.side_effect = OSError("Not registered")
    app.dependency_overrides[anc_app] = lambda: nc
    client = TestClient(app)
    for path in ("/ui/live.js", "/ui/live.css"):
        assert client.get(path, headers=headers).status_code == 404
    response = client.put("/enabled?enabled=true", headers=headers)
    assert response.json() == {"error": ""}
    assert service.enabled
    nc.ui.resources.delete_script.assert_awaited_once_with("top_menu", "live", "ui/live")
    nc.ui.top_menu.unregister.assert_awaited_once_with("live")
    # 0.6.x's "live" page is gone; the home page (home_page.py) is the one menu entry.
    nc.ui.top_menu.register.assert_awaited_once_with(
        "home", "Cassini Live", "img/app.svg", admin_required=False
    )


def test_manifest_routes():
    from scripts import check_manifest

    check_manifest.main()


def test_status_keeps_its_shape_and_exports_the_observing_admission_block(headers):
    app, _ = make_app()
    status = TestClient(app).get("/status", headers=headers).json()
    assert status["version"] == __version__
    for key in ("capacityAssessment", "resourceSafety", "engine"):
        assert key in status
    assert status["lagSeconds"] == 0.0 and status["p95RequestSeconds"] == 0.0
    safety = status["resourceSafety"]
    assert safety["cpuHealth"] == {} and safety["state"] == "ready"
    admission = safety["admission"]
    assert set(admission) == {
        "open", "reason", "seats", "ceiling", "speaking", "paused", "lagSeconds",
        "lagSustainedSeconds", "captionAgeSeconds", "cpuStall", "cpuPressureAvg10",
        "memoryHeadroomMiB", "memoryUsedMiB", "memoryLimitMiB", "memoryLimitSource",
        "memoryStatAvailable",
        "pressureAvailable", "engineDuty", "lastRefusal", "lastShed", "shedEvents",
        "measuring", "tickMs", "observeOnly",
    }  # fmt: skip
    assert admission["observeOnly"] is True and admission["open"] is True
    assert admission["seats"] == 0 and admission["ceiling"] == 2


def test_the_requesting_user_is_remembered_for_participant_names(headers):
    app, service = make_app()
    seen = []
    service.clients["room1"] = type(
        "Room", (), {"note_user": lambda self, user: seen.append(user)}
    )()
    client = TestClient(app)
    req = {"roomToken": "room1", "ncSessionId": "session1"}
    assert client.post("/api/v1/call/transcribe", headers=headers, json=req).status_code == 200
    assert seen == ["alice"]
    req["enable"] = False
    assert client.post("/api/v1/call/transcribe", headers=headers, json=req).status_code == 200
    assert seen == ["alice"]
