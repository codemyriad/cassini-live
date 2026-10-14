"""Isolated 0.5 fps VP8 diagnostic publisher; stdin contains aggregate metrics only."""

import asyncio
import fractions
import hashlib
import hmac
import json
import math
import os
import resource
import signal
import sys
import time
from collections import deque
from contextlib import suppress
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
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
FONT = f"{FONT_DIR}/DejaVuSansCondensed.ttf"
FONT_BOLD = f"{FONT_DIR}/DejaVuSansCondensed-Bold.ttf"
FONT_MONO = f"{FONT_DIR}/DejaVuSansMono.ttf"
# Debian's fonts-dejavu-core lacks the condensed faces; degrade rather than fail.
if not os.path.exists(FONT_BOLD):
    FONT, FONT_BOLD = f"{FONT_DIR}/DejaVuSans.ttf", f"{FONT_DIR}/DejaVuSans-Bold.ttf"

# Two minutes of history at one column per two-second sample.
COLUMNS, COLUMN_W = 60, 12
LANES_X, LANES_Y, LANES_W, LANES_H, LANE_GAP = 64, 218, COLUMNS * COLUMN_W, 212, 8
NOW_X = LANES_X + LANES_W
MEMORY_PAUSE = 0.8  # Safety controller stops captions at 80% of the memory budget.

BG = "#090909"
LANE_BG = "#141413"
TRACK_BG = "#262624"
TEXT_MAIN = "#e5e5d9"
TEXT_SOFT = "#c9c9bf"
TEXT_DIM = "#9d9d94"
BAR_GREY = "#8f8f86"
OUTLINE = "#55554f"
CYAN = "#83cddf"
CYAN_BG = "#162126"
CYAN_INK = "#0b1a1e"
WAITING = "#4f8a99"
GOLD = "#f6b84d"
AMBER = "#fcb700"
AMBER_BG = "#2d2208"
RED = "#ff6f4c"
RED_BG = "#2d1510"

_FONTS = {}
_BASE_IMAGE = None


def _font(kind, size):
    key = (kind, size)
    if key not in _FONTS:
        path = {"regular": FONT, "bold": FONT_BOLD, "mono": FONT_MONO}[kind]
        _FONTS[key] = ImageFont.truetype(path, size)
    return _FONTS[key]


def _spaced(draw, x, y, text, font, fill, spacing=0.12, anchor="ls"):
    """Letter-spaced caps labels; Pillow has no tracking option."""
    extra = font.size * spacing
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, anchor=anchor)
        x += font.getlength(ch) + extra
    return x


def _saturn(image, x, y, size=32):
    scale = 4
    s = size * scale
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    c = s / 2
    r = 8 * scale
    draw.ellipse((c - r, c - r, c + r, c + r), fill=GOLD)
    cos_a, sin_a = math.cos(math.radians(-20)), math.sin(math.radians(-20))
    rx, ry = 15 * scale, 5 * scale
    points = [
        (
            c + rx * math.cos(t) * cos_a - ry * math.sin(t) * sin_a,
            c + rx * math.cos(t) * sin_a + ry * math.sin(t) * cos_a,
        )
        for t in (2 * math.pi * i / 96 for i in range(97))
    ]
    draw.line(points, fill=CYAN, width=3 * scale, joint="curve")
    small = layer.resize((size, size), Image.Resampling.LANCZOS)
    image.paste(small, (x, y), small)


