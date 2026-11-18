"""Closing Nextcloud clients: nc-py-api offers no close, so nc_client reaches in.

A dropped AsyncNextcloudApp strands its QUIC sockets and their fds for the life of the
process (the 2026-11-13 production fd exhaustion), so every client the app constructs
must go through close_client when it is done.
"""

import asyncio
from types import SimpleNamespace

from nc_py_api import AsyncNextcloudApp

from cassini_live import nc_client as module
from cassini_live.nc_client import close_client, nc_client


class Adapter:
    def __init__(self, closed, name, error=None, hang=False):
        self.closed, self.name, self.error, self.hang = closed, name, error, hang

    async def close(self):
        if self.hang:
            await asyncio.Event().wait()
        if self.error:
            raise self.error
        self.closed.append(self.name)


def client(closed, **kwargs):
    return SimpleNamespace(
        _session=SimpleNamespace(
            adapter=Adapter(closed, "ocs", **kwargs), adapter_dav=Adapter(closed, "dav")
        )
    )


async def test_both_sessions_are_closed():
    closed = []
    await close_client(client(closed))
    assert closed == ["ocs", "dav"]


async def test_a_client_without_sessions_is_left_alone():
    await close_client(SimpleNamespace())
    await close_client(SimpleNamespace(_session=None))


async def test_a_failing_close_neither_raises_nor_stops_the_other_session():
    closed = []
    await close_client(client(closed, error=RuntimeError("already closed")))
    assert closed == ["dav"]


async def test_a_hanging_close_is_bounded(monkeypatch):
    monkeypatch.setattr(module, "CLOSE_TIMEOUT_S", 0.01)
    closed = []
    await close_client(client(closed, hang=True))
    assert closed == ["dav"]


async def test_nc_client_closes_on_success_and_on_error():
    closed = []
    async with nc_client(lambda: client(closed)):
        pass
    assert closed == ["ocs", "dav"]
    closed.clear()
    try:
        async with nc_client(lambda: client(closed)):
            raise OSError("Nextcloud is down")
    except OSError:
        pass
    assert closed == ["ocs", "dav"]


async def test_a_cancelled_caller_still_gets_the_close(monkeypatch):
    """A retry loop stopped mid-close must not strand the sockets."""
    closed, started = [], asyncio.Event()

    class Slow(Adapter):
        async def close(self):
            started.set()
            await asyncio.sleep(0.01)
            self.closed.append(self.name)

    nc = SimpleNamespace(
        _session=SimpleNamespace(adapter=Slow(closed, "ocs"), adapter_dav=Adapter(closed, "dav"))
    )
    caller = asyncio.create_task(close_client(nc))
    await started.wait()
    caller.cancel()
    try:
        await caller
    except asyncio.CancelledError:
        pass
    await asyncio.gather(*module._CLOSING)
    assert closed == ["ocs", "dav"]


async def test_the_real_client_still_fits_close_client():
    """Canary for the nc-py-api layout close_client relies on (pinned <0.25): the
    session's two adapters and their awaitable close. If an upgrade moves them, fail
    here rather than leak fds in production for a week."""
    nc = AsyncNextcloudApp(
        app_id="test", app_version="1.0.0", app_secret="secret",
        nextcloud_url="http://localhost/", app_api_version="1",
    )
    session = nc._session
    assert asyncio.iscoroutinefunction(session.adapter.close)
    assert asyncio.iscoroutinefunction(session.adapter_dav.close)
    await close_client(nc)
