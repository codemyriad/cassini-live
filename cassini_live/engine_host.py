"""Shared Nemotron engine host: one recognizer, one continuous stream per speaker.

Private JSON-lines protocol on stdin/stdout (engine-selection.md 6.5). Exactly one
terminal reply per request; a `cancel` also terminates the stream's outstanding
request. stderr carries native logging only, never text or audio. This is the only
module that imports sherpa_onnx, and only inside main().
"""

import argparse
import base64
import json
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass

import numpy as np

RATE = 16000
COMMIT_PAD_S = 0.56
FINISH_PAD_S = 1.2
ROTATE_SAMPLES = 30 * 60 * RATE
CASSINI_LANGUAGES = ("en", "de", "fr", "es", "it", "nl", "pl")
# The option form sherpa-onnx 1.13.8 accepts; tests/test_engine_integration.py checks it.
LANGUAGE_OPTION = {code: code for code in CASSINI_LANGUAGES}
TAG = re.compile(r"<([a-z]{2,3})(?:[-_][A-Za-z0-9]{2,4})?>")
MODEL_FILES = ("tokens.txt", "encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx")


@dataclass(frozen=True)
class Options:
    engine: str
    multilingual: bool
    max_streams: int = 12
    max_batch: int = 4
    segments: str = "continuous"  # or "per_segment"


class StreamError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class _Stream:
    def __init__(self, native, language, order):
        self.native = native
        self.language = language  # option sent to the recognizer
        self.detected = None  # last automatic language tag, as a Cassini code
        self.committed = 0
        self.samples = 0
        self.request = None
        self.order = order
        # Measurement only: when the outstanding request arrived and when its first
        # decode batch began, so a result reports queueing apart from decoding.
        self.received = None
        self.started = None


def clean(text):
    """Strip locale tags; return the text and the last tag's language code, if any."""
    tags = TAG.findall(text)
    return " ".join(TAG.sub(" ", text).split()), tags[-1] if tags else None


class Host:
    def __init__(self, recognizer, options, write):
        self.recognizer = recognizer
        self.options = options
        self.write = write
        self.streams = {}
        self.order = 0

    @property
    def busy(self):
        return any(s.request for s in self.streams.values())

    def _native(self, language):
        native = self.recognizer.create_stream()
        if self.options.multilingual:
            native.set_option("language", language)
        return native

    def _accept(self, stream, samples):
        stream.native.accept_waveform(RATE, samples)
        stream.samples += len(samples)

    def _pad(self, stream, seconds, finished):
        stream.native.accept_waveform(RATE, np.zeros(int(seconds * RATE), dtype="<f4"))
        if finished:
            stream.native.input_finished()

    def handle(self, message):
        kind, sid = message.get("type"), message.get("id")
        if not isinstance(sid, int) or isinstance(sid, bool):
            raise ValueError("Request without a stream id")
        try:
            self._handle(kind, sid, message)
        except StreamError as error:
            self.streams.pop(sid, None)
            self.write({"type": "error", "id": sid, "code": error.code, "message": str(error)})

    def _handle(self, kind, sid, message):
        if kind == "open":
            if sid in self.streams:
                raise StreamError("protocol", "Stream is already open")
            if len(self.streams) >= self.options.max_streams:
                raise StreamError("limit", "Too many open streams")
            language = message.get("language", "auto")
            if self.options.multilingual and language != "auto":
                if language not in LANGUAGE_OPTION.values():
                    raise StreamError("language", "Unsupported language")
            self.streams[sid] = _Stream(self._native(language), language, self.order)
            self.order += 1
            self.write({"type": "opened", "id": sid})
            return
        stream = self.streams.get(sid)
        if stream is None:
            raise StreamError("protocol", "Unknown stream")
        if kind == "cancel":
            # Supersedes any outstanding request on this stream.
            del self.streams[sid]
            self.write({"type": "closed", "id": sid})
            return
        if stream.request is not None:
            raise StreamError("protocol", "A request is already outstanding on this stream")
        if kind == "audio":
            raw = base64.b64decode(message.get("pcm", ""), validate=True)
            if not raw or len(raw) % 4 or len(raw) > 4 * RATE:
                raise StreamError("protocol", "Expected 1 to 16000 float32 samples")
            self._accept(stream, np.frombuffer(raw, dtype="<f4"))
        elif kind == "commit":
            per_segment = self.options.segments == "per_segment"
            self._pad(stream, FINISH_PAD_S if per_segment else COMMIT_PAD_S, per_segment)
        elif kind == "finish":
            self._pad(stream, FINISH_PAD_S, True)
        else:
            raise StreamError("protocol", "Unknown request")
        stream.request = kind
        stream.order = self.order
        self.order += 1
        stream.received, stream.started = time.monotonic(), None

    def step(self):
        ready = sorted(
            (s for s in self.streams.values() if s.request and self.recognizer.is_ready(s.native)),
            key=lambda s: s.order,
        )
        if ready:
            batch = ready[: self.options.max_batch]
            now = time.monotonic()
            for stream in batch:
                if stream.started is None:
                    stream.started = now
            self.recognizer.decode_streams([s.native for s in batch])
        done = None
        for sid, stream in list(self.streams.items()):
            if stream.request and not self.recognizer.is_ready(stream.native):
                done = time.monotonic() if done is None else done
                self._complete(sid, stream, done)

    def _complete(self, sid, stream, done):
        kind, stream.request = stream.request, None
        # A request that never needed a batch (nothing left to decode) waited the whole time.
        started = done if stream.started is None else stream.started
        timing = {
            "waitMs": round((started - stream.received) * 1000),
            "decodeMs": round((done - started) * 1000),
        }
        text, tag = clean(self.recognizer.get_result(stream.native))
        if tag in CASSINI_LANGUAGES:
            stream.detected = tag
        new = text[stream.committed :].strip()
        result = {"text": "", "pending": new} if kind == "audio" else {"text": new, "pending": ""}
        if stream.detected and stream.language == "auto":
            result["language"] = stream.detected
        if kind == "finish":
            del self.streams[sid]
        elif kind == "commit":
            stream.committed = len(text)
            if self.options.segments == "per_segment" or stream.samples >= ROTATE_SAMPLES:
                # Committed text is already delivered: a fresh stream bounds memory.
                stream.native = self._native(stream.language)
                stream.committed = stream.samples = 0
        self.write({"type": "result", "id": sid, "result": result, **timing})


