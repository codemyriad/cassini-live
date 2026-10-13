"""Run inside an installed container: readiness, HPB auth, bundled STT.

This check never joins a call. Pass the included JFK WAV fixture to also check
recorded speech. It reads deployment credentials from the container environment
and prints no secrets or call data.
"""

import asyncio
import base64
import http.client
import json
import os
import socket
import sys
import wave

import numpy as np
from nc_py_api import AsyncNextcloudApp
from websockets.asyncio.client import connect

from cassini_live.config import Config
from cassini_live.pool import Worker
from cassini_live.service import CaptionService
from cassini_live.talk.client import SpreedClient
from cassini_live.talk.utils import get_ssl_context


def readiness():
    connection = http.client.HTTPConnection("localhost", timeout=15)
    if os.environ.get("HP_SHARED_KEY"):
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(15)
        connection.sock.connect(os.environ.get("HP_EXAPP_SOCK", "/tmp/exapp.sock"))
    else:
        connection = http.client.HTTPConnection(
            "127.0.0.1", int(os.environ.get("APP_PORT", "8080")), timeout=15
        )
    credential = base64.b64encode((":" + os.environ["APP_SECRET"]).encode()).decode()
    try:
        connection.request("GET", "/status", headers={
            "EX-APP-ID": os.environ["APP_ID"],
            "EX-APP-VERSION": os.environ["APP_VERSION"],
            "AUTHORIZATION-APP-API": credential,
        })
        response = connection.getresponse()
        assert response.status == 200, f"Status HTTP {response.status}"
        status = json.loads(response.read())
        assert status["enabled"] and status["signallingConfigured"] and status["workers"] > 0
        print("Caption service ready:", status["workers"], "preloaded workers")
    finally:
        connection.close()


async def main():
    readiness()
    config = Config.from_env()
    service = CaptionService(config)
    await service.discover(AsyncNextcloudApp())
    client = SpreedClient("unused-deployment-check", service.settings, "auto", None)
    url = service.settings.server
    ssl_context = get_ssl_context(url)
    async with connect(url, **({"ssl": ssl_context} if ssl_context else {})) as ws:
        client._server = ws
        await client.send_hello()
        async with asyncio.timeout(15):
            while True:
                message = json.loads(await ws.recv())
                if message["type"] == "hello":
                    print("Talk HPB: internal authentication passed")
                    break
                assert message["type"] == "welcome", "HPB rejected authentication"
        await ws.send(json.dumps({"type": "bye"}))
    if len(sys.argv) < 2:
        return
    with wave.open(sys.argv[1]) as source:
        assert source.getframerate() == 16000 and source.getnchannels() == 1
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    samples = samples.astype("<f4") / 32768
    worker = Worker(config.weights)
    words = []
    try:
        await worker.start()
        await worker.send({"type": "start", "language": "auto"})
        for start in range(0, len(samples), 16000):
            await worker.send({"type": "audio", "pcm": base64.b64encode(
                samples[start:start + 16000].tobytes()
            ).decode()})
            words.append((await worker.receive())["result"].get("text", ""))
        await worker.send({"type": "end"})
        while (message := await worker.receive())["type"] != "done":
            words.append(message["result"].get("text", ""))
        assert "country" in " ".join(words).lower(), "Recorded speech was not recognised"
        print("Bundled Whistle: recorded speech recognised")
    finally:
        await worker.close()


if __name__ == "__main__":
    asyncio.run(main())
