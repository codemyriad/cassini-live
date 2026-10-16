"""Tiny model archives and a scriptable HTTP opener for the model store tests."""

import dataclasses
import hashlib
import io
import tarfile
import urllib.error
from email.message import Message

from cassini_live import catalog

DIRECTORY = "tiny-model"
FILES = {
    "encoder.int8.onnx": b"encoder" * 300,
    "decoder.int8.onnx": b"decoder" * 50,
    "joiner.int8.onnx": b"joiner" * 20,
    "tokens.txt": b"<blk> 0\na 1\n",
}


def member(name, data=b"", kind=tarfile.REGTYPE, linkname=""):
    info = tarfile.TarInfo(name)
    info.type, info.linkname = kind, linkname
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    return info, data


def make_archive(files=None, extra_members=(), directory=DIRECTORY, skip=()) -> bytes:
    """A .tar.bz2 with `directory/<file>` members, then any (TarInfo, data) extras."""
    files = FILES if files is None else files
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:bz2") as tar:
        info, data = member(f"{directory}/test_wavs/0.wav", b"RIFF" * 10)
        tar.addfile(info, io.BytesIO(data))
        for name, data in files.items():
            if name not in skip:
                info, data = member(f"{directory}/{name}", data)
                tar.addfile(info, io.BytesIO(data))
        for info, data in extra_members:
            tar.addfile(info, io.BytesIO(data) if info.isfile() else None)
    return out.getvalue()


def fake_catalog(archive: bytes, files=None, *, archive_bytes=None, sha=None, size=None,
                 archive_sha=None):
    """The real catalog with tiny pinned files for both Nemotron engines."""
    files = FILES if files is None else files
    sha, size = sha or {}, size or {}
    engines = dict(catalog.ENGINES)
    for engine_id in ("nemotron-en", "nemotron-multilingual"):
        name = f"{engine_id}.tar.bz2"
        asset = catalog.ModelAsset(
            archive=name,
            url=f"https://example.test/{name}",
            archive_bytes=len(archive) if archive_bytes is None else archive_bytes,
            archive_sha256=archive_sha or hashlib.sha256(archive).hexdigest(),
            directory=DIRECTORY,
            files=tuple(
                catalog.ModelFile(
                    n,
                    sha.get(n, hashlib.sha256(d).hexdigest()),
                    size.get(n, len(d)),
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
    """Serves one archive with Content-Length, Range and If-Range, like GitHub's CDN."""

    def __init__(self, archive: bytes, *, etag='"v1"', chunk=97, status=None,
                 content_length=None, ignore_range=False, stall=None, on_read=None, gate=None):
        self.archive, self.etag, self.chunk = archive, etag, chunk
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
        rng, if_range = request.get_header("Range"), request.get_header("If-range")
        if rng and not self.ignore_range and if_range == self.etag:
            start = int(rng.removeprefix("bytes=").rstrip("-"))
            body = self.archive[start:]
            headers = {"Content-Range": f"bytes {start}-{len(self.archive) - 1}/"
                       f"{self.content_length or len(self.archive)}",
                       "Content-Length": len(body), "ETag": self.etag}
            return FakeResponse(206, body, headers, self.chunk, self.on_read, self.stall)
        headers = {"Content-Length": self.content_length or len(self.archive), "ETag": self.etag}
        return FakeResponse(self.status or 200, self.archive, headers, self.chunk,
                            self.on_read, self.stall)


class Disk:
    def __init__(self, free=10**12):
        self.free, self.total, self.used = free, 10**12, 0

    def __call__(self, path):
        return self