def serve(stdin, stdout, recognizer, options):
    """Serve requests until quit or EOF. Returns the process exit code."""
    lock = threading.Lock()

    def write(message):
        with lock:
            stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
            stdout.flush()

    inbox = queue.SimpleQueue()

    def read():
        try:
            for line in stdin:
                inbox.put(line)
        finally:
            inbox.put(None)

    threading.Thread(target=read, daemon=True).start()
    host = Host(recognizer, options, write)
    ended = False
    try:
        while True:
            lines = []
            if not ended:
                try:
                    lines.append(inbox.get(block=not host.busy))
                    while True:
                        lines.append(inbox.get_nowait())
                except queue.Empty:
                    pass
            for line in lines:
                if line is None:
                    ended = True
                    break
                if not line.strip():
                    continue
                message = json.loads(line)
                if message.get("type") == "quit":
                    return 0
                host.handle(message)
            if ended and not host.busy:
                return 0
            host.step()
    except Exception as error:
        # Errors contain no audio or transcript data. The supervisor restarts the host.
        write({"type": "error", "message": type(error).__name__})
        return 1


def rss_mib():
    try:
        with open("/proc/self/status") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m cassini_live.engine_host")
    parser.add_argument("--engine", required=True, choices=("nemotron-en", "nemotron-multilingual"))
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--threads", type=int, default=1, choices=range(1, 5))
    parser.add_argument("--max-streams", type=int, default=12)
    parser.add_argument("--max-batch", type=int, default=4)
    parser.add_argument("--segments", choices=("continuous", "per_segment"), default="continuous")
    args = parser.parse_args(argv)
    # Native code and stray prints must never write into the protocol: fd 1 goes to stderr.
    out = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    try:
        # Prefer sacrificing recognition to killing the lightweight supervisor.
        try:
            with open("/proc/self/oom_score_adj", "w") as handle:
                handle.write("1000")
        except OSError:
            pass
        import sherpa_onnx

        paths = [os.path.join(args.model_dir, name) for name in MODEL_FILES]
        recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=paths[0],
            encoder=paths[1],
            decoder=paths[2],
            joiner=paths[3],
            num_threads=args.threads,
        )
        # A cold load charges the model's page cache to the container's memory.
        for path in paths[1:]:
            try:
                fd = os.open(path, os.O_RDONLY)
                try:
                    os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
                finally:
                    os.close(fd)
            except (OSError, AttributeError):
                pass
        multilingual = args.engine == "nemotron-multilingual"
        languages = list(CASSINI_LANGUAGES) if multilingual else ["en"]
        message = {
            "type": "ready",
            "engine": args.engine,
            "languages": languages,
            "chunkMs": 560,
            "rssMiB": rss_mib(),
        }
        out.write(json.dumps(message) + "\n")
        out.flush()
    except Exception as error:
        out.write(json.dumps({"type": "error", "message": type(error).__name__}) + "\n")
        out.flush()
        return 1
    options = Options(args.engine, multilingual, args.max_streams, args.max_batch, args.segments)
    return serve(sys.stdin, out, recognizer, options)


if __name__ == "__main__":
    sys.exit(main())
