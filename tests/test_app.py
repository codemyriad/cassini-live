import base64
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from nc_py_api.ex_app.integration_fastapi import anc_app

from cassini_live.app import create_app
from cassini_live.config import Config, websocket_url
from cassini_live.pool import WorkerPool
from cassini_live.service import CaptionService
from tests.test_streaming import FakeWorker


def make_app():
    config = Config(None, 2, "secret", "")
    service = CaptionService(config)
    service.enabled = True
    service.settings = object()
    service.request = AsyncMock()
    app = create_app(config, WorkerPool(2), service)
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
    caps = client.get("/capabilities", headers=headers).json()["live_transcription"]
    assert caps["features"] == ["live_transcription"]
    req = {"roomToken": "room1", "ncSessionId": "session1"}
    assert client.post("/api/v1/call/transcribe", headers=headers, json=req).status_code == 200
    assert service.request.call_args.args[0].langId == "auto"
    assert (
        client.post(
            "/api/v1/call/transcribe", headers=headers, json={**req, "langId": "xx"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/call/transcribe",
            headers=headers,
            json={**req, "translationTargetLangId": "de"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/v1/call/set-language",
            headers=headers,
            json={"roomToken": "room1", "langId": "de"},
        ).status_code
        == 404
    )
    service.enabled = False
    assert client.post("/api/v1/call/transcribe", headers=headers, json=req).status_code == 503


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
        assert pool.idle.qsize() == 1
        service.discover.assert_awaited_once()
    assert pool.closed and not service.enabled
