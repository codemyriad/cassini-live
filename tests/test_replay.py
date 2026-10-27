"""The meeting replay: an archived per-track recording through the caption pipeline.

The recording is synthesised here with PyAV in the recorder's layout (one audio
stream per participant, `PARTICIPANT_ID` / `PARTICIPANT_NAME` tags, tracks that
start at different moments) from the public jfk fixture, and the engine is the
in-process fake host, which says one word per 0.56 s of non-zero audio."""

import json
import re
import wave
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest

from cassini_live import catalog, replay
from cassini_live.engines.shared import SharedEngine
from tests.fakes_engine import FakeHostProcess

FIXTURE = Path(__file__).parent / "assets" / "jfk.wav"
RATE = 16000


def speech():
    with wave.open(str(FIXTURE)) as handle:
        assert handle.getframerate() == RATE and handle.getnchannels() == 1
        return np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2")


def write_recording(path, participants, *, session="daily_20260313T112934.888Z"):
    """`participants`: [(id, name, start_seconds, samples)] → a Cassini-style mkv."""
    with av.open(str(path), "w") as out:
        out.metadata["title"] = "Cassini Go Recording test"
        out.metadata["SESSION_ID"] = session
        streams = []
        for pid, name, start, samples in participants:
            stream = out.add_stream("pcm_s16le", rate=RATE)
            stream.codec_context.layout = "mono"
            stream.metadata["PARTICIPANT_ID"] = pid
            stream.metadata["PARTICIPANT_NAME"] = name
            stream.metadata["LTID"] = f"p:{pid}:audio:janus"
            streams.append((stream, start, samples))
        # Interleave 20 ms frames in time order, as the recorder's remux does.
        frames = []
        for stream, start, samples in streams:
            for offset in range(0, len(samples), 320):
                frame = av.AudioFrame.from_ndarray(
                    samples[None, offset : offset + 320], format="s16", layout="mono"
                )
                frame.sample_rate = RATE
                frame.pts = int(start * RATE) + offset
                frame.time_base = Fraction(1, RATE)
                frames.append((frame.pts, stream, frame))
        for _, stream, frame in sorted(frames, key=lambda f: f[0]):
            for packet in stream.encode(frame):
                out.mux(packet)
        for stream, _, _ in streams:
            for packet in stream.encode(None):
                out.mux(packet)


@pytest.fixture
def recording(tmp_path):
    words = speech()
    silence = np.zeros(RATE, dtype="<i2")
    path = tmp_path / "meeting.mkv"
    write_recording(
        path,
        [
            ("alice", "Alice Lovelace", 0.0, np.concatenate((words, silence))),
            ("bob", "Bob Babbage", 3.0, np.concatenate((silence, words))),
        ],
    )
    return path


def test_probe_reads_participants_and_start(recording):
    source, tracks = replay.probe(recording)
    assert source["title"] == "Cassini Go Recording test"
    assert source["startedAt"] == "2026-03-13T11:29:34Z"
    assert [(t.sid, t.name, t.user_id) for t in tracks] == [
        ("alice", "Alice Lovelace", "alice"),
        ("bob", "Bob Babbage", "bob"),
    ]
    assert tracks[0].start == pytest.approx(0.0, abs=0.05)
    assert tracks[1].start == pytest.approx(3.0, abs=0.05)


def test_started_at_is_unknown_without_a_stamp(tmp_path):
    path = tmp_path / "legacy.mkv"
    write_recording(path, [("x", "X", 0.0, np.zeros(RATE, dtype="<i2"))], session="legacy")
    source, _ = replay.probe(path)
    assert source["startedAt"] is None


@pytest.mark.parametrize(
    "text, seconds", [("90", 90), ("1:30", 90), ("01:02:03", 3723), ("0:00", 0)]
)
def test_parse_clock(text, seconds):
    assert replay.parse_clock(text) == seconds


def test_parse_clock_rejects_junk():
    with pytest.raises(Exception):
        replay.parse_clock("1:2:3:4")


def fake_backend():
    return SharedEngine(catalog.get("nemotron-en"), "/m", 2, process_factory=FakeHostProcess)


async def test_replay_times_every_speaker_on_the_meeting_clock(recording):
    result = await replay.run(recording, fake_backend(), speed=0)
    body = result.to_json()
    assert body["format"] == replay.FORMAT
    assert [p["sid"] for p in body["participants"]] == ["alice", "bob"]
    joins = [e for e in body["events"] if e["type"] == "join"]
    assert [(j["speakerSessionId"], j["at"]) for j in joins] == [("alice", 0), ("bob", 3000)]
    finals = [e for e in body["events"] if e["type"] == "final"]
    assert {f["speakerSessionId"] for f in finals} == {"alice", "bob"}
    for final in finals:
        assert re.fullmatch(r"(w\d+ ?)+", final["message"]), final
        assert final["langId"] == "en"
    # Alice talks from 0 s, Bob from 4 s: the clock, not the arrival order, dates them.
    first = {f["speakerSessionId"]: f["at"] for f in reversed(finals)}
    assert first["alice"] < first["bob"]
    assert 3000 < first["bob"] < 20000
    ats = [e["at"] for e in body["events"]]
    assert ats == sorted(ats)
    assert body["engine"]["id"] == "nemotron-en"
    assert body["stats"]["finals"] == len(finals)
    assert body["stats"]["framesDelivered"] == {"alice": 600, "bob": 600}
    assert body["window"] == {"fromMs": 0, "toMs": None}
    json.dumps(body)  # serialisable as written


async def test_window_limits_audio_and_dates_earlier_joins_at_its_start(recording):
    result = await replay.run(recording, fake_backend(), speed=0, start=4.0, end=8.0)
    body = result.to_json()
    assert body["window"] == {"fromMs": 4000, "toMs": 8000}
    joins = {e["speakerSessionId"]: e["at"] for e in body["events"] if e["type"] == "join"}
    assert joins == {"alice": 4000, "bob": 4000}
    assert all(4000 <= e["at"] <= 8100 for e in body["events"])
    delivered = body["stats"]["framesDelivered"]
    assert delivered["alice"] == 200 and delivered["bob"] == 200


async def test_only_the_chosen_tracks_are_replayed(recording):
    _, tracks = replay.probe(recording)
    bob = [t for t in tracks if t.sid == "bob"]
    result = await replay.run(recording, fake_backend(), tracks=bob, speed=0, end=6.0)
    body = result.to_json()
    assert [p["sid"] for p in body["participants"]] == ["bob"]
    assert {e["speakerSessionId"] for e in body["events"]} == {"bob"}


async def test_real_time_pacing_stamps_wall_lag(recording):
    result = await replay.run(recording, fake_backend(), speed=8.0, end=2.0)
    captions = [e for e in result.events if e.type != "join"]
    assert captions, "the fake engine says a word per half second of speech"
    assert all(e.wall is not None for e in captions)
    assert result.stats["elapsedS"] >= 2.0 / 8


def test_cli_lists_tracks(recording, capsys):
    assert replay.main([str(recording), "--list"]) == 0
    out = capsys.readouterr().out
    assert "alice" in out and "Bob Babbage" in out and "started 2026-03-13T11:29:34Z" in out


def test_cli_rejects_unknown_participant(recording):
    with pytest.raises(SystemExit):
        replay.main([str(recording), "--only", "nobody"])
