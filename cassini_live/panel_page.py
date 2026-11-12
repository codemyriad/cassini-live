"""The transcript panel bundle, served through the AppAPI proxy.

Any logged-in user may fetch it (USER access level in the manifests): the panel
script that the "Live transcript" bookmark injects into Talk call pages. People pick
the bookmark up on the home page (home_page.py). The bundle is read-only static
content built from ui/ into cassini_live/static/panel/ — no state, no CSRF surface.

Serving the bundle same-origin with Nextcloud is load-bearing, not a convenience:
Talk pages ship `script-src 'nonce-…' 'strict-dynamic'` and `connect-src 'self'`,
so a nonce-carrying <script src> for this route passes the page's CSP, and any
future panel fetches to ExApp routes stay same-origin.

The bundle reaches this app's routes through the AppAPI proxy, whose path names the app
id. The build leaves APP_ID_PLACEHOLDER where the id goes (ui/src/app-id.js), and this
router fills in the id it is given.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response

from . import appapi

STATIC = Path(__file__).parent / "static" / "panel"
ASSETS = {"panel.js": "text/javascript; charset=utf-8"}
# Must match ui/src/app-id.js.
APP_ID_PLACEHOLDER = b"__CASSINI_LIVE_APP_ID__"
# The route serves a script, not a page; this keeps a direct visit inert.
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
# The bookmark cache-busts panel.js with a timestamp query, so it is never served stale.
NO_STORE = "no-store"


def load_assets(app_id: str, static: Path = STATIC) -> dict[str, bytes]:
    """The served files, with `app_id` in place of the build's placeholder."""
    if not appapi.APP_ID.fullmatch(app_id):
        raise ValueError("invalid app id")
    return {
        name: (static / name).read_bytes().replace(APP_ID_PLACEHOLDER, app_id.encode())
        for name in ASSETS
    }


def _secure(response: Response) -> Response:
    headers = response.headers
    headers["Content-Security-Policy"] = CSP
    headers["X-Content-Type-Options"] = "nosniff"
    headers["X-Frame-Options"] = "DENY"
    headers["Referrer-Policy"] = "no-referrer"
    headers["Cross-Origin-Opener-Policy"] = "same-origin"
    headers["Cross-Origin-Resource-Policy"] = "same-origin"
    headers["Cache-Control"] = NO_STORE
    return response


def create_router(app_id: str) -> APIRouter:
    router = APIRouter()
    assets = load_assets(app_id)

    @router.get("/panel/{name}")
    async def asset(request: Request, name: str):
        if name not in assets:
            return _secure(Response("Not found", status_code=404, media_type="text/plain"))
        return _secure(Response(assets[name], media_type=ASSETS[name]))

    return router
