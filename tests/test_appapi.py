"""The app id: read once from the environment, validated, and turned into proxy paths."""

import pytest
from fastapi.testclient import TestClient

from cassini_live import appapi
from tests.test_app import make_app


@pytest.mark.parametrize("app_id", ["cassini_live", "live_transcription"])
def test_reads_the_registered_app_id(app_id):
    assert appapi.app_id_from_env({"APP_ID": app_id}) == app_id


@pytest.mark.parametrize("environ", [{}, {"APP_ID": ""}, {"APP_ID": "Cassini"},
                                     {"APP_ID": "a/b"}, {"APP_ID": "1app"}])
def test_refuses_a_missing_or_invalid_app_id(environ):
    with pytest.raises(RuntimeError):
        appapi.app_id_from_env(environ)


def test_native_mode_is_talks_provider_id_only():
    assert appapi.is_talk_provider("live_transcription")
    assert not appapi.is_talk_provider("cassini_live")


def test_proxy_path():
    assert appapi.proxy_path("cassini_live", "settings/index.html") == (
        "/index.php/apps/app_api/proxy/cassini_live/settings/index.html"
    )
    assert appapi.proxy_path("cassini_live", "/panel/") == (
        "/index.php/apps/app_api/proxy/cassini_live/panel/"
    )
    with pytest.raises(ValueError):
        appapi.proxy_path("../x", "")


@pytest.mark.parametrize("app_id", ["cassini_live", "live_transcription"])
def test_requests_are_accepted_only_for_the_app_id_passed_in(monkeypatch, headers, app_id):
    # The middleware compares EX-APP-ID with the id create_app was given, not the env.
    monkeypatch.setenv("APP_ID", "something_else")
    app, _ = make_app(app_id=app_id)
    client = TestClient(app)
    assert client.get("/status", headers={**headers, "EX-APP-ID": app_id}).status_code == 200
    other = "cassini_live" if app_id == "live_transcription" else "live_transcription"
    assert client.get("/status", headers={**headers, "EX-APP-ID": other}).status_code == 401
