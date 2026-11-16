"""Closing an AsyncNextcloudApp.

nc-py-api (0.24.x) builds two niquests sessions in the client's __init__ and exposes no
public close, so a dropped client strands its sockets: over HTTP/3 they are QUIC over
UDP, which no peer reset or idle timer ever reclaims, and each one holds its file
descriptors until the process dies (the 2026-11-13 production fd exhaustion). The
sessions themselves close fine — the library awaits adapter.close() internally when it
discards one — so reach past the privacy until upstream grows aclose(). The dependency
is pinned <0.25; test_nc_client.py fails the build if the attribute layout moves.
"""

import asyncio
import contextlib
import logging

LOGGER = logging.getLogger(__name__)

CLOSE_TIMEOUT_S = 2.0

# asyncio holds only weak references to tasks; a close that outlives its cancelled
# caller must be kept alive here until it finishes.
_CLOSING: set[asyncio.Task] = set()


async def _close(adapters) -> None:
    """Bounded and swallowing: this runs on error paths, after a request may have been
    cancelled mid-flight, so a failure to close must neither mask the original error
    nor stall the caller."""
    for adapter in adapters:
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT_S):
                await adapter.close()
        except Exception as error:
            LOGGER.debug("Closing a Nextcloud client failed (%s)", type(error).__name__)


async def close_client(nc) -> None:
    """Close both of a client's sessions; never raises except for cancellation.

    The close runs as its own task: a caller cancelled mid-close (a retry loop being
    stopped, a timeout expiring around the context manager) propagates its cancellation
    immediately while the close still finishes in the background — otherwise the
    cancelled path would strand the sockets this module exists to reclaim."""
    session = getattr(nc, "_session", None)
    adapters = [
        adapter
        for adapter in (getattr(session, "adapter", None), getattr(session, "adapter_dav", None))
        if adapter is not None
    ]
    if not adapters:
        return
    task = asyncio.ensure_future(_close(adapters))
    _CLOSING.add(task)
    task.add_done_callback(_CLOSING.discard)
    await asyncio.shield(task)


@contextlib.asynccontextmanager
async def nc_client(nc_factory):
    """A Nextcloud client for one piece of work, closed on the way out."""
    nc = nc_factory()
    try:
        yield nc
    finally:
        await close_client(nc)
