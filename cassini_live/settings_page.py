"""The admin-only Cassini Live settings page and its JSON API, served through the AppAPI proxy.

AppAPI and HaRP enforce the ADMIN access level of the routes in appinfo/info.xml, but the
proxy is exempt from Nextcloud's CSRF checks: state-changing requests must carry a custom
header (which a cross-site page cannot send without a CORS preflight that nothing answers),
a same-origin Sec-Fetch-Site and Origin when present, and a JSON body. The page and the
declarative form change engines through the same EngineManager methods.
"""

import functools
import json
import os
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import __version__, admin_form, catalog, model_store
from .engine_switch import EngineManager, SwitchRefused
from .model_store import ModelError

APP_ID = "live_transcription"
PROXY_PATH = admin_form.PAGE_PATH
ADMIN_SETTINGS_PATH = "/index.php/settings/admin/ai"
DOCS_URL = (
    "https://github.com/codemyriad/cassini-live/blob/main/docs/install.md#choose-a-speech-engine"
)
STATIC = Path(__file__).parent / "static" / "settings"
ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
MAX_BODY = 1024
MAX_ENGINE = 64
NO_STORE = "no-store"
CACHED = "private, max-age=86400"  # assets are versioned with ?v=
CANCEL_REASON = "Cancelled on the Cassini Live settings page"
LICENSES = {e.license.id: e.license for e in catalog.ENGINES.values()}


def webroot() -> str:
    """Path of the Nextcloud web root ("" or "/nc"), from the daemon's Nextcloud URL."""
    path = urlsplit(os.environ.get("NEXTCLOUD_URL", "")).path.rstrip("/")
    if path.endswith("/index.php"):
        path = path[: -len("/index.php")]
    return path.rstrip("/")


def page_url() -> str:
    # Root-relative on purpose: it resolves against the browser's origin, so an internal
    # NEXTCLOUD_URL host cannot break the link, while a sub-directory install keeps its prefix.
    return webroot() + PROXY_PATH


def admin_settings_url() -> str:
    return webroot() + ADMIN_SETTINGS_PATH


def secure(response: Response, cache: str = NO_STORE) -> Response:
    headers = response.headers
    headers["Content-Security-Policy"] = CSP
    headers["X-Content-Type-Options"] = "nosniff"
    headers["X-Frame-Options"] = "DENY"
    headers["Referrer-Policy"] = "no-referrer"
    headers["Cross-Origin-Opener-Policy"] = "same-origin"
    headers["Cross-Origin-Resource-Policy"] = "same-origin"
    headers["Cache-Control"] = cache
    return response


class Refused(Exception):
    def __init__(self, status: int, code: str, message: str, reasons: list | None = None):
        super().__init__(message)
        self.status, self.code, self.reasons = status, code, reasons or []


def error_response(status: int, code: str, message: str, reasons=()) -> Response:
    body = {"error": {"code": code, "message": message, "reasons": list(reasons)}}
    return secure(JSONResponse(body, status_code=status))


def json_response(body: dict, status: int = 200) -> Response:
    return secure(JSONResponse(body, status_code=status))


def require_admin(request: Request) -> None:
    # AppAPI and HaRP enforce ADMIN; this rejects the container-local path without a user.
    username = request.scope.get("username")
    if not isinstance(username, str) or not username:
        raise Refused(403, "forbidden", "Open this page from Nextcloud as an administrator")


def _netloc(value: str) -> str:
    return value.strip().lower()


def _origin_allowed(request: Request, origin: str) -> bool:
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    allowed = {_netloc(request.headers.get("host", ""))}
    forwarded = request.headers.get("x-forwarded-host")
    if forwarded:
        allowed.add(_netloc(forwarded.split(",")[0]))
    nextcloud = urlsplit(os.environ.get("NEXTCLOUD_URL", "")).netloc
    if nextcloud:
        allowed.add(_netloc(nextcloud))
    allowed.discard("")
    return _netloc(parts.netloc) in allowed


async def _read_body(request: Request) -> bytes:
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY:
            raise Refused(413, "too_large", "The request body is too large")
    return body


async def require_same_origin(request: Request) -> dict | None:
    """CSRF defense for POST and DELETE. Returns the parsed JSON object for POST."""
    if request.headers.get("x-cassini-request") != "1":
        raise Refused(403, "csrf", "Missing request header")
    site = request.headers.get("sec-fetch-site")
    if site is not None and site != "same-origin":
        raise Refused(403, "csrf", "Cross-site requests are not allowed")
    origin = request.headers.get("origin")
    if origin is not None and origin != "null" and not _origin_allowed(request, origin):
        raise Refused(403, "csrf", "Cross-origin requests are not allowed")
    if request.method != "POST":
        return None
    media = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media != "application/json":
        raise Refused(415, "unsupported_media_type", "Send the request as application/json")
    body = await _read_body(request)
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        data = None
    if not isinstance(data, dict):
        raise Refused(400, "bad_request", "The request body must be a JSON object")
    return data


