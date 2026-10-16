"""Receive every Talk track; recognize only speech, with isolated recovery."""

import asyncio
import base64
import logging
import time
from contextlib import suppress

from aiortc.mediastreams import MediaStreamError
from av import AudioResampler

from .engines.shared import EngineFailed, LateReply
from .pool import CapacityError
from .speech import SpeechGate
from .talk.types import StreamEndedException, Transcript

LOGGER = logging.getLogger(__name__)
POOL = None  # Set by application lifespan before rooms can be created.
# Decision 4: the only per-speaker channel Talk offers is the speaker's own preview line,
# seen by every caption viewer in the room; the next partial replaces it.
PAUSED_NOTICE = "Captions paused for this speaker while the server catches up"
# Derived: a quarter of the 1 s monitor tick, so a seat freed or a reopen made by one tick
# is noticed within that tick by a speaker parked on admission.
ADMIT_POLL_S = 0.25


class AdmissionRefused(CapacityError):
    """The load governor refused this speaker; reason is its rule (lag, cpu, ...)."""

    def __init__(self, reason):
        super().__init__(f"Caption admission refused ({reason})")
        self.reason = reason


class WhistleTranscriber:
    SEGMENT_SECONDS = 15

    def __init__(self, session_id, language, transcript_queue, *_translation_hooks, pool=None):
        self._pool = pool
        self.session_id = session_id
        self.language = language
        self.transcripts = transcript_queue
        self.worker = None
        self.lease_samples = 0
        self.audio_task = None
        self.capture_task = None
        self.queue = asyncio.Queue(maxsize=6)
        self.gate = SpeechGate(max_seconds=self.SEGMENT_SECONDS)
        self.dropped_chunks = 0
        self.lost_chunks = 0  # Every discarded chunk, for the diagnostic lanes.
        self.pending = ""
        self.stream = None
        self.stopping = False
        self.keep_track = False
        self.input_ended = False
        self.started = asyncio.Event()
        self.lock = asyncio.Lock()
        # Load signals (0.8.0-instant-admission.md): created_at of the oldest chunk
        # still waiting for its result, and when the current lease was granted.
        self.pending_since = None
        self.lease_at = None
        # Hold-off after a shed or a lag/CPU refusal, and the shed mark the load governor's
        # tick writes; this task acts on the mark at its next piece, never the tick itself.
        self.paused_until = None
        self.shed_reason = None
        self.noticed_at = None  # monotonic time of the last paused notice
        if self.pool is not None:
            self.pool.transcribers.add(self)

    @property
    def pool(self):
        return self._pool if self._pool is not None else POOL

    def has_lease(self):
        return self.worker is not None

    @property
    def governor(self):
        return getattr(self.pool, "governor", None)

    async def _admit(self, timeout):
        """Ask the load governor before waiting for a worker or slot. A refused speaker
        stays parked on admission for the same bounded wait as on a slot, so a seat freed
        by a closing track or a reopen within it is taken at once."""
        governor = self.governor
        if governor is None:
            return
        deadline = time.monotonic() + timeout
        while True:
            decision = governor.admit(self)
            if decision.admitted:
                return
            if decision.reason == "shed" or time.monotonic() >= deadline:
                raise AdmissionRefused(decision.reason)
            await asyncio.sleep(min(ADMIT_POLL_S, max(0, deadline - time.monotonic())))

    async def _refused(self, error):
        """A bounded wait ended without a worker, slot or seat: drop the piece."""
        self.pool.capacity_waits += 1
        self._note_drop()
        governor = self.governor
        reason = getattr(error, "reason", None)
        if governor is not None and reason in {"lag", "cpu"}:
            # Hold this speaker off rather than retrying every piece into the backlog.
            governor.pause(self)
        if reason is not None:
            await self._notify_paused()
        else:
            await self._clear_preview()

    async def _shed(self):
        """The governor chose this speaker to relieve a sustained backlog. Acted on here, in
        the transcriber's own task, so no in-flight engine request is preempted: the segment
        ends normally (its text is kept), the lease goes, and the speaker is held off."""
        self.shed_reason = None
        await self._finish("shed")
        self._note_drop()  # this piece is not decoded
        if self.governor is not None:
            self.governor.pause(self)
        await self._notify_paused()

    async def _notify_paused(self):
        if self.pending == PAUSED_NOTICE:
            return
        interval = getattr(getattr(self.governor, "thresholds", None), "notice_interval", 60)
        now = time.monotonic()
        if self.noticed_at is not None and now - self.noticed_at < interval:
            await self._clear_preview()
            return
        self.noticed_at = now
        await self.transcripts.put(Transcript(False, self.language, PAUSED_NOTICE, self.session_id))
        self.pending = PAUSED_NOTICE

    async def connect(self):
        # Track arrival must never reserve recognition capacity or close a room.
        if self.pool is None or self.pool.closed:
            raise CapacityError("Caption service is stopping")

    async def start(self, stream):
        self.stream = stream
        self.stopping = False
        self.input_ended = False
        self.started.clear()
        self.audio_task = asyncio.create_task(self._run())
        await self.started.wait()

    def _enqueue(self, chunk):
        if self.queue.full():
            self.queue.get_nowait()
            self.dropped_chunks += 1
            self._note_drop()
            if self.dropped_chunks == 1 or self.dropped_chunks % 30 == 0:
                LOGGER.warning("Caption backlog: discarded %s speech chunks", self.dropped_chunks)
        self.queue.put_nowait(chunk)
        if self.pending_since is None:
            self.pending_since = chunk.created_at

    def _note_drop(self):
        self.lost_chunks += 1
        self.pool.note_drop()

    async def _put_audio(self, samples):
        for chunk in self.gate.feed(samples):
            self._enqueue(chunk)
        await asyncio.sleep(0)

    def _flush_gate(self):
        for chunk in self.gate.flush():
            self._enqueue(chunk)

    async def _capture(self):
        resampler = AudioResampler(format="fltp", rate=16000)
        try:
            while True:
                try:
                    # DTX/muted clients can stop sending frames altogether.
                    # Finish speech and free its worker even without silence frames.
                    frame = await asyncio.wait_for(self.stream.receive(), 1.0)
                except TimeoutError:
                    self._flush_gate()
                    continue
                for converted in resampler.resample(frame):
                    await self._put_audio(converted.to_ndarray().mean(axis=0))
        except (MediaStreamError, StreamEndedException):
            pass
        except asyncio.CancelledError:
            if not self.stopping:
                raise
        for converted in resampler.resample(None):
            await self._put_audio(converted.to_ndarray().mean(axis=0))
        self._flush_gate()
        await self.queue.put(None)
        self.input_ended = True

    async def _emit(self, result):
        language = result.get("language") or self.language
        if text := result.get("text", "").strip():
            await self.transcripts.put(Transcript(True, language, text, self.session_id))
            self.pending = ""
            if self.pool is not None:
                self.pool.finals_emitted = getattr(self.pool, "finals_emitted", 0) + 1
        pending = result.get("pending", "").strip()
        if pending != self.pending:
            await self.transcripts.put(Transcript(False, language, pending, self.session_id))
            self.pending = pending
            if pending and self.pool is not None:
                self.pool.partials_emitted = getattr(self.pool, "partials_emitted", 0) + 1

    async def _release(self, healthy):
        if self.worker:
            worker, self.worker = self.worker, None
            self.lease_samples = 0
            self.lease_at = None
            await self.pool.release(worker, healthy=healthy)

    async def abort(self):
        await self._release(healthy=False)

    async def _finish(self, reason="flush"):
        if not self.worker:
            return
        await self.worker.send({"type": "end"})
        while True:
            message = await self.worker.receive()
            if message["type"] == "done":
                break
            if message["type"] != "result":
                raise RuntimeError("Unexpected Whistle response")
            await self._emit(message["result"])
        await self._release(healthy=True)
        self.pool.stream_ends[reason] += 1

    async def _decode_audio(self, chunk):
        if self.shed_reason is not None:
            await self._shed()
            return
        offset = 0
        while offset < len(chunk.samples):
            if self.worker is None:
                await self._admit(timeout=2)
                self.worker = await self.pool.acquire_wait(timeout=2)
                self.lease_at = time.monotonic()
                if time.monotonic() - chunk.created_at > 4:
                    await self._release(healthy=True)
                    self._note_drop()
                    return
                await self.worker.send({"type": "start", "language": self.language})
            cut = len(chunk.samples)
            reason = None
            total = self.lease_samples + cut - offset
            if self.pool.waiting and total >= 32000 and not (chunk.end and total == 32000):
                # Prefer a pause already in this chunk, with at least one
                # second of useful work per lease. Never extend the 2s budget.
                cut = offset + max(0, 32000 - self.lease_samples)
                reason = "schedulingLimit"
                pause = chunk.pause_cut
                if (
                    pause is not None
                    and offset < pause <= cut
                    and self.lease_samples + pause - offset >= 16000
                ):
                    cut = pause
                    reason = "schedulingPause"
            if cut > offset:
                await self.worker.send(
                    {
                        "type": "audio",
                        "pcm": base64.b64encode(chunk.samples[offset:cut].tobytes()).decode(),
                    }
                )
                message = await self.worker.receive()
                self.pool.lag.record(time.monotonic() - chunk.created_at)
                if message["type"] != "result":
                    raise RuntimeError("Unexpected Whistle response")
                await self._emit(message["result"])
                self.lease_samples += cut - offset
                if self.pool is not None:
                    self.pool.chunks_processed = getattr(self.pool, "chunks_processed", 0) + 1
                    self.pool.audio_seconds = (
                        getattr(self.pool, "audio_seconds", 0.0) + (cut - offset) / 16000
                    )
                offset = cut
            # A waiter can arrive while native inference is running. Release
            # immediately at the deadline rather than waiting for more audio.
            if reason is None and self.pool.waiting and self.lease_samples >= 32000:
                reason = "schedulingLimit"
            if reason:
                # A natural endpoint at exactly the deadline needs no extra
                # reset and should be reported as the natural endpoint.
                if offset == len(chunk.samples) and chunk.end:
                    reason = chunk.end_reason
                else:
                    self.pool.scheduling_yields += 1
                await self._finish(reason)

    async def _clear_preview(self):
        if self.pending:
            await self.transcripts.put(Transcript(False, self.language, "", self.session_id))
            self.pending = ""

    async def _decode(self):
        expected = 0
        while True:
            chunk = await self.queue.get()
            if chunk is not None:
                self.pending_since = chunk.created_at
            try:
                if chunk is None:
                    await self._finish()
                    return
                if chunk.sequence != expected:
                    # Never splice audio across a lost interval in native state.
                    await self._finish("audioGap")
                    await self._clear_preview()
                expected = chunk.sequence + 1
                if time.monotonic() - chunk.created_at > 4:
                    self._note_drop()
                    await self._finish("expiredAudio")
                    await self._clear_preview()
                    continue
                if len(chunk.samples):
                    await self._decode_audio(chunk)
                if chunk.end:
                    await self._finish(chunk.end_reason)
            except CapacityError as error:
                # Bounded waiting per speaker; other speakers/rooms stay alive.
                await self._refused(error)
            except Exception as error:
                LOGGER.warning(
                    "Caption recognition failed (%s); retrying next speech chunk",
                    type(error).__name__,
                )
                await self._release(healthy=False)
                await self._clear_preview()
                if chunk is None:
                    return
            finally:
                # Nothing is in flight here; a non-empty queue is picked up at once.
                if self.queue.empty():
                    self.pending_since = None

    async def _run(self):
        try:
            async with asyncio.TaskGroup() as group:
                self.capture_task = group.create_task(self._capture())
                group.create_task(self._decode())
                self.started.set()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.warning("Caption audio input failed (%s)", type(error).__name__)
        finally:
            if not self.keep_track:
                self.stream.stop()
            # A cancelled decoder leaves its queued chunks behind; none is pending any more.
            self.pending_since = None
            await self._release(healthy=False)
            if self.governor is not None:
                self.governor.release(self)  # a closed track gives its seat back at once

    async def shutdown(self):
        async with self.lock:
            await self._stop()

    async def _stop(self):
        self.stopping = True
        try:
            async with asyncio.timeout(10):
                if self.capture_task and not self.capture_task.done():
                    self.capture_task.cancel()
                if self.audio_task:
                    await asyncio.shield(self.audio_task)
        except TimeoutError:
            if self.audio_task:
                self.audio_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.audio_task
        finally:
            self.audio_task = None

    async def set_language(self, language):
        async with self.lock:
            if language == self.language:
                return
            stream = self.stream
            if stream is None:
                self.language = language
                return
            self.keep_track = True
            try:
                await self._stop()
                self.language = language
                self.queue = asyncio.Queue(maxsize=6)
                self.gate = SpeechGate(max_seconds=self.SEGMENT_SECONDS)
                self.pending = ""
                self.pending_since = None
                await self.connect()
                await self.start(stream)
            finally:
                self.keep_track = False