def _base_image():
    global _BASE_IMAGE
    if _BASE_IMAGE is None:
        image = Image.new("RGB", (WIDTH, HEIGHT), BG)
        draw = ImageDraw.Draw(image)
        _saturn(image, 32, 24)
        draw.text((76, 40), "Cassini", font=_font("bold", 26), fill=TEXT_MAIN, anchor="lm")

        _spaced(draw, 32, 206, "ONE LANE PER OPEN MIC", _font("bold", 16), TEXT_DIM)
        # Swatch legend, right-aligned to the "now" line.
        legend = _font("regular", 15)
        items = [
            ("fill", CYAN, "captioned"),
            ("outline", WAITING, "waiting"),
            ("fill", RED, "lost"),
            ("mark", GOLD, "captions pause"),
        ]
        widths = [14 + 6 + legend.getlength(label) for _, _, label in items]
        x = NOW_X - sum(widths) - 18 * (len(items) - 1)
        for (kind, colour, label), w in zip(items, widths, strict=True):
            if kind == "fill":
                draw.rectangle((x, 193, x + 13, 206), fill=colour)
            elif kind == "outline":
                draw.rectangle((x, 193, x + 13, 206), outline=colour, width=3)
            else:
                draw.rectangle((x + 5, 190, x + 8, 209), fill=colour)
            draw.text((x + 20, 206), label, font=legend, fill=TEXT_DIM, anchor="ls")
            x += w + 18

        draw.rectangle((LANES_X, 438, NOW_X - 1, 453), fill=LANE_BG)
        draw.text((32, 446), "cc", font=_font("mono", 15), fill=GOLD, anchor="lm")
        draw.rectangle((NOW_X, 214, NOW_X + 3, 457), fill=TEXT_DIM)
        axis = _font("mono", 14)
        draw.text((LANES_X, 476), "−2 min", font=axis, fill=TEXT_DIM, anchor="ls")
        draw.text((LANES_X + LANES_W // 2, 476), "−1 min", font=axis, fill=TEXT_DIM, anchor="ms")
        draw.text((NOW_X + 4, 476), "now", font=axis, fill=TEXT_DIM, anchor="rs")
        _BASE_IMAGE = image
    return _BASE_IMAGE


def _plural(n, one, many):
    return one if n == 1 else many


def _status(data, history):
    if not data:
        return "Starting", CYAN, CYAN_BG
    recent = time.monotonic() - 60
    if any(c["drops"] for c in history if c["t"] >= recent):
        return "Audio lost", RED, RED_BG
    if int(data.get("waiting", 0)):
        return "Speakers waiting", AMBER, AMBER_BG
    if int(data.get("capacity", 0)) and int(data.get("spare", 0)) == 0:
        return "At capacity", AMBER, AMBER_BG
    return "Captions live", CYAN, CYAN_BG


def _hero(draw, x, number, colour):
    big = _font("bold", 96)
    draw.text((x, 122), number, font=big, fill=colour, anchor="lm")
    return x + big.getlength(number) + 14


def _label(draw, x, y, first, second):
    font = _font("regular", 22)
    draw.text((x, y), first, font=font, fill=TEXT_SOFT, anchor="lm")
    draw.text((x, y + 26), second, font=font, fill=TEXT_SOFT, anchor="lm")


def _limit_bar(draw, right, label, fraction, mark=None, warn=0.6):
    """Draw "label [bar] NN%" ending at `right`; return its left edge."""
    value = f"{fraction * 100:.0f}%"
    vfont, lfont = _font("mono", 14), _font("regular", 15)
    x = right - vfont.getlength("100%")
    draw.text((right, 522), value, font=vfont, fill=TEXT_SOFT, anchor="rd")
    x -= 10 + 100
    draw.rectangle((x, 509, x + 99, 518), fill=TRACK_BG)
    clamped = max(0.0, min(1.0, fraction))
    if clamped > 0:
        colour = AMBER if clamped >= warn else BAR_GREY
        draw.rectangle((x, 509, x + max(2, round(100 * clamped)) - 1, 518), fill=colour)
    if mark is not None:
        mx = x + round(100 * mark)
        draw.rectangle((mx - 2, 505, mx + 1, 522), fill=GOLD)
    x -= 8
    draw.text((x, 522), label, font=lfont, fill=TEXT_MAIN, anchor="rd")
    return x - lfont.getlength(label)


def render(data, history=()):
    image = _base_image().copy()
    draw = ImageDraw.Draw(image)
    history = list(history)

    # Header: status pill and clock.
    label, ink, bg = _status(data, history)
    pill_font = _font("bold", 17)
    x0 = 76 + _font("bold", 26).getlength("Cassini") + 12
    x1 = x0 + 12 + 10 + 8 + pill_font.getlength(label) + 12
    draw.rounded_rectangle((x0, 25, x1, 55), 15, fill=bg)
    draw.ellipse((x0 + 12, 35, x0 + 22, 45), fill=ink)
    draw.text((x0 + 30, 40), label, font=pill_font, fill=ink, anchor="lm")
    draw.text(
        (WIDTH - 32, 40),
        time.strftime("%H:%M:%S UTC", time.gmtime()),
        font=_font("mono", 17),
        fill=TEXT_DIM,
        anchor="rm",
    )

    # Hero row: voices now, room for more, captions per minute.
    voices = int(data.get("vadActive", 0))
    x = _hero(draw, 32, str(voices), CYAN)
    _label(draw, x, 109, _plural(voices, "voice", "voices"), "heard now")

    spare = int(data.get("spare", 0))
    capacity = int(data.get("capacity", 0))
    busy = int(data.get("busy", 0))
    x = _hero(draw, 339, f"+{spare}", TEXT_MAIN if spare else AMBER)
    _label(draw, x, 99, f"more {_plural(spare, 'voice', 'voices')}", "fit now")
    for i in range(min(capacity, 8)):
        bx = x + i * 34
        if i < busy:
            draw.rectangle((bx, 146, bx + 27, 157), fill=CYAN)
        else:
            draw.rectangle((bx, 146, bx + 27, 157), outline=OUTLINE, width=3)

    now = time.monotonic()
    finals = sum(c["finals"] for c in history if c["t"] >= now - 60)
    span = now - history[0]["t"] if history else 0
    x = _hero(draw, 645, str(finals), GOLD)
    _label(draw, x, 109, "captions", "per minute" if span >= 58 else "so far")

    # Lanes: one per open mic, newest column against the "now" line.
    lanes = sorted(data.get("lanes", []), key=lambda lane: lane["id"])[:10]
    columns = history[-COLUMNS:]
    if not lanes:
        draw.text(
            (LANES_X + LANES_W // 2, LANES_Y + LANES_H // 2),
            "No microphones open",
            font=_font("regular", 20),
            fill=TEXT_DIM,
            anchor="mm",
        )
    lane_h = min(36, (LANES_H + LANE_GAP) // max(1, len(lanes)) - LANE_GAP)
    tag_font = _font("bold", 18 if lane_h >= 30 else 15 if lane_h >= 20 else 12)
    for row, lane in enumerate(lanes):
        y = LANES_Y + row * (lane_h + LANE_GAP)
        draw.rectangle((LANES_X, y, NOW_X - 1, y + lane_h - 1), fill=LANE_BG)
        draw.text(
            (32, y + lane_h / 2),
            f"{row + 1:02d}",
            font=_font("mono", min(15, lane_h)),
            fill=TEXT_DIM,
            anchor="lm",
        )
        states = [c["lanes"].get(lane["id"]) for c in columns]
        start = 0
        for i in range(1, len(states) + 1):
            if i < len(states) and states[i] == states[start]:
                continue
            state = states[start]
            if state:
                left = NOW_X - (len(states) - start) * COLUMN_W
                right = NOW_X - (len(states) - i) * COLUMN_W - 3
                box = (left, y, right, y + lane_h - 1)
                if state == "waiting":
                    draw.rectangle(box, outline=WAITING, width=3)
                else:
                    draw.rectangle(box, fill=RED if state == "lost" else CYAN)
            start = i
        tag = (NOW_X + 12, y, NOW_X + 147, y + lane_h - 1)
        if lane.get("scribe"):
            draw.rectangle(tag, fill=CYAN)
            draw.text(
                (tag[0] + 10, y + lane_h / 2),
                f"scribe {lane['scribe']}",
                font=tag_font,
                fill=CYAN_INK,
                anchor="lm",
            )
        elif lane.get("waiting"):
            draw.rectangle(tag, outline=AMBER, width=3)
            draw.text(
                (tag[0] + 10, y + lane_h / 2), "waiting", font=tag_font, fill=AMBER, anchor="lm"
            )

    # Caption lines sent, aligned with the lanes' columns.
    for j, column in enumerate(columns):
        if column["finals"]:
            left = NOW_X - (len(columns) - j) * COLUMN_W + 3
            draw.rectangle((left, 438, left + 5, 453), fill=GOLD)

    # Footer: the tile's own cost, then the service's safety limits.
    draw.text(
        (32, 522),
        f"this tile: {float(data.get('diagnosticCpu', 0)):.0f}% CPU · "
        f"{float(data.get('diagnosticMiB', 0)):.0f} MiB · "
        f"{float(data.get('kbps', 0)):.0f} kbit/s",
        font=_font("mono", 14),
        fill=TEXT_DIM,
        anchor="ld",
    )
    quota = max(0.1, float(data.get("quota", 1.0) or 1.0))
    cpu = float(data.get("captionCpu", 0.0)) / (quota * 100)
    memory = int(data.get("captionMiB", 0)) / max(1, int(data.get("limitMiB", 1) or 1))
    x = _limit_bar(draw, WIDTH - 32, "CPU", cpu, warn=0.85)
    _limit_bar(draw, x - 24, "Memory", memory, mark=MEMORY_PAUSE)
    return image


def history_column(previous, data, now):
    """One two-second column from cumulative counters; previous is the last sample."""

    def delta(new, old):
        # Counters restart when a speaker's gate is rebuilt (language change).
        return new if new < old else new - old

    lanes = {}
    before = {lane["id"]: lane for lane in previous.get("lanes", [])}
    for lane in data.get("lanes", []):
        old = before.get(lane["id"], {})
        speech = delta(lane.get("speech", 0), old.get("speech", lane.get("speech", 0)))
        lost = delta(lane.get("lost", 0), old.get("lost", lane.get("lost", 0)))
        if lost:
            lanes[lane["id"]] = "lost"
        elif lane.get("waiting"):
            lanes[lane["id"]] = "waiting"
        elif speech or lane.get("active"):
            lanes[lane["id"]] = "speech"
    finals = int(data.get("finals", 0))
    drops = int(data.get("drops", 0))
    return {
        "t": now,
        "lanes": lanes,
        "finals": delta(finals, int(previous.get("finals", finals))),
        "drops": delta(drops, int(previous.get("drops", drops))),
    }


class DashboardTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()
        self.data = {}
        self.history = deque(maxlen=COLUMNS)
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

    def update(self, data):
        if self.data:
            self.history.append(history_column(self.data, data, time.monotonic()))
        self.data = data

    async def recv(self):
        await asyncio.sleep(max(0, self.next_frame - time.monotonic()))
        self.next_frame = time.monotonic() + 2
        frame = av.VideoFrame.from_image(render(self.data, self.history))
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
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    if main_task is not None:
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(signal.SIGTERM, main_task.cancel)
    reader = asyncio.StreamReader(limit=8192)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
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
            try:
                await ws.send(
                    json.dumps(
                        {"type": "room", "room": {"roomid": config["room"], "sessionid": sid}}
                    )
                )
                # IN_CALL | WITH_VIDEO, never request or receive participant media.
                await ws.send(
                    json.dumps(
                        {
                            "type": "internal",
                            "internal": {"type": "incall", "incall": {"incall": 5}},
                        }
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
                        track.update(json.loads(line))
                        stats = await sender.getStats()
                        sent = sum(s.bytesSent for s in stats.values() if s.type == "outbound-rtp")
                        now = time.monotonic()
                        if pc.connectionState == "connected" and now - track.last_frame > 10:
                            raise RuntimeError("Diagnostic video stalled")
                        kbps = (sent - previous_bytes) * 8 / max(0.01, now - previous_time) / 1000
                        track.data["kbps"] = kbps
                        # Talk tracks media availability separately from RTP. Repeat
                        # the standard state announcements for late joiners, too.
                        if (
                            pc.connectionState == "connected"
                            and track.frames
                            and now - announced >= 6
                        ):
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
                with suppress(Exception):
                    await asyncio.wait_for(
                        ws.send(
                            json.dumps(
                                {
                                    "type": "internal",
                                    "internal": {"type": "incall", "incall": {"incall": 0}},
                                }
                            )
                        ),
                        1,
                    )
                    await asyncio.wait_for(ws.send(json.dumps({"type": "bye", "bye": {}})), 1)
    finally:
        track.stop()
        await pc.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except asyncio.CancelledError:
        raise SystemExit(0)
    except Exception as error:
        print(json.dumps({"state": "failed", "error": type(error).__name__}), flush=True)
        raise SystemExit(1)
