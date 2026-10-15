import asyncio
import time

import numpy as np
import pytest

from cassini_live import catalog, transcriber
from cassini_live.engines.shared import EngineStreamError, SharedEngine
from cassini_live.speech import SpeechChunk, SpeechGate
from cassini_live.transcriber import StreamTranscriber
from tests.fakes_engine import CHUNK, FakeBackend, FakeHostProcess
from tests.test_streaming import AlwaysSpeech, AudioSource

SPEECH = np.full(CHUNK, 0.1, dtype="<f4")


@pytest.fixture
def speech_gate(monkeypatch):
    monkeypatch.setattr(transcriber, "SpeechGate", lambda **kw: SpeechGate(AlwaysSpeech(), **kw))


async def backend(**kwargs):
    fake = FakeBackend(**kwargs)
    await fake.start()
    return fake


def chunk(sequence, end=False, samples=SPEECH, created_at=None, reason="silence"):
    created = time.monotonic() if created_at is None else created_at
    return SpeechChunk(samples, end, sequence, created, end_reason=reason)


async def run(track, *chunks):
    for item in chunks:
        track.queue.put_nowait(item)
    track.queue.put_nowait(None)
    async with asyncio.timeout(5):
        await track._decode()


def events(queue):
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


async def test_stream_stays_open_across_segments_and_ends_once():
    fake = await backend()
    captions = asyncio.Queue()
    track = fake.transcriber("s", "en", captions)
    assert isinstance(track, StreamTranscriber)
    await run(track, chunk(0), chunk(1, end=True), chunk(2), chunk(3, end=True))
    (stream,) = fake.streams
    assert [c[0] for c in stream.calls] == [
        "audio",
        "audio",
        "commit",
        "audio",
        "audio",
        "commit",
        "cancel",
    ]
    finals = [e.message for e in events(captions) if e.final]
    assert finals == ["w w", "w w"]
    assert fake.stream_ends["silence"] == 2
    assert fake.busy == 0 and track.slot is None and track.lease_samples == 0


async def test_end_of_track_finishes_uncommitted_audio():
    fake = await backend()
    captions = asyncio.Queue()
    track = fake.transcriber("s", "en", captions)
    await run(track, chunk(0))
    (stream,) = fake.streams
    assert stream.calls == [("audio", CHUNK), ("finish",)]
    assert [e.message for e in events(captions) if e.final] == ["w"]
    assert fake.stream_ends["flush"] == 1 and fake.busy == 0


async def test_gap_finishes_the_stream_and_opens_a_new_one():
    fake = await backend()
    captions = asyncio.Queue()
    track = fake.transcriber("s", "en", captions)
    await run(track, chunk(0), chunk(2, end=True))
    first, second = fake.streams
    assert first.calls == [("audio", CHUNK), ("finish",)]
    assert [c[0] for c in second.calls] == ["audio", "commit", "cancel"]
    assert fake.stream_ends["audioGap"] == 1


async def test_expired_audio_ends_the_stream():
    fake = await backend()
    track = fake.transcriber("s", "en", asyncio.Queue())
    await run(track, chunk(0), chunk(1, created_at=time.monotonic() - 5))
    (stream,) = fake.streams
    assert stream.calls == [("audio", CHUNK), ("finish",)]
    assert fake.stream_ends["expiredAudio"] == 1 and fake.dropped_chunks == 1


async def test_waiting_speaker_never_preempts_an_open_segment():
    shared = SharedEngine(catalog.get("nemotron-en"), "/m", 1, process_factory=FakeHostProcess)
    await shared.start()
    first = shared.transcriber("a", "en", asyncio.Queue())
    second = shared.transcriber("b", "en", asyncio.Queue())
    holder = asyncio.create_task(first._decode())
    for i in range(3):
        first.queue.put_nowait(chunk(i))
    second.queue.put_nowait(chunk(0, end=True))
    waiter = asyncio.create_task(second._decode())
    async with asyncio.timeout(2):
        while not (shared.waiting and first.queue.empty() and first.uncommitted == 3 * CHUNK):
            await asyncio.sleep(0.005)
    # Plenty of audio past the old 2 s handoff budget; the holder keeps its slot.
    assert first.slot is not None and second.slot is None
    first.queue.put_nowait(chunk(3, end=True))
    async with asyncio.timeout(2):
        while shared.stream_ends["silence"] < 2:
            await asyncio.sleep(0.005)
    first.queue.put_nowait(None)
    second.queue.put_nowait(None)
    await asyncio.gather(holder, waiter)
    assert shared.scheduling_yields == 0 and shared.capacity_waits == 0
    commits = [m["id"] for m in shared.process.sent if m["type"] == "commit"]
    assert commits == [1, 2]
    await shared.close()


async def test_waiting_speaker_drops_after_four_seconds(monkeypatch):
    fake = await backend(size=1)
    slots = []
    original = fake.acquire_slot

    async def quick(timeout=4):
        slots.append(timeout)
        return await original(timeout=0.01)

    monkeypatch.setattr(fake, "acquire_slot", quick)
    held = await original()
    track = fake.transcriber("b", "en", asyncio.Queue())
    await run(track, chunk(0, end=True))
    assert slots == [4] and fake.capacity_waits == 1 and fake.dropped_chunks == 1
    assert fake.streams == []
    fake.release_slot(held)


