import asyncio
import base64
import time
import wave
from pathlib import Path

import numpy as np
import pytest

from cassini_live import transcriber
from cassini_live.pool import WorkerPool
from cassini_live.speech import SpeechChunk, SpeechGate
from cassini_live.transcriber import WhistleTranscriber

from .test_streaming import FakeWorker


def speech_fixture():
    with wave.open(str(Path(__file__).parent / "assets/jfk.wav")) as wav:
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype("<f4") / 32768


def test_real_vad_preserves_speech_and_flushes_silence():
    gate = SpeechGate()
    silence = np.zeros(16000, dtype="<f4")
    assert gate.feed(silence) == []
    chunks = gate.feed(speech_fixture()) + gate.feed(silence) + gate.flush()
    assert chunks and chunks[-1].end
    assert all(len(c.samples) <= 16000 for c in chunks)
    assert [c.sequence for c in chunks] == list(range(len(chunks)))
    speech_seconds = sum(len(c.samples) for c in chunks) / 16000
    assert speech_seconds > len(speech_fixture()) / 16000 - 2
    assert gate.feed(silence) == []


def test_flush_preserves_sequence_and_final_partial_frame():
    from .test_streaming import AlwaysSpeech

    gate = SpeechGate(AlwaysSpeech())
    chunks = gate.feed(np.ones(16713, dtype="<f4")) + gate.flush()
    assert sum(len(c.samples) for c in chunks) == 16713
    more = gate.feed(np.ones(3213, dtype="<f4")) + gate.flush()
    assert [c.sequence for c in chunks + more] == list(range(len(chunks + more)))
    assert more[-1].end


async def test_ten_tracks_one_speaker_use_one_worker(monkeypatch):
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    monkeypatch.setattr(transcriber, "POOL", pool)
    captions = asyncio.Queue()
    tracks = [WhistleTranscriber(str(i), "en", captions) for i in range(10)]
    await asyncio.gather(*(t.connect() for t in tracks))
    assert pool.idle.qsize() == 1
    decoders = [asyncio.create_task(t._decode()) for t in tracks]
    samples = speech_fixture()
    try:
        for start in range(0, len(samples), 16000):
            await asyncio.gather(
                *(
                    t._put_audio(
                        samples[start : start + 16000]
                        if i == 0
                        else np.zeros(len(samples[start : start + 16000]), dtype="<f4")
                    )
                    for i, t in enumerate(tracks)
                )
            )
            await asyncio.sleep(0.001)
        for t in tracks:
            t._flush_gate()
            await t.queue.put(None)
        await asyncio.wait_for(asyncio.gather(*decoders), 5)
        assert pool.idle.qsize() == 1
        assert pool.failures == pool.capacity_waits == pool.dropped_chunks == 0
        assert not captions.empty()
        while not captions.empty():
            assert captions.get_nowait().speaker_session_id == "0"
        worker = next(iter(pool.workers))
        audio = [m for m in worker.sent if m["type"] == "audio"]
        assert audio
        assert sum(len(base64.b64decode(m["pcm"])) for m in audio) < 4 * (len(samples) + 16000)
    finally:
        for task in decoders:
            task.cancel()
        await asyncio.gather(*decoders, return_exceptions=True)
        await pool.close()


async def test_failed_recognition_recovers_same_track(monkeypatch):
    class FailingWorker(FakeWorker):
        async def receive(self, **kwargs):
            raise TimeoutError()

    pool = WorkerPool(1, worker_factory=FailingWorker)
    await pool.start()
    pool.worker_factory = FakeWorker
    monkeypatch.setattr(transcriber, "POOL", pool)
    track = WhistleTranscriber("speaker", "en", asyncio.Queue())
    await track.connect()
    audio = np.ones(16000, dtype="<f4")
    await track.queue.put(SpeechChunk(audio, sequence=0))
    await track.queue.put(SpeechChunk(audio, end=True, sequence=1))
    await track.queue.put(None)
    await asyncio.wait_for(track._decode(), 5)
    assert pool.failures == 1
    assert pool.idle.qsize() == 1
    assert any(m["type"] == "end" for m in next(iter(pool.workers)).sent)
    assert not track.transcripts.empty()
    await pool.close()


