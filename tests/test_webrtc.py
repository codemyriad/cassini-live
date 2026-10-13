"""Real local WebRTC/Opus reception feeding the caption audio adapter."""

import asyncio
from fractions import Fraction

import numpy as np
import pytest
from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from av import AudioFrame

from cassini_live import transcriber
from cassini_live.pool import WorkerPool
from cassini_live.talk.client import SpreedClient
from cassini_live.talk.types import HPBSettings

from .test_streaming import FakeWorker, speech_gate  # noqa: F401


class ToneTrack(AudioStreamTrack):
    def __init__(self):
        super().__init__()
        self.index = 0

    async def recv(self):
        await asyncio.sleep(0.02)
        t = (np.arange(960) + self.index * 960) / 48000
        mono = (np.sin(2 * np.pi * 440 * t) * 4096).astype(np.int16)
        stereo = np.column_stack((mono, mono)).reshape(1, -1)
        frame = AudioFrame.from_ndarray(stereo, format="s16", layout="stereo")
        frame.sample_rate = 48000
        frame.time_base = Fraction(1, 48000)
        frame.pts = self.index * 960
        self.index += 1
        return frame


@pytest.mark.usefixtures("speech_gate")
async def test_real_opus_track_is_captured_and_flushed(monkeypatch):
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    monkeypatch.setattr(transcriber, "POOL", pool)
    publisher = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    publisher.addTrack(ToneTrack())
    publisher.addTransceiver("video", direction="sendonly")
    settings = HPBSettings(server="ws://unused/spreed", stunservers=[], turnservers=[])
    client = SpreedClient("room", settings, "en", None)
    answer = asyncio.Event()

    async def send(message):
        data = message.get("message", {}).get("data", {})
        if data.get("type") == "answer":
            # Read the gathered local SDP, as HPB does when forwarding candidates.
            pc = client.peer_connections["speaker"].pc
            await publisher.setRemoteDescription(
                RTCSessionDescription(type="answer", sdp=pc.localDescription.sdp)
            )
            answer.set()

    monkeypatch.setattr(client, "send_message", send)
    try:
        offer = await publisher.createOffer()
        await publisher.setLocalDescription(offer)
        await client.handle_offer(
            {
                "message": {
                    "sender": {"sessionid": "speaker"},
                    "data": {
                        "type": "offer",
                        "from": "speaker",
                        "sid": "offer-id",
                        "payload": {"sdp": publisher.localDescription.sdp},
                    },
                }
            }
        )
        await asyncio.wait_for(answer.wait(), 2)
        video = next(
            t for t in client.peer_connections["speaker"].pc.getTransceivers() if t.kind == "video"
        )
        assert video.currentDirection == "inactive"
        async with asyncio.timeout(5):
            event = await client.transcript_queue.get()
            client.transcript_queue.task_done()
        assert event.speaker_session_id == "speaker"
        assert event.message == "hello" and not event.final
        await client.transcribers["speaker"].shutdown()
        final = await asyncio.wait_for(client.transcript_queue.get(), 2)
        client.transcript_queue.task_done()
        assert final.final and final.message == "hello world"
        assert pool.idle.qsize() == 1
    finally:
        await publisher.close()
        await client.close()
        await pool.close()
