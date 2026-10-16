import asyncio
import time
from dataclasses import replace

import numpy as np
import pytest

from cassini_live import catalog, transcriber
from cassini_live.engines.shared import EngineStreamError, LateReply, SharedEngine
from cassini_live.load import THRESHOLDS, LoadGovernor, Thresholds
from cassini_live.speech import SpeechChunk, SpeechGate
from cassini_live.transcriber import PAUSED_NOTICE, StreamTranscriber
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


async def test_latency_is_recorded_per_audio_result_but_not_per_commit():
    fake = await backend()
    track = fake.transcriber("s", "en", asyncio.Queue())
    await run(track, chunk(0, created_at=time.monotonic() - 1.5), chunk(1, end=True))
    (stream,) = fake.streams
    assert [c[0] for c in stream.calls] == ["audio", "audio", "commit", "cancel"]
    first, second = fake.lag.requests
    assert 1.5 <= first < 3 and 0 <= second < 1
    assert fake.status()["lagSeconds"] == second


class Permits:
    """An audio gate that lets one result through per permit."""

    def __init__(self):
        self.queue = asyncio.Queue()

    def grant(self):
        self.queue.put_nowait(None)

    async def wait(self):
        await self.queue.get()


async def test_pending_since_follows_the_oldest_undecoded_chunk():
    fake = await backend()
    fake.audio_gate = permits = Permits()
    track = fake.transcriber("s", "en", asyncio.Queue())
    assert track.pending_since is None
    base = time.monotonic()
    track._enqueue(chunk(0, created_at=base))
    track._enqueue(chunk(1, created_at=base + 0.5))
    assert track.pending_since == base
    decoding = asyncio.create_task(track._decode())
    async with asyncio.timeout(2):
        while not fake.streams or not fake.streams[0].calls:
            await asyncio.sleep(0.005)
    assert track.pending_since == base  # in flight, still undecoded
    permits.grant()
    async with asyncio.timeout(2):
        while len(fake.streams[0].calls) < 2:
            await asyncio.sleep(0.005)
    assert track.pending_since == base + 0.5
    permits.grant()
    async with asyncio.timeout(2):
        while track.pending_since is not None:
            await asyncio.sleep(0.005)
    track.queue.put_nowait(None)
    await decoding
    assert track.pending_since is None
    # Cancelled mid-decode with a chunk still queued (the shutdown timeout path): the
    # chunks go with the task, so nothing stays pending.
    track = fake.transcriber("s", "en", asyncio.Queue())
    await track.start(SilentTrack())
    track._enqueue(chunk(0, created_at=base))
    track._enqueue(chunk(1, created_at=base + 0.5))
    async with asyncio.timeout(2):
        while len(fake.streams) < 2 or not fake.streams[1].calls:
            await asyncio.sleep(0.005)
    assert track.pending_since == base and track.has_lease()
    track.audio_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await track.audio_task
    assert track.pending_since is None and not track.has_lease()


class SilentTrack:
    """A track that never delivers a frame: the capture loop only flushes the gate."""

    async def receive(self):
        await asyncio.Future()

    def stop(self):
        pass


class EndedTrack(SilentTrack):
    """A track whose sender has already gone."""

    async def receive(self):
        from aiortc.mediastreams import MediaStreamError

        raise MediaStreamError


async def test_lease_fields_and_registration():
    fake = await backend()
    track = fake.transcriber("s", "en", asyncio.Queue())
    assert track in fake.transcribers
    assert track.paused_until is None and track.shed_reason is None
    assert not track.has_lease() and track.lease_at is None
    before = time.monotonic()
    await track._decode_audio(chunk(0))
    assert track.has_lease() and before <= track.lease_at <= time.monotonic()
    await track._finish("silence")
    assert not track.has_lease() and track.lease_at is None


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
    # One deadline for the seat and the slot: the slot gets what admission left of 4 s.
    assert len(slots) == 1 and 3.9 < slots[0] <= 4
    assert fake.capacity_waits == 1 and fake.dropped_chunks == 1
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


