"""The app id: read once from the environment, validated, and turned into proxy paths."""

import pytest
from fastapi.testclient import TestClient

from cassini_live import appapi
from tests.test_app import make_app


def test_reads_the_registered_app_id():
    assert appapi.app_id_from_env({"APP_ID": "cassini_live"}) == "cassini_live"


@pytest.mark.parametrize("environ", [{}, {"APP_ID": ""}, {"APP_ID": "Cassini"},
                                     {"APP_ID": "a/b"}, {"APP_ID": "1app"}])
def test_refuses_a_missing_or_invalid_app_id(environ):
    with pytest.raises(RuntimeError):
        appapi.app_id_from_env(environ)


def test_proxy_path():
    assert appapi.proxy_path("cassini_live", "settings/index.html") == (
        "/index.php/apps/app_api/proxy/cassini_live/settings/index.html"
    )
    assert appapi.proxy_path("cassini_live", "/panel/") == (
        "/index.php/apps/app_api/proxy/cassini_live/panel/"
    )
    with pytest.raises(ValueError):
        appapi.proxy_path("../x", "")


def test_requests_are_accepted_only_for_the_app_id_passed_in(monkeypatch, headers):
    # The middleware compares EX-APP-ID with the id create_app was given, not the env.
    monkeypatch.setenv("APP_ID", "something_else")
    app, _ = make_app(app_id="cassini_live")
    client = TestClient(app)
    own = client.get("/status", headers={**headers, "EX-APP-ID": "cassini_live"})
    assert own.status_code == 200
    other = client.get("/status", headers={**headers, "EX-APP-ID": "other_app"})
    assert other.status_code == 401
