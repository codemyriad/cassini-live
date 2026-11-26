"""Whether Talk encrypts calls end to end, read from Nextcloud's capabilities.

When Talk's `call_end_to_end_encryption` is on, participants encrypt their media in
the browser (WebRTC encoded transforms) and Janus forwards the frames without
decrypting them. Cassini Live subscribes server side and takes no part in the key
exchange, so the Opus payloads never decode and captions would start and produce
nothing (see docs/known-limitations.md). The native caption API refuses to start
captions instead, so the person who asked sees why.

The flag is `spreed.config.call["end-to-end-encryption"]` in
`/ocs/v2.php/cloud/capabilities`, asked as the app. An administrator can flip it at
any time with occ, so the answer is cached only briefly, and concurrent starts
share one request. A failed read answers False: captions must not stop working
over a capabilities hiccup, and an encrypted call that slips through fails no
worse than before the check existed.
"""

import asyncio
import logging
import time

from .nc_client import nc_client

LOGGER = logging.getLogger(__name__)


class CallEncryption:
    def __init__(self, nc_factory, ttl=30.0, clock=time.monotonic):
        self.nc_factory = nc_factory
        self.ttl = ttl
        self.clock = clock
        self._answer: tuple[float, bool] | None = None
        self._asking: asyncio.Future | None = None

    async def active(self) -> bool:
        if self._answer and self.clock() - self._answer[0] < self.ttl:
            return self._answer[1]
        if self._asking is None:
            self._asking = asyncio.ensure_future(self._ask())
            self._asking.add_done_callback(lambda _: setattr(self, "_asking", None))
        # A caller that goes away (a cancelled start) leaves the answer to the others.
        return await asyncio.shield(self._asking)

    async def _ask(self) -> bool:
        try:
            async with nc_client(self.nc_factory) as nc:
                data = await nc.ocs("GET", "/ocs/v2.php/cloud/capabilities")
            call = data["capabilities"]["spreed"]["config"]["call"]
            # Strictly the boolean: an absent or oddly shaped flag means "not encrypted".
            active = call.get("end-to-end-encryption") is True
        except Exception as error:
            LOGGER.warning(
                "Could not read Talk's call encryption flag (%s); assuming calls are not"
                " end-to-end encrypted",
                type(error).__name__,
            )
            active = False
        self._answer = (self.clock(), active)
        return active