async def test_overlapping_speakers_wait_and_keep_identity(monkeypatch):
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    monkeypatch.setattr(transcriber, "POOL", pool)
    tracks = [WhistleTranscriber(str(i), "en", asyncio.Queue()) for i in range(3)]
    for track in tracks:
        await track.connect()
        await track.queue.put(SpeechChunk(np.ones(16000, dtype="<f4"), end=True))
        await track.queue.put(None)
    await asyncio.wait_for(asyncio.gather(*(t._decode() for t in tracks)), 5)
    for i, track in enumerate(tracks):
        assert not track.transcripts.empty()
        while not track.transcripts.empty():
            assert track.transcripts.get_nowait().speaker_session_id == str(i)
    assert pool.idle.qsize() == 1
    await pool.close()


async def test_audio_gap_ends_native_segment_before_resuming(monkeypatch):
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    monkeypatch.setattr(transcriber, "POOL", pool)
    track = WhistleTranscriber("speaker", "en", asyncio.Queue())
    samples = np.ones(16000, dtype="<f4")
    await track.queue.put(SpeechChunk(samples, sequence=0))
    await track.queue.put(SpeechChunk(samples, sequence=3, end=True))
    await track.queue.put(None)
    await track._decode()
    worker = next(iter(pool.workers))
    assert [m["type"] for m in worker.sent] == ["start", "audio", "end"] * 2
    await pool.close()


async def test_capacity_timeout_does_not_kill_track(monkeypatch):
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    monkeypatch.setattr(transcriber, "POOL", pool)
    held = pool.acquire()
    track = WhistleTranscriber("waiting", "en", asyncio.Queue())
    await track.connect()
    task = asyncio.create_task(track._decode())
    await track.queue.put(SpeechChunk(np.ones(16000, dtype="<f4"), sequence=0))
    async with asyncio.timeout(3):
        while not pool.capacity_waits:
            await asyncio.sleep(0.02)
    assert not task.done()
    await pool.release(held)
    await track.queue.put(SpeechChunk(np.ones(16000, dtype="<f4"), end=True, sequence=1))
    await track.queue.put(None)
    await asyncio.wait_for(task, 2)
    assert not track.transcripts.empty()
    assert pool.idle.qsize() == 1
    await pool.close()


async def test_muted_track_without_frames_flushes_and_releases(monkeypatch):
    from .test_streaming import AlwaysSpeech, AudioSource

    monkeypatch.setattr(transcriber, "SpeechGate", lambda **kw: SpeechGate(AlwaysSpeech(), **kw))
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    monkeypatch.setattr(transcriber, "POOL", pool)

    class MutedSource(AudioSource):
        async def receive(self):
            if self.index >= 75:
                await asyncio.Future()
            return await super().receive()

    source = MutedSource(endless=True)
    track = WhistleTranscriber("speaker", "en", asyncio.Queue())
    await track.start(source)
    try:
        async with asyncio.timeout(3):
            while True:
                event = await track.transcripts.get()
                if event.final:
                    break
        async with asyncio.timeout(1):
            while not pool.idle.qsize():
                await asyncio.sleep(0.01)
        assert not source.stopped
        assert not track.audio_task.done()
    finally:
        await track.shutdown()
        await pool.close()


def test_continuous_speech_yields_worker_every_fifteen_seconds():
    from .test_streaming import AlwaysSpeech

    gate = SpeechGate(AlwaysSpeech())
    chunks = gate.feed(np.ones(16000 * 31, dtype="<f4")) + gate.flush()
    assert sum(c.end for c in chunks) == 3
    assert sum(len(c.samples) for c in chunks) == 16000 * 31
    assert max(len(c.samples) for c in chunks) <= 16000


def test_flush_keeps_the_configured_segment_limit():
    from .test_streaming import AlwaysSpeech

    # Receive timeouts flush idle gates; a stream engine must keep its shorter segments.
    gate = SpeechGate(AlwaysSpeech(), max_seconds=8)
    assert gate.flush() == []
    chunks = gate.feed(np.ones(16000 * 9, dtype="<f4"))
    end = next(i for i, c in enumerate(chunks) if c.end)
    assert chunks[end].end_reason == "durationLimit"
    assert sum(len(c.samples) for c in chunks[: end + 1]) == 16000 * 8


def test_neural_gate_rejects_backgrounds_but_preserves_noisy_speech():
    from cassini_live.calibration import background

    for kind in ("hiss", "fan", "keyboard"):
        for dbfs in (-55, -40, -25):
            for seed in (1, 2):
                gate = SpeechGate()
                assert not gate.feed(background(kind, 64000, dbfs, seed)) + gate.flush()
        speech = speech_fixture()
        for samples in (speech * 0.1, speech + background(kind, len(speech), -25, 1)):
            gate = SpeechGate()
            chunks = gate.feed(samples) + gate.flush()
            assert sum(len(c.samples) for c in chunks) > len(speech) * 0.85


