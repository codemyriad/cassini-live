"""Replay an archived Cassini meeting recording through the live caption pipeline.

The Cassini recorder keeps one audio track per participant in its meeting `.mkv`
(tags `PARTICIPANT_ID` / `PARTICIPANT_NAME` on each stream, see gocassini's
`docs/mkv-format.md`). This module plays those tracks in lockstep into the same
objects a Talk call feeds (`WhistleTranscriber` / `StreamTranscriber` behind the
real VAD gate and the real engine) and records every caption event with the
meeting time at which it would have reached the browser.

The result is a replay timeline, JSON, that the transcript panel can be scrubbed
through (`ui/replay/`), so "what did Cassini Live show ten minutes into last
Friday's standup" is a question with an answer, for any engine and any build.

    uv run python -m cassini_live.replay meeting.mkv --from 10:00 --to 13:00 \
        --engine whistle --speed 0 -o replay.json

Speed 1 paces the audio at real time: contention, dropped chunks and shedding
then happen as they would in a call, and each event's `at` minus the speech it
transcribes is the latency a participant saw. Speed 0 runs as fast as the engine
keeps up without dropping, which is right for text and for the UI and wrong for
latency. No Nextcloud, no HPB and no network are involved.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import json
import logging
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import av

from . import catalog, transcriber
from .talk.types import StreamEndedException

LOGGER = logging.getLogger("cassini_live.replay")

FORMAT = "cassini-live-replay/1"
# Meeting-time granularity of the shared clock. A 20 ms Opus frame is the finest
# thing the tracks deliver, so a finer tick would only spin.
TICK = 0.1
# At speed 0 a track may stall this long (wall seconds) while the engine works
# through another track's speech, without the silence timeout ending its segment.
FAST_RECEIVE_TIMEOUT = 3600.0

PARTICIPANT_TAGS = ("PARTICIPANT_ID", "participant_id")
NAME_TAGS = ("PARTICIPANT_NAME", "participant_name", "title")


@dataclasses.dataclass(frozen=True)
class Track:
    """One participant's audio track in the recording."""

    index: int  # container stream index
    sid: str  # stands in for the Talk session id: the participant id
    name: str
    user_id: str | None
    start: float  # first audio frame, seconds into the recording


@dataclasses.dataclass
class Event:
    at: float  # meeting seconds
    type: str  # join | final | partial
    sid: str
    message: str = ""
    lang: str | None = None
    wall: float | None = None  # wall seconds since the replay started, speed > 0 only

    def to_json(self):
        body = {
            "at": round(self.at * 1000),
            "type": self.type,
            "speakerSessionId": self.sid,
        }
        if self.type != "join":
            body["message"] = self.message
            body["langId"] = self.lang
        if self.wall is not None:
            body["wallMs"] = round(self.wall * 1000)
        return body


# --- Reading the recording ---------------------------------------------------


def _tag(metadata: dict, keys) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if value:
            return str(value)
    return None


def probe(path: str | os.PathLike) -> tuple[dict, list[Track]]:
    """The recording's identity and its participant audio tracks.

    Streams without a participant tag (a mixdown, a legacy recording) are kept
    too, named after their stream index, so the replay never silently drops
    audio."""
    with av.open(str(path)) as container:
        meta = dict(container.metadata)
        tracks = []
        for stream in container.streams.audio:
            tags = dict(stream.metadata)
            pid = _tag(tags, PARTICIPANT_TAGS)
            name = _tag(tags, NAME_TAGS) or pid or f"track{stream.index}"
            sid = pid or f"track{stream.index}"
            start = _first_frame_time(path, stream.index)
            tracks.append(Track(stream.index, sid, name, pid, start))
    info = {
        "title": meta.get("title") or meta.get("TITLE"),
        "sessionId": meta.get("SESSION_ID") or meta.get("session_id"),
        "startedAt": _started_at(meta, path),
    }
    return info, tracks


def _first_frame_time(path, index) -> float:
    # A fresh handle per stream: decoding from the top is cheap (one packet)
    # and seeking back to 0 is not allowed on every demuxer.
    with av.open(str(path)) as container:
        stream = container.streams.get(index)[0]
        for frame in container.decode(stream):
            if frame.time is not None:
                return float(frame.time)
    return 0.0


_SESSION_STAMP = re.compile(r"(\d{8}T\d{6})(?:\.(\d+))?Z$")


