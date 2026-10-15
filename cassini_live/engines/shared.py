"""Supervisor-side client of the shared engine host (engine-selection.md 6.4).

One host process serves every speaker. Admission is a count of speaking slots equal
to the measured speaker count; a slot is held for one gate segment, never preempted.
Native streams stay open across pauses and are bounded by max_streams.
"""

import asyncio
import base64
import json
import logging
import os
import sys
import time
import weakref
from collections import Counter, deque
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import catalog
from ..engine_host import LANGUAGE_OPTION
from ..pool import CapacityError, _LagMeter

LOGGER = logging.getLogger(__name__)
SEGMENTS = "continuous"  # "per_segment" if commit turns out to truncate finals


class EngineFailed(RuntimeError):
    """The host exited or timed out; every stream is invalid."""


class EngineStreamError(RuntimeError):
    """The host refused or failed one stream and lives on."""


@dataclass(eq=False)
class Slot:
    index: int


class EngineStream:
    def __init__(self, engine, stream_id):
        self.engine = engine
        self.id = stream_id
        self.alive = True

    def _closed(self):
        self.alive = False
        if self.engine.streams.get(self.id) is self:
            del self.engine.streams[self.id]

    async def _call(self, message):
        if not self.alive:
            raise EngineStreamError("Stream is closed")
        reply = await self.engine._request(self.id, {**message, "id": self.id})
        if reply.get("type") == "error":
            self._closed()
            raise EngineStreamError(reply.get("code", "protocol"))
        return reply["result"]

    async def audio(self, samples):
        pcm = base64.b64encode(np.asarray(samples, dtype="<f4").tobytes()).decode()
        return await self._call({"type": "audio", "pcm": pcm})

    async def commit(self):
        return await self._call({"type": "commit"})

    async def finish(self):
        try:
            return await self._call({"type": "finish"})
        finally:
            self._closed()

    async def cancel(self):
        if not self.alive:
            return
        self._closed()
        with suppress(Exception):
            await self.engine._request(self.id, {"type": "cancel", "id": self.id})


class EngineHostProcess:
    def __init__(self):
        self.process = None

    @property
    def returncode(self):
        return self.process.returncode if self.process else None

    async def start(self, argv, env):
        self.process = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            # Native loading messages go to stderr, never captions.
            limit=1024 * 1024,
        )

    async def send(self, message):
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        await asyncio.wait_for(self.process.stdin.drain(), 10)

    async def receive(self):
        line = await self.process.stdout.readline()
        if not line:
            raise EOFError("Engine host exited")
        return json.loads(line)

    async def close(self):
        if self.process is None or self.process.returncode is not None:
            return
        try:
            with suppress(Exception):
                await self.send({"type": "quit"})
            for stop in (self.process.terminate, self.process.kill):
                try:
                    await asyncio.wait_for(self.process.wait(), 3)
                    return
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        stop()
            await self.process.wait()
        except asyncio.CancelledError:
            # Never leave a model-sized process behind when shutdown is interrupted.
            with suppress(ProcessLookupError):
                self.process.kill()
            raise