def test_neural_state_is_separate_between_tracks_and_reset_on_flush():
    samples = speech_fixture()
    expected_gate = SpeechGate()
    expected = expected_gate.feed(samples) + expected_gate.flush()
    a, b = SpeechGate(), SpeechGate()
    heard, quiet = [], []
    for offset in range(0, len(samples), 320):
        heard.extend(a.feed(samples[offset : offset + 320]))
        quiet.extend(b.feed(np.zeros(320, dtype="<f4")))
    heard.extend(a.flush())
    quiet.extend(b.flush())
    assert not quiet
    assert np.array_equal(
        np.concatenate([c.samples for c in heard]), np.concatenate([c.samples for c in expected])
    )
    again = a.feed(samples) + a.flush()
    assert np.array_equal(
        np.concatenate([c.samples for c in again]), np.concatenate([c.samples for c in expected])
    )


async def test_waiting_speaker_gets_turn_after_two_seconds(monkeypatch):
    class SlowWorker(FakeWorker):
        async def receive(self, **kwargs):
            await asyncio.sleep(0.001)
            return await super().receive(**kwargs)

    pool = WorkerPool(1, worker_factory=SlowWorker)
    await pool.start()
    tracks = [
        WhistleTranscriber(str(i), lang, asyncio.Queue(), pool=pool)
        for i, lang in enumerate(("en", "de", "fr"))
    ]
    for i, track in enumerate(tracks):
        for j in range(6 if i == 0 else 1):
            track.queue.put_nowait(SpeechChunk(np.ones(16000, dtype="<f4"), sequence=j))
        # A sentinel must not overwrite the bounded queue's existing speech.
    tasks = [asyncio.create_task(t._decode()) for t in tracks]
    for track in tracks:
        await track.queue.put(None)
    await asyncio.wait_for(asyncio.gather(*tasks), 2)
    messages = next(iter(pool.workers)).sent
    starts = [i for i, m in enumerate(messages) if m["type"] == "start"]
    assert [messages[i]["language"] for i in starts[:4]] == ["en", "de", "fr", "en"]
    assert sum(m["type"] == "audio" for m in messages[: starts[1]]) == 2
    assert pool.scheduling_yields == 1
    assert not pool.waiting and pool.idle.qsize() == 1
    await pool.close()


async def test_latency_is_recorded_per_audio_reply_but_not_for_the_end():
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    track = WhistleTranscriber("a", "en", asyncio.Queue(), pool=pool)
    assert track in pool.transcribers
    samples = np.ones(16000, dtype="<f4")
    track.queue.put_nowait(SpeechChunk(samples, end=True, created_at=time.monotonic() - 1.5))
    track.queue.put_nowait(None)
    await track._decode()
    assert [m["type"] for m in next(iter(pool.workers)).sent] == ["start", "audio", "end"]
    (recorded,) = pool.lag.requests
    assert 1.5 <= recorded < 3
    assert pool.status()["lagSeconds"] == recorded
    await pool.close()


async def test_pending_since_and_lease_follow_the_whistle_worker():
    permits = asyncio.Queue()

    class GatedWorker(FakeWorker):
        async def receive(self, **kwargs):
            await permits.get()
            return await super().receive(**kwargs)

    pool = WorkerPool(1, worker_factory=GatedWorker)
    await pool.start()
    track = WhistleTranscriber("a", "en", asyncio.Queue(), pool=pool)
    assert track.pending_since is None and not track.has_lease()
    samples = np.ones(16000, dtype="<f4")
    base = time.monotonic()
    track._enqueue(SpeechChunk(samples, sequence=0, created_at=base))
    track._enqueue(SpeechChunk(samples, sequence=1, created_at=base + 0.5))
    assert track.pending_since == base
    decoding = asyncio.create_task(track._decode())
    worker = next(iter(pool.workers))
    async with asyncio.timeout(2):
        while not any(m["type"] == "audio" for m in worker.sent):
            await asyncio.sleep(0.005)
    assert track.pending_since == base and track.has_lease() and track.lease_at is not None
    permits.put_nowait(None)
    async with asyncio.timeout(2):
        while sum(m["type"] == "audio" for m in worker.sent) < 2:
            await asyncio.sleep(0.005)
    assert track.pending_since == base + 0.5
    permits.put_nowait(None)
    async with asyncio.timeout(2):
        while track.pending_since is not None:
            await asyncio.sleep(0.005)
    assert track.has_lease()  # the segment has not ended
    for _ in range(2):  # the end's result and done replies
        permits.put_nowait(None)
    track.queue.put_nowait(None)
    await decoding
    assert not track.has_lease() and track.lease_at is None and track.pending_since is None
    # Cancelled with a chunk still queued and one in flight: nothing stays pending.
    from .test_stream_transcriber import SilentTrack

    track = WhistleTranscriber("b", "en", asyncio.Queue(), pool=pool)
    await track.start(SilentTrack())
    track._enqueue(SpeechChunk(samples, sequence=0, created_at=base))
    track._enqueue(SpeechChunk(samples, sequence=1, created_at=base + 0.5))
    async with asyncio.timeout(2):
        while sum(m["type"] == "audio" for m in worker.sent) < 3:
            await asyncio.sleep(0.005)
    assert track.pending_since == base and track.has_lease()
    track.audio_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await track.audio_task
    assert track.pending_since is None and not track.has_lease()
    await pool.close()


