"""Isolated 1 fps VP8 diagnostic publisher; stdin carries aggregate metrics and, for the
owning room only, its speakers' display names."""

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
FONT_ITALIC = f"{FONT_DIR}/DejaVuSansCondensed-Oblique.ttf"
FONT_MONO = f"{FONT_DIR}/DejaVuSansMono.ttf"
# Debian's fonts-dejavu-core lacks the condensed faces; degrade rather than fail.
if not os.path.exists(FONT_BOLD):
    FONT, FONT_BOLD = f"{FONT_DIR}/DejaVuSans.ttf", f"{FONT_DIR}/DejaVuSans-Bold.ttf"
if not os.path.exists(FONT_ITALIC):
    FONT_ITALIC = FONT

# One frame and one metrics sample per second: the governor ticks once per second, so a
# faster frame would repeat the same figures. Measured at 0.5 fps on staging: 1.5-4.9 %
# of one CPU; per-frame cost is the same, so 1 fps stays under the 15 % stop rule.
FRAME_S = 1.0
# Two minutes of history at one 6 px column per one-second sample.
COLUMNS, COLUMN_W = 120, 6
LANES_X, LANES_Y, LANES_W = 32, 214, COLUMNS * COLUMN_W
LANES_H, LANE_GAP = 152, 8  # Four 30 px lanes with 8 px gaps fill the band (mockup).
NOW_X = LANES_X + LANES_W
TAG_X, TAG_W = NOW_X + 12, WIDTH - 32 - NOW_X - 12
GRAPH_Y, GRAPH_H = 372, 58
# 0.7.3 still trips at 80 % of the memory budget (safety.py pressure_problem).
MEMORY_PAUSE = 0.8
# The lanes and pipeline views alternate; 20 s is long enough to read either one.
VIEW_S = 20
# A late joiner sees the feed within 4 s (it waited up to 2 s at 0.5 fps all-keyframe).
KEYFRAME_EVERY = 4
QUANTIZER = 40  # text stays legible at 960x540; see DashboardTrack for frame sizes
# A frame over FRAME_CAP is re-encoded at a coarser quantizer, 8 steps at a time, and
# skipped if still too big at QUANTIZER_MAX. Measured on the busiest lanes view (ten
# lanes changing state every column, a noisy lag graph, 40-character names): 54 KB at
# 40, 38 KB at 48, 26 KB at 56. Text is still readable at 56.
QUANTIZER_STEP, QUANTIZER_MAX = 8, 56
# Per-frame byte cap, from the first diagnostic video (e8015c1, unexplained there): at
# the 128 kbit/s the offer asks for, 32 KiB is two seconds of sending, so one frame can
# never queue more than two frame periods behind itself.
FRAME_CAP = 32768
NAME_MAX = 40  # characters; longer names are cut with an ellipsis before drawing
# Longest metrics line read from the parent; diagnostics.NAME_BYTES keeps samples well
# under it, and a longer line is skipped, not fatal.
LINE_LIMIT = 8192

BG = "#090909"
LANE_BG = "#141413"
TRACK_BG = "#262624"
CHIP_BG = "#151514"
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
# Paused speech: a flat mid-dark amber fill inside a bright amber edge. Darker than the
# captioned and lost fills, filled where held is only an outline, so the four states
# differ in lightness and not only in hue. Flat on purpose: the 0.7.4 45-degree
# hatching cost VP8 41-61 KB per keyframe once a lane paused (over the frame cap).
PAUSED_BG = "#6b4f05"
RED = "#ff6f4c"
RED_BG = "#2d1510"

_FONTS = {}
_BASE_IMAGES = {}


def _font(kind, size):
    key = (kind, size)
    if key not in _FONTS:
        path = {"regular": FONT, "bold": FONT_BOLD, "italic": FONT_ITALIC, "mono": FONT_MONO}[kind]
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