def _started_at(meta: dict, path) -> str | None:
    """RFC 3339 UTC start of the meeting: the recorder's tag, else the stamp the
    recorder puts at the end of SESSION_ID, else nothing (never the epoch)."""
    tagged = meta.get("session_started_at") or meta.get("SESSION_STARTED_AT")
    if tagged:
        return str(tagged)
    session = meta.get("SESSION_ID") or meta.get("session_id") or ""
    match = _SESSION_STAMP.search(session)
    if not match:
        return None
    stamp = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    return stamp.isoformat().replace("+00:00", "Z")


def parse_clock(text: str | None) -> float | None:
    """`SS`, `MM:SS` or `HH:MM:SS` to seconds."""
    if text is None:
        return None
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(p.strip() for p in parts):
        raise argparse.ArgumentTypeError(f"not a clock time: {text!r}")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


# --- The shared meeting clock ------------------------------------------------


class Clock:
    """Meeting time shared by every track. Only the driver moves it; a track that
    has delivered everything up to `now` parks in `wait_until` and says so."""

    def __init__(self, start: float):
        self.now = start
        self._moved = asyncio.Event()
        self.settled = asyncio.Event()  # a track parked or finished: re-check the barrier

    async def wait_until(self, moment: float):
        while self.now < moment:
            self._moved.clear()
            await self._moved.wait()

    def advance(self, step: float):
        self.now += step
        self._moved.set()


class ReplayStream:
    """What the transcriber reads instead of a WebRTC track: the participant's
    decoded audio frames, each released when the meeting clock reaches its end."""

    def __init__(self, path, track: Track, clock: Clock, start: float, end: float | None):
        self.track = track
        self.clock = clock
        self.start = start
        self.end = end
        self.finished = False
        # The meeting time the next frame is due; the track is idle while that
        # is ahead of the clock. A flag flipped inside the task would lag the
        # driver, which never yields between a tick and the next barrier check.
        self.due: float | None = None
        self.frames_delivered = 0
        self._container = av.open(str(path))
        stream = self._container.streams.get(track.index)[0]
        if start > 0:
            # Land before the window; frames ahead of it are skipped below.
            self._container.seek(int(max(0.0, start - 1.0) * av.time_base))
        self._frames = self._container.decode(stream)
        self._pending = None  # a frame fetched but not yet delivered (cancellation-safe)

    def _next(self):
        if self._pending is not None:
            return self._pending
        for frame in self._frames:
            if frame.time is None:
                continue
            ends = frame.time + frame.samples / frame.sample_rate
            if ends <= self.start:
                continue
            if self.end is not None and frame.time >= self.end:
                break
            self._pending = frame
            return frame
        return None

    async def receive(self):
        frame = self._next()
        if frame is None:
            self._finish()
            raise StreamEndedException("Recording ended")
        ends = frame.time + frame.samples / frame.sample_rate
        if self.clock.now < ends:
            self.due = ends
            self.clock.settled.set()
            await self.clock.wait_until(ends)
        self.due = None
        self._pending = None
        self.frames_delivered += 1
        return frame

    def _finish(self):
        if not self.finished:
            self.finished = True
            self.clock.settled.set()

    def stop(self):
        self._finish()
        with contextlib.suppress(Exception):
            self._container.close()

    @property
    def idle(self):
        return self.finished or (self.due is not None and self.due > self.clock.now)


# --- Engine backends ---------------------------------------------------------


def default_weights() -> str | None:
    here = Path(__file__).resolve().parents[1]
    baked = Path("/opt/whistle/whistle.cact")
    for candidate in (baked, here / "models" / "whistle.cact"):
        if candidate.is_file():
            return str(candidate)
    return None


# Reference copies in local/stt-eval/models keep the sherpa-onnx release names; the catalog
# no longer records them, since the app downloads single files from dist.gocassini.com.
REFERENCE_DIRS = {
    "nemotron-en": "sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25",
    "nemotron-multilingual": "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11",
}