def load_assets(static: Path, types: dict[str, str]) -> dict[str, bytes]:
    """A page's files by name, with the app version in its index.html."""
    assets = {}
    for name in types:
        try:
            content = (static / name).read_bytes()
        except OSError:
            continue  # Served as 404; the image always ships the files.
        if name == "index.html":
            content = content.replace(b"{{VERSION}}", __version__.encode())
        assets[name] = content
    return assets


def guard(handler):
    """Require an administrator and map refusals to the error envelope."""

    @functools.wraps(handler)
    async def run(request: Request, **params):
        try:
            require_admin(request)
            return await handler(request, **params)
        except Refused as refused:
            return error_response(refused.status, refused.code, str(refused), refused.reasons)
        except SwitchRefused as refused:
            reasons = [asdict(r) for r in refused.reasons]
            return error_response(refused.status, refused.code, str(refused), reasons)

    return run


def create_router(manager: EngineManager, status_body: Callable[[], dict]) -> APIRouter:
    router = APIRouter()
    assets = load_assets(STATIC, ASSETS)

    @router.get("/settings/api/state")
    @guard
    async def state(request: Request):
        return json_response({
            "version": __version__,
            "panel": manager.panel(),
            "status": status_body(),
            "links": {
                "docs": DOCS_URL,
                "adminSettings": admin_settings_url(),
                "licenses": {k: f"licenses/{k}.txt" for k in LICENSES},
            },
        })

    @router.post("/settings/api/engine")
    @guard
    async def engine(request: Request):
        data = await require_same_origin(request)
        engine = data.get("engine")
        if not isinstance(engine, str) or not engine or len(engine) > MAX_ENGINE:
            raise Refused(400, "bad_request", "Choose a speech engine")
        # Page refusals go back to the browser only; lastRefusal is the form's channel.
        switch = await manager.request_switch(engine)
        return json_response({"switch": switch.to_json(), "panel": manager.panel()}, 202)

    @router.post("/settings/api/capacity")
    @guard
    async def capacity(request: Request):
        await require_same_origin(request)
        # Refused with 409 while calls have captions, a change runs or captions are not
        # ready; captions answer 503 with Retry-After while it runs.
        result = await manager.measure()
        return json_response({"capacity": result, "panel": manager.panel()}, 202)

    @router.post("/settings/api/cancel")
    @guard
    async def cancel(request: Request):
        await require_same_origin(request)
        switch = await manager.cancel(CANCEL_REASON)
        return json_response({"switch": switch.to_json(), "panel": manager.panel()})

    @router.delete("/settings/api/models/{engine_id}")
    @guard
    async def delete_model(request: Request, engine_id: str):
        await require_same_origin(request)
        if engine_id in catalog.ENGINES and catalog.get(engine_id).asset is None:
            raise Refused(400, "bundled", "Whistle is part of the image and cannot be removed")
        try:
            card = await manager.delete_model(engine_id)
        except (ModelError, OSError):
            raise Refused(500, "storage", "Could not remove the model files") from None
        return json_response({"card": card, "panel": manager.panel()})

    @router.get("/settings/licenses/{license_id}.txt")
    @guard
    async def license_text(request: Request, license_id: str):
        license = LICENSES.get(license_id)
        if license is None:
            raise Refused(404, "not_found", "Unknown license")
        try:
            text = (model_store.REPO_ROOT / license.text_file).read_bytes()
        except OSError:
            raise Refused(404, "not_found", "The license text is missing") from None
        return secure(Response(text, media_type="text/plain; charset=utf-8"), CACHED)

    @router.get("/settings/{name}")
    @guard
    async def asset(request: Request, name: str):
        if name not in assets:
            raise Refused(404, "not_found", "Not found")
        response = Response(assets[name], media_type=ASSETS[name])
        return secure(response, NO_STORE if name == "index.html" else CACHED)

    known = list(router.routes)

    @router.api_route(
        "/settings/{rest:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def fallback(request: Request, rest: str):
        # Anything else under /settings/ still gets the security headers.
        if any(route.path_regex.match(request.url.path) for route in known):
            return error_response(405, "method_not_allowed", "Method not allowed")
        return error_response(404, "not_found", "Not found")

    return router