async def test_stale_audio_after_waiting_is_dropped(monkeypatch):
    fake = await backend()
    original = fake.acquire_slot

    async def slow(timeout=2):
        await asyncio.sleep(0.2)
        return await original(timeout)

    monkeypatch.setattr(fake, "acquire_slot", slow)
    track = fake.transcriber("b", "en", asyncio.Queue())
    await run(track, chunk(0, created_at=time.monotonic() - 3.9))
    assert fake.streams == [] and fake.busy == 0 and fake.dropped_chunks == 1


async def test_abort_cancels_without_a_host_restart():
    shared = SharedEngine(catalog.get("nemotron-en"), "/m", 2, process_factory=FakeHostProcess)
    await shared.start()
    track = shared.transcriber("s", "en", asyncio.Queue())
    track.queue.put_nowait(chunk(0))
    task = asyncio.create_task(track._decode())
    async with asyncio.timeout(2):
        while not track.uncommitted:
            await asyncio.sleep(0.005)
    task.cancel()
    await track.abort()
    assert shared.streams == {} and shared.busy == 0
    assert shared.failures == 0 and shared.restarts == 0 and shared.ready
    assert shared.process.sent[-1] == {"type": "cancel", "id": 1}
    await shared.close()


async def test_shared_engine_round_trip_with_automatic_language():
    shared = SharedEngine(
        catalog.get("nemotron-multilingual"), "/m", 2, process_factory=FakeHostProcess
    )
    await shared.start()
    captions = asyncio.Queue()
    track = shared.transcriber("s", "auto", captions)
    await run(track, chunk(0), chunk(1, end=True), chunk(2, end=True))
    sent = [m["type"] for m in shared.process.sent]
    assert sent == ["open", "audio", "audio", "commit", "audio", "commit", "cancel"]
    finals = [e for e in events(captions) if e.final]
    assert [e.message for e in finals] == ["w1 w2", "w3"]
    assert all(e.lang_id == "de" for e in finals)
    assert shared.is_idle()
    await shared.close()


async def test_stream_error_cancels_only_that_stream(caplog):
    fake = await backend()
    fake.stream_error = EngineStreamError("limit")
    captions = asyncio.Queue()
    track = fake.transcriber("s", "en", captions)
    await run(track, chunk(0))
    assert fake.busy == 0 and track.engine_stream is None
    assert "EngineStreamError" in caplog.text
    fake.stream_error = None
    await run(fake.transcriber("t", "en", captions), chunk(0, end=True))
    assert len(fake.streams) == 2


async def test_host_failure_drops_references():
    shared = SharedEngine(catalog.get("nemotron-en"), "/m", 2, process_factory=FakeHostProcess)
    await shared.start()
    track = shared.transcriber("s", "en", asyncio.Queue())
    await track._decode_audio(chunk(0))
    shared.process.crash()
    async with asyncio.timeout(2):
        while shared.restarts < 1:
            await asyncio.sleep(0.005)
    await run(track, chunk(1), chunk(2, end=True))
    # The dead stream is never reused; the next speech opens a new one.
    assert track.engine_stream is None and shared.busy == 0
    assert shared.restarts == 1 and shared.failures == 1
    await shared.close()


async def test_set_language_restarts_the_track_with_the_new_option(speech_gate):
    shared = SharedEngine(
        catalog.get("nemotron-multilingual"), "/m", 2, process_factory=FakeHostProcess
    )
    await shared.start()
    track = transcriber.create("s", "en", asyncio.Queue(), pool=shared)
    await track.connect()
    source = AudioSource(endless=True)
    await track.start(source)
    async with asyncio.timeout(2):
        while not any(m["type"] == "open" for m in shared.process.sent):
            await asyncio.sleep(0.01)
    await track.set_language("fr")
    assert not source.stopped
    async with asyncio.timeout(2):
        while len([m for m in shared.process.sent if m["type"] == "open"]) < 2:
            await asyncio.sleep(0.01)
    opens = [m["language"] for m in shared.process.sent if m["type"] == "open"]
    assert opens == ["en", "fr"]
    await track.shutdown()
    assert source.stopped and shared.busy == 0
    await shared.close()


@pytest.mark.parametrize(
    "track, cuts",
    [(StreamTranscriber, [8, 16, 17]), (transcriber.WhistleTranscriber, [15, 17])],
)
def test_gate_cuts_continuous_speech_per_engine(speech_gate, track, cuts):
    # The shared engine never preempts, so its segments end sooner than Whistle's.
    gate = track("s", "en", asyncio.Queue()).gate
    chunks = gate.feed(np.ones(16000 * 17, dtype="<f4")) + gate.flush()
    ends, total = [], 0
    for item in chunks:
        total += len(item.samples)
        if item.end:
            ends.append(total)
    assert ends == [16000 * s for s in cuts]
