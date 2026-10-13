"""Receive every Talk track; recognize only speech, with isolated recovery."""

import asyncio
import base64
import logging
import time
from contextlib import suppress

from aiortc.mediastreams import MediaStreamError
from av import AudioResampler

from .pool import CapacityError
from .speech import SpeechGate
from .talk.types import StreamEndedException, Transcript

LOGGER = logging.getLogger(__name__)
POOL = None  # Set by application lifespan before rooms can be created.


class WhistleTranscriber:
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
        self.gate = SpeechGate()
        self.dropped_chunks = 0
        self.pending = ""
        self.stream = None
        self.stopping = False
        self.keep_track = False
        self.input_ended = False
        self.started = asyncio.Event()
        self.lock = asyncio.Lock()

    @property
    def pool(self):
        return self._pool if self._pool is not None else POOL

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
            self.pool.note_drop()
            if self.dropped_chunks == 1 or self.dropped_chunks % 30 == 0:
                LOGGER.warning("Caption backlog: discarded %s speech chunks", self.dropped_chunks)
        self.queue.put_nowait(chunk)

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
        pending = result.get("pending", "").strip()
        if pending != self.pending:
            await self.transcripts.put(Transcript(False, language, pending, self.session_id))
            self.pending = pending

    async def _release(self, healthy):
        if self.worker:
            worker, self.worker = self.worker, None
            self.lease_samples = 0
            await self.pool.release(worker, healthy=healthy)

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
        offset = 0
        while offset < len(chunk.samples):
            if self.worker is None:
                self.worker = await self.pool.acquire_wait(timeout=2)
                if time.monotonic() - chunk.created_at > 4:
                    await self._release(healthy=True)
                    self.pool.note_drop()
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
                if message["type"] != "result":
                    raise RuntimeError("Unexpected Whistle response")
                await self._emit(message["result"])
                self.lease_samples += cut - offset
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
                    self.pool.note_drop()
                    await self._finish("expiredAudio")
                    await self._clear_preview()
                    continue
                if len(chunk.samples):
                    await self._decode_audio(chunk)
                if chunk.end:
                    await self._finish(chunk.end_reason)
            except CapacityError:
                # Bounded waiting per speaker; other speakers/rooms stay alive.
                self.pool.capacity_waits += 1
                self.pool.note_drop()
                await self._clear_preview()
            except Exception as error:
                LOGGER.warning(
                    "Caption recognition failed (%s); retrying next speech chunk",
                    type(error).__name__,
                )
                await self._release(healthy=False)
                await self._clear_preview()
                if chunk is None:
                    return

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
            await self._release(healthy=False)

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
                self.gate = SpeechGate()
                self.pending = ""
                await self.connect()
                await self.start(stream)
            finally:
                self.keep_track = False
