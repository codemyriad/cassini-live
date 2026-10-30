"""Tiny .zst model objects and a scriptable HTTP opener for the model store tests."""

import dataclasses
import hashlib
import urllib.error
from email.message import Message

import zstandard

from cassini_live import catalog

FILES = {
    "encoder.int8.onnx": b"encoder" * 300,
    "decoder.int8.onnx": b"decoder" * 50,
    "joiner.int8.onnx": b"joiner" * 20,
    "tokens.txt": b"<blk> 0\na 1\n",
}


def compress(data: bytes, frame=256) -> bytes:
    """Independent frames, like the dist objects (there each holds up to 256 KiB)."""
    out = zstandard.ZstdCompressor(level=3)
    return b"".join(out.compress(data[i:i + frame]) for i in range(0, len(data), frame))


def make_objects(files=None) -> dict[str, bytes]:
    files = FILES if files is None else files
    return {name: compress(data) for name, data in files.items()}


def fake_catalog(objects: dict[str, bytes], files=None, *, sha=None, size=None, zst_sha=None,
                 zst_bytes=None):
    """The real catalog with tiny pinned files for both Nemotron engines."""
    files = FILES if files is None else files
    sha, size, zst_sha, zst_bytes = sha or {}, size or {}, zst_sha or {}, zst_bytes or {}
    engines = dict(catalog.ENGINES)
    for engine_id in ("nemotron-en", "nemotron-multilingual"):
        asset = catalog.ModelAsset(
            model=engine_id,
            files=tuple(
                catalog.ModelFile(
                    n,
                    sha.get(n, hashlib.sha256(d).hexdigest()),
                    size.get(n, len(d)),
                    zst_sha.get(n, hashlib.sha256(objects[n]).hexdigest()),
                    zst_bytes.get(n, len(objects[n])),
                )
                for n, d in files.items()
            ),
        )
        engines[engine_id] = dataclasses.replace(engines[engine_id], asset=asset)
    return engines


class FakeResponse:
    def __init__(self, status, body, headers, chunk, on_read=None, stall=None):
        self.status, self.body, self.chunk, self.on_read, self.stall = (
            status, body, chunk, on_read, stall
        )
        self.headers = Message()
        for key, value in headers.items():
            if value is not None:
                self.headers[key] = str(value)
        self.position = 0

    def getcode(self):
        return self.status

    def read(self, size=-1):
        if self.stall is not None and self.position >= self.stall:
            raise TimeoutError("read timed out")
        if self.on_read:
            self.on_read(self)
        size = self.chunk if size < 0 else min(size, self.chunk)
        if self.stall is not None:
            size = min(size, self.stall - self.position) or size
        block = self.body[self.position:self.position + size]
        self.position += len(block)
        return block

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Serves .zst objects by file name with Content-Length, Range and If-Range, like the
    dist Worker."""

    def __init__(self, objects: dict[str, bytes], *, etag='"v1"', chunk=97, status=None,
                 content_length=None, ignore_range=False, stall=None, on_read=None, gate=None):
        self.objects, self.etag, self.chunk = objects, etag, chunk
        self.status, self.content_length, self.ignore_range = status, content_length, ignore_range
        self.stall, self.on_read, self.gate = stall, on_read, gate
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        assert timeout is not None
        if self.gate is not None:
            self.gate.wait(5)
        if self.status and self.status >= 400:
            raise urllib.error.HTTPError(request.full_url, self.status, "error", Message(), None)
        if request.get_method() == "HEAD":
            return FakeResponse(200, b"", {}, self.chunk)
        body = self.objects[request.full_url.rsplit("/", 1)[1].removesuffix(".zst")]
        length = self.content_length or len(body)
        rng, if_range = request.get_header("Range"), request.get_header("If-range")
        if rng and not self.ignore_range and if_range == self.etag:
            start = int(rng.removeprefix("bytes=").rstrip("-"))
            headers = {"Content-Range": f"bytes {start}-{len(body) - 1}/{length}",
                       "Content-Length": len(body) - start, "ETag": self.etag}
            return FakeResponse(206, body[start:], headers, self.chunk, self.on_read, self.stall)
        headers = {"Content-Length": length, "ETag": self.etag}
        return FakeResponse(self.status or 200, body, headers, self.chunk,
                            self.on_read, self.stall)


class Disk:
    def __init__(self, free=10**12):
        self.free, self.total, self.used = free, 10**12, 0

    def __call__(self, path):
        return self

