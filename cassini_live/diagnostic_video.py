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
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

# gocassini.com "saturn-dark" palette
BG_BASE = "#090909"
BG_PANEL = "#111111"
BG_CARD = "#1a1a1a"
BG_CARD_HERO = "#162126"
BG_SURFACE = "#242424"
BORDER_SUBTLE = "#2b2b2b"
BORDER_CARD = "#333333"
BORDER_HERO = "#83cddf"

TEXT_MAIN = "#e5e5d9"
TEXT_MUTED = "#a3a39b"
TEXT_DIM = "#7d7d76"

PRIMARY_CYAN = "#83cddf"
SECONDARY_GOLD = "#f6b84d"
BADGE_GOLD_BG = "#2d2312"
BADGE_GOLD_BORDER = "#4d3a1a"
BADGE_CYAN_BG = "#142c35"
BADGE_CYAN_BORDER = "#2b5b6c"

STATUS_GREEN = "#49ba71"
STATUS_AMBER = "#fcb700"
STATUS_RED = "#ff6f4c"

_FONTS = None
_BASE_IMAGE = None


def _get_fonts():
    global _FONTS
    if _FONTS is None:
        _FONTS = {
            ("regular", s): ImageFont.truetype(FONT, s) for s in (11, 12, 13, 14, 15, 16, 18, 22)
        }
        _FONTS.update(
            {
                ("bold", s): ImageFont.truetype(FONT_BOLD, s)
                for s in (10, 11, 12, 13, 14, 15, 16, 18, 20, 22, 26, 30, 34, 48)
            }
        )
        _FONTS.update(
            {("mono", s): ImageFont.truetype(FONT_MONO, s) for s in (10, 11, 12, 13, 14, 16)}
        )
        _FONTS.update(
            {
                ("mono_bold", s): ImageFont.truetype(FONT_MONO_BOLD, s)
                for s in (10, 11, 12, 13, 14, 15, 16, 20)
            }
        )
    return _FONTS


