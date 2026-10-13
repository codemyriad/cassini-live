import asyncio
from unittest.mock import AsyncMock

import pytest

from cassini_live.app import TranscribeRequest
from cassini_live.config import Config
from cassini_live.service import CaptionService
from cassini_live.talk.types import SigConnectResult, SpreedClientException


class FakeClient:
    def __init__(self, token, *_args):
        self.token = token
        self.defunct = asyncio.Event()
        self.targets = {}
        self._nc_sid_wait_stash = {}
        self.leave_call_cb = None
        self.connect = AsyncMock(return_value=SigConnectResult.SUCCESS)

    async def add_target(self, session):
        self.targets[session] = True

    async def remove_target(self, session):
        self.targets.pop(session, None)

    async def close(self):
        self.defunct.set()
        if self.leave_call_cb:
            await self.leave_call_cb(self.token)


async def test_concurrent_requests_join_room_once_and_stop_last_receiver():
    created = []

    def factory(*args):
        client = FakeClient(*args)
        created.append(client)
        return client

    service = CaptionService(Config(None, 2, "secret", ""), factory)
    service.settings = object()
    req = TranscribeRequest(roomToken="room", ncSessionId="a")
    await asyncio.gather(
        service.request(req), service.request(req.model_copy(update={"ncSessionId": "b"}))
    )
    assert len(created) == 1
    assert set(created[0].targets) == {"a", "b"}
    await service.request(req.model_copy(update={"enable": False}))
    assert "room" in service.clients
    await service.request(req.model_copy(update={"enable": False, "ncSessionId": "b"}))
    assert service.clients == {}
    assert created[0].defunct.is_set()


async def test_failed_join_cleans_room_and_reports_failure():
    client = FakeClient("room")
    client.connect.return_value = SigConnectResult.FAILURE
    service = CaptionService(Config(None, 1, "secret", ""), lambda *_args: client)
    service.settings = object()
    with pytest.raises(SpreedClientException):
        await service.request(TranscribeRequest(roomToken="room", ncSessionId="a"))
    assert not service.clients
    assert client.defunct.is_set()


async def test_connect_crossing_pause_cannot_revive_old_room():
    from cassini_live.safety import ResourceRefused

    connected = asyncio.Event()
    finish = asyncio.Event()
    client = FakeClient("room")

    async def connect():
        connected.set()
        await finish.wait()
        return SigConnectResult.SUCCESS

    client.connect = connect
    service = CaptionService(Config(None, 1, "secret", ""), lambda *_: client)
    service.settings = object()
    request = asyncio.create_task(
        service.request(TranscribeRequest(roomToken="room", ncSessionId="a"))
    )
    await connected.wait()
    await service.stop_rooms()
    finish.set()
    with pytest.raises(ResourceRefused):
        await request
    assert not service.clients and not client.targets
    assert client.defunct.is_set()


async def test_cancelled_teardown_remains_tracked_until_it_finishes():
    started, finish = asyncio.Event(), asyncio.Event()
    client = FakeClient("room")

    async def close():
        started.set()
        await finish.wait()
        client.defunct.set()

    client.close = close
    service = CaptionService(Config(None, 1, "secret", ""))
    service.clients["room"] = client
    task = asyncio.create_task(service.stop_rooms())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client in service.closing and not service.closing[client].cancelled()
    finish.set()
    await service.stop_rooms()
    assert not service.closing and client.defunct.is_set()
