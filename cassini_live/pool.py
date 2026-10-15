"""Bounded, preloaded process pool with exclusive leases for speaker streams."""

import asyncio
import json
import logging
import os
import sys
import time
from collections import Counter, deque
from contextlib import suppress

LOGGER = logging.getLogger(__name__)


class CapacityError(RuntimeError):
    pass


class Worker:
    def __init__(self, weights=None):
        self.weights = weights
        self.process = None

    async def start(self):
        args = [sys.executable, "-m", "cassini_live.worker"]
        if self.weights:
            args.append(self.weights)
        env = os.environ.copy()
        threads = int(env.get("CASSINI_WHISTLE_THREADS", "2"))
        if not 1 <= threads <= 32:
            raise ValueError("CASSINI_WHISTLE_THREADS must be between 1 and 32")
        if shim := env.get("CASSINI_WHISTLE_CPU_SHIM"):
            if not os.path.isfile(shim):
                raise RuntimeError("Whistle CPU compatibility library is missing")
            env["LD_PRELOAD"] = shim + (" " + env["LD_PRELOAD"] if env.get("LD_PRELOAD") else "")
            env["CASSINI_WHISTLE_THREADS"] = str(threads)
        self.process = await asyncio.create_subprocess_exec(
            *args,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            # Native loading messages go to stderr, never captions.
            limit=1024 * 1024,
        )
        try:
            if (await self.receive(timeout=120))["type"] != "ready":
                raise RuntimeError("Whistle did not become ready")
        except BaseException:
            await self.close()
            raise

    async def send(self, message):
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        await asyncio.wait_for(self.process.stdin.drain(), 10)

    async def receive(self, timeout=20):
        line = await asyncio.wait_for(self.process.stdout.readline(), timeout)
        if not line:
            raise RuntimeError("Whistle worker exited")
        message = json.loads(line)
        if message["type"] == "error":
            raise RuntimeError("Whistle worker failed")
        return message

    async def close(self):
        if self.process is None or self.process.returncode is not None:
            return
        with suppress(ProcessLookupError):
            self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), 3)
        except TimeoutError:
            with suppress(ProcessLookupError):
                self.process.kill()
            await self.process.wait()