def default_model_dir(engine_id: str) -> Path | None:
    """The app's own install (ModelStore: <storage>/models/<engine id>), else a reference copy."""
    if catalog.get(engine_id).asset is None:
        return None
    here = Path(__file__).resolve().parents[1]
    candidates = [Path("/nc_app_live_transcription_data/models") / engine_id]
    if engine_id in REFERENCE_DIRS:
        candidates.append(here / "local/stt-eval/models" / REFERENCE_DIRS[engine_id])
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def make_backend(engine_id: str, cpus: float, *, weights=None, model_dir=None, workers=None):
    """The same backend production would build for `cpus` CPUs, unless `workers`
    overrides the seat count."""
    engine = catalog.get(engine_id)
    size = workers or catalog.worker_ceiling(engine_id, cpus)
    if engine.kind == "worker_pool":
        from .pool import WorkerPool

        here = Path(__file__).resolve().parents[1]
        if not os.environ.get("NEEDLE3_LIB_PATH") and (here / "models/libneedle3.so").is_file():
            os.environ["NEEDLE3_LIB_PATH"] = str(here / "models/libneedle3.so")
        return WorkerPool(size, weights or default_weights())
    from .engines.shared import SharedEngine

    model_dir = model_dir or default_model_dir(engine_id)
    if model_dir is None:
        raise SystemExit(
            f"No model directory for {engine_id}; pass --model-dir (see local/stt-eval/models)"
        )
    return SharedEngine(engine, Path(model_dir), size, threads=catalog.engine_threads(cpus))


# --- The replay ---------------------------------------------------------------


@dataclasses.dataclass
class Replay:
    source: dict
    tracks: list[Track]
    window: tuple[float, float | None]
    engine: dict
    events: list[Event]
    stats: dict

    def to_json(self):
        start, end = self.window
        return {
            "format": FORMAT,
            "source": self.source,
            "window": {
                "fromMs": round(start * 1000),
                "toMs": None if end is None else round(end * 1000),
            },
            "engine": self.engine,
            "participants": [
                {"sid": t.sid, "name": t.name, "userId": t.user_id, "joinMs": round(t.start * 1000)}
                for t in self.tracks
            ],
            "events": [e.to_json() for e in self.events],
            "stats": self.stats,
        }


async def run(
    path,
    backend,
    *,
    tracks: list[Track] | None = None,
    language: str = "en",
    speed: float = 0.0,
    start: float = 0.0,
    end: float | None = None,
    progress=None,
) -> Replay:
    """Feed every track of `path` through `backend` and collect the timeline."""
    source, found = probe(path)
    tracks = list(tracks if tracks is not None else found)
    if not tracks:
        raise ValueError("The recording has no audio track")
    clock = Clock(start)
    captions: asyncio.Queue = asyncio.Queue()
    events: list[Event] = []
    for track in tracks:
        if track.start >= start and (end is None or track.start < end):
            events.append(Event(track.start, "join", track.sid))
        elif track.start < start:
            # Already in the call when the window opens: joined at its start.
            events.append(Event(start, "join", track.sid))

    await backend.start()
    transcriber.POOL = backend
    receive_timeout = transcriber.RECEIVE_TIMEOUT
    if speed <= 0:
        transcriber.RECEIVE_TIMEOUT = FAST_RECEIVE_TIMEOUT
    streams: list[ReplayStream] = []
    transcribers = []
    wall0 = time.monotonic()
    last_progress = 0.0
    try:
        for track in tracks:
            stream = ReplayStream(path, track, clock, start, end)
            streams.append(stream)
            handle = backend.transcriber(track.sid, language, captions)
            await handle.connect()
            await handle.start(stream)
            transcribers.append(handle)

        def idle(handle):
            # pending_since is set while a chunk is being recognised and cleared once
            # the queue is drained (transcriber._decode).
            return handle.queue.empty() and handle.pending_since is None

        def drain():
            while not captions.empty():
                item = captions.get_nowait()
                wall = time.monotonic() - wall0 if speed > 0 else None
                events.append(
                    Event(
                        clock.now,
                        "final" if item.final else "partial",
                        item.speaker_session_id,
                        item.message,
                        item.lang_id,
                        wall,
                    )
                )

        async def settled():
            # Every track either parked at the clock or ran out of frames.
            while not all(s.idle for s in streams):
                clock.settled.clear()
                await clock.settled.wait()

        while not all(s.finished for s in streams):
            await settled()
            drain()
            if speed > 0:
                due = wall0 + (clock.now + TICK - start) / speed
                delay = due - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
            else:
                # An engine of infinite speed: the clock waits for every result, so an
                # event's `at` is the moment its speech chunk closed.
                while not all(idle(t) for t in transcribers):
                    await asyncio.sleep(0.005)
                    drain()
            clock.advance(TICK)
            await asyncio.sleep(0)  # let the woken tracks deliver before the next barrier
            if progress and clock.now - last_progress >= 10:
                last_progress = clock.now
                progress(clock.now, len(events))
        # Everything is fed; let the recognisers flush their last words.
        await asyncio.gather(
            *(t.audio_task for t in transcribers if t.audio_task), return_exceptions=True
        )
        await asyncio.sleep(0)
        drain()
    finally:
        for handle in transcribers:
            with contextlib.suppress(Exception):
                await handle.shutdown()
        for stream in streams:
            stream.stop()
        status = {}
        with contextlib.suppress(Exception):
            status = backend.status()
        await backend.close()
        transcriber.POOL = None
        transcriber.RECEIVE_TIMEOUT = receive_timeout

    events.sort(key=lambda e: (e.at, 0 if e.type == "join" else 1))
    elapsed = time.monotonic() - wall0
    stats = {
        "finals": sum(e.type == "final" for e in events),
        "partials": sum(1 for e in events if e.type == "partial" and e.message),
        "elapsedS": round(elapsed, 1),
        "framesDelivered": {s.track.sid: s.frames_delivered for s in streams},
        "backend": status,
    }
    engine = {
        "id": getattr(backend, "engine_id", type(backend).__name__),
        "seats": getattr(backend, "size", None),
        "language": language,
        "speed": speed,
    }
    source = {**source, "path": str(path)}
    return Replay(source, tracks, (start, end), engine, events, stats)


