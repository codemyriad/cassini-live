"""Boundary selection and fair handoff, without model-dependent transcripts."""

import asyncio
import base64

import numpy as np
import pytest

from cassini_live.pool import WorkerPool
from cassini_live.speech import SpeechChunk, SpeechGate
from cassini_live.transcriber import WhistleTranscriber

from .test_streaming import FakeWorker


class MarkedSpeech:
    frame_size = 512

    def probability(self, frame):
        return float(np.any(frame))


def test_gate_marks_sustained_pause_without_extra_buffering():
    gate = SpeechGate(MarkedSpeech())
    audio = np.ones(32512, dtype="<f4")
    audio[20480:23040] = 0  # 160 ms, shorter than the normal speech endpoint.
    chunks = gate.feed(audio) + gate.flush()
    assert np.array_equal(np.concatenate([c.samples for c in chunks]), audio)
    assert chunks[0].pause_cut is None
    assert chunks[1].pause_cut is not None
    assert 20480 < 16000 + chunks[1].pause_cut < 23040
    assert not chunks[1].end


def test_short_consonant_dip_is_not_a_handoff_pause():
    gate = SpeechGate(MarkedSpeech())
    audio = np.ones(32000, dtype="<f4")
    audio[20480:20992] = 0
    chunks = gate.feed(audio) + gate.flush()
    assert all(c.pause_cut is None for c in chunks)


def test_duration_limit_prefers_a_pause_in_last_second():
    gate = SpeechGate(MarkedSpeech())
    audio = np.ones(16 * 16000, dtype="<f4")
    audio[226304:228864] = 0
    chunks = gate.feed(audio) + gate.flush()
    first_end = next(i for i, c in enumerate(chunks) if c.end)
    assert chunks[first_end].end_reason == "durationPause"
    assert 14 * 16000 < sum(len(c.samples) for c in chunks[: first_end + 1]) < 15 * 16000
    assert np.array_equal(np.concatenate([c.samples for c in chunks]), audio)


@pytest.mark.parametrize("pause", [None, 8000])
async def test_handoff_preserves_audio_and_does_not_extend_deadline(pause):
    class SlowWorker(FakeWorker):
        async def receive(self, **kwargs):
            await asyncio.sleep(0.001)
            return await super().receive(**kwargs)

    pool = WorkerPool(1, worker_factory=SlowWorker)
    await pool.start()
    a = WhistleTranscriber("a", "en", asyncio.Queue(), pool=pool)
    b = WhistleTranscriber("b", "de", asyncio.Queue(), pool=pool)
    audio = np.arange(48000, dtype="<f4") / 48000
    for i in range(3):
        a.queue.put_nowait(
            SpeechChunk(
                audio[i * 16000 : (i + 1) * 16000],
                sequence=i,
                pause_cut=pause if i == 1 else None,
            )
        )
    a.queue.put_nowait(None)
    b.queue.put_nowait(SpeechChunk(np.ones(16000, dtype="<f4"), end=True))
    b.queue.put_nowait(None)
    try:
        await asyncio.wait_for(asyncio.gather(a._decode(), b._decode()), 2)
        messages = next(iter(pool.workers)).sent
        language, heard, first_lease = None, [], 0
        handed_off = False
        for m in messages:
            if m["type"] == "start":
                language = m["language"]
                handed_off |= language == "de"
            elif m["type"] == "audio" and language == "en":
                samples = np.frombuffer(base64.b64decode(m["pcm"]), dtype="<f4")
                heard.append(samples)
                if not handed_off:
                    first_lease += len(samples)
        assert np.array_equal(np.concatenate(heard), audio)
        assert first_lease == (24000 if pause else 32000)
        reason = "schedulingPause" if pause else "schedulingLimit"
        assert pool.status()["streamEnds"][reason] == 1
        assert not pool.waiting and pool.idle.qsize() == 1
        assert pool.dropped_chunks == 0
    finally:
        await pool.close()


async def test_repeated_handoffs_keep_two_second_budget_after_partial_chunk():
    class ContendedPool(WorkerPool):
        @property
        def waiting(self):
            return int(bool(self.leased))

    pool = ContendedPool(1, worker_factory=FakeWorker)
    await pool.start()
    track = WhistleTranscriber("a", "en", asyncio.Queue(), pool=pool)
    audio = np.arange(8 * 16000, dtype="<f4")
    try:
        for i in range(8):
            await track._decode_audio(
                SpeechChunk(
                    audio[i * 16000 : (i + 1) * 16000],
                    sequence=i,
                    pause_cut=8000 if i == 1 else None,
                )
            )
        await track._finish()
        sizes, heard, size = [], [], 0
        for m in next(iter(pool.workers)).sent:
            if m["type"] == "audio":
                samples = np.frombuffer(base64.b64decode(m["pcm"]), dtype="<f4")
                assert 0 < len(samples) <= 16000
                size += len(samples)
                heard.append(samples)
            elif m["type"] == "end":
                sizes.append(size)
                size = 0
        assert sizes == [24000, 32000, 32000, 32000, 8000]
        assert np.array_equal(np.concatenate(heard), audio)
    finally:
        await pool.close()
