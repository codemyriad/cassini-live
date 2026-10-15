"""Engine fakes: no model, no sherpa_onnx, no subprocess (engine-selection.md 12.1)."""

import asyncio
import json
import queue
import threading
from collections import Counter
from contextlib import suppress

import numpy as np

from cassini_live import catalog, engine_host
from cassini_live.pool import CapacityError

CHUNK = 8960  # 0.56 s at 16 kHz


class FakeNativeStream:
    def __init__(self):
        self.samples = np.empty(0, dtype="<f4")
        self.decoded = 0
        self.words = []
        self.finished = False
        self.options = {}

    def accept_waveform(self, rate, samples):
        assert rate == 16000
        self.samples = np.concatenate((self.samples, np.asarray(samples, dtype="<f4")))

    def input_finished(self):
        self.finished = True

    def set_option(self, key, value):
        self.options[key] = value


class FakeRecognizer:
    """One word per 0.56 s of non-zero audio; an <de-DE> tag on automatic-language streams."""

    def __init__(self, fail_decode=False):
        self.streams = []
        self.batches = []
        self.fail_decode = fail_decode

    def create_stream(self):
        stream = FakeNativeStream()
        self.streams.append(stream)
        return stream

    def is_ready(self, stream):
        left = len(stream.samples) - stream.decoded
        return left >= CHUNK or (stream.finished and left > 0)

    def decode_streams(self, streams):
        if self.fail_decode:
            raise RuntimeError("native failure")
        self.batches.append(len(streams))
        for stream in streams:
            chunk = stream.samples[stream.decoded : stream.decoded + CHUNK]
            stream.decoded += len(chunk)
            if np.any(chunk):
                stream.words.append(f"w{len(stream.words) + 1}")

    def get_result(self, stream):
        text = " ".join(stream.words)
        if text and stream.options.get("language") == "auto":
            text += " <de-DE>"
        return text


def pcm(samples):
    import base64

    return base64.b64encode(np.asarray(samples, dtype="<f4").tobytes()).decode()


class HostHarness:
    """engine_host.serve in a thread, with blocking request/reply helpers."""

    def __init__(self, recognizer=None, **options):
        self.recognizer = recognizer or FakeRecognizer()
        options.setdefault("engine", "nemotron-multilingual")
        options.setdefault("multilingual", options["engine"] == "nemotron-multilingual")
        self.options = engine_host.Options(**options)
        self.requests = queue.Queue()
        self.replies = queue.Queue()
        self.exit_code = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        self.exit_code = engine_host.serve(
            iter(self.requests.get, None), self, self.recognizer, self.options
        )
        self.replies.put(None)

    def write(self, line):
        self.replies.put(json.loads(line))

    def flush(self):
        pass

    def send(self, message):
        self.requests.put(json.dumps(message) + "\n")

    def receive(self, timeout=2):
        return self.replies.get(timeout=timeout)

    def call(self, message):
        self.send(message)
        return self.receive()

    def close(self):
        self.requests.put(None)
        self.thread.join(2)


class FakeHostProcess:
    """EngineHostProcess replacement running engine_host.serve in-process.

    `ready=False` never becomes ready; `mute=True` swallows every reply after ready.
    """

    def __init__(self, recognizer=None, *, ready=True, ready_type="ready", mute=False):
        self.recognizer = recognizer or FakeRecognizer()
        self.ready = ready
        self.ready_type = ready_type
        self.mute = mute
        self.returncode = None
        self.argv = None
        self.env = None
        self.sent = []
        self.closed = False
        self._out = None
        self._in = None

    async def start(self, argv, env):
        self.argv, self.env = argv, env
        loop = asyncio.get_running_loop()
        self._out = asyncio.Queue()
        self._in = queue.Queue()
        args = dict(zip(argv[3::2], argv[4::2], strict=True))
        engine = args["--engine"]
        options = engine_host.Options(
            engine,
            engine == "nemotron-multilingual",
            int(args["--max-streams"]),
            int(args["--max-batch"]),
            args["--segments"],
        )
        host = self

        def post(*args):
            with suppress(RuntimeError):  # the test's loop may already be closed
                loop.call_soon_threadsafe(*args)

        class Out:
            def write(self, line):
                if not host.mute:
                    post(host._out.put_nowait, json.loads(line))

            def flush(self):
                pass

        def run():
            code = engine_host.serve(iter(self._in.get, None), Out(), self.recognizer, options)
            post(self._exited, code)

        if self.ready:
            self._out.put_nowait({"type": self.ready_type, "engine": engine, "rssMiB": 900})
        threading.Thread(target=run, daemon=True).start()

    def _exited(self, code):
        if self.returncode is None:
            self.returncode = code
        self._out.put_nowait(None)

    async def send(self, message):
        if self.returncode is not None:
            raise BrokenPipeError
        self.sent.append(message)
        self._in.put(json.dumps(message) + "\n")

    async def receive(self):
        message = await self._out.get()
        if message is None:
            self._out.put_nowait(None)
            raise EOFError
        return message

    def crash(self):
        """The host dies: EOF on stdout."""
        self.returncode = -9
        self._in.put(None)
        self._out.put_nowait(None)

    async def close(self):
        self.closed = True
        if self._in is not None and self.returncode is None:
            self.returncode = 0
            self._in.put(None)
            self._out.put_nowait(None)


