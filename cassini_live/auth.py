"""AppAPI credential checks that never print secrets on authentication failure.

nc_py_api 0.24's default middleware prints its sign_check exception, including
the configured secret for mismatched credentials. Validate first and return a
generic 401, before any SDK lifecycle dependency can perform that check.
"""

import base64
import binascii
import hmac
import os

from nc_py_api.ex_app import AppAPIAuthMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import PlainTextResponse


class SafeAppAPIAuthMiddleware(AppAPIAuthMiddleware):
    def __init__(self, app, *, app_id: str, disable_for: list[str] | None = None):
        super().__init__(app, disable_for)
        # AppAPI names the registered app in EX-APP-ID; create_app passes the same id.
        self.app_id = app_id

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or scope.get("path") == "/heartbeat":
            await self.app(scope, receive, send)
            return
        conn = HTTPConnection(scope)
        username, secret = "", ""
        try:
            encoded = conn.headers.get("AUTHORIZATION-APP-API", "")
            username, secret = base64.b64decode(encoded, validate=True).decode().split(":", 1)
        except (ValueError, UnicodeError, binascii.Error):
            pass
        expected = os.environ.get("APP_SECRET", "")
        valid = (
            expected
            and hmac.compare_digest(secret.encode(), expected.encode())
            and conn.headers.get("EX-APP-ID") == self.app_id
            and bool(conn.headers.get("EX-APP-VERSION"))
        )
        if not valid:
            await PlainTextResponse("Unauthorized", status_code=401)(scope, receive, send)
            return
        scope["username"] = username
        await self.app(scope, receive, send)
