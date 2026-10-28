"""The admin-only live diagnostics page's feed, served through the AppAPI proxy.

AppAPI and HaRP enforce the ADMIN access level of the routes in appinfo/info.xml; every
route here is a read-only GET. Polls keep the feed sampling (diagnostic_feed). Names of the
owner room's speakers go only to an administrator who is a participant of that room, checked
with Talk as them on each poll (answers cached briefly); everyone else reads generic labels. Lanes
carry the sampler's anonymous numbers, never a room token, session or participant id.
"""

import copy
import math
from collections.abc import Callable

from fastapi import APIRouter, Request

from . import __version__
from .diagnostic_feed import PRESENCE_S, DiagnosticFeed
from .diagnostic_history import COLUMNS
from .diagnostics import OTHER_CALL_SPEAKER, SOME_SPEAKER, UNNAMED
from .membership import PolledMembership, RoomMembership
from .settings_page import Refused, error_response, guard, json_response


def _since(value: str | None) -> float:
    if value is None:
        return 0.0
    try:
        since = float(value)
    except ValueError:
        since = math.nan
    if not math.isfinite(since) or since < 0:
        raise Refused(400, "bad_request", "since must be a finite number of seconds, 0 or more")
    return since


def redact(body: dict) -> dict:
    """The body without the owner room's speaker names."""
    sample = copy.deepcopy(body["sample"])
    if sample is not None:
        for lane in sample.get("lanes", []):
            if "name" in lane:
                lane["name"] = UNNAMED
        decision = sample.get("decision")
        if decision and decision.get("who") not in (SOME_SPEAKER, OTHER_CALL_SPEAKER):
            decision["who"] = SOME_SPEAKER
    return {**body, "sample": sample}


def create_router(
    feed: DiagnosticFeed, membership: RoomMembership, video_status: Callable[[], dict]
) -> APIRouter:
    router = APIRouter()
    # The usual viewer is an administrator outside the call, polling once a second.
    polled = PolledMembership(membership)

    @router.get("/diagnostics/api/feed")
    @guard
    async def feed_route(request: Request):
        since = _since(request.query_params.get("since"))
        username = request.scope.get("username") or ""
        token = feed.names_token() or feed.owner_token()
        member = bool(token) and await polled.check(username, token)
        # The owner room may have changed while Talk answered.
        member = member and (feed.names_token() or feed.owner_token()) == token
        feed.touch(token if member else None)
        body = feed.read(since)
        return json_response({
            "version": __version__,
            "window": COLUMNS,
            "presence": PRESENCE_S,
            "names": member,
            **(body if member else redact(body)),
            "video": video_status(),
        })

    known = list(router.routes)

    @router.api_route(
        "/diagnostics/{rest:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def fallback(request: Request, rest: str):
        # Anything else under /diagnostics/ still gets the security headers.
        if any(route.path_regex.match(request.url.path) for route in known):
            return error_response(405, "method_not_allowed", "Method not allowed")
        return error_response(404, "not_found", "Not found")

    return router
