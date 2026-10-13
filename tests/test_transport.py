"""Exercise the real Talk transport against a local signalling protocol fixture."""

import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from websockets.asyncio.server import serve

from cassini_live.talk import client as transport
from cassini_live.talk.types import HPBSettings, ReconnectMethod, SigConnectResult, Transcript


async def test_handshake_targeting_reconnect_and_final_before_bye(monkeypatch):
    connections = []
    messages = []
    received = asyncio.Event()

    async def hpb(socket):
        connections.append(socket)
        await socket.send(json.dumps({"type": "welcome", "welcome": {}}))
        async for raw in socket:
            message = json.loads(raw)
            messages.append(message)
            if message["type"] == "hello":
                auth = message["hello"]["auth"]
                assert auth["type"] == "internal"
                expected = hmac.new(
                    b"test-hpb", auth["params"]["random"].encode(), hashlib.sha256
                ).hexdigest()
                assert auth["params"]["token"] == expected
                await socket.send(
                    json.dumps(
                        {"type": "hello", "hello": {"sessionid": "bot", "resumeid": "resume"}}
                    )
                )
            if message["type"] == "room":
                await socket.send(
                    json.dumps(
                        {
                            "type": "event",
                            "event": {
                                "target": "participants",
                                "type": "update",
                                "update": {
                                    "users": [
                                        {
                                            "sessionId": "alice-hpb",
                                            "nextcloudSessionId": "alice-nc",
                                            "inCall": 1,
                                        },
                                        {
                                            "sessionId": "bob-hpb",
                                            "nextcloudSessionId": "bob-nc",
                                            "inCall": 1,
                                        },
                                        {"sessionId": "bot", "inCall": 1, "internal": True},
                                    ]
                                },
                            },
                        }
                    )
                )
            if message["type"] == "message":
                received.set()

    nc = SimpleNamespace(
        ocs=AsyncMock(return_value={"absolute_url": "http://cloud.test"}),
        app_cfg=SimpleNamespace(endpoint="http://cloud.test"),
    )
    monkeypatch.setattr(transport, "AsyncNextcloudApp", lambda: nc)
    async with serve(hpb, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        settings = HPBSettings(
            server=f"ws://127.0.0.1:{port}/spreed", stunservers=[], turnservers=[]
        )
        client = transport.SpreedClient("room", settings, "en", None)
        try:
            assert await client.connect() == SigConnectResult.SUCCESS
            await client.add_target("alice-nc")
            async with asyncio.timeout(2):
                while "alice-hpb" not in client.targets:
                    await asyncio.sleep(0.01)
            await client.send_transcript(Transcript(False, "en", "hello", "bob-hpb"))
            await asyncio.wait_for(received.wait(), 2)
            caption = [m for m in messages if m["type"] == "message"][-1]
            assert caption["message"]["recipient"]["sessionid"] == "alice-hpb"
            assert caption["message"]["data"] == {
                "final": False,
                "langId": "en",
                "message": "hello",
                "speakerSessionId": "bob-hpb",
                "type": "transcript",
            }
            # Explicit full reconnect uses a new socket and preserves opt-in receivers.
            assert await client.connect(ReconnectMethod.FULL_RECONNECT) == SigConnectResult.SUCCESS
            assert len(connections) == 2
            async with asyncio.timeout(2):
                while "alice-hpb" not in client.targets:
                    await asyncio.sleep(0.01)
            await client.transcript_queue.put(Transcript(True, "en", "last words", "bob-hpb"))
            await client.close()
            await asyncio.sleep(0.02)
            last_caption = next(
                i
                for i, m in enumerate(messages)
                if m.get("message", {}).get("data", {}).get("message") == "last words"
            )
            assert last_caption < len(messages) - 1
            assert messages[-1]["type"] == "bye"
        finally:
            await client.close()
