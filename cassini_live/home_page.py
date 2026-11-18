"""The home page behind the "Cassini Live" entry in Nextcloud's app menu.

AppAPI shows a top-menu entry for an ExApp that registers one and, when it is clicked,
opens its embedded page: a bare <div id="content"> on a Nextcloud page, plus the script
registered for the entry. The browser fetches that script and the entry's icon through
the AppAPI proxy, so both are routes here and USER routes in appinfo/info.xml: every
logged-in user sees the entry.

The script (ui/src/home/, built into static/home/home.js) draws the page: how to add
the "Live transcript" bookmark, and for administrators what is left to set up, read
from /status and the ADMIN settings state. Nothing here is a boundary: the admin block
is hidden for others in the browser, and its data stays behind ADMIN routes.
"""

import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import Response

from . import appapi, model_store
from .panel_page import APP_ID_PLACEHOLDER

LOGGER = logging.getLogger(__name__)

ENTRY = "home"
DISPLAY_NAME = "Cassini Live"
SCRIPT_PATH = "ui/home"  # AppAPI appends ".js" and loads it through the proxy
ICON_PATH = "img/app.svg"
STATIC = Path(__file__).parent / "static" / "home"
ICON = model_store.REPO_ROOT / "img" / "app.svg"
# The script carries the bookmark's address and the icon is the store's: neither may
# be served stale after an update.
NO_STORE = "no-store"


def _headers(response: Response) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Cache-Control"] = NO_STORE
    return response


def create_router(app_id: str) -> APIRouter:
    if not appapi.APP_ID.fullmatch(app_id):
        raise ValueError("invalid app id")
    router = APIRouter()
    script = (STATIC / "home.js").read_bytes().replace(APP_ID_PLACEHOLDER, app_id.encode())
    try:
        icon = ICON.read_bytes()
    except OSError:
        icon = None  # Served as 404; the image always ships img/app.svg.

    @router.get("/ui/home.js")
    async def home_script():
        return _headers(Response(script, media_type="text/javascript; charset=utf-8"))

    @router.get("/img/app.svg")
    async def app_icon():
        if icon is None:
            return _headers(Response("Not found", status_code=404, media_type="text/plain"))
        return _headers(Response(icon, media_type="image/svg+xml"))

    return router


async def register(nc) -> None:
    """Add the menu entry and its script. Captions do not depend on it: a failure is
    logged and the app carries on."""
    try:
        await nc.ui.resources.set_script("top_menu", ENTRY, SCRIPT_PATH)
        await nc.ui.top_menu.register(ENTRY, DISPLAY_NAME, ICON_PATH, admin_required=False)
    except Exception as error:
        LOGGER.warning("Could not add Cassini Live to the app menu (%s)", type(error).__name__)


async def unregister(nc) -> None:
    try:
        await nc.ui.top_menu.unregister(ENTRY)
        await nc.ui.resources.delete_script("top_menu", ENTRY, SCRIPT_PATH)
    except Exception as error:
        LOGGER.debug("Could not remove the app menu entry (%s)", type(error).__name__)