async def test_late_reply_drops_the_piece_and_reopens_the_stream():
    fake = await backend()
    captions = asyncio.Queue()
    track = fake.transcriber("s", "en", captions)
    fake.stream_error = LateReply("late")
    await run(track, chunk(0))
    # The piece is lost and counted; the stream is cancelled, the slot freed.
    assert track.lost_chunks == 1 and fake.dropped_chunks == 1
    assert track.engine_stream is None and fake.busy == 0
    (first,) = fake.streams
    assert not first.alive
    fake.stream_error = None
    await run(track, chunk(0, end=True))
    # The next speech opens a fresh stream and is captioned.
    assert len(fake.streams) == 2
    assert [e.message for e in events(captions) if e.final] == ["w"]


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


# Live admission (0.8.0 step 8). Thresholds are provisional until the staging lag run, so
# these build measured ones; the default stays observe-only.

MEASURED = Thresholds(provisional=False, measured_on="2026-10-16")


def governed(fake, monkeypatch, *, thresholds=MEASURED, ceiling=1, **changes):
    monkeypatch.setattr(StreamTranscriber, "SLOT_WAIT_S", 0.05)
    gov = LoadGovernor(
        ceiling=ceiling,
        thresholds=replace(thresholds, **changes),
        transcribers=fake.transcribers,
    )
    gov.tick({})
    fake.governor = gov
    return gov


async def test_a_refused_speaker_gets_the_paused_notice_and_no_engine_traffic(monkeypatch):
    fake = await backend()
    gov = governed(fake, monkeypatch)
    captions = asyncio.Queue()
    first = fake.transcriber("a", "en", captions)
    assert gov.admit(first).admitted  # the first speaker is always admitted
    second = fake.transcriber("b", "en", captions)
    await run(second, chunk(0))
    # Ceiling reached: the second speaker waited its bounded time, then dropped the piece.
    assert fake.streams == [] and fake.capacity_waits == 1 and second.lost_chunks == 1
    assert gov.last_refusal["reason"] == "ceiling" and second not in gov.seats
    notice = events(captions)
    assert [(e.final, e.message, e.speaker_session_id) for e in notice] == [
        (False, PAUSED_NOTICE, "b")
    ]
    # A ceiling refusal is no hold-off: the next segment asks again.
    assert second.paused_until is None


async def test_the_notice_is_sent_once_per_interval(monkeypatch):
    fake = await backend()
    gov = governed(fake, monkeypatch)
    captions = asyncio.Queue()
    first = fake.transcriber("a", "en", captions)  # seats are weak: keep the holder alive
    gov.admit(first)
    second = fake.transcriber("b", "en", captions)
    await run(second, chunk(0))
    await run(second, chunk(0))
    assert [e.message for e in events(captions)] == [PAUSED_NOTICE]
    second.pending = "words"  # a partial replaced the notice
    await run(second, chunk(0))
    # Within the interval the preview is cleared instead of repeating the notice.
    assert [e.message for e in events(captions)] == [""]


async def test_a_lag_refusal_holds_the_speaker_off_without_waiting(monkeypatch):
    fake = await backend()
    gov = governed(fake, monkeypatch, ceiling=3)
    captions = asyncio.Queue()
    first = fake.transcriber("a", "en", captions)
    gov.admit(first)
    first.slot, first.lease_at = object(), time.monotonic() - 3  # a 3 s backlog
    first.pending_since = first.lease_at
    second = fake.transcriber("b", "en", captions)
    await run(second, chunk(0))
    assert gov.last_refusal["reason"] == "lag" and gov.closed
    assert second.paused_until is not None and second.paused_until > time.monotonic()
    started = time.monotonic()
    await run(second, chunk(0))
    # Paused: refused at once ("shed"), dropped without engine traffic or a slot wait.
    assert time.monotonic() - started < 0.04
    assert gov.last_refusal["reason"] == "shed" and fake.streams == []
    assert second.lost_chunks == 2
    first.slot = None


async def test_a_seat_freed_during_the_wait_is_taken(monkeypatch):
    fake = await backend()
    gov = governed(fake, monkeypatch)
    monkeypatch.setattr(StreamTranscriber, "SLOT_WAIT_S", 1.0)
    captions = asyncio.Queue()
    first = fake.transcriber("a", "en", captions)
    gov.admit(first)
    second = fake.transcriber("b", "en", captions)
    asyncio.get_running_loop().call_later(0.1, gov.release, first)  # a's track closed
    await run(second, chunk(0, end=True))
    assert second in gov.seats and fake.capacity_waits == 0
    assert [e.message for e in events(captions) if e.final] == ["w"]


