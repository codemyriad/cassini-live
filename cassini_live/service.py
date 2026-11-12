"""Native Talk lifecycle: a single transport per room, independent room locks."""

import asyncio
import json
import time
import weakref
from contextlib import suppress

from websockets.asyncio.client import connect

from .config import websocket_url
from .diagnostic_feed import DiagnosticFeed
from .diagnostics import DiagnosticVideo, NameHolds
from .safety import ResourceRefused
from .talk.client import SpreedClient
from .talk.types import HPBSettings, SigConnectResult, SpreedClientException
from .talk.utils import get_ssl_context
from .talk_setup import SECRET_MISSING, Check, TalkSetup

HPB_CHECK_TIMEOUT = 10


class CaptionService:
    def __init__(self, config, client_factory=SpreedClient, talk=None, connector=connect):
        self.config = config
        self.client_factory = client_factory
        # Deploy options, else what an administrator saved on the settings page.
        self.talk = talk or TalkSetup.from_config(config)
        self.connector = connector
        self.clients = {}
        self.locks = weakref.WeakValueDictionary()
        self.settings = None
        self.enabled = False
        self.admission = None
        self.generation = 0
        self.closing = {}
        self.inflight = set()
        # The video and the diagnostics page can both want one room's names.
        self.name_holds = NameHolds()
        self.diagnostics = DiagnosticVideo(self, names=self.name_holds)
        self.diagnostic_feed = DiagnosticFeed(self, names=self.name_holds)

    async def discover(self, nc):
        secret, _ = self.talk.secret()
        if not secret:
            raise ValueError(SECRET_MISSING)
        override, _ = self.talk.hpb_url()
        settings = await nc.ocs("GET", "/ocs/v2.php/apps/spreed/api/v3/signaling/settings")
        settings["server"] = websocket_url(override or settings.get("server", ""))
        self.settings = HPBSettings(**settings, secret=secret)

    async def check_hpb(self) -> Check:
        """Authenticate to the HPB as an internal client and leave, joining no room: the
        same hello a caption room sends. Records and returns the outcome."""
        settings = self.settings
        if settings is None:
            check = Check("unreachable", "Talk's signalling settings are not loaded", time.time())
        else:
            check = await self._hello(settings)
        self.talk.check = check
        return check

    async def _hello(self, settings) -> Check:
        client = self.client_factory("cassini-live-setup-check", settings, "auto", None)
        ssl = get_ssl_context(settings.server)
        try:
            async with asyncio.timeout(HPB_CHECK_TIMEOUT):
                async with self.connector(
                    settings.server, **({"ssl": ssl} if ssl else {})
                ) as ws:
                    client._server = ws
                    await client.send_hello()
                    while True:
                        message = json.loads(await ws.recv())
                        kind = message.get("type")
                        if kind == "hello":
                            with suppress(Exception):
                                await ws.send(json.dumps({"type": "bye", "bye": {}}))
                            accepted = "The HPB accepted the internal secret"
                            return Check("passed", accepted, time.time())
                        if kind == "error":
                            code = (message.get("error") or {}).get("code") or "error"
                            return Check("failed", hpb_error_text(code), time.time())
        except TimeoutError:
            return Check("unreachable", "The HPB did not answer in time", time.time())
        except Exception as error:
            return Check(
                "unreachable",
                f"Could not reach the HPB at {settings.server} ({type(error).__name__})",
                time.time(),
            )

    def _lock(self, token):
        return self.locks.setdefault(token, asyncio.Lock())

    async def request(self, req):
        task = asyncio.current_task()
        self.inflight.add(task)
        try:
            return await self._request(req)
        finally:
            self.inflight.discard(task)

    async def _request(self, req):
        generation = self.generation
        async with self._lock(req.roomToken):
            if req.enable and self.admission:
                self.admission.require_ready()
            client = self.clients.get(req.roomToken)
            if client and client.defunct.is_set():
                self.clients.pop(req.roomToken, None)
                client = None
            if not req.enable:
                if client:
                    await client.remove_target(req.ncSessionId)
                    # Stop promptly when nobody is receiving captions.
                    if not client.targets and not client._nc_sid_wait_stash:
                        self.clients.pop(req.roomToken, None)
                        await asyncio.shield(self._closing_task(client))
                return
            if client:
                await client.add_target(req.ncSessionId)
                self.diagnostic_feed.start()
                return
            if self.settings is None:
                raise SpreedClientException("Talk's high-performance backend is not ready")
            if len(self.clients) >= 64:
                raise SpreedClientException("Caption room capacity reached")
            if self.admission:
                self.admission.allow_room(len(self.clients))
            client = self.client_factory(req.roomToken, self.settings, req.langId, None)
            client.admission = self.admission
            self.clients[req.roomToken] = client

            # Transport close can call back while request() holds the room lock.
            # A synchronous dictionary removal here needs no second lock.
            async def closed(_token):
                await self.diagnostics.stop(_token)
                if self.clients.get(req.roomToken) is client:
                    self.clients.pop(req.roomToken, None)

            client.leave_call_cb = closed
            try:
                async with asyncio.timeout(45):
                    result = await client.connect()
                    if result != SigConnectResult.SUCCESS:
                        raise SpreedClientException("Could not connect to Talk's signalling server")
                    if generation != self.generation:
                        raise ResourceRefused("Caption service paused during connection")
                    if self.admission:
                        self.admission.require_ready()
                    await client.add_target(req.ncSessionId)
                    self.diagnostics.start(req.roomToken)
                    self.diagnostic_feed.start()
            except BaseException:
                if self.clients.get(req.roomToken) is client:
                    self.clients.pop(req.roomToken, None)
                with suppress(Exception):
                    await asyncio.shield(self._closing_task(client))
                if generation != self.generation:
                    raise ResourceRefused("Caption service paused during connection") from None
                raise

    async def set_language(self, req):
        async with self._lock(req.roomToken):
            client = self.clients.get(req.roomToken)
            if client is None:
                raise KeyError(req.roomToken)
            await client.set_language(req.langId)

    async def leave(self, token):
        async with self._lock(token):
            client = self.clients.pop(token, None)
            if client:
                await asyncio.shield(self._closing_task(client))

    async def close(self):
        self.enabled = False
        await self.stop_rooms()
        await self.diagnostic_feed.close()

    def _closing_task(self, client):
        if client not in self.closing:
            task = asyncio.create_task(client.close())
            self.closing[client] = task

            def finished(done):
                if not done.cancelled() and done.exception() is None:
                    self.closing.pop(client, None)

            task.add_done_callback(finished)
        return self.closing[client]

    async def stop_rooms(self):
        self.generation += 1
        await self.diagnostics.stop()
        clients = list(self.clients.values())
        self.clients.clear()
        for client in clients:
            self._closing_task(client)
        pending = [task for task in self.inflight if task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.shield(asyncio.gather(*pending, return_exceptions=True))
        # Never discard a still-running close when the caller is cancelled.
        if self.closing:
            await asyncio.shield(asyncio.gather(*self.closing.values()))
            self.closing.clear()


def hpb_error_text(code: str) -> str:
    if code in {"invalid_token", "auth_failed", "invalid_hello_request"}:
        return "The HPB refused the internal secret: check it against [clients] internalsecret"
    if code == "invalid_client_type":
        return "The HPB does not accept internal clients: set [clients] internalsecret on it"
    if code == "invalid_backend":
        return "The HPB does not know this Nextcloud: check its [backend] configuration"
    return f"The HPB refused the connection ({code})"
