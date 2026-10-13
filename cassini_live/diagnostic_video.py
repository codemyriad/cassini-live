"""Isolated 0.5 fps VP8 diagnostic publisher; stdin contains aggregate metrics only."""

import asyncio
import fractions
import hashlib
import hmac
import json
import os
import resource
import sys
import time
from secrets import token_urlsafe

import av
from aiortc import (
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCRtpSender,
    RTCSessionDescription,
    VideoStreamTrack,
)
from aiortc.sdp import candidate_from_sdp
from PIL import Image, ImageDraw, ImageFont
from websockets.asyncio.client import connect

WIDTH, HEIGHT = 960, 540
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def render(data):
    image = Image.new("RGB", (WIDTH, HEIGHT), "#0b1220")
    draw = ImageDraw.Draw(image)
    fonts = {size: ImageFont.truetype(FONT, size) for size in (14, 18, 22, 32, 54)}

    def text(x, y, value, size=18, fill="#dde7f5"):
        draw.text((x, y), str(value), font=fonts[size], fill=fill)

    def card(x, y, w, h):
        draw.rounded_rectangle((x, y, x + w, y + h), 16, fill="#152137", outline="#24334e")

    draw.rectangle((0, 0, WIDTH, 5), fill="#5ee5bc")
    text(32, 24, "CASSINI / LIVE OBSERVATORY", 22)
    text(690, 28, "●  " + data.get("state", "connecting").upper(), 18, "#5ee5bc")
    text(32, 64, "Server-wide telemetry · no audio, names or transcripts", 14, "#93a8c4")
    card(28, 104, 440, 172)
    text(48, 122, "CAPTION CAPACITY", 14, "#93a8c4")
    text(48, 147, f"{data.get('spare', 0)}", 54, "#5ee5bc")
    text(106, 165, "speaker slots available now", 22)
    text(48, 220, f"{data.get('busy', 0)} busy / {data.get('capacity', 0)} measured slots", 18)
    text(48, 248, "Instantaneous headroom, not a meeting guarantee", 14, "#93a8c4")
    card(488, 104, 444, 172)
    text(508, 122, "LIVE WORK", 14, "#93a8c4")
    text(508, 154, f"{data.get('rooms', 0)} rooms   /   {data.get('tracks', 0)} audio tracks", 22)
    text(508, 207, f"{data.get('maxTracks', 10)} tracks max across all rooms", 18)
    text(
        508,
        244,
        f"{data.get('waiting', 0)} waiting    ·    {data.get('drops', 0)} dropped chunks",
        14,
        "#93a8c4",
    )
    card(28, 292, 440, 137)
    text(48, 308, "HOST", 14, "#93a8c4")
    text(48, 341, f"CPU  {data.get('hostCpu', 0):.1f}%", 22)
    text(
        48,
        382,
        f"{data.get('hostAvailableGiB', 0):.1f} / {data.get('hostGiB', 0):.1f} GiB available",
        18,
    )
    card(488, 292, 444, 137)
    text(508, 308, "CAPTION CONTAINER · INCLUDES THIS VIDEO", 14, "#93a8c4")
    text(
        508,
        341,
        f"CPU  {data.get('captionCpu', 0):.0f}% / {data.get('quota', 0) * 100:.0f}% cap",
        22,
    )
    text(508, 382, f"RAM  {data.get('captionMiB', 0)} / {data.get('limitMiB', 0)} MiB", 18)
    text(32, 449, "OBSERVER COST", 14, "#5ee5bc")
    text(
        32,
        475,
        f"{data.get('diagnosticCpu', 0):.2f}% of one CPU  ·  "
        f"{data.get('diagnosticMiB', 0):.1f} MiB  ·  "
        f"{data.get('kbps', 0):.1f} kbit/s uplink  ·  0.5 fps",
        18,
    )
    text(
        32,
        509,
        f"Sampler {data.get('samplerMs', 0):.2f} ms/update  ·  "
        "SFU fan-out / viewer decoding excluded  ·  "
        + time.strftime("%H:%M:%S UTC", time.gmtime()),
        14,
        "#93a8c4",
    )
    return image


class DashboardTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()
        self.data = {}
        self.codec = av.CodecContext.create("libvpx", "w")
        self.codec.width, self.codec.height = WIDTH, HEIGHT
        self.codec.pix_fmt = "yuv420p"
        self.codec.time_base = fractions.Fraction(1, 90000)
        self.codec.framerate = fractions.Fraction(1, 2)
        self.codec.thread_count = 1
        self.codec.bit_rate = 48000
        self.codec.qmin = 36
        self.codec.qmax = 36
        self.codec.options = {
            "deadline": "realtime",
            "cpu-used": "8",
            "lag-in-frames": "0",
            "crf": "36",
        }
        self.next_frame = 0
        self.origin = time.monotonic()
        self.frames = 0
        self.last_frame = time.monotonic()
        self.bytes = 0

    async def recv(self):
        await asyncio.sleep(max(0, self.next_frame - time.monotonic()))
        self.next_frame = time.monotonic() + 2
        frame = av.VideoFrame.from_image(render(self.data))
        frame.pts = int((time.monotonic() - self.origin) * 90000)
        frame.time_base = self.codec.time_base
        # Each sparse frame is independent: late joiners need no keyframe queue.
        frame.pict_type = av.video.frame.PictureType.I
        packets = self.codec.encode(frame)
        if len(packets) != 1 or packets[0].size > 32768:
            raise RuntimeError("Diagnostic frame exceeds budget")
        self.frames += 1
        self.last_frame = time.monotonic()
        self.bytes += packets[0].size
        return packets[0]