def _header(image, draw, data, pill, title=None):
    x0 = 76 + _font("bold", 26).getlength("Cassini") + 12
    if title:
        _spaced(draw, x0 + 4, 46, title, _font("bold", 14), TEXT_DIM)
    else:
        label, ink, bg = pill
        pill_font = _font("bold", 17)
        x1 = x0 + 12 + 10 + 8 + pill_font.getlength(label) + 12
        draw.rounded_rectangle((x0, 25, x1, 55), 15, fill=bg)
        draw.ellipse((x0 + 12, 35, x0 + 22, 45), fill=ink)
        draw.text((x0 + 30, 40), label, font=pill_font, fill=ink, anchor="lm")
    clock = time.strftime("%H:%M:%S UTC", time.gmtime())
    clock_font, text_font = _font("mono", 17), _font("regular", 17)
    x = WIDTH - 32
    draw.text((x, 40), clock, font=clock_font, fill=TEXT_DIM, anchor="rm")
    x -= clock_font.getlength(clock) + 18
    engine = data.get("engine", "whistle")
    shape = ENGINE_LABELS.get(engine, engine)
    quota, limit = data.get("quota"), data.get("limitMiB")
    if quota and limit:
        shape += f" · {float(quota):g} CPU · {int(limit) / 1024:.3g} GiB"
    draw.text((x, 40), shape, font=text_font, fill=TEXT_SOFT, anchor="rm")
    x -= text_font.getlength(shape) + 14
    if (data.get("admission") or {}).get("observeOnly"):
        badge = "observe only"
        font = _font("mono", 13)
        w = font.getlength(badge) + 18
        draw.rounded_rectangle((x - w, 28, x, 52), 4, outline=OUTLINE, width=2)
        draw.text((x - w / 2, 40), badge, font=font, fill=TEXT_DIM, anchor="mm")