# --- CLI ----------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0], prog="cassini_live.replay"
    )
    parser.add_argument("recording", help="a Cassini meeting .mkv")
    parser.add_argument("-o", "--output", help="replay JSON (default: <recording>.replay.json)")
    parser.add_argument(
        "--engine",
        default="whistle",
        choices=sorted(catalog.ENGINES),
        help="speech engine (default: whistle)",
    )
    parser.add_argument("--language", default="en", help="caption language, or auto (default: en)")
    parser.add_argument(
        "--speed",
        type=float,
        default=0.0,
        help="0: as fast as the engine keeps up without dropping audio; 1: real time, "
        "faithful contention and latency (default: 0)",
    )
    parser.add_argument(
        "--cpus",
        type=float,
        default=4,
        help="size the engine as for this CPU quota (default: 4, production)",
    )
    parser.add_argument("--workers", type=int, help="override the seat count derived from --cpus")
    parser.add_argument("--weights", help="Whistle weights (default: models/whistle.cact)")
    parser.add_argument(
        "--model-dir", help="Nemotron model directory (default: local/stt-eval/models/<engine>)"
    )
    parser.add_argument(
        "--from", dest="start", type=parse_clock, default=0.0, help="window start, MM:SS"
    )
    parser.add_argument("--to", dest="end", type=parse_clock, help="window end, MM:SS")
    parser.add_argument(
        "--only", action="append", help="replay only this participant id (repeatable)"
    )
    parser.add_argument("--list", action="store_true", help="list the recording's tracks and exit")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    source, tracks = probe(args.recording)
    if args.list:
        print(f"{source.get('title') or args.recording}  started {source.get('startedAt') or '?'}")
        for track in tracks:
            print(
                f"  stream {track.index:2d}  {track.sid:<16} {track.name:<20} "
                f"first audio at {track.start:8.2f}s"
            )
        return 0
    if args.only:
        tracks = [t for t in tracks if t.sid in args.only]
        missing = set(args.only) - {t.sid for t in tracks}
        if missing:
            parser.error(f"no such participant: {', '.join(sorted(missing))}")
    if args.end is not None and args.end <= args.start:
        parser.error("--to must be after --from")

    backend = make_backend(
        args.engine, args.cpus, weights=args.weights, model_dir=args.model_dir, workers=args.workers
    )

    def progress(now, count):
        line = f"  {now / 60:6.1f} min of meeting, {count} events"
        end = "\r" if sys.stderr.isatty() else "\n"
        print(line, end=end, file=sys.stderr, flush=True)

    replay = asyncio.run(
        run(
            args.recording,
            backend,
            tracks=tracks,
            language=args.language,
            speed=args.speed,
            start=args.start,
            end=args.end,
            progress=progress,
        )
    )
    print(file=sys.stderr)
    output = Path(args.output) if args.output else Path(args.recording).with_suffix(".replay.json")
    output.write_text(json.dumps(replay.to_json(), indent=1))
    stats = replay.stats
    print(
        f"{output}: {stats['finals']} finals, {stats['partials']} partials from "
        f"{len(replay.tracks)} tracks in {stats['elapsedS']} s "
        f"({replay.engine['id']}, speed {args.speed})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