async def main():
    os.nice(19)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Bound allocations in the optional process independently of application logic.
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    reader = asyncio.StreamReader(limit=8192)
    await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin
    )
    config = json.loads(await asyncio.wait_for(reader.readline(), 8))
    backend = config["backend"]
    servers = [RTCIceServer(urls=s["urls"]) for s in config["stunservers"]]
    servers += [
        RTCIceServer(urls=s["urls"], username=s["username"], credential=s["credential"])
        for s in config["turnservers"]
    ]
    pc = RTCPeerConnection(RTCConfiguration(iceServers=servers))
    track = DashboardTrack()
    sender = pc.addTrack(track)
    transceiver = pc.getTransceivers()[0]
    transceiver.direction = "sendonly"
    transceiver.setCodecPreferences(
        [c for c in RTCRtpSender.getCapabilities("video").codecs if c.mimeType == "video/VP8"]
    )
    nonce = token_urlsafe(48)
    token = hmac.new(
        os.environ["CASSINI_TALK_SIGNALING_INTERNAL_SECRET"].encode(),
        nonce.encode(),
        hashlib.sha256,
    ).hexdigest()
    try:
        async with connect(config["server"], max_size=1024 * 1024) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "hello": {
                            "version": "2.0",
                            "features": ["internal-incall"],
                            "auth": {
                                "type": "internal",
                                "params": {"random": nonce, "token": token, "backend": backend},
                            },
                        },
                    }
                )
            )
            async with asyncio.timeout(15):
                while True:
                    msg = json.loads(await ws.recv())
                    if msg["type"] == "error":
                        raise RuntimeError("Diagnostic authentication refused")
                    if msg["type"] == "hello":
                        sid = msg["hello"]["sessionid"]
                        break
            await ws.send(
                json.dumps({"type": "room", "room": {"roomid": config["room"], "sessionid": sid}})
            )
            # IN_CALL | WITH_VIDEO, never request or receive participant media.
            await ws.send(
                json.dumps(
                    {"type": "internal", "internal": {"type": "incall", "incall": {"incall": 5}}}
                )
            )
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            stream_id = token_urlsafe(12)
            await ws.send(
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "recipient": {"type": "session", "sessionid": sid},
                            "data": {
                                "to": sid,
                                "type": "offer",
                                "sid": stream_id,
                                "roomType": "video",
                                "bitrate": 64000,
                                "videocodec": "vp8",
                                "payload": {
                                    "nick": "Cassini · diagnostics",
                                    "type": "offer",
                                    "sdp": pc.localDescription.sdp,
                                },
                            },
                        },
                    }
                )
            )

            async def signaling():
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg["type"] in ("bye", "error"):
                        raise RuntimeError(
                            "Diagnostic signaling closed: "
                            + msg.get("error", {}).get("code", "bye")
                        )
                    data = msg.get("message", {}).get("data", {})
                    if data.get("type") == "answer":
                        await pc.setRemoteDescription(
                            RTCSessionDescription(sdp=data["payload"]["sdp"], type="answer")
                        )
                    elif data.get("type") == "candidate":
                        candidate = data["payload"].get("candidate")
                        if candidate and candidate.get("candidate"):
                            ice = candidate_from_sdp(candidate["candidate"])
                            ice.sdpMid = candidate.get("sdpMid")
                            ice.sdpMLineIndex = candidate.get("sdpMLineIndex")
                            await pc.addIceCandidate(ice)
                raise RuntimeError("Diagnostic signaling ended")

            async def updates():
                previous_bytes, previous_time = 0, time.monotonic()
                announced = 0.0
                while True:
                    line = await asyncio.wait_for(reader.readline(), 10)
                    if not line:
                        return
                    track.data = json.loads(line)
                    stats = await sender.getStats()
                    sent = sum(s.bytesSent for s in stats.values() if s.type == "outbound-rtp")
                    now = time.monotonic()
                    if pc.connectionState == "connected" and now - track.last_frame > 10:
                        raise RuntimeError("Diagnostic video stalled")
                    kbps = (sent - previous_bytes) * 8 / max(0.01, now - previous_time) / 1000
                    track.data["kbps"] = kbps
                    # Talk tracks media availability separately from RTP. Repeat
                    # the standard state announcements for late joiners, too.
                    if pc.connectionState == "connected" and track.frames and now - announced >= 6:
                        for kind, payload in (
                            ("unmute", {"name": "video"}),
                            ("mute", {"name": "audio"}),
                            ("nickChanged", {"name": "Cassini · diagnostics"}),
                        ):
                            await ws.send(
                                json.dumps(
                                    {
                                        "type": "message",
                                        "message": {
                                            "recipient": {"type": "room"},
                                            "data": {
                                                "type": kind,
                                                "roomType": "video",
                                                "payload": payload,
                                            },
                                        },
                                    }
                                )
                            )
                        announced = now

                    print(
                        json.dumps(
                            {
                                "state": pc.connectionState,
                                "frames": track.frames,
                                "kbps": round(kbps, 1),
                            }
                        ),
                        flush=True,
                    )
                    previous_bytes, previous_time = sent, now

            tasks = [asyncio.create_task(signaling()), asyncio.create_task(updates())]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        track.stop()
        await pc.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        print(json.dumps({"state": "failed", "error": type(error).__name__}), flush=True)
        raise SystemExit(1)