def _base_image(view):
    if view not in _BASE_IMAGES:
        image = Image.new("RGB", (WIDTH, HEIGHT), BG)
        draw = ImageDraw.Draw(image)
        _saturn(image, 32, 24, 34)
        draw.text((76, 40), "Cassini", font=_font("bold", 26), fill=TEXT_MAIN, anchor="lm")
        if view == "lanes":
            _spaced(draw, 32, 206, "SPEAKERS, LAST TWO MINUTES", _font("bold", 14), TEXT_DIM)
            legend = _font("regular", 15)
            items = [
                ("fill", CYAN, "captioned"),
                ("outline", WAITING, "held"),
                ("paused", AMBER, "paused"),
                ("fill", RED, "lost"),
            ]
            widths = [14 + 6 + legend.getlength(label) for _, _, label in items]
            x = WIDTH - 32 - sum(widths) - 18 * (len(items) - 1)
            for (kind, colour, label), w in zip(items, widths, strict=True):
                box = (x, 193, x + 13, 206)
                if kind == "fill":
                    draw.rectangle(box, fill=colour)
                elif kind == "outline":
                    draw.rectangle(box, outline=colour, width=3)
                else:
                    draw.rectangle(box, fill=PAUSED_BG, outline=AMBER, width=2)
                draw.text((x + 20, 206), label, font=legend, fill=TEXT_SOFT, anchor="ls")
                x += w + 18
            draw.rectangle((LANES_X, GRAPH_Y, NOW_X - 1, GRAPH_Y + GRAPH_H - 1), fill=LANE_BG)
            draw.text(
                (LANES_X + 8, GRAPH_Y + 2), "lag", font=_font("mono", 12), fill=GOLD, anchor="lt"
            )
            draw.rectangle((NOW_X - 1, LANES_Y, NOW_X + 1, GRAPH_Y + GRAPH_H - 1), fill=BAR_GREY)
            axis = _font("mono", 13)
            draw.text((LANES_X, 448), "−2 min", font=axis, fill=TEXT_DIM, anchor="ls")
            draw.text(
                (LANES_X + LANES_W // 2, 448), "−1 min", font=axis, fill=TEXT_DIM, anchor="ms"
            )
            draw.text((NOW_X, 448), "now", font=axis, fill=TEXT_DIM, anchor="rs")
        _BASE_IMAGES[view] = image
    return _BASE_IMAGES[view]


ENGINE_LABELS = {
    "whistle": "Whistle",
    "nemotron-en": "Nemotron EN",
    "nemotron-multilingual": "Nemotron ML",
}

REASONS = {
    "lag": "lag",
    "cpu": "CPU stall",
    "memory": "memory",
    "ceiling": "all seats taken",
    "shed": "paused",
}


def _plural(n, one, many):
    return one if n == 1 else many


def _seconds(value):
    return "–" if value is None else f"{float(value):.1f} s"


def _fit(text, font, width):
    """Cut text with an ellipsis so it fits `width` pixels."""
    if font.getlength(text) <= width:
        return text
    while text and font.getlength(text + "…") > width:
        text = text[:-1]
    return text + "…"


def _admission(data):
    admission = data.get("admission") or {}
    return admission, bool(admission.get("observeOnly"))


def _status(data, history):
    if not data:
        return "Starting", CYAN, CYAN_BG
    recent = time.monotonic() - 60
    if any(c["drops"] for c in history if c["t"] >= recent):
        return "Audio lost", RED, RED_BG
    admission, observing = _admission(data)
    if admission and not admission.get("open", True):
        return ("Would hold new voices" if observing else "Holding new voices"), AMBER, AMBER_BG
    if int(data.get("waiting", 0)):
        return "Speakers waiting", AMBER, AMBER_BG
    return "Captions live", CYAN, CYAN_BG


def _hero(draw, x, number, colour, suffix=None, suffix_colour=None):
    big = _font("bold", 84)
    draw.text((x, 124), number, font=big, fill=colour, anchor="lm")
    x += big.getlength(number)
    if suffix:
        small = _font("bold", 40) if suffix.startswith(" ") else big
        draw.text(
            (x, 124 + (20 if small is not big else 0)),
            suffix,
            font=small,
            fill=suffix_colour or colour,
            anchor="lm" if small is big else "ls",
        )
        x += small.getlength(suffix)
    return x + 14


def _label(draw, x, y, first, second, colour=TEXT_SOFT):
    font = _font("regular", 21)
    draw.text((x, y), first, font=font, fill=colour, anchor="lm")
    draw.text((x, y + 25), second, font=font, fill=colour, anchor="lm")


def _limit_bar(draw, x, label, fraction, text, mark=None, warn=0.6):
    """Draw "label [bar] text" starting at `x`; return its right edge."""
    lfont, vfont = _font("regular", 15), _font("mono", 14)
    draw.text((x, 522), label, font=lfont, fill=TEXT_MAIN, anchor="ld")
    x += lfont.getlength(label) + 8
    draw.rectangle((x, 509, x + 99, 518), fill=TRACK_BG)
    clamped = max(0.0, min(1.0, fraction))
    if clamped > 0:
        colour = AMBER if clamped >= warn else BAR_GREY
        draw.rectangle((x, 509, x + max(2, round(100 * clamped)) - 1, 518), fill=colour)
    if mark is not None:
        mx = x + round(100 * mark)
        draw.rectangle((mx - 1, 505, mx + 1, 522), fill=GOLD)
    x += 108
    draw.text((x, 522), text, font=vfont, fill=TEXT_SOFT, anchor="ld")
    return x + vfont.getlength(text)


def _metric(draw, x, label, value, colour=TEXT_SOFT):
    lfont, vfont = _font("regular", 15), _font("mono", 14)
    draw.text((x, 522), label, font=lfont, fill=TEXT_MAIN, anchor="ld")
    x += lfont.getlength(label) + 8
    draw.text((x, 522), value, font=vfont, fill=colour, anchor="ld")
    return x + vfont.getlength(value)


def _percent(value):
    return "–" if value is None else f"{float(value) * 100:.0f}%"


def _lane_label(lane):
    if lane.get("other"):
        return "other call", "italic", TEXT_DIM
    return (lane.get("name") or "Participant")[:NAME_MAX], "bold", TEXT_MAIN


def render(data, history=(), view="lanes"):
    if view == "pipeline":
        return _render_pipeline(data, list(history))
    return _render_lanes(data, list(history))


def _render_lanes(data, history):
    image = _base_image("lanes").copy()
    draw = ImageDraw.Draw(image)
    admission, observing = _admission(data)
    _header(image, draw, data, _status(data, history))

    # Hero row: voices now, seats taken of the ceiling, how far captions trail speech.
    voices = int(data.get("vadActive", 0))
    x = _hero(draw, 32, str(voices), CYAN)
    _label(draw, x, 112, _plural(voices, "voice", "voices"), "heard now")

    seats = int(admission.get("seats", data.get("busy", 0)) or 0)
    ceiling = int(admission.get("ceiling", data.get("capacity", 0)) or 0)
    closed = bool(admission) and not admission.get("open", True)
    x = _hero(draw, 288, str(seats), TEXT_MAIN, f"/{ceiling}", OUTLINE)
    reason = REASONS.get(admission.get("reason"), admission.get("reason") or "")
    if closed:
        _label(
            draw,
            x,
            100,
            "seats taken",
            ("would hold: " if observing else "holding: ") + reason,
            AMBER,
        )
    else:
        _label(draw, x, 100, "seats", "taken")
    for i in range(min(ceiling, 8)):
        bx = x + i * 32
        if i < seats:
            draw.rectangle((bx, 142, bx + 25, 153), fill=CYAN)
        else:
            draw.rectangle((bx, 142, bx + 25, 153), outline=OUTLINE, width=3)

    lag = admission.get("lagSeconds")
    lag_admit = float(admission.get("lagAdmit") or 1.0)
    lag_shed = float(admission.get("lagShed") or 2.0)
    late = lag is not None and float(lag) >= lag_admit
    colour = AMBER if late else GOLD
    x = _hero(
        draw,
        610,
        "–" if lag is None else f"{float(lag):.1f}",
        colour,
        None if lag is None else " s",
    )
    _label(draw, x, 100, "captions trail", "speech by")
    # 0-3 s scale: the shed threshold sits two thirds along, the admit one a third.
    scale = 130 / 3.0
    draw.rectangle((x, 142, x + 129, 153), fill=TRACK_BG)
    if lag:
        draw.rectangle((x, 142, x + min(129, max(2, round(float(lag) * scale))), 153), fill=colour)
    for value, mark in ((lag_admit, TEXT_SOFT), (lag_shed, RED)):
        mx = x + round(min(3.0, value) * scale)
        draw.rectangle((mx - 1, 138, mx, 157), fill=mark)

    # Lanes: one per open microphone, newest column against the "now" line.
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
    lane_h = min(30, (LANES_H + LANE_GAP) // max(1, len(lanes)) - LANE_GAP)
    tag_font = _font("bold", 16 if lane_h >= 24 else 12)
    tag_mono = _font("mono", 14 if lane_h >= 24 else 11)
    chip_size = 15 if lane_h >= 24 else 11 if lane_h >= 14 else 9
    for row, lane in enumerate(lanes):
        y = LANES_Y + row * (lane_h + LANE_GAP)
        draw.rectangle((LANES_X, y, NOW_X - 1, y + lane_h - 1), fill=LANE_BG)
        states = [c["lanes"].get(lane["id"]) for c in columns]
        start = 0
        for i in range(1, len(states) + 1):
            if i < len(states) and states[i] == states[start]:
                continue
            state = states[start]
            if state:
                left = NOW_X - (len(states) - start) * COLUMN_W
                right = NOW_X - (len(states) - i) * COLUMN_W - 2
                box = (left, y, right, y + lane_h - 1)
                if state == "waiting":
                    draw.rectangle(box, outline=WAITING, width=3)
                elif state == "paused":
                    draw.rectangle(box, fill=PAUSED_BG, outline=AMBER, width=2)
                else:
                    draw.rectangle(box, fill=RED if state == "lost" else CYAN)
            start = i
        # The speaker's name, over the lane's oldest end.
        text, kind, ink = _lane_label(lane)
        font = _font(kind, chip_size)
        text = _fit(text, font, LANES_W // 2)
        cw = font.getlength(text) + 16
        ch = max(10, min(22, lane_h - 8))
        cy = y + (lane_h - ch) / 2
        draw.rounded_rectangle((LANES_X + 6, cy, LANES_X + 6 + cw, cy + ch), 3, fill=CHIP_BG)
        draw.text((LANES_X + 14, y + lane_h / 2), text, font=font, fill=ink, anchor="lm")

        tag = (TAG_X, y, TAG_X + TAG_W, y + lane_h - 1)
        lane_lag = lane.get("lag")
        lag_text = "" if lane_lag is None else _seconds(lane_lag)
        slot = "scribe" if data.get("engine", "whistle") == "whistle" else "slot"
        if lane.get("paused"):
            draw.rectangle(tag, outline=AMBER, width=3)
            name, ink, value = "paused", AMBER, lag_text
        elif lane.get("scribe"):
            draw.rectangle(tag, fill=CYAN)
            name, ink, value = f"{slot} {lane['scribe']}", CYAN_INK, lag_text
        elif lane.get("waiting"):
            draw.rectangle(tag, outline=WAITING, width=3)
            name, ink, value = "held", CYAN, lag_text
        else:
            draw.rectangle(tag, outline=TRACK_BG, width=2)
            name, ink, value = "idle", OUTLINE, ""
        draw.text((TAG_X + 10, y + lane_h / 2), name, font=tag_font, fill=ink, anchor="lm")
        if value:
            draw.text(
                (TAG_X + TAG_W - 10, y + lane_h / 2), value, font=tag_mono, fill=ink, anchor="rm"
            )

    # Lag graph: two minutes of control lag against the admit and shed lines.
    def gy(value):
        return GRAPH_Y + 50 - min(2.5, max(0.0, float(value))) * 18

    for value, mark in ((lag_admit, TEXT_SOFT), (lag_shed, RED)):
        yy = round(gy(value))
        for xx in range(LANES_X, NOW_X - 1, 8):
            draw.line((xx, yy, min(xx + 3, NOW_X - 2), yy), fill=mark, width=1)
    points = []
    for j, column in enumerate(columns):
        cx = NOW_X - (len(columns) - j) * COLUMN_W + COLUMN_W / 2
        if column.get("shed"):
            draw.rectangle((cx - 1, GRAPH_Y, cx + 1, GRAPH_Y + GRAPH_H - 1), fill=AMBER)
        if column["finals"]:
            draw.rectangle((cx - 2, GRAPH_Y + 52, cx + 2, GRAPH_Y + GRAPH_H - 1), fill=GOLD)
        if column.get("lag") is not None:
            points.append((cx, gy(column["lag"])))
    if len(points) > 1:
        draw.line(points, fill=GOLD, width=3, joint="curve")
    legend = _font("mono", 12)
    draw.text(
        (TAG_X - 2, GRAPH_Y + 10), f"shed {lag_shed:.1f} s", font=legend, fill=RED, anchor="lm"
    )
    draw.text(
        (TAG_X - 2, GRAPH_Y + 28),
        f"admit {lag_admit:.1f} s",
        font=legend,
        fill=TEXT_SOFT,
        anchor="lm",
    )
    draw.text((TAG_X - 2, GRAPH_Y + 46), "▮ caption sent", font=legend, fill=GOLD, anchor="lm")

    # Decision line: the governor's last admission decision and its inputs.
    decision = data.get("decision")
    draw.rectangle((32, 462, WIDTH - 33, 489), fill=LANE_BG)
    mono = _font("mono", 14)
    if decision:
        x = 42
        when = time.strftime("%H:%M:%S", time.gmtime(float(decision.get("at", 0))))
        draw.text((x, 476), when, font=mono, fill=TEXT_DIM, anchor="lm")
        x += mono.getlength(when) + 10
        verb = decision.get("verb", "")
        if observing and verb:
            verb = "would " + verb
        verb_text = f"{verb} {decision.get('who', '')}".strip()
        ink = CYAN if decision.get("verb") == "admit" else AMBER
        draw.text((x, 476), verb_text, font=mono, fill=ink, anchor="lm")
        x += mono.getlength(verb_text) + 10
        sans = _font("regular", 16)
        detail = _fit(decision.get("text", ""), sans, WIDTH - 42 - x)
        draw.text((x, 476), detail, font=sans, fill=TEXT_SOFT, anchor="lm")
    else:
        draw.text((42, 476), "No admission decision yet", font=mono, fill=TEXT_DIM, anchor="lm")

    # Footer: memory against its limit, CPU stall, engine duty, reply p95, the tile's cost.
    limit = int(data.get("limitMiB", 0) or 0)
    used = int(data.get("captionMiB", 0) or 0)
    x = _limit_bar(
        draw,
        32,
        "Memory",
        used / max(1, limit),
        f"{used / 1024:.2f}/{limit / 1024:.3g} GiB" if limit else "–",
        mark=MEMORY_PAUSE,
    )
    stall = admission.get("cpuStall")
    x = _metric(draw, x + 20, "CPU stall", _percent(stall))
    duty = admission.get("engineDuty")
    x = _metric(
        draw,
        x + 20,
        "Engine busy",
        _percent(duty),
        AMBER if duty is not None and float(duty) >= 0.9 else TEXT_SOFT,
    )
    _metric(draw, x + 20, "Reply p95", _seconds(data.get("p95")))
    draw.text(
        (WIDTH - 32, 522),
        f"tile {float(data.get('diagnosticCpu', 0)):.0f}% · "
        f"{float(data.get('diagnosticMiB', 0)):.0f} MiB · "
        f"{float(data.get('kbps', 0)):.0f} kbit/s",
        font=_font("mono", 13),
        fill=TEXT_DIM,
        anchor="rd",
    )
    return image


def _render_pipeline(data, history):
    image = _base_image("pipeline").copy()
    draw = ImageDraw.Draw(image)
    admission, observing = _admission(data)
    _header(image, draw, data, None, "HOW A CAPTION IS MADE")

    now = time.monotonic()
    finals = sum(c["finals"] for c in history if c["t"] >= now - 60)
    span = now - history[0]["t"] if history else 0
    lanes = data.get("lanes", [])
    seats = int(admission.get("seats", data.get("busy", 0)) or 0)
    ceiling = int(admission.get("ceiling", data.get("capacity", 0)) or 0)
    closed = bool(admission) and not admission.get("open", True)
    reason = REASONS.get(admission.get("reason"), admission.get("reason") or "")
    lag_admit = float(admission.get("lagAdmit") or 1.0)
    lag_shed = float(admission.get("lagShed") or 2.0)
    stall_admit = float(admission.get("cpuStallAdmit") or 0.2)
    duty = admission.get("engineDuty")
    engine = data.get("engine", "whistle")
    used = int(data.get("captionMiB", 0) or 0)
    limit = int(data.get("limitMiB", 0) or 0)
    held = ("would hold: " if observing else "holding: ") + reason
    threads, size = int(data.get("threads", 1) or 1), int(data.get("capacity", 0) or 0)
    if engine == "whistle":
        shape = f"{size} {_plural(size, 'worker', 'workers')}"
    else:
        shape = f"1 process, {size} {_plural(size, 'slot', 'slots')}"
    stages = [
        (
            "1 · LISTEN",
            "Microphones",
            str(int(data.get("vadActive", 0))),
            f"voices of {len(lanes)} {_plural(len(lanes), 'mic', 'mics')}",
            "voice gate per mic\n32 ms frames\nsilence ends a turn",
            CYAN,
        ),
        (
            "2 · CUT",
            "Speech pieces",
            "1 s",
            # Each engine cuts at its own length; unknown until a microphone opens.
            f"per piece, {segment} s max"
            if (segment := data.get("segmentSeconds"))
            else "per piece",
            f"queue {int(data.get('queuedChunks', 0))} of {6 * max(1, len(lanes))}\n"
            "older than 4 s:\ndropped, not late",
            CYAN,
        ),
        (
            "3 · ADMIT",
            "Seats",
            f"{seats}/{ceiling}",
            held if closed else "admitting",
            f"first voice: always\nthen lag < {lag_admit:.1f} s\n"
            f"and stall < {stall_admit * 100:.0f}%",
            AMBER if closed else CYAN,
        ),
        (
            "4 · RECOGNISE",
            "Engine",
            _percent(duty),
            f"busy, {threads} {_plural(threads, 'thread', 'threads')}",
            f"{shape}\nreply p95 {_seconds(data.get('p95'))}\n"
            f"{used / 1024:.2f} of {limit / 1024:.3g} GiB"
            if limit
            else "memory –",
            AMBER if duty is not None and float(duty) >= 0.9 else CYAN,
        ),
        (
            "5 · SEND",
            "Captions",
            str(finals),
            "lines per minute" if span >= 58 else "lines so far",
            f"trail speech {_seconds(admission.get('lagSeconds'))}\nvia Talk signaling\n"
            "no audio leaves",
            GOLD,
        ),
    ]
    x = 32
    for i, (step, name, big, unit, detail, ink) in enumerate(stages):
        draw.rectangle((x, 84, x + 151, 297), fill=LANE_BG)
        draw.rectangle((x, 84, x + 151, 87), fill=ink)
        _spaced(draw, x + 14, 112, step, _font("bold", 13), TEXT_DIM, spacing=0.1)
        draw.text((x + 14, 136), name, font=_font("bold", 21), fill=TEXT_MAIN, anchor="ls")
        draw.text((x + 14, 184), big, font=_fit_font(big, 46, 124), fill=ink, anchor="ls")
        draw.text(
            (x + 14, 206),
            _fit(unit, _font("regular", 15), 128),
            font=_font("regular", 15),
            fill=TEXT_SOFT,
            anchor="ls",
        )
        for k, line in enumerate(detail.split("\n")):
            font = _font("regular", 14)
            draw.text(
                (x + 14, 248 + k * 18),
                _fit(line, font, 128),
                font=font,
                fill=TEXT_DIM,
                anchor="ls",
            )
        x += 152
        if i < len(stages) - 1:
            draw.line((x + 4, 190, x + 25, 190), fill=OUTLINE, width=3)
            draw.line([(x + 20, 183), (x + 29, 190), (x + 20, 197)], fill=OUTLINE, width=3)
            x += 34

    mono = _font("mono", 14)
    draw.text((32, 331), "◆", font=mono, fill=AMBER, anchor="lm")
    draw.text(
        (52, 331),
        "Admission decides per speaker, the moment they start talking. "
        "A seat, once given, is kept.",
        font=mono,
        fill=TEXT_SOFT,
        anchor="lm",
    )

    weight = data.get("cpuWeight")
    quota = f"{float(data['quota']):g} CPU" if data.get("quota") else "–"
    gib = f"{limit / 1024:.3g} GiB" if limit else "–"
    panels = [
        (
            "THE KERNEL PROTECTS THE CALL",
            [
                f"cpu.max     {quota} hard cap",
                f"cpu.weight  {weight if weight else '–'}, Talk 100: we yield",
                f"memory.max  {gib}, no swap",
                "OOM         engine dies, Talk lives",
            ],
        ),
        (
            "LAG KEEPS CAPTIONS USEFUL",
            [
                f"lag ≥ {lag_admit:.1f} s   hold new voices",
                f"lag ≥ {lag_shed:.1f} s   for 5 s: pause the",
                f"              newest speaker {admission.get('holdOff', 10):g} s",
                f"lag < {lag_admit / 2:.1f} s   for 5 s: reopen",
            ],
        ),
        (
            "ONLY A CRASH STOPS CAPTIONS",
            [
                "engine died    restart, ~2 s",
                "3 deaths/60 s  rest 2 min, doubling",
                "OOM kill       rest 2 min",
                f"since start    {int(data.get('engineRestarts', 0))} "
                f"{_plural(int(data.get('engineRestarts', 0)), 'restart', 'restarts')} · "
                f"{int(data.get('oomKills', 0))} OOM",
            ],
        ),
    ]
    small = _font("mono", 12)
    for i, (title, lines) in enumerate(panels):
        px = 32 + i * (288 + 16)
        draw.rectangle((px, 356, px + 287, 487), fill=LANE_BG)
        _spaced(draw, px + 14, 380, title, _font("bold", 13), TEXT_DIM, spacing=0.1)
        for k, line in enumerate(lines):
            draw.text(
                (px + 14, 404 + k * 20),
                _fit(line, small, 262),
                font=small,
                fill=TEXT_SOFT,
                anchor="ls",
            )

    note = "This view alternates with the lanes view every 20 s."
    if observing:
        note = "0.7.3 observes these rules; nothing is held or paused yet."
    draw.text((32, 522), note, font=_font("mono", 13), fill=TEXT_DIM, anchor="ld")
    draw.text(
        (WIDTH - 32, 522),
        f"tile {float(data.get('diagnosticCpu', 0)):.0f}% · "
        f"{float(data.get('diagnosticMiB', 0)):.0f} MiB · "
        f"{float(data.get('kbps', 0)):.0f} kbit/s",
        font=_font("mono", 13),
        fill=TEXT_DIM,
        anchor="rd",
    )
    return image


def _fit_font(text, size, width):
    while size > 20 and _font("bold", size).getlength(text) > width:
        size -= 2
    return _font("bold", size)


def history_column(previous, data, now):
    """One one-second column from cumulative counters; previous is the last sample."""

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
        elif lane.get("paused"):
            lanes[lane["id"]] = "paused"
        elif lane.get("waiting"):
            lanes[lane["id"]] = "waiting"
        elif speech or lane.get("active"):
            lanes[lane["id"]] = "speech"
    finals = int(data.get("finals", 0))
    drops = int(data.get("drops", 0))
    admission = data.get("admission") or {}
    sheds = int(admission.get("shedEvents", 0) or 0)
    before_sheds = int((previous.get("admission") or {}).get("shedEvents", sheds) or 0)
    return {
        "t": now,
        "lanes": lanes,
        "finals": delta(finals, int(previous.get("finals", finals))),
        "drops": delta(drops, int(previous.get("drops", drops))),
        "lag": admission.get("lagSeconds"),
        "shed": delta(sheds, before_sheds) > 0,
    }


class DashboardTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()
        self.data = {}
        self.history = deque(maxlen=COLUMNS)
        self.view = None
        self.next_frame = 0
        self.origin = time.monotonic()
        self.frames = 0
        self.skipped = 0
        self.force_key = False
        self.last_frame = time.monotonic()
        self.bytes = 0
        self._open(QUANTIZER)

    def _open(self, quantizer):
        """A fresh encoder at a fixed quantizer; its first frame is a keyframe. One at a
        time: each libvpx encoder at 960x540 holds about 19 MiB of the 128 MiB budget."""
        self.quantizer = quantizer
        self.codec = av.CodecContext.create("libvpx", "w")
        self.codec.width, self.codec.height = WIDTH, HEIGHT
        self.codec.pix_fmt = "yuv420p"
        self.codec.time_base = fractions.Fraction(1, 90000)
        self.codec.framerate = fractions.Fraction(1, int(FRAME_S))
        self.codec.thread_count = 1
        # Measured on the 0.7.4 views: keyframes of 20-28 KiB at quantizer 40 (31 KiB at
        # 36, too close to the 32 KiB frame cap), 2 KiB for an unchanged inter frame.
        # One keyframe per KEYFRAME_EVERY frames averages about 70 kbit/s.
        self.codec.bit_rate = 96000
        self.codec.qmin = quantizer
        self.codec.qmax = quantizer
        self.codec.options = {
            "deadline": "realtime",
            "cpu-used": "8",
            "lag-in-frames": "0",
            "crf": str(quantizer),
        }

    def update(self, data):
        if self.data:
            self.history.append(history_column(self.data, data, time.monotonic()))
        self.data = data

    def _encode(self, image, pts, key):
        """One packet of at most FRAME_CAP bytes, or None to skip this frame.

        A frame over the cap is encoded again, as a keyframe, by a coarser encoder, up to
        QUANTIZER_MAX. Inter frames reference the encoder's last frame, so after a frame
        that was never sent the next one must be a keyframe."""
        while True:
            frame = av.VideoFrame.from_image(image)
            frame.pts = pts
            frame.time_base = self.codec.time_base
            if key:
                frame.pict_type = av.video.frame.PictureType.I
            packets = self.codec.encode(frame)
            if len(packets) == 1 and packets[0].size <= FRAME_CAP:
                return packets[0]
            if self.quantizer >= QUANTIZER_MAX:
                # Viewers keep the previous picture; the stream itself must not end.
                self.skipped += 1
                self.force_key = True
                return None
            self._open(min(QUANTIZER_MAX, self.quantizer + QUANTIZER_STEP))
            key = True

    async def recv(self):
        while True:
            await asyncio.sleep(max(0, self.next_frame - time.monotonic()))
            self.next_frame = time.monotonic() + FRAME_S
            elapsed = time.monotonic() - self.origin
            view = "lanes" if int(elapsed // VIEW_S) % 2 == 0 else "pipeline"
            # The packets bypass aiortc's encoder, so a viewer's keyframe request cannot be
            # honoured: a periodic keyframe bounds a late joiner's wait instead. A view
            # change redraws everything, so it starts a keyframe too, and goes back to the
            # finest quantizer: a busy view should not coarsen the next one.
            key = self.frames % KEYFRAME_EVERY == 0 or view != self.view or self.force_key
            if view != self.view and self.quantizer != QUANTIZER:
                self._open(QUANTIZER)
            self.view = view
            image = render(self.data, self.history, view)
            packet = self._encode(image, int(elapsed * 90000), key)
            if packet is not None:
                break
        self.force_key = False
        self.frames += 1
        self.last_frame = time.monotonic()
        self.bytes += packet.size
        return packet


async def read_line(reader):
    """One line from the parent; None for a line over the reader's limit, which is consumed
    whole and dropped so the next read starts at the next line; b"" at the end of input."""
    oversized = False
    while True:
        try:
            line = await reader.readuntil(b"\n")
        except asyncio.LimitOverrunError as error:
            await reader.readexactly(error.consumed)
            oversized = True
            continue
        except asyncio.IncompleteReadError:
            return b""
        return None if oversized else line


async def main(nice=19):
    os.nice(nice)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Bound allocations in the optional process independently of application logic.
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    if main_task is not None:
        with suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(signal.SIGTERM, main_task.cancel)
    reader = asyncio.StreamReader(limit=LINE_LIMIT)
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
                                    "bitrate": 128000,  # headroom over the ~80 kbit/s 1 fps feed
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
                    skipped_samples = 0
                    while True:
                        line = await asyncio.wait_for(read_line(reader), 10)
                        if line == b"":
                            return
                        # An oversized sample is skipped, but still answered: the parent
                        # reads one reply per line it writes.
                        if line is None:
                            skipped_samples += 1
                        else:
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
                                    "skippedFrames": track.skipped,
                                    "skippedSamples": skipped_samples,
                                    "quantizer": track.quantizer,
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


def run(nice=19):
    """The child's entry point. Tests pass nice=0: at 19 on a loaded machine the child's
    one-second shutdown sends (incall 0, bye) can miss their deadlines."""
    try:
        asyncio.run(main(nice))
    except asyncio.CancelledError:
        raise SystemExit(0)
    except Exception as error:
        print(json.dumps({"state": "failed", "error": type(error).__name__}), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    run()
