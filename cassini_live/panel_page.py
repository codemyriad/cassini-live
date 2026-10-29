"""The transcript panel bundle and its bookmarklet install page, served through the AppAPI proxy.

Any logged-in user may fetch these (USER access level in appinfo/info.xml): the
panel script that the bookmarklet injects into Talk call pages, and the page where
people pick up the bookmarklet. Everything is read-only static content built from
ui/ into cassini_live/static/panel/ — no state, no CSRF surface.

Serving the bundle same-origin with Nextcloud is load-bearing, not a convenience:
Talk pages ship `script-src 'nonce-…' 'strict-dynamic'` and `connect-src 'self'`,
so a nonce-carrying <script src> for this route passes the page's CSP, and any
future panel fetches to ExApp routes stay same-origin.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response

STATIC = Path(__file__).parent / "static" / "panel"
ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "panel.js": "text/javascript; charset=utf-8",
    "install.js": "text/javascript; charset=utf-8",
    "install.css": "text/css; charset=utf-8",
}
# The install page runs its own scripts and styles; everything it needs is
# served by this router, so 'self' covers it.
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
# The loader bookmarklet cache-busts panel.js with a timestamp query and the
# install page embeds the bookmarklets, so nothing here may be served stale.
NO_STORE = "no-store"


def _load_assets() -> dict[str, bytes]:
    return {name: (STATIC / name).read_bytes() for name in ASSETS}


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


def create_router() -> APIRouter:
    router = APIRouter()
    assets = _load_assets()

    @router.get("/panel/")
    @router.get("/panel/{name}")
    async def asset(request: Request, name: str = "index.html"):
        if name not in assets:
            return _secure(Response("Not found", status_code=404, media_type="text/plain"))
        return _secure(Response(assets[name], media_type=ASSETS[name]))

    return router