def _draw_saturn_logo(image, cx, cy, scale=1.0):
    """Render the gocassini.com Saturn mark (tilted cyan ring, warm globe, cyan moon)."""
    size = int(64 * scale)
    layer = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(layer)
    c = size

    angle = math.radians(-42)
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    def ring_points(rx, ry, steps=72):
        pts = []
        for i in range(steps):
            t = 2 * math.pi * i / steps
            px, py = rx * math.cos(t), ry * math.sin(t)
            pts.append((c + px * cos_a - py * sin_a, c + px * sin_a + py * cos_a))
        return pts

    r_planet = int(17 * scale)
    ldraw.ellipse(
        (c - r_planet, c - r_planet, c + r_planet, c + r_planet), fill=(215, 212, 205, 255)
    )
    outer = ring_points(34 * scale, 10.5 * scale)
    ldraw.polygon(outer, outline=(128, 199, 249, 255), width=max(2, int(5 * scale)))
    ldraw.chord(
        (c - r_planet, c - r_planet, c + r_planet, c + r_planet),
        start=135,
        end=315,
        fill=(215, 212, 205, 255),
    )
    mx, my, mr = int(c - 18 * scale), int(c + 22 * scale), max(2, int(4.2 * scale))
    ldraw.ellipse((mx - mr, my - mr, mx + mr, my + mr), fill=(128, 199, 249, 255))

    small = layer.resize((size, size), Image.Resampling.LANCZOS)
    image.paste(small, (cx - size // 2, cy - size // 2), small)


def _get_base_image():
    global _BASE_IMAGE
    if _BASE_IMAGE is None:
        fonts = _get_fonts()
        base = Image.new("RGB", (WIDTH, HEIGHT), BG_BASE)
        draw = ImageDraw.Draw(base)

        # Outer frame
        draw.rounded_rectangle(
            (12, 10, WIDTH - 12, HEIGHT - 10), 16, fill=BG_PANEL, outline=BORDER_SUBTLE, width=1
        )

        # Header brand mark + "Cassini" + "Live" pill
        _draw_saturn_logo(base, 46, 36, scale=0.80)
        draw.text((70, 21), "Cassini", font=fonts[("bold", 22)], fill=TEXT_MAIN)
        draw.rounded_rectangle((162, 22, 212, 46), 7, fill=TEXT_MAIN)
        draw.text((171, 26), "Live", font=fonts[("bold", 14)], fill=BG_BASE)
        draw.text(
            (226, 27),
            "Caption pipeline & resource monitor  ·  aggregate telemetry",
            font=fonts[("regular", 13)],
            fill=TEXT_MUTED,
        )

        # 0.5 FPS · VP8 pill
        draw.rounded_rectangle((756, 22, 860, 46), 6, fill=BG_SURFACE, outline=BORDER_CARD, width=1)
        draw.text((766, 28), "0.5 FPS · VP8", font=fonts[("mono", 11)], fill=TEXT_MUTED)

        # Top row: 3-stage live caption pipeline
        # Stage 1: AUDIO & SILERO VAD
        draw.rounded_rectangle(
            (28, 62, 318, 284), 12, fill=BG_CARD, outline=BORDER_CARD, width=1
        )
        draw.text((44, 76), "1. AUDIO & SILERO VAD", font=fonts[("bold", 11)], fill=PRIMARY_CYAN)
        draw.rounded_rectangle(
            (222, 72, 304, 92), 5, fill=BADGE_GOLD_BG, outline=BADGE_GOLD_BORDER, width=1
        )
        draw.text((229, 76), "SILERO v6.2", font=fonts[("mono_bold", 10)], fill=SECONDARY_GOLD)

        # Pipeline arrow 1 -> 2
        draw.polygon([(323, 167), (331, 173), (323, 179)], fill=PRIMARY_CYAN)

        # Stage 2: WHISTLE STT POOL (Hero card)
        draw.rounded_rectangle(
            (335, 62, 625, 284), 12, fill=BG_CARD_HERO, outline=BORDER_HERO, width=2
        )
        draw.text((351, 76), "2. WHISTLE STT POOL", font=fonts[("bold", 11)], fill=PRIMARY_CYAN)

        # Pipeline arrow 2 -> 3
        draw.polygon([(630, 167), (638, 173), (630, 179)], fill=PRIMARY_CYAN)

        # Stage 3: STREAM & CAPTIONS
        draw.rounded_rectangle(
            (642, 62, 932, 284), 12, fill=BG_CARD, outline=BORDER_CARD, width=1
        )
        draw.text((658, 76), "3. STREAM & CAPTIONS", font=fonts[("bold", 11)], fill=PRIMARY_CYAN)

        # Bottom row: 2 resource & headroom cards
        # Card 4: HOST INFRASTRUCTURE & HEADROOM
        draw.rounded_rectangle(
            (28, 296, 472, 464), 12, fill=BG_CARD, outline=BORDER_CARD, width=1
        )
        draw.text(
            (48, 310), "HOST INFRASTRUCTURE & HEADROOM", font=fonts[("bold", 11)], fill=TEXT_MUTED
        )
        draw.rounded_rectangle(
            (380, 306, 454, 326), 5, fill=BADGE_GOLD_BG, outline=BADGE_GOLD_BORDER, width=1
        )
        draw.text((388, 310), "HOST NODE", font=fonts[("mono_bold", 11)], fill=SECONDARY_GOLD)

        # Card 5: CAPTION CONTAINER & MEMORY GATE
        draw.rounded_rectangle(
            (488, 296, 932, 464), 12, fill=BG_CARD, outline=BORDER_CARD, width=1
        )
        draw.text(
            (508, 310),
            "CAPTION CONTAINER & MEMORY GATE",
            font=fonts[("bold", 11)],
            fill=TEXT_MUTED,
        )
        draw.rounded_rectangle(
            (808, 306, 914, 326), 5, fill=BADGE_GOLD_BG, outline=BADGE_GOLD_BORDER, width=1
        )
        draw.text((816, 310), "INCLUDES VIDEO", font=fonts[("mono_bold", 11)], fill=SECONDARY_GOLD)

        # Footer strip
        draw.rounded_rectangle(
            (28, 476, 932, 520), 10, fill="#141414", outline=BORDER_SUBTLE, width=1
        )
        draw.rounded_rectangle((28, 476, 34, 520), 3, fill=PRIMARY_CYAN)
        draw.text((48, 491), "~ observer", font=fonts[("mono_bold", 12)], fill=PRIMARY_CYAN)

        _BASE_IMAGE = base
    return _BASE_IMAGE


def render(data):
    fonts = _get_fonts()
    image = _get_base_image().copy()
    draw = ImageDraw.Draw(image)

    def t(x, y, value, font_key=("regular", 14), fill=TEXT_MAIN):
        draw.text((x, y), str(value), font=fonts[font_key], fill=fill)

    def badge_right(
        right_x, y, label, bg=BADGE_GOLD_BG, border=BADGE_GOLD_BORDER, fg=SECONDARY_GOLD
    ):
        f = fonts[("mono_bold", 10)]
        bbox = draw.textbbox((0, 0), label, font=f)
        w = (bbox[2] - bbox[0]) + 14
        x = right_x - w
        draw.rounded_rectangle((x, y, right_x, y + 20), 5, fill=bg, outline=border, width=1)
        draw.text((x + 7, y + 4), label, font=f, fill=fg)

    def progress_bar(x, y, w, h, fraction, fill_color, bg_color="#262626"):
        draw.rounded_rectangle((x, y, x + w, y + h), h // 2, fill=bg_color)
        clamped = max(0.0, min(1.0, float(fraction)))
        if clamped > 0.01:
            fw = max(h, int(w * clamped))
            draw.rounded_rectangle((x, y, x + fw, y + h), h // 2, fill=fill_color)

    # Header status pill
    state_raw = str(data.get("state", "connecting")).upper()
    if state_raw == "READY":
        state_color, state_bg, state_border = STATUS_GREEN, "#132a1c", "#255838"
    elif "PAUSED" in state_raw or "PRESSURE" in state_raw or "REFUSED" in state_raw:
        state_color, state_bg, state_border = STATUS_AMBER, "#2d2208", "#5c4510"
    else:
        state_color, state_bg, state_border = PRIMARY_CYAN, BADGE_CYAN_BG, BADGE_CYAN_BORDER

    state_label = state_raw if len(state_raw) <= 14 else state_raw[:14]
    f_state = fonts[("mono_bold", 12)]
    sb = draw.textbbox((0, 0), state_label, font=f_state)
    sw = (sb[2] - sb[0]) + 28
    sx = 932 - sw
    draw.rounded_rectangle((sx, 22, 932, 46), 6, fill=state_bg, outline=state_border, width=1)
    draw.ellipse((sx + 9, 30, sx + 17, 38), fill=state_color)
    draw.text((sx + 22, 27), state_label, font=f_state, fill=state_color)

    # -------------------------------------------------------------------------
    # Stage 1: AUDIO & SILERO VAD
    # -------------------------------------------------------------------------
    tracks = int(data.get("tracks", 0))
    max_tracks = max(1, int(data.get("maxTracks", 10)))
    vad_active = int(data.get("vadActive", 0))
    vad_peak = float(data.get("vadPeak", 0.0))
    vad_chunks = int(data.get("vadChunks", 0))
    silent_tracks = max(0, tracks - vad_active)

    vad_color = SECONDARY_GOLD if vad_active > 0 else PRIMARY_CYAN
    t(44, 100, f"{vad_active}", ("bold", 34), vad_color)
    t(74, 106, f"/ {tracks} tracks voiced", ("bold", 16), TEXT_MAIN)
    t(74, 129, f"{silent_tracks} silence-gated  ·  {max_tracks} max", ("mono", 11), TEXT_MUTED)

    # Track slot strip (gold = voiced, cyan = open silent, dark = spare track slots)
    bar_x, bar_y, bar_w, bar_h = 44, 152, 258, 10
    t_gap = 3
    t_w = max(4, (bar_w - t_gap * (max_tracks - 1)) // max_tracks)
    for i in range(min(max_tracks, 20)):
        tx_i = bar_x + i * (t_w + t_gap)
        if i < vad_active:
            c_fill = SECONDARY_GOLD
        elif i < tracks:
            c_fill = PRIMARY_CYAN
        else:
            c_fill = "#2a2a2a"
        draw.rounded_rectangle((tx_i, bar_y, tx_i + t_w, bar_y + bar_h), 3, fill=c_fill)

    # VAD speech probability bar with 0.35 hold / 0.50 trigger thresholds
    t(44, 172, "Peak VAD", ("regular", 12), TEXT_MUTED)
    t(110, 171, f"{vad_peak * 100:.0f}%", ("mono_bold", 13), TEXT_MAIN)
    t(184, 173, "35% hold · 50% trig", ("mono", 10), TEXT_DIM)
    prob_color = (
        SECONDARY_GOLD
        if vad_peak >= 0.5
        else (PRIMARY_CYAN if vad_peak >= 0.35 else "#52524e")
    )
    progress_bar(44, 192, 258, 8, vad_peak, prob_color)
    hold_x = 44 + int(258 * 0.35)
    trig_x = 44 + int(258 * 0.50)
    draw.line((hold_x, 190, hold_x, 202), fill=PRIMARY_CYAN, width=1)
    draw.line((trig_x, 190, trig_x, 202), fill=SECONDARY_GOLD, width=2)

    # Gated chunks & window box
    draw.rounded_rectangle((44, 212, 302, 270), 6, fill="#141414", outline=BORDER_SUBTLE, width=1)
    t(54, 220, "Speech chunks gated:", ("regular", 12), TEXT_MUTED)
    t(194, 220, f"{vad_chunks} (1s PCM)", ("mono_bold", 12), TEXT_MAIN)
    t(54, 244, "VAD window:", ("regular", 12), TEXT_MUTED)
    t(140, 244, "320ms pre · 608ms post", ("mono", 11), TEXT_DIM)

    # -------------------------------------------------------------------------
    # Stage 2: WHISTLE STT POOL (Hero card)
    # -------------------------------------------------------------------------
    spare = int(data.get("spare", 0))
    busy = int(data.get("busy", 0))
    capacity = max(1, int(data.get("capacity", 1)))
    leased_sec = float(data.get("leasedSec", 0.0))
    stt_chunks = int(data.get("sttChunks", 0))
    stt_audio_sec = float(data.get("sttAudioSec", 0.0))
    queued_chunks = int(data.get("queuedChunks", 0))
    waiting = int(data.get("waiting", 0))
    yields = int(data.get("yields", 0))
    drops = int(data.get("drops", 0))

    badge_right(
        611,
        72,
        f"{spare} HEADROOM",
        bg=BADGE_CYAN_BG if spare > 0 else BADGE_GOLD_BG,
        border=BADGE_CYAN_BORDER if spare > 0 else BADGE_GOLD_BORDER,
        fg=PRIMARY_CYAN if spare > 0 else SECONDARY_GOLD,
    )

    spare_color = PRIMARY_CYAN if spare > 0 else STATUS_AMBER
    t(351, 100, f"{spare}", ("bold", 34), spare_color)
    slot_word = "speaker slot free" if spare == 1 else "speaker slots free"
    t(381, 106, slot_word, ("bold", 16), TEXT_MAIN)
    t(
        381,
        129,
        f"{busy}/{capacity} STT busy  ·  lease {leased_sec:.1f}s/2.0s",
        ("mono", 11),
        TEXT_MUTED,
    )

    # Calibrated worker slots bar
    w_x, w_y, w_w, w_h = 351, 152, 258, 10
    w_gap = 6
    slot_w = max(8, (w_w - w_gap * (capacity - 1)) // capacity)
    for i in range(min(capacity, 16)):
        sx_i = w_x + i * (slot_w + w_gap)
        c_fill = SECONDARY_GOLD if i < busy else PRIMARY_CYAN
        draw.rounded_rectangle((sx_i, w_y, sx_i + slot_w, w_y + w_h), 4, fill=c_fill)

    # STT model input & queue stats
    draw.rounded_rectangle(
        (351, 172, 609, 234), 6, fill="#11191d", outline=BADGE_CYAN_BORDER, width=1
    )
    t(361, 180, "Passed to STT:", ("regular", 12), TEXT_MUTED)
    t(454, 180, f"{stt_chunks} chunks ({stt_audio_sec:.0f}s)", ("mono_bold", 12), TEXT_MAIN)
    t(361, 206, "Chunk queue:", ("regular", 12), TEXT_MUTED)
    q_color = STATUS_AMBER if queued_chunks > 2 else PRIMARY_CYAN
    t(454, 206, f"{queued_chunks} buffered (cap 6/spk)", ("mono_bold", 11), q_color)

    # Contention & fair-share scheduler pills
    wait_c = STATUS_AMBER if waiting > 0 else STATUS_GREEN
    drop_c = STATUS_RED if drops > 0 else STATUS_GREEN
    draw.rounded_rectangle((351, 242, 433, 270), 5, fill="#11191d", outline=BORDER_SUBTLE, width=1)
    draw.ellipse((359, 252, 367, 260), fill=wait_c)
    t(372, 249, f"Wait: {waiting}", ("mono", 11), TEXT_MAIN if waiting else TEXT_MUTED)

    draw.rounded_rectangle((439, 242, 523, 270), 5, fill="#11191d", outline=BORDER_SUBTLE, width=1)
    t(447, 249, f"Yields: {yields}", ("mono", 11), PRIMARY_CYAN if yields else TEXT_MUTED)

    draw.rounded_rectangle((529, 242, 609, 270), 5, fill="#11191d", outline=BORDER_SUBTLE, width=1)
    draw.ellipse((537, 252, 545, 260), fill=drop_c)
    t(550, 249, f"Drop: {drops}", ("mono", 11), TEXT_MAIN if drops else TEXT_MUTED)

    # -------------------------------------------------------------------------
    # Stage 3: SEGMENTATION & CAPTIONS
    # -------------------------------------------------------------------------
    rooms = int(data.get("rooms", 0))
    targets = int(data.get("targets", 0))
    previewing = int(data.get("previewing", 0))
    finals = int(data.get("finals", 0))
    partials = int(data.get("partials", 0))
    end_silence = int(data.get("endSilence", 0))
    end_duration = int(data.get("endDuration", 0))
    end_sched = int(data.get("endSched", 0))
    end_flush = int(data.get("endFlush", 0))

    badge_right(918, 72, f"{rooms}R · {targets} VIEW")

    t(658, 100, f"{finals}", ("bold", 34), STATUS_GREEN if finals > 0 else PRIMARY_CYAN)
    t(710, 106, "final segments", ("bold", 16), TEXT_MAIN)
    t(
        710,
        129,
        f"{partials} partials  ·  {previewing} live preview",
        ("mono", 11),
        TEXT_MUTED,
    )

    # Stream segmentation / end reasons box
    draw.rounded_rectangle((658, 154, 916, 270), 6, fill="#141414", outline=BORDER_SUBTLE, width=1)
    t(668, 162, "STT STREAM END REASONS", ("bold", 10), TEXT_MUTED)

    reasons = [
        ("Silence (608ms pause)", end_silence, STATUS_GREEN),
        ("15s cap / pause cut", end_duration, PRIMARY_CYAN),
        ("2s fair-share handoff", end_sched, SECONDARY_GOLD),
        ("DTX / track flush", end_flush, TEXT_MUTED),
    ]
    for idx, (label, count, dot_color) in enumerate(reasons):
        ry = 182 + idx * 21
        draw.ellipse((668, ry + 4, 676, ry + 12), fill=dot_color)
        t(684, ry, label, ("regular", 12), TEXT_MUTED)
        t(880, ry, f"{count:>3}", ("mono_bold", 12), TEXT_MAIN)

    # -------------------------------------------------------------------------
    # Bottom Row — Card 4: HOST INFRASTRUCTURE & HEADROOM
    # -------------------------------------------------------------------------
    host_cpu = float(data.get("hostCpu", 0.0))
    host_avail = float(data.get("hostAvailableGiB", 0.0))
    host_total = max(0.1, float(data.get("hostGiB", 1.0)))
    host_used = max(0.0, host_total - host_avail)
    host_mem_frac = host_used / host_total

    t(48, 336, "CPU usage", ("regular", 13), TEXT_MUTED)
    t(126, 333, f"{host_cpu:.1f}%", ("mono_bold", 15), TEXT_MAIN)
    t(352, 336, f"{max(0.0, 100.0 - host_cpu):.1f}% headroom", ("mono", 11), TEXT_DIM)
    cpu_bar_color = (
        STATUS_RED if host_cpu >= 85 else (SECONDARY_GOLD if host_cpu >= 65 else PRIMARY_CYAN)
    )
    progress_bar(48, 356, 404, 9, host_cpu / 100.0, cpu_bar_color)

    t(48, 382, "RAM available", ("regular", 13), TEXT_MUTED)
    t(152, 379, f"{host_avail:.1f} GiB", ("mono_bold", 15), TEXT_MAIN)
    t(336, 382, f"of {host_total:.1f} GiB total", ("mono", 11), TEXT_DIM)
    mem_bar_color = (
        STATUS_RED
        if host_mem_frac >= 0.85
        else (SECONDARY_GOLD if host_mem_frac >= 0.70 else PRIMARY_CYAN)
    )
    progress_bar(48, 402, 404, 9, host_mem_frac, mem_bar_color)
    t(
        48,
        426,
        f"Used {host_used:.1f} GiB   ·   Headroom {host_avail:.1f} GiB   ·   Active rooms: {rooms}",
        ("mono", 11),
        TEXT_DIM,
    )

    # -------------------------------------------------------------------------
    # Bottom Row — Card 5: CAPTION CONTAINER & MEMORY GATE
    # -------------------------------------------------------------------------
    cap_cpu = float(data.get("captionCpu", 0.0))
    quota = max(0.1, float(data.get("quota", 1.0)))
    quota_pct = quota * 100.0
    cap_cpu_frac = cap_cpu / quota_pct

    cap_mib = int(data.get("captionMiB", 0))
    limit_mib = max(1, int(data.get("limitMiB", 2048)))
    cap_mem_frac = cap_mib / limit_mib

    t(508, 336, "CPU usage", ("regular", 13), TEXT_MUTED)
    t(586, 333, f"{cap_cpu:.0f}%", ("mono_bold", 15), TEXT_MAIN)
    t(786, 336, f"/ {quota_pct:.0f}% cap ({quota:g} CPU)", ("mono", 11), TEXT_DIM)
    cap_cpu_color = (
        STATUS_RED
        if cap_cpu_frac >= 0.85
        else (SECONDARY_GOLD if cap_cpu_frac >= 0.65 else PRIMARY_CYAN)
    )
    progress_bar(508, 356, 404, 9, cap_cpu_frac, cap_cpu_color)

    t(508, 382, "RAM usage", ("regular", 13), TEXT_MUTED)
    t(590, 379, f"{cap_mib} MiB", ("mono_bold", 15), TEXT_MAIN)
    t(798, 382, f"/ {limit_mib} MiB limit", ("mono", 11), TEXT_DIM)
    cap_mem_color = (
        STATUS_RED
        if cap_mem_frac >= 0.70
        else (SECONDARY_GOLD if cap_mem_frac >= 0.55 else PRIMARY_CYAN)
    )
    progress_bar(508, 402, 404, 9, cap_mem_frac, cap_mem_color)
    gate_x = 508 + int(404 * 0.70)
    draw.line((gate_x, 399, gate_x, 414), fill=SECONDARY_GOLD, width=2)
    t(
        508,
        426,
        f"{max(0, int(limit_mib * 0.70) - cap_mib)} MiB below 70% safety gate   ·   "
        f"{max(0.0, quota_pct - cap_cpu):.0f}% CPU headroom",
        ("mono", 11),
        TEXT_DIM,
    )

    # -------------------------------------------------------------------------
    # Bottom terminal strip
    # -------------------------------------------------------------------------
    diag_cpu = float(data.get("diagnosticCpu", 0.0))
    diag_mib = float(data.get("diagnosticMiB", 0.0))
    kbps = float(data.get("kbps", 0.0))
    sampler_ms = float(data.get("samplerMs", 0.0))
    t(
        142,
        491,
        f"CPU {diag_cpu:.2f}%/15%   ·   RSS {diag_mib:.1f}/128 MiB   ·   "
        f"Uplink {kbps:.1f} kbit/s   ·   Sampler {sampler_ms:.2f} ms",
        ("mono", 12),
        TEXT_MAIN,
    )
    utc_now = time.strftime("%H:%M:%S UTC", time.gmtime())
    t(826, 491, utc_now, ("mono", 12), TEXT_DIM)

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
