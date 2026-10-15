import base64
import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from cassini_live import __version__, settings_page
from cassini_live.eligibility import Reason
from cassini_live.engine_switch import SwitchRefused, SwitchStatus
from tests.test_app import make_app

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "cassini_live" / "static" / "settings"
PAGE = "/settings/index.html"
CACHED = "private, max-age=86400"
needs_frontend = pytest.mark.skipif(
    not all((STATIC / name).is_file() for name in settings_page.ASSETS),
    reason="the settings page files (cassini_live/static/settings) are not there yet",
)


@pytest.fixture
def page(headers):
    app, _ = make_app()
    client = TestClient(app)
    return app.state.manager, client, headers


def same_origin(headers, **extra):
    return {
        **headers,
        "X-Cassini-Request": "1",
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
        **extra,
    }


def assert_secure(response, cache="no-store"):
    h = response.headers
    assert h["content-security-policy"] == settings_page.CSP
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer"
    assert h["cross-origin-opener-policy"] == "same-origin"
    assert h["cross-origin-resource-policy"] == "same-origin"
    assert h["cache-control"] == cache


def error(response, status, code):
    assert response.status_code == status, response.text
    assert_secure(response)
    body = response.json()["error"]
    assert body["code"] == code and body["message"]
    return body


# Static files


