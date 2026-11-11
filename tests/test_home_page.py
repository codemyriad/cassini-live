"""The app-menu entry: its script and icon routes, and its registration on enable."""

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from nc_py_api.ex_app.integration_fastapi import anc_app

from cassini_live import home_page
from cassini_live.panel_page import APP_ID_PLACEHOLDER
from tests.test_app import make_app

TALK_SETTINGS = {"server": "https://cloud.test/signaling/", "stunservers": [], "turnservers": []}


def test_the_script_and_icon_are_served_fresh(headers):
    app, _ = make_app()
    client = TestClient(app)
    script = client.get("/ui/home.js", headers=headers)
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert script.headers["cache-control"] == "no-store"
    assert script.headers["x-content-type-options"] == "nosniff"
    icon = client.get("/img/app.svg", headers=headers)
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg+xml")
    assert icon.content == home_page.ICON.read_bytes()


def test_the_script_names_the_app_it_is_served_by(headers):
    for app_id in ("live_transcription", "cassini_live"):
        app, _ = make_app(app_id=app_id)
        headers["EX-APP-ID"] = app_id
        script = TestClient(app).get("/ui/home.js", headers=headers).text
        assert APP_ID_PLACEHOLDER.decode() not in script
        # The bookmark's address and the page's own requests go through this app's proxy.
        assert f"/apps/app_api/proxy/{app_id}/panel/panel.js" in script
        assert "Live transcript" in script


def test_the_routes_need_the_appapi_gateway():
    app, _ = make_app()
    client = TestClient(app)
    assert client.get("/ui/home.js").status_code == 401
    assert client.get("/img/app.svg").status_code == 401


def test_enabling_adds_the_menu_entry_and_disabling_removes_it(headers):
    app, _ = make_app()
    nc = AsyncMock()
    nc.ocs.return_value = dict(TALK_SETTINGS)
    app.dependency_overrides[anc_app] = lambda: nc
    client = TestClient(app)
    assert client.put("/enabled?enabled=true", headers=headers).json() == {"error": ""}
    nc.ui.resources.set_script.assert_awaited_once_with("top_menu", "home", "ui/home")
    nc.ui.top_menu.register.assert_awaited_once_with(
        "home", "Cassini Live", "img/app.svg", admin_required=False
    )
    assert client.put("/enabled?enabled=false", headers=headers).json() == {"error": ""}
    nc.ui.top_menu.unregister.assert_any_await("home")
    nc.ui.resources.delete_script.assert_any_await("top_menu", "home", "ui/home")


def test_the_entry_is_added_even_when_talk_is_not_ready(headers):
    # Right after a one-click install there is no HPB secret: the menu entry is how
    # the administrator finds out.
    app, _ = make_app()
    nc = AsyncMock()
    nc.ocs.side_effect = RuntimeError("Talk is not ready")
    app.dependency_overrides[anc_app] = lambda: nc
    assert TestClient(app).put("/enabled?enabled=true", headers=headers).json() == {"error": ""}
    nc.ui.top_menu.register.assert_awaited_once()


def test_a_failed_registration_does_not_stop_enabling(headers):
    app, service = make_app()
    nc = AsyncMock()
    nc.ocs.return_value = dict(TALK_SETTINGS)
    nc.ui.top_menu.register.side_effect = RuntimeError("AppAPI refused")
    app.dependency_overrides[anc_app] = lambda: nc
    assert TestClient(app).put("/enabled?enabled=true", headers=headers).json() == {"error": ""}
    assert service.enabled