class WorkerPool:
    engine_id = "whistle"
    load_check = None

    def __init__(self, size, weights=None, worker_factory=Worker):
        self.size = size
        self.weights = weights
        self.worker_factory = worker_factory
        self.idle = asyncio.Queue(maxsize=size)
        self.workers = set()
        self.closed = False
        self.failures = 0
        self.capacity_waits = 0
        self.dropped_chunks = 0
        self.waiters = deque()
        self.scheduling_yields = 0
        self.stream_ends = Counter()
        self.chunks_processed = 0
        self.audio_seconds = 0.0
        self.finals_emitted = 0
        self.partials_emitted = 0
        self.last_pressure = 0.0
        self.admission_limit = size
        self.leased = set()
        self.failure_times = deque(maxlen=3)
        self.failure_circuit_open = False
        self.resource_check = None
        self.generation = 0

    @property
    def available(self):
        return min(self.idle.qsize(), max(0, self.admission_limit - len(self.leased)))

    @property
    def waiting(self):
        return sum(not future.done() for future in self.waiters)

    @property
    def busy(self):
        return len(self.leased)

    def is_idle(self):
        return self.idle.qsize() == self.size

    def resize(self, size):
        if not self.closed:
            raise RuntimeError("Resize the worker pool only while it is closed")
        self.size = size
        self.idle = asyncio.Queue(maxsize=size)

    def transcriber(self, session_id, language, transcript_queue, *translation_hooks):
        from .transcriber import WhistleTranscriber

        return WhistleTranscriber(
            session_id, language, transcript_queue, *translation_hooks, pool=self
        )

    def note_drop(self):
        self.dropped_chunks += 1
        self.last_pressure = time.monotonic()

    def _offer(self, worker):
        # Hand the lease directly to the oldest live waiter. A returning speaker
        # cannot steal a worker while that waiter's coroutine is waking up.
        while self.waiters and len(self.leased) < self.admission_limit:
            future = self.waiters.popleft()
            if not future.done():
                self.leased.add(worker)
                future.set_result(worker)
                return
        self.idle.put_nowait(worker)

    async def _new(self):
        if self.resource_check and self.resource_check():
            raise CapacityError("Insufficient resource headroom for a recognition worker")
        generation = self.generation
        worker = self.worker_factory(self.weights)
        self.workers.add(worker)
        try:
            await worker.start()
            if self.resource_check and self.resource_check():
                await worker.close()
                raise CapacityError("Recognition worker exhausted the resource headroom")
        except BaseException:
            await worker.close()
            self.workers.discard(worker)
            raise
        if self.closed or generation != self.generation:
            await worker.close()
            self.workers.discard(worker)
            return
        self._offer(worker)

    async def start(self):
        if self.workers:
            raise RuntimeError("Close the existing worker pool before starting it again")
        self.closed = False
        self.admission_limit = self.size
        self.failure_times.clear()
        self.failure_circuit_open = False
        try:
            # Sequential preload avoids competing model downloads in development.
            for _ in range(self.size):
                await self._new()
        except BaseException:
            await self.close()
            raise

    def acquire(self):
        if self.closed:
            raise CapacityError("Caption service is stopping")
        if not self.available:
            raise CapacityError("Measured caption capacity reached")
        try:
            worker = self.idle.get_nowait()
            self.leased.add(worker)
            return worker
        except asyncio.QueueEmpty:
            raise CapacityError(
                "Caption capacity reached; try again after another call ends"
            ) from None

    async def release(self, worker, healthy=True):
        if worker not in self.workers or worker not in self.leased:
            return
        self.leased.discard(worker)
        if self.closed:
            return
        if healthy and worker.process.returncode is None:
            self._offer(worker)
            return
        generation = self.generation
        self.failures += 1
        await worker.close()
        if generation != self.generation:
            return
        self.workers.discard(worker)
        self.failure_times.append(time.monotonic())
        if len(self.failure_times) == 3 and self.failure_times[-1] - self.failure_times[0] < 60:
            self.failure_circuit_open = True
            return
        try:
            await self._new()
        except Exception:
            LOGGER.exception("Could not replace Whistle worker")

    async def acquire_wait(self, timeout=2):
        if self.closed:
            raise CapacityError("Caption service is stopping")
        if self.available and not self.waiting:
            return self.acquire()
        future = asyncio.get_running_loop().create_future()
        self.waiters.append(future)
        acquired = False
        try:
            worker = await asyncio.wait_for(asyncio.shield(future), timeout)
            acquired = True
            return worker
        except TimeoutError:
            raise CapacityError("All caption workers are speaking") from None
        finally:
            # A timeout/cancellation can race with a direct hand-off. Return a
            # granted lease unless this coroutine actually returned it.
            if not future.done():
                future.cancel()
            elif not acquired and not future.cancelled() and future.exception() is None:
                if not self.closed and future.result() in self.workers:
                    self.leased.discard(future.result())
                    self._offer(future.result())
            if future in self.waiters:
                self.waiters.remove(future)

    async def close(self):
        self.generation += 1
        self.closed = True
        for future in self.waiters:
            if not future.done():
                future.set_exception(CapacityError("Caption service is stopping"))
        self.waiters.clear()
        await asyncio.gather(*(w.close() for w in list(self.workers)), return_exceptions=True)
        self.workers.clear()
        self.leased.clear()
        while not self.idle.empty():
            self.idle.get_nowait()

    def status(self):
        return {
            "capacity": self.size,
            "available": self.available,
            "admittedSpeakers": self.admission_limit,
            "workers": len(self.workers),
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
        }