@needs_frontend
@pytest.mark.parametrize(
    ("name", "content_type", "cache"),
    [
        ("index.html", "text/html; charset=utf-8", "no-store"),
        ("app.js", "text/javascript; charset=utf-8", CACHED),
        ("app.css", "text/css; charset=utf-8", CACHED),
    ],
)
def test_assets_are_served_with_security_headers(page, name, content_type, cache):
    _, client, headers = page
    response = client.get(f"/settings/{name}", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == content_type
    assert_secure(response, cache)


@needs_frontend
def test_index_is_versioned(page):
    _, client, headers = page
    html = client.get(PAGE, headers=headers).text
    assert f"app.js?v={__version__}" in html and f"app.css?v={__version__}" in html
    assert "{{VERSION}}" not in html
    assert __version__ == "0.7.1"


class Hygiene(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.styles, self.inline, self.handlers, self.content = [], 0, [], [], []
        self.controls, self.labels, self.ids = [], set(), set()
        self.lang = self.viewport = self.status = False
        self.in_script = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "html":
            self.lang = bool(a.get("lang"))
        if tag == "meta" and a.get("name") == "viewport":
            self.viewport = True
        if a.get("role") == "status":
            self.status = True
        if tag == "script":
            self.scripts.append(a.get("src"))
            self.in_script = True
        if tag == "style":
            self.styles += 1
        if "style" in a:
            self.inline.append(tag)
        self.handlers += [name for name in a if name.startswith("on")]
        if tag == "label" and a.get("for"):
            self.labels.add(a["for"])
        if tag in ("input", "select", "progress", "textarea"):
            self.controls.append(a)

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_script and data.strip():
            self.content.append(data)


@needs_frontend
def test_index_html_hygiene(page):
    _, client, headers = page
    parser = Hygiene()
    parser.feed(client.get(PAGE, headers=headers).text)
    assert parser.scripts and all(parser.scripts) and parser.content == []
    assert parser.styles == 0 and parser.inline == [] and parser.handlers == []
    for control in parser.controls:
        assert control.get("id") in parser.labels or control.get("aria-label"), control
    assert parser.lang and parser.viewport and parser.status
    for required in ("notice", "live", "progress", "cancel", "engine-list", "back"):
        assert required in parser.ids, required


@needs_frontend
def test_app_js_parses():
    if not shutil.which("node"):
        pytest.skip("node is not installed")
    result = subprocess.run(
        ["node", "--check", str(STATIC / "app.js")], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "path",
    [
        "/settings/other.js",
        "/settings/../app.py",
        "/settings/licenses/../../app.txt",
        "/settings/licenses/MIT.txt",
        "/settings/licenses/NVIDIA-OML",
        "/settings/api/nothing",
    ],
)
def test_unknown_files_are_not_found(page, path):
    _, client, headers = page
    assert client.get(path, headers=headers).status_code == 404


def test_license_texts(page):
    _, client, headers = page
    response = client.get("/settings/licenses/NVIDIA-OML.txt", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.content == (ROOT / "LICENSES/NVIDIA-Open-Model-License.txt").read_bytes()
    assert_secure(response, CACHED)
    for license_id in ("Apache-2.0", "OpenMDW-1.1"):
        assert client.get(f"/settings/licenses/{license_id}.txt", headers=headers).status_code \
            == 200
    error(client.get("/settings/licenses/MIT.txt", headers=headers), 404, "not_found")


# State


def test_state(page):
    _, client, headers = page
    response = client.get("/settings/api/state", headers=headers)
    assert response.status_code == 200
    assert_secure(response)
    body = response.json()
    assert set(body) == {"version", "panel", "status", "links"}
    assert body["version"] == __version__
    assert body["panel"]["active"] == "whistle" and len(body["panel"]["engines"]) == 3
    assert body["status"]["name"] == "Cassini Live"
    assert body["links"]["licenses"] == {
        "Apache-2.0": "licenses/Apache-2.0.txt",
        "NVIDIA-OML": "licenses/NVIDIA-OML.txt",
        "OpenMDW-1.1": "licenses/OpenMDW-1.1.txt",
    }
    assert body["links"]["adminSettings"] == "/index.php/settings/admin/ai"
    assert body["links"]["docs"].startswith("https://github.com/codemyriad/cassini-live/")
    text = json.dumps(body)
    assert "acknowledged" not in text and "requiresAcknowledgement" not in text


def test_requests_without_a_user_are_forbidden(page):
    _, client, headers = page
    headers["AUTHORIZATION-APP-API"] = base64.b64encode(b":test-exapp-secret").decode()
    error(client.get("/settings/api/state", headers=headers), 403, "forbidden")
    error(client.get("/settings/licenses/NVIDIA-OML.txt", headers=headers), 403, "forbidden")
    response = client.post(
        "/settings/api/engine", headers=same_origin(headers), json={"engine": "nemotron-en"}
    )
    error(response, 403, "forbidden")


def test_requests_without_appapi_credentials_are_rejected(page):
    _, client, _ = page
    assert client.get("/settings/api/state").status_code == 401


# CSRF defense


def post_engine(client, headers, body=None, content=None):
    if content is not None:
        return client.post("/settings/api/engine", headers=headers, content=content)
    return client.post("/settings/api/engine", headers=headers, json=body)


@pytest.fixture
def switch(page):
    manager, client, headers = page
    status = SwitchStatus("validating", "nemotron-en", "whistle")
    manager.request_switch = AsyncMock(return_value=status)
    return manager, client, headers


def test_post_needs_the_custom_header(switch):
    manager, client, headers = switch
    plain = {**same_origin(headers)}
    del plain["X-Cassini-Request"]
    error(post_engine(client, plain, {"engine": "nemotron-en"}), 403, "csrf")
    wrong = same_origin(headers, **{"X-Cassini-Request": "yes"})
    error(post_engine(client, wrong, {"engine": "nemotron-en"}), 403, "csrf")
    manager.request_switch.assert_not_awaited()


@pytest.mark.parametrize("site", ["cross-site", "same-site", "none"])
def test_post_from_another_site_is_rejected(switch, site):
    manager, client, headers = switch
    response = post_engine(client, same_origin(headers, **{"Sec-Fetch-Site": site}),
                           {"engine": "nemotron-en"})
    error(response, 403, "csrf")
    manager.request_switch.assert_not_awaited()


@pytest.mark.parametrize("origin", ["https://evil.test", "http://cloud.test.evil.test", "file://"])
def test_post_with_a_foreign_origin_is_rejected(switch, origin):
    manager, client, headers = switch
    response = post_engine(client, same_origin(headers, Origin=origin), {"engine": "nemotron-en"})
    error(response, 403, "csrf")
    manager.request_switch.assert_not_awaited()


@pytest.mark.parametrize(
    "extra",
    [
        {"Origin": "null"},  # Referrer-Policy: no-referrer on the Nextcloud document
        {"Origin": "http://cloud.test"},  # NEXTCLOUD_URL
        {"Origin": "http://testserver"},  # Host
        {"Origin": "https://cloud.example", "X-Forwarded-Host": "cloud.example, proxy"},
        {},
    ],
)
def test_same_origin_json_is_accepted(switch, extra):
    manager, client, headers = switch
    response = post_engine(client, same_origin(headers, **extra), {"engine": "nemotron-en"})
    assert response.status_code == 202, response.text
    assert_secure(response)
    body = response.json()
    assert body["switch"]["target"] == "nemotron-en" and body["panel"]["active"] == "whistle"
    manager.request_switch.assert_awaited_once_with("nemotron-en")


def test_post_without_sec_fetch_site_is_accepted(switch):
    # Older browsers send no Fetch Metadata; the custom header still blocks cross-site posts.
    manager, client, headers = switch
    plain = same_origin(headers)
    del plain["Sec-Fetch-Site"]
    assert post_engine(client, plain, {"engine": "nemotron-en"}).status_code == 202


@pytest.mark.parametrize(
    "content_type",
    ["application/x-www-form-urlencoded", "text/plain", "multipart/form-data; boundary=x"],
)
def test_form_and_text_bodies_are_rejected(switch, content_type):
    manager, client, headers = switch
    response = post_engine(
        client, same_origin(headers, **{"Content-Type": content_type}),
        content=b'{"engine": "nemotron-en"}',
    )
    error(response, 415, "unsupported_media_type")
    manager.request_switch.assert_not_awaited()


def test_body_limits(switch):
    manager, client, headers = switch
    big = json.dumps({"engine": "nemotron-en", "pad": "x" * 2048}).encode()
    error(post_engine(client, same_origin(headers), content=big), 413, "too_large")
    for body in (b"", b"not json", b"[]", b'"nemotron-en"'):
        error(post_engine(client, same_origin(headers), content=body), 400, "bad_request")
    for value in (5, None, "", "x" * 65, ["nemotron-en"]):
        error(post_engine(client, same_origin(headers), {"engine": value}), 400, "bad_request")
    charset = same_origin(headers, **{"Content-Type": "application/json; charset=utf-8"})
    assert post_engine(client, charset, {"engine": "nemotron-en"}).status_code == 202
    manager.request_switch.assert_awaited_once_with("nemotron-en")


# Engine changes


def test_refusals_use_the_envelope_and_are_not_recorded(page):
    manager, client, headers = page
    manager.request_switch = AsyncMock(
        side_effect=SwitchRefused(409, "busy", "A speech engine change is already in progress")
    )
    body = error(post_engine(client, same_origin(headers), {"engine": "nemotron-en"}), 409, "busy")
    assert body == {"code": "busy", "message": "A speech engine change is already in progress",
                    "reasons": []}
    reason = Reason("memory_limit", "The container memory limit is too low.", 2048, 2304)
    manager.request_switch = AsyncMock(side_effect=SwitchRefused(
        400, "not_eligible", "Nemotron English cannot run here.", (reason,)
    ))
    body = error(
        post_engine(client, same_origin(headers), {"engine": "nemotron-en"}), 400, "not_eligible"
    )
    assert body["reasons"] == [{"code": "memory_limit",
                                "message": "The container memory limit is too low.",
                                "measured": 2048, "required": 2304}]
    assert manager.last_refusal is None


def test_real_manager_refuses_unknown_and_active_engines(page):
    manager, client, headers = page
    error(post_engine(client, same_origin(headers), {"engine": "vosk"}), 400, "unknown_engine")
    error(post_engine(client, same_origin(headers), {"engine": "whistle"}), 400, "active")
    assert manager.last_refusal is None and not manager.busy


def test_other_methods_are_not_allowed(page):
    _, client, headers = page
    response = client.put("/settings/api/engine", headers=same_origin(headers), json={})
    error(response, 405, "method_not_allowed")
    error(client.get("/settings/api/engine", headers=headers), 405, "method_not_allowed")
    error(client.post("/settings/api/state", headers=same_origin(headers), json={}), 405,
          "method_not_allowed")


def test_cancel(page):
    manager, client, headers = page
    error(client.post("/settings/api/cancel", headers=same_origin(headers), json={}), 409, "idle")
    status = SwitchStatus("downloading", "nemotron-en", "whistle")
    manager.cancel = AsyncMock(return_value=status)
    response = client.post("/settings/api/cancel", headers=same_origin(headers), json={})
    assert response.status_code == 200
    assert response.json()["switch"]["state"] == "downloading"
    manager.cancel.assert_awaited_once_with("Cancelled on the Cassini Live settings page")
    plain = same_origin(headers)
    del plain["X-Cassini-Request"]
    error(client.post("/settings/api/cancel", headers=plain, json={}), 403, "csrf")


# Models


def delete(client, headers, engine):
    return client.delete(f"/settings/api/models/{engine}", headers=headers)


def test_delete_models(page):
    manager, client, headers = page
    error(delete(client, same_origin(headers), "whistle"), 400, "bundled")
    response = delete(client, same_origin(headers), "nemotron-multilingual")
    assert response.status_code == 200, response.text
    assert_secure(response)
    body = response.json()
    assert body["card"]["id"] == "nemotron-multilingual" and "panel" in body
    error(delete(client, same_origin(headers), "vosk"), 400, "unknown_engine")
    manager.active = "nemotron-en"
    error(delete(client, same_origin(headers), "nemotron-en"), 409, "active")


def test_switch_and_delete_while_busy_are_refused_at_once(page):
    """The page refuses like the form does instead of queueing behind a running switch."""
    manager, client, headers = page
    manager.status = SwitchStatus("checking", "nemotron-en", "whistle")
    body = error(post_engine(client, same_origin(headers), {"engine": "nemotron-multilingual"}),
                 409, "busy")
    assert "in progress" in body["message"]
    error(delete(client, same_origin(headers), "nemotron-multilingual"), 409, "busy")
    assert manager.last_refusal is None
    manager.status = SwitchStatus()


def test_delete_needs_the_csrf_defense(page):
    manager, client, headers = page
    manager.delete_model = AsyncMock()
    plain = {**headers, "Sec-Fetch-Site": "same-origin"}
    error(delete(client, plain, "nemotron-en"), 403, "csrf")
    foreign = same_origin(headers, Origin="https://evil.test")
    error(delete(client, foreign, "nemotron-en"), 403, "csrf")
    manager.delete_model.assert_not_awaited()


def test_delete_storage_errors_are_generic(page):
    manager, client, headers = page
    manager.delete_model = AsyncMock(side_effect=OSError("/data/secret/path"))
    body = error(delete(client, same_origin(headers), "nemotron-en"), 500, "storage")
    assert "/data" not in body["message"]


# URLs


@pytest.mark.parametrize(
    ("nextcloud_url", "root"),
    [
        ("http://cloud.test", ""),
        ("http://cloud.test/", ""),
        ("https://x/nc/index.php", "/nc"),
        ("https://x/nc/", "/nc"),
        (None, ""),
    ],
)
def test_page_url(monkeypatch, nextcloud_url, root):
    if nextcloud_url is None:
        monkeypatch.delenv("NEXTCLOUD_URL", raising=False)
    else:
        monkeypatch.setenv("NEXTCLOUD_URL", nextcloud_url)
    assert settings_page.webroot() == root
    assert settings_page.page_url() == (
        root + "/index.php/apps/app_api/proxy/live_transcription/settings/index.html"
    )
    assert settings_page.admin_settings_url() == root + "/index.php/settings/admin/ai"
