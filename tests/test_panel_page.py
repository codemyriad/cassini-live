"""The transcript panel route: static content, strict headers, no surprises."""

import pytest
from fastapi.testclient import TestClient

from cassini_live import panel_page
from tests.test_app import make_app


def get(path, headers):
    app, _ = make_app()
    return TestClient(app).get(path, headers=headers)


def test_panel_assets_are_served_with_security_headers(headers):
    for name, content_type, marker in [
        ("", "text/html", b"Cassini Live transcript panel"),
        ("index.html", "text/html", b"Cassini Live transcript panel"),
        ("panel.js", "text/javascript", b"__CASSINI_LIVE_PANEL__"),
        ("install.js", "text/javascript", b"CASSINI_LIVE_BOOKMARKLETS"),
        ("install.css", "text/css", b"color-scheme"),
    ]:
        response = get(f"/panel/{name}", headers=headers)
        assert response.status_code == 200, name
        assert response.headers["content-type"].startswith(content_type), name
        assert marker in response.content, name
        assert response.headers["cache-control"] == "no-store", name
        assert "default-src 'none'" in response.headers["content-security-policy"], name
        assert response.headers["x-content-type-options"] == "nosniff", name


def test_the_served_bundle_is_the_built_one(headers):
    served = get("/panel/panel.js", headers=headers).content
    from cassini_live.panel_page import STATIC

    # The built bundle, with the app's id (conftest: live_transcription) filled in.
    built = (STATIC / "panel.js").read_bytes()
    assert served == built.replace(b"__CASSINI_LIVE_APP_ID__", b"live_transcription")


def test_unknown_panel_paths_are_refused_with_headers(headers):
    response = get("/panel/secrets.txt", headers=headers)
    assert response.status_code == 404
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_panel_requires_the_appapi_gateway(headers):
    # Without the AppAPI auth headers the middleware refuses the request, the
    # same as every other route: the proxy (and its USER access level) is the
    # only way in.
    app, _ = make_app()
    assert TestClient(app).get("/panel/panel.js").status_code == 401


def test_bookmarklets_point_at_the_proxy_route(headers):
    # make_app runs under the APP_ID of the environment (conftest: live_transcription).
    install = get("/panel/install.js", headers=headers).text
    assert "/apps/app_api/proxy/" in install and "panel/panel.js" in install
    assert "live_transcription" in install
    assert panel_page.APP_ID_PLACEHOLDER.decode() not in install
    assert "javascript:" in install


@pytest.mark.parametrize("app_id", ["cassini_live", "live_transcription"])
def test_served_bundles_name_the_app_id_they_are_given(app_id):
    assets = panel_page.load_assets(app_id)
    for name in ("panel.js", "install.js"):
        built = (panel_page.STATIC / name).read_bytes()
        placeholders = built.count(panel_page.APP_ID_PLACEHOLDER)
        assert placeholders and panel_page.APP_ID_PLACEHOLDER not in assets[name]
        assert assets[name].count(app_id.encode()) == placeholders + built.count(
            app_id.encode()
        ), name


def test_the_build_leaves_the_placeholder_in_every_bundle():
    # ui/src/app-id.js: a bundle without it would hardcode an id.
    for name in ("panel.js", "install.js"):
        assert panel_page.APP_ID_PLACEHOLDER in (panel_page.STATIC / name).read_bytes(), name


@pytest.mark.parametrize("app_id", ["", "Cassini", "x/../y", 'a"+alert(1)+"', "a b"])
def test_refuses_an_app_id_that_could_break_out_of_the_bundle(app_id):
    with pytest.raises(ValueError):
        panel_page.load_assets(app_id)
