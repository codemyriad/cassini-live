"""A stand-in for Talk, until Talk can call a caption provider it does not name.

Talk reaches its caption provider like this:

    browser -> Talk (its live-transcription endpoints) -> live_transcription (/api/v1/...)

Talk calls only the app id `live_transcription` (nextcloud/spreed#20296), so under any
other id nothing reaches the native caption API. These routes play Talk's part:

    browser -> /mock_talk_proxy/... -> this app's /api/v1/...

Each route makes the checks Talk makes (see call_access), then sends the native route a
real HTTP request: the path, the fields and the AppAPI headers Talk sends through AppAPI,
with AppAPI's timeout, and reads only what Talk reads from the answer. The native API
gets no special treatment from here, so what works through this module works for Talk.

They are the only caption routes declared for the browser, and only what the transcript
panel needs is here: starting and stopping captions. The paths follow Talk's
(`POST` and `DELETE /live-transcription/{token}`), with one difference: Talk knows the
caller's session, and here the browser sends it as `ncSessionId` and it is checked.

Temporary. When Talk can call this app, delete this module, its two lines in app.py and
its routes in the manifest, and let the panel call Talk's endpoints.
"""

import asyncio
import base64
import json
import logging
import math
import os
import secrets
from collections.abc import Awaitable, Callable
from urllib.parse import quote

import niquests
from fastapi import APIRouter, HTTPException, Request

from . import __version__, settings_page
from .call_access import ROOM_TOKEN, CallSessions, RateLimit

LOGGER = logging.getLogger(__name__)
# AppAPI's default for a request to an ExApp, which is what Talk gets.
TIMEOUT_S = 3.0
TRANSCRIBE = "/api/v1/call/transcribe"

# (method, path, headers, JSON body, timeout) -> (status, headers, body)
Transport = Callable[[str, str, dict, dict | None, float], Awaitable[tuple[int, dict, bytes]]]


def own_address(environ=os.environ) -> str:
    """Where this app listens, as nc_py_api's run_app binds it: a Unix socket behind
    HaRP, otherwise APP_HOST and APP_PORT."""
    if environ.get("HP_SHARED_KEY"):
        return "http+unix://" + quote(environ.get("HP_EXAPP_SOCK", "/tmp/exapp.sock"), safe="")
    host = environ.get("APP_HOST", "127.0.0.1")
    if host in ("", "0.0.0.0"):
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}:{int(environ['APP_PORT'])}"


async def loopback(method, path, headers, body, timeout):
    async with niquests.AsyncSession() as session:
        session.trust_env = False  # never through a proxy from the environment
        response = await session.request(
            method, own_address() + path, headers=headers, json=body, timeout=timeout
        )
        return response.status_code, dict(response.headers), response.content or b""


class NativeApi:
    """Requests to this app's native caption API, built as AppAPI builds Talk's."""

    def __init__(self, app_id: str, transport: Transport = loopback, timeout: float = TIMEOUT_S):
        self.app_id, self.transport, self.timeout = app_id, transport, timeout

    def headers(self, user: str | None) -> dict:
        credentials = f"{user or ''}:{os.environ.get('APP_SECRET', '')}"
        return {
            "AA-VERSION": os.environ.get("AA_VERSION", ""),
            "EX-APP-ID": self.app_id,
            "EX-APP-VERSION": os.environ.get("APP_VERSION") or __version__,
            "AUTHORIZATION-APP-API": base64.b64encode(credentials.encode()).decode(),
            "AA-REQUEST-ID": secrets.token_hex(16),
        }

    async def request(self, method: str, route: str, user: str | None, params: dict | None = None):
        """(status, error text, response headers). Raises TimeoutError or another
        exception when there is no answer, where AppAPI would hand Talk an error."""
        async with asyncio.timeout(self.timeout):
            status, headers, body = await self.transport(
                method, route, self.headers(user), params, self.timeout
            )
        error = None
        if not 200 <= status < 300:
            # Talk takes the reason from the JSON body's "error" and from nowhere else.
            try:
                decoded = json.loads(body)
            except ValueError:
                decoded = None
            if isinstance(decoded, dict) and isinstance(decoded.get("error"), str):
                error = decoded["error"]
        return status, error, headers


def create_router(nc_factory) -> APIRouter:
    router = APIRouter()
    sessions = CallSessions(nc_factory)
    toggles = RateLimit()

    async def toggle(request: Request, token: str, enable: bool, data: dict | None):
        """Talk's checks, then Talk's request to the provider."""
        username = request.scope.get("username") or ""
        if not username:
            raise HTTPException(403, "Turn captions on from Nextcloud")
        session = (data or {}).get("ncSessionId")
        if not isinstance(session, str) or not 1 <= len(session) <= 512:
            raise HTTPException(400, "ncSessionId is required")
        if not ROOM_TOKEN.fullmatch(token):
            raise HTTPException(400, "Invalid room token")
        retry = toggles.retry_after(username)
        if retry:
            raise HTTPException(
                429,
                "Too many caption requests; try again shortly",
                headers={"Retry-After": str(max(1, math.ceil(retry)))},
            )
        if not await sessions.may_toggle(username, token, session, enable):
            raise HTTPException(
                403,
                "Join the call to turn captions on" if enable else "Not your session in this call",
            )
        # What Talk sends: no language unless the conversation has one stored, and here
        # there is nowhere to store one, so the provider's default applies.
        params = {"roomToken": token, "ncSessionId": session, "enable": enable}
        language = (data or {}).get("langId")
        if enable and isinstance(language, str) and language:
            params["langId"] = language
        try:
            status, error, headers = await request.app.state.native_api.request(
                "POST", TRANSCRIBE, username, params
            )
        except TimeoutError:
            raise HTTPException(504, "Captions did not answer in time; please try again") from None
        except Exception as failure:
            LOGGER.warning("The native caption API is unreachable (%s)", type(failure).__name__)
            raise HTTPException(502, "Captions are not reachable; please try again") from None
        if 200 <= status < 300:
            return {}
        retry_after = {k.lower(): v for k, v in headers.items()}.get("retry-after")
        raise HTTPException(
            status,
            error or "Captions could not be changed",
            headers={"Retry-After": retry_after} if retry_after else None,
        )

    async def same_origin_json(request: Request) -> dict | None:
        try:
            return await settings_page.require_same_origin(request, with_body=True)
        except settings_page.Refused as refused:
            raise HTTPException(refused.status, str(refused)) from None

    @router.post("/mock_talk_proxy/live-transcription/{token}")
    async def enable(token: str, request: Request):
        data = await same_origin_json(request)
        # Talk's enable takes no language; the conversation's stored one applies.
        return await toggle(request, token, True, {"ncSessionId": (data or {}).get("ncSessionId")})

    @router.delete("/mock_talk_proxy/live-transcription/{token}")
    async def disable(token: str, request: Request):
        return await toggle(request, token, False, await same_origin_json(request))

    @router.post("/captions/transcribe")
    async def panel_before_the_mock(request: Request):
        # The route panels built before this module call, with the native body. Kept for
        # one release so a tab holding an old panel keeps working across an upgrade.
        data = await same_origin_json(request) or {}
        token = data.get("roomToken")
        if not isinstance(token, str) or not isinstance(data.get("enable", True), bool):
            raise HTTPException(400, "Invalid request")
        return await toggle(request, token, data.get("enable", True), data)

    return router