async def test_observe_only_admits_everyone_and_records_the_refusal(monkeypatch):
    fake = await backend()
    gov = governed(fake, monkeypatch, thresholds=THRESHOLDS)
    assert gov.observe_only
    captions = asyncio.Queue()
    first = fake.transcriber("a", "en", captions)
    gov.admit(first)
    second = fake.transcriber("b", "en", captions)
    await run(second, chunk(0, end=True))
    assert gov.last_refusal["reason"] == "ceiling" and second in gov.seats
    assert [e.message for e in events(captions) if e.final] == ["w"]
    assert fake.capacity_waits == 0 and second.paused_until is None


async def test_a_closed_track_gives_its_seat_back():
    fake = await backend()
    gov = LoadGovernor(ceiling=2, thresholds=MEASURED, transcribers=fake.transcribers)
    fake.governor = gov
    track = fake.transcriber("a", "en", asyncio.Queue())
    track.stream = EndedTrack()
    gov.admit(track)
    assert track in gov.seats
    await track._run()
    assert track not in gov.seats


async def test_a_shed_speaker_ends_its_segment_keeps_its_stream_and_resumes(monkeypatch):
    from cassini_live.transcriber import AdmissionRefused

    fake = await backend()
    gov = governed(fake, monkeypatch, ceiling=3)
    captions = asyncio.Queue()
    first = fake.transcriber("a", "en", captions)
    newest = fake.transcriber("b", "en", captions)
    await first._decode_audio(chunk(0))
    await newest._decode_audio(chunk(0))
    stream = newest.engine_stream
    assert newest.slot is not None and newest in gov.seats and fake.busy == 2
    newest.shed_reason = "lag"  # what the governor's tick writes; the tick never acts
    await newest._decode_audio(chunk(1))
    # The segment ended with its text, the slot is free, the native stream stays open.
    assert newest.slot is None and fake.busy == 1 and fake.stream_ends["shed"] == 1
    assert newest.engine_stream is stream and stream.alive
    assert [c[0] for c in stream.calls] == ["audio", "commit"]
    assert newest.shed_reason is None and newest not in gov.seats
    assert newest.paused_until > time.monotonic() and newest.lost_chunks == 1
    messages = [e.message for e in events(captions)]
    assert messages[-2:] == ["w", PAUSED_NOTICE]
    # During the hold-off every piece is refused at once, without engine traffic.
    with pytest.raises(AdmissionRefused) as refused:
        await newest._decode_audio(chunk(2))
    assert refused.value.reason == "shed" and stream.calls[-1] == ("commit",)
    # After it, the speaker asks again like a new one and continues on the same stream.
    newest.paused_until = time.monotonic() - 0.01
    await newest._decode_audio(chunk(3))
    assert newest in gov.seats and newest.slot is not None
    assert newest.engine_stream is stream and stream.calls[-1] == ("audio", CHUNK)


async def test_a_stale_shed_mark_never_pauses_a_speaker_whose_backlog_is_gone(monkeypatch):
    # Review 0.8.0: the tick marked B while B decoded the last piece of its segment; the
    # segment ended and freed the slot, B fell silent, and minutes later (lag 0) B's first
    # chunk was dropped and B paused with the notice.
    fake = await backend(size=3)
    gov = governed(fake, monkeypatch, ceiling=3)
    captions = asyncio.Queue()
    a = fake.transcriber("a", "en", captions)
    b = fake.transcriber("b", "en", captions)
    await a._decode_audio(chunk(0))
    fake.audio_gate = permits = Permits()
    b.queue.put_nowait(chunk(0, end=True))
    task = asyncio.create_task(b._decode())
    async with asyncio.timeout(2):
        while not (len(fake.streams) == 2 and fake.streams[1].calls):
            await asyncio.sleep(0.005)
    gov.lag.sustained = lambda now: 3.0  # the cliff: B is the newest with a lease
    gov.tick({})
    assert b.shed_reason == "lag" and b not in gov.seats
    permits.grant()
    async with asyncio.timeout(2):
        while b.slot is not None:
            await asyncio.sleep(0.005)
    assert b.shed_reason == "lag" and not b.has_lease()  # ended normally, mark pending
    # The backlog is gone and admission has reopened.
    gov.lag.sustained = lambda now: 0.0
    gov.closed_reason = None
    fake.audio_gate = None
    events(captions)
    b.queue.put_nowait(chunk(1, end=True))
    b.queue.put_nowait(None)
    await asyncio.wait_for(task, 5)
    assert b.lost_chunks == 0 and b.paused_until is None and b.shed_reason is None
    assert PAUSED_NOTICE not in [e.message for e in events(captions)]
    a.slot = None


