"""Native Talk lifecycle: a single transport per room, independent room locks."""

import asyncio
import weakref
from contextlib import suppress

from .config import websocket_url
from .talk.client import SpreedClient
from .talk.types import HPBSettings, SigConnectResult, SpreedClientException


class CaptionService:
    def __init__(self, config, client_factory=SpreedClient):
        self.config = config
        self.client_factory = client_factory
        self.clients = {}
        self.locks = weakref.WeakValueDictionary()
        self.settings = None
        self.enabled = False
        self.admission = None

    async def discover(self, nc):
        if not self.config.secret:
            raise ValueError("Enter the Talk HPB internal secret in the app's deploy options")
        settings = await nc.ocs("GET", "/ocs/v2.php/apps/spreed/api/v3/signaling/settings")
        settings["server"] = websocket_url(self.config.hpb_url or settings.get("server", ""))
        self.settings = HPBSettings(**settings)

    def _lock(self, token):
        return self.locks.setdefault(token, asyncio.Lock())

    async def request(self, req):
        async with self._lock(req.roomToken):
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
                        await client.close()
                return
            if client:
                await client.add_target(req.ncSessionId)
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
                if self.clients.get(req.roomToken) is client:
                    self.clients.pop(req.roomToken, None)

            client.leave_call_cb = closed
            try:
                async with asyncio.timeout(45):
                    result = await client.connect()
                    if result != SigConnectResult.SUCCESS:
                        raise SpreedClientException("Could not connect to Talk's signalling server")
                    await client.add_target(req.ncSessionId)
            except BaseException:
                self.clients.pop(req.roomToken, None)
                with suppress(Exception):
                    await client.close()
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
                await client.close()

    async def close(self):
        self.enabled = False
        await self.stop_rooms()

    async def stop_rooms(self):
        clients = list(self.clients.values())
        self.clients.clear()
        await asyncio.gather(*(c.close() for c in clients), return_exceptions=True)