class FakeEngineStream:
    def __init__(self, backend, language):
        self.backend = backend
        self.language = language
        self.alive = True
        self.calls = []
        self.words = 0
        self.pending_words = 0

    def _result(self):
        result = {"text": "", "pending": " ".join(["w"] * self.pending_words)}
        if self.backend.detected:
            result["language"] = self.backend.detected
        return result

    async def audio(self, samples):
        self.calls.append(("audio", len(samples)))
        if self.backend.audio_gate is not None:
            await self.backend.audio_gate.wait()
        if self.backend.stream_error:
            self.alive = False
            raise self.backend.stream_error
        self.pending_words += 1
        return self._result()

    async def commit(self):
        self.calls.append(("commit",))
        result = self._result()
        result["text"], result["pending"] = result["pending"], ""
        self.pending_words = 0
        return result

    async def finish(self):
        self.calls.append(("finish",))
        result = await self.commit()
        self.calls.pop()
        self.alive = False
        return result

    async def cancel(self):
        self.calls.append(("cancel",))
        self.alive = False


class FakeBackend:
    """Backend protocol without any process. Slots are immediate or a CapacityError."""

    def __init__(self, engine_id="nemotron-en", size=2, *, start_error=None, start_gate=None):
        self.engine_id = engine_id
        self.size = size
        self.admission_limit = size
        self.closed = True
        self.started = False
        self.resource_check = None
        self.load_check = None
        self.failure_circuit_open = False
        self.failures = self.capacity_waits = self.dropped_chunks = 0
        self.scheduling_yields = 0
        self.last_pressure = 0.0
        self.stream_ends = Counter()
        self.chunks_processed = 0
        self.audio_seconds = 0.0
        self.finals_emitted = self.partials_emitted = 0
        self.leased = set()
        self.start_error = start_error
        self.start_gate = start_gate
        self.starts = self.closes = self.generation = 0
        self._closing = None
        self.streams = []
        self.detected = None
        self.stream_error = None
        self.audio_gate = None
        self.waiters = 0

    @property
    def workers(self):
        return self.leased

    @property
    def waiting(self):
        return self.waiters

    @property
    def busy(self):
        return len(self.leased)

    def is_idle(self):
        return self.started and not self.leased

    def resize(self, size):
        if not self.closed:
            raise RuntimeError("Resize only while closed")
        self.size = size

    def note_drop(self):
        self.dropped_chunks += 1

    async def start(self):
        self.starts += 1
        if self.load_check and (problem := self.load_check()):
            raise CapacityError(problem)
        if self.start_gate is not None:
            # Like a real host load, a close() during start wins.
            generation = self.generation
            self._closing = asyncio.Event()
            gate = asyncio.ensure_future(self.start_gate.wait())
            closing = asyncio.ensure_future(self._closing.wait())
            try:
                await asyncio.wait((gate, closing), return_when=asyncio.FIRST_COMPLETED)
            finally:
                gate.cancel()
                closing.cancel()
            if generation != self.generation:
                raise CapacityError("Caption service is stopping")
        if self.start_error is not None:
            raise self.start_error
        self.closed = False
        self.started = True
        self.admission_limit = self.size
        if self.resource_check and self.resource_check():
            await self.close()
            raise CapacityError("Speech engine exhausted the resource headroom")

    async def close(self):
        self.closes += 1
        self.generation += 1
        if self._closing is not None:
            self._closing.set()
        self.closed = True
        self.started = False
        self.leased.clear()

    async def acquire_slot(self, timeout=2):
        from cassini_live.engines.shared import Slot

        if self.closed:
            raise CapacityError("Caption service is stopping")
        if len(self.leased) >= self.admission_limit:
            self.waiters += 1
            try:
                await asyncio.sleep(timeout)
            finally:
                self.waiters -= 1
            raise CapacityError("All speaking slots are in use")
        slot = Slot(len(self.leased) + 1)
        self.leased.add(slot)
        return slot

    def release_slot(self, slot):
        self.leased.discard(slot)

    async def open_stream(self, language):
        stream = FakeEngineStream(self, language)
        self.streams.append(stream)
        return stream

    def transcriber(self, session_id, language, transcript_queue, *hooks):
        from cassini_live.transcriber import StreamTranscriber

        return StreamTranscriber(session_id, language, transcript_queue, *hooks, pool=self)

    def status(self):
        return {
            "capacity": self.size,
            "available": max(0, self.admission_limit - self.busy),
            "admittedSpeakers": self.admission_limit,
            "workers": int(self.started),
            "engine": self.engine_id,
        }


def nemotron(engine_id="nemotron-en"):
    return catalog.get(engine_id)