class SharedEngine:
    def __init__(
        self,
        engine: catalog.Engine,
        model_dir: Path,
        size: int,
        *,
        threads: int | None = None,
        max_streams: int = catalog.MAX_STREAMS,
        max_batch: int | None = None,
        request_timeout: float = 5.0,
        ready_timeout: float = 120.0,
        process_factory=EngineHostProcess,
        clock=time.monotonic,
    ):
        self.engine = engine
        self.engine_id = engine.id
        self.model_dir = Path(model_dir)
        self.size = size
        override = os.environ.get("CASSINI_ENGINE_THREADS")
        self.threads = int(override) if override else threads or 1
        if not 1 <= self.threads <= 4:
            raise ValueError("CASSINI_ENGINE_THREADS must be between 1 and 4")
        self.max_streams = max_streams
        self.max_batch = max_batch or engine.requirements.max_batch
        self.request_timeout = request_timeout
        self.ready_timeout = ready_timeout
        self.process_factory = process_factory
        self.clock = clock
        self.multilingual = len(engine.languages) > 1
        self.admission_limit = size
        self.closed = False
        self.resource_check = None
        self.load_check = None
        self.failures = 0
        self.failure_times = deque(maxlen=3)
        self.failure_circuit_open = False
        self.capacity_waits = 0
        self.dropped_chunks = 0
        self.scheduling_yields = 0
        self.last_pressure = 0.0
        self.stream_ends = Counter()
        self.chunks_processed = 0
        self.audio_seconds = 0.0
        self.finals_emitted = 0
        self.partials_emitted = 0
        self.leased = set()
        self.waiters = deque()
        self.streams = {}
        self._orphans: set[asyncio.Task] = set()
        self.pending = {}
        self.process = None
        self.ready = False
        self.restarts = 0
        self.generation = 0
        self._starting = set()
        self._reader = None
        self._restart = None
        self._next_id = 0
        self.lag = _LagMeter()
        # Every live transcriber on this backend, for the load governor (step 4).
        self.transcribers = weakref.WeakSet()
        # Clock reading of the last host reply; step 5 tells a late reply from a dead host.
        self.last_message_at = None

    # Speaking slots. Same hand-off rules as WorkerPool._offer/acquire_wait.

    @property
    def workers(self):
        return self.leased

    @property
    def available(self):
        return max(0, self.admission_limit - len(self.leased))

    @property
    def waiting(self):
        return sum(not future.done() for future in self.waiters)

    @property
    def busy(self):
        return len(self.leased)

    def is_idle(self):
        return self.ready and not self.leased and not self.streams

    def resize(self, size):
        if not self.closed:
            raise RuntimeError("Resize the engine only while it is closed")
        self.size = size

    def note_drop(self):
        self.dropped_chunks += 1
        self.last_pressure = time.monotonic()

    def _slot(self):
        used = {slot.index for slot in self.leased}
        slot = Slot(next(n for n in range(1, len(used) + 2) if n not in used))
        self.leased.add(slot)
        return slot

    def _offer(self):
        while self.waiters and len(self.leased) < self.admission_limit:
            future = self.waiters.popleft()
            if not future.done():
                future.set_result(self._slot())
                return

    async def acquire_slot(self, timeout: float = 2) -> Slot:
        if self.closed:
            raise CapacityError("Caption service is stopping")
        if self.available and not self.waiting:
            return self._slot()
        future = asyncio.get_running_loop().create_future()
        self.waiters.append(future)
        acquired = False
        try:
            slot = await asyncio.wait_for(asyncio.shield(future), timeout)
            acquired = True
            return slot
        except TimeoutError:
            raise CapacityError("All speaking slots are in use") from None
        finally:
            # A timeout/cancellation can race with a direct hand-off.
            if not future.done():
                future.cancel()
            elif not acquired and not future.cancelled() and future.exception() is None:
                self.release_slot(future.result())
            if future in self.waiters:
                self.waiters.remove(future)

    def release_slot(self, slot: Slot) -> None:
        if slot not in self.leased:
            return
        self.leased.discard(slot)
        if not self.closed:
            self._offer()

    # Native streams.

    async def open_stream(self, language: str) -> EngineStream:
        if self.closed:
            raise CapacityError("Caption service is stopping")
        if len(self.streams) >= self.max_streams:
            raise CapacityError("Too many open caption streams")
        if not self.multilingual:
            option = "en"
        else:
            option = "auto" if language == "auto" else LANGUAGE_OPTION.get(language, language)
        self._next_id += 1
        stream = EngineStream(self, self._next_id)
        self.streams[stream.id] = stream
        try:
            reply = await self._request(
                stream.id, {"type": "open", "id": stream.id, "language": option}
            )
            if reply.get("type") != "opened":
                raise EngineStreamError(reply.get("code", "protocol"))
        except asyncio.CancelledError:
            # The host may already hold the native stream; free it, or it counts against
            # max_streams until the host restarts.
            stream._closed()
            task = asyncio.create_task(self._cancel_orphan(stream.id))
            self._orphans.add(task)
            task.add_done_callback(self._orphans.discard)
            raise
        except BaseException:
            stream._closed()
            raise
        return stream

    async def _cancel_orphan(self, stream_id):
        with suppress(Exception):
            await self._request(stream_id, {"type": "cancel", "id": stream_id})

    async def _request(self, stream_id, message):
        host = self.process
        if host is None or not self.ready:
            raise EngineFailed("Speech engine is not running")
        if (previous := self.pending.get(stream_id)) and not previous.done():
            previous.set_exception(EngineStreamError("Superseded"))
        future = asyncio.get_running_loop().create_future()
        self.pending[stream_id] = future
        try:
            await host.send(message)
            return await asyncio.wait_for(future, self.request_timeout)
        except TimeoutError:
            self._host_failed(host, "request timeout")
            raise EngineFailed("Speech engine did not answer in time") from None
        except (OSError, EOFError):
            self._host_failed(host, "write failed")
            raise EngineFailed("Speech engine exited") from None
        finally:
            if self.pending.get(stream_id) is future:
                del self.pending[stream_id]

    async def _read(self, host):
        try:
            while True:
                message = await host.receive()
                self.last_message_at = self.clock()
                if message.get("type") == "error" and "id" not in message:
                    raise EOFError("Engine host failed")
                future = self.pending.pop(message.get("id"), None)
                if future is None or future.done():
                    LOGGER.debug("Ignoring engine reply for stream %s", message.get("id"))
                    continue
                future.set_result(message)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._host_failed(host, type(error).__name__)

    def _host_failed(self, host, reason):
        if self.closed or host is not self.process:
            return
        LOGGER.warning("Speech engine host failed (%s)", reason)
        self.process = None
        self.ready = False
        self._invalidate(EngineFailed("Speech engine failed"))
        self.failures += 1
        self.failure_times.append(self.clock())
        if len(self.failure_times) == 3 and self.failure_times[-1] - self.failure_times[0] < 60:
            self.failure_circuit_open = True
        self._restart = asyncio.create_task(self._replace(host, self.generation))

    def _invalidate(self, error):
        for future in self.pending.values():
            if not future.done():
                future.set_exception(error)
        self.pending.clear()
        for stream in list(self.streams.values()):
            stream.alive = False
        self.streams.clear()

    async def _replace(self, host, generation):
        if self._reader and self._reader is not asyncio.current_task():
            self._reader.cancel()
        await host.close()
        if self.failure_circuit_open or generation != self.generation:
            return
        try:
            await self._spawn()
            self.restarts += 1
        except Exception as error:
            # Safety trips on the open circuit and rebuilds or pauses the service.
            LOGGER.warning("Could not restart the speech engine (%s)", type(error).__name__)
            self.failure_circuit_open = True

    def _argv(self):
        return [
            sys.executable,
            "-m",
            "cassini_live.engine_host",
            "--engine",
            self.engine_id,
            "--model-dir",
            str(self.model_dir),
            "--threads",
            str(self.threads),
            "--max-streams",
            str(self.max_streams),
            "--max-batch",
            str(self.max_batch),
            "--segments",
            SEGMENTS,
        ]

    async def _spawn(self):
        if self.load_check and (problem := self.load_check()):
            raise CapacityError(problem)
        generation = self.generation
        env = {**os.environ, "OMP_NUM_THREADS": "1", "ORT_DISABLE_TELEMETRY": "1"}
        host = self.process_factory()
        self._starting.add(host)
        try:
            await host.start(self._argv(), env)
            message = await asyncio.wait_for(host.receive(), self.ready_timeout)
            if message.get("type") != "ready":
                raise RuntimeError("Speech engine did not become ready")
            if self.resource_check and self.resource_check():
                raise CapacityError("Speech engine exhausted the resource headroom")
        except BaseException:
            await host.close()
            raise
        finally:
            self._starting.discard(host)
        if self.closed or generation != self.generation:
            await host.close()
            return
        self.rss_mib = message.get("rssMiB")
        self.process = host
        self.ready = True
        self._reader = asyncio.create_task(self._read(host))

    async def start(self):
        if self.process is not None:
            raise RuntimeError("Close the speech engine before starting it again")
        self.closed = False
        self.admission_limit = self.size
        self.failure_times.clear()
        self.failure_circuit_open = False
        try:
            await self._spawn()
        except BaseException:
            await self.close()
            raise

    async def close(self):
        self.generation += 1
        self.closed = True
        self.ready = False
        for future in self.waiters:
            if not future.done():
                future.set_exception(CapacityError("Caption service is stopping"))
        self.waiters.clear()
        self._invalidate(CapacityError("Caption service is stopping"))
        self.leased.clear()
        tasks = [t for t in (self._reader, self._restart) if t and t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        hosts = [h for h in (self.process, *self._starting) if h is not None]
        self.process = None
        # Safety cancels a tripped monitor mid-teardown; the old host must still exit
        # before the caller loads another model, or two models share the memory budget.
        closing = asyncio.gather(*(h.close() for h in hosts), return_exceptions=True)
        try:
            await asyncio.shield(closing)
        except asyncio.CancelledError:
            await closing
            raise

    def transcriber(self, session_id, language, transcript_queue, *translation_hooks):
        from ..transcriber import StreamTranscriber

        return StreamTranscriber(
            session_id, language, transcript_queue, *translation_hooks, pool=self
        )

    def status(self):
        return {
            "capacity": self.size,
            "available": self.available,
            "admittedSpeakers": self.admission_limit,
            "workers": 1 if self.ready else 0,
            "streamFailures": self.failures,
            "capacityWaits": self.capacity_waits,
            "droppedSpeechChunks": self.dropped_chunks,
            "speechGating": True,
            "speechDetector": "silero-v6.2.1",
            "waitingSpeakers": self.waiting,
            "schedulingYields": self.scheduling_yields,
            "streamEnds": dict(self.stream_ends),
            "recentCapacityPressure": bool(
                self.last_pressure and time.monotonic() - self.last_pressure < 60
            ),
            "engine": self.engine_id,
            "openStreams": len(self.streams),
            "hostRestarts": self.restarts,
            "lagSeconds": self.lag.last(),
            "p95RequestSeconds": self.lag.p95(),
        }
