"""The transcript panel route: static content, strict headers, no surprises."""

import pytest
from fastapi.testclient import TestClient

from cassini_live import panel_page
from tests.test_app import make_app


def get(path, headers):
    app, _ = make_app()
    return TestClient(app).get(path, headers=headers)


def test_the_panel_bundle_is_served_with_security_headers(headers):
    response = get("/panel/panel.js", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert b"__CASSINI_LIVE_PANEL__" in response.content
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("name", ["", "index.html", "install.js", "install.css"])
def test_the_former_bookmark_page_is_gone(headers, name):
    # The "Live transcript" bookmark is on the home page now (home_page.py).
    assert get(f"/panel/{name}", headers=headers).status_code == 404


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


@pytest.mark.parametrize("app_id", ["cassini_live", "live_transcription"])
def test_the_served_bundle_names_the_app_id_it_is_given(app_id):
    assets = panel_page.load_assets(app_id)
    for name in ("panel.js",):
        built = (panel_page.STATIC / name).read_bytes()
        placeholders = built.count(panel_page.APP_ID_PLACEHOLDER)
        assert placeholders and panel_page.APP_ID_PLACEHOLDER not in assets[name]
        assert assets[name].count(app_id.encode()) == placeholders + built.count(
            app_id.encode()
        ), name


def test_the_build_leaves_the_placeholder_in_the_bundle():
    # ui/src/app-id.js: a bundle without it would hardcode an id.
    for name in ("panel.js",):
        assert panel_page.APP_ID_PLACEHOLDER in (panel_page.STATIC / name).read_bytes(), name


@pytest.mark.parametrize("app_id", ["", "Cassini", "x/../y", 'a"+alert(1)+"', "a b"])
def test_refuses_an_app_id_that_could_break_out_of_the_bundle(app_id):
    with pytest.raises(ValueError):
        panel_page.load_assets(app_id)
