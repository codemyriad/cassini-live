"""The transcript panel route: static content, strict headers, no surprises."""

from fastapi.testclient import TestClient

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

    assert served == (STATIC / "panel.js").read_bytes()


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
    install = get("/panel/install.js", headers=headers).text
    assert "/apps/app_api/proxy/live_transcription/panel/panel.js" in install
    assert "javascript:" in install