async def test_expired_audio_is_dropped_without_joining_native_contexts():
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    track = WhistleTranscriber("a", "en", asyncio.Queue(), pool=pool)
    samples = np.ones(16000, dtype="<f4")
    for chunk in (
        SpeechChunk(samples),
        SpeechChunk(samples, sequence=1, created_at=0),
        SpeechChunk(samples, sequence=2, end=True),
        None,
    ):
        track.queue.put_nowait(chunk)
    await track._decode()
    assert pool.dropped_chunks == 1
    assert pool.status()["recentCapacityPressure"]
    assert [m["type"] for m in next(iter(pool.workers)).sent] == ["start", "audio", "end"] * 2
    await pool.close()


async def test_whistle_asks_admission_before_taking_a_worker(monkeypatch):
    from cassini_live.load import LoadGovernor, Thresholds

    pool = WorkerPool(2, worker_factory=FakeWorker)
    await pool.start()
    pool.governor = LoadGovernor(
        ceiling=1,
        thresholds=Thresholds(provisional=False, measured_on="2026-10-16"),
        transcribers=pool.transcribers,
    )
    pool.governor.tick({})
    holder = WhistleTranscriber("a", "en", asyncio.Queue(), pool=pool)
    pool.governor.admit(holder)
    track = WhistleTranscriber("b", "en", asyncio.Queue(), pool=pool)
    track.queue.put_nowait(SpeechChunk(np.ones(16000, dtype="<f4"), sequence=0))
    track.queue.put_nowait(None)
    # A free worker is not enough: the seat ceiling refuses after the bounded 2 s wait.
    await asyncio.wait_for(track._decode(), 4)
    assert pool.capacity_waits == 1 and pool.idle.qsize() == 2
    assert all(not w.sent for w in pool.workers)
    notice = track.transcripts.get_nowait()
    assert not notice.final and notice.message == transcriber.PAUSED_NOTICE
    await pool.close()


async def test_a_shed_whistle_speaker_finishes_its_lease_and_is_held_off():
    from cassini_live.load import LoadGovernor, Thresholds

    pool = WorkerPool(2, worker_factory=FakeWorker)
    await pool.start()
    pool.governor = LoadGovernor(
        ceiling=2,
        thresholds=Thresholds(provisional=False, measured_on="2026-10-16"),
        transcribers=pool.transcribers,
    )
    pool.governor.tick({})
    track = WhistleTranscriber("b", "en", asyncio.Queue(), pool=pool)
    await track._decode_audio(SpeechChunk(np.ones(16000, dtype="<f4"), sequence=0))
    worker = track.worker
    assert worker is not None and track in pool.governor.seats
    track.shed_reason = "lag"
    await track._decode_audio(SpeechChunk(np.ones(16000, dtype="<f4"), sequence=1))
    # The lease ended normally ("end" sent, its text emitted) and the worker is free.
    assert track.worker is None and pool.idle.qsize() == 2 and pool.stream_ends["shed"] == 1
    assert [m["type"] for m in worker.sent] == ["start", "audio", "end"]
    assert track.paused_until is not None and track not in pool.governor.seats
    messages = []
    while not track.transcripts.empty():
        messages.append(track.transcripts.get_nowait().message)
    assert messages[-1] == transcriber.PAUSED_NOTICE
    await pool.close()