async def test_a_late_reply_on_the_shed_commit_still_holds_the_speaker_off(monkeypatch):
    # Review 0.8.0: _finish("shed") raising skipped the hold-off and kept the mark.
    fake = await backend(size=3)
    gov = governed(fake, monkeypatch, ceiling=3)
    captions = asyncio.Queue()
    a = fake.transcriber("a", "en", captions)
    b = fake.transcriber("b", "en", captions)
    await a._decode_audio(chunk(0))
    await b._decode_audio(chunk(0))
    gov.lag.sustained = lambda now: 3.0
    gov.tick({})
    assert b.shed_reason == "lag"

    async def late():
        raise LateReply("late")

    b.engine_stream.commit = late
    await run(b, chunk(0), chunk(1))
    assert b.paused_until is not None and b.paused_until > time.monotonic()
    assert b.shed_reason is None and b not in gov.seats and b.slot is None
    a.slot = None


async def test_drops_while_paused_are_not_capacity_waits(monkeypatch):
    fake = await backend()
    gov = governed(fake, monkeypatch, ceiling=3)
    track = fake.transcriber("a", "en", asyncio.Queue())
    gov.pause(track)
    await run(track, chunk(0), chunk(1))
    assert fake.paused_drops == 2 and fake.capacity_waits == 0 and track.lost_chunks == 2
    shared = SharedEngine(catalog.get("nemotron-en"), "/m", 1, process_factory=FakeHostProcess)
    assert shared.status()["pausedDrops"] == 0


async def test_refused_speakers_are_admitted_first_come_first_served(monkeypatch):
    # Review 0.8.0: a 0.25 s re-poll let a newcomer overtake an earlier waiter.
    from cassini_live.transcriber import AdmissionRefused

    fake = await backend(size=3)
    gov = governed(fake, monkeypatch, ceiling=1)
    q = asyncio.Queue()
    a = fake.transcriber("a", "en", q)
    gov.admit(a)
    c = fake.transcriber("c", "en", q)  # refused first
    d = fake.transcriber("d", "en", q)  # refused 0.1 s later
    tc = asyncio.create_task(c._admit(time.monotonic() + 1.0))
    await asyncio.sleep(0.1)
    td = asyncio.create_task(d._admit(time.monotonic() + 0.5))
    await asyncio.sleep(0.2)
    gov.release(a)  # a's track closed: the seat goes to c, the earlier waiter
    assert await asyncio.wait_for(tc, 2) is None and c in gov.seats
    with pytest.raises(AdmissionRefused) as refused:
        await asyncio.wait_for(td, 2)
    assert refused.value.reason == "ceiling" and d not in gov.seats
    # A newcomer cannot overtake a parked speaker either.
    gov.release(c)
    e = fake.transcriber("e", "en", q)
    gov.admit(e)  # first speaker again
    f = fake.transcriber("f", "en", q)
    g = fake.transcriber("g", "en", q)
    tf = asyncio.create_task(f._admit(time.monotonic() + 1.0))
    await asyncio.sleep(0.01)
    gov.ceiling = 2  # a seat appears without a release; only a tick hands it out
    assert not gov.admit(g).admitted  # g must queue behind f
    gov.tick({})
    assert await asyncio.wait_for(tf, 2) is None and f in gov.seats and g not in gov.seats


async def test_admission_and_slot_share_one_bounded_wait(monkeypatch):
    fake = await backend(size=1)
    gov = governed(fake, monkeypatch, ceiling=1)
    monkeypatch.setattr(StreamTranscriber, "SLOT_WAIT_S", 0.3)
    q = asyncio.Queue()
    first = fake.transcriber("a", "en", q)
    gov.admit(first)
    held = await fake.acquire_slot()
    asyncio.get_running_loop().call_later(0.2, gov.release, first)  # seat at 0.2 s
    second = fake.transcriber("b", "en", q)
    started = time.monotonic()
    await run(second, chunk(0))
    # Seat after 0.2 s, then the slot gets only the remaining 0.1 s, not another 0.3 s.
    assert time.monotonic() - started < 0.45 and fake.capacity_waits == 1
    fake.release_slot(held)