class StreamTranscriber(WhistleTranscriber):
    """One continuous native stream per speaker on the shared engine host.

    A speaking slot is held for one gate segment and never preempted; the native
    stream stays open across pauses and ends only at a real end of audio.
    """

    # No preemption on the shared engine: shorter segments free slots sooner, and a waiting
    # speaker keeps audio as long as the stale limit allows.
    SEGMENT_SECONDS = 8
    SLOT_WAIT_S = 4

    def __init__(self, *args, **kwargs):
        self.slot = None
        super().__init__(*args, **kwargs)
        self.engine_stream = None
        self.uncommitted = 0

    @property
    def worker(self):
        # Diagnostics lanes show the held slot like a Whistle worker.
        return self.slot

    @worker.setter
    def worker(self, value):
        self.slot = value

    def _drop_stream(self):
        self.engine_stream = None
        self.uncommitted = 0

    async def _release(self, healthy):
        if self.slot is not None:
            slot, self.slot = self.slot, None
            self.lease_samples = 0
            self.lease_at = None
            self.pool.release_slot(slot)
        if not healthy and self.engine_stream is not None:
            stream = self.engine_stream
            self._drop_stream()
            await stream.cancel()

    async def _end_stream(self, reason="flush"):
        """The only real end: pad, input_finished and final text, or a plain cancel."""
        stream, uncommitted = self.engine_stream, self.uncommitted
        self._drop_stream()
        if stream is not None:
            if uncommitted:
                await self._emit(await stream.finish())
                self.pool.stream_ends[reason] += 1
            else:
                await stream.cancel()
        await self._release(healthy=True)

    async def _finish(self, reason="flush"):
        # Segment end: final text since the last commit; the stream stays open.
        if self.engine_stream is not None and self.uncommitted:
            self.uncommitted = 0
            await self._emit(await self.engine_stream.commit())
            self.pool.stream_ends[reason] += 1
        await self._release(healthy=True)

    async def _decode_audio(self, chunk):
        if self.shed_reason is not None:
            await self._shed()  # commits and frees the slot; the native stream stays open
            return
        if self.slot is None:
            await self._admit(timeout=self.SLOT_WAIT_S)
            self.slot = await self.pool.acquire_slot(timeout=self.SLOT_WAIT_S)
            self.lease_at = time.monotonic()
            if time.monotonic() - chunk.created_at > 4:
                await self._release(healthy=True)
                self._note_drop()
                return
        if self.engine_stream is None:
            self.engine_stream = await self.pool.open_stream(self.language)
        for offset in range(0, len(chunk.samples), 16000):
            samples = chunk.samples[offset : offset + 16000]
            result = await self.engine_stream.audio(samples)
            self.pool.lag.record(time.monotonic() - chunk.created_at)
            self.uncommitted += len(samples)
            self.lease_samples += len(samples)
            self.pool.chunks_processed += 1
            self.pool.audio_seconds += len(samples) / 16000
            await self._emit(result)

    async def _decode(self):
        expected = 0
        while True:
            chunk = await self.queue.get()
            if chunk is not None:
                self.pending_since = chunk.created_at
            try:
                if chunk is None:
                    await self._end_stream()
                    return
                if chunk.sequence != expected:
                    # Never splice audio across a lost interval in native state.
                    await self._end_stream("audioGap")
                    await self._clear_preview()
                expected = chunk.sequence + 1
                if time.monotonic() - chunk.created_at > 4:
                    self._note_drop()
                    await self._end_stream("expiredAudio")
                    await self._clear_preview()
                    continue
                if len(chunk.samples):
                    await self._decode_audio(chunk)
                if chunk.end:
                    await self._finish(chunk.end_reason)
            except CapacityError as error:
                await self._refused(error)
            except LateReply:
                # The host lives on but this answer is lost: drop the piece and reopen the
                # stream at the next one, so no native state spans a missing reply.
                LOGGER.warning("Caption engine answered late; reopening this speaker's stream")
                self._note_drop()
                await self._release(healthy=False)
                await self._clear_preview()
            except EngineFailed:
                # The engine restarts itself; never from here.
                LOGGER.warning("Caption engine unavailable; retrying next speech chunk")
                self._note_drop()
                self._drop_stream()
                await self._release(healthy=True)
                await self._clear_preview()
            except Exception as error:
                # EngineStreamError included: only this stream is cancelled.
                LOGGER.warning("Caption recognition failed (%s)", type(error).__name__)
                await self._release(healthy=False)
                await self._clear_preview()
            finally:
                # Nothing is in flight here; a non-empty queue is picked up at once.
                if self.queue.empty():
                    self.pending_since = None
            if chunk is None:
                return


def create(session_id, language, transcript_queue, *translation_hooks, pool=None):
    backend = pool if pool is not None else POOL
    if backend is None:
        return WhistleTranscriber(session_id, language, transcript_queue, *translation_hooks)
    return backend.transcriber(session_id, language, transcript_queue, *translation_hooks)
