"""Install pinned, seekable Zstandard objects from the Cassini distribution."""

import fcntl
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

import zstandard

DIST_URL = "https://dist.gocassini.com/models/files/"
BLOCK = 1024 * 1024


@dataclass(frozen=True)
class Asset:
    name: str
    sha256: str
    size: int
    zst_sha256: str
    zst_size: int


def file_sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def download_cache(target):
    return Path(os.environ.get("CASSINI_BUILD_MODEL_CACHE") or target / ".downloads")


def _download(asset, compressed):
    done = compressed.stat().st_size if compressed.exists() else 0
    if done > asset.zst_size:
        compressed.unlink()
        done = 0
    if done == asset.zst_size:
        return

    # The compressed hash is in the immutable URL, so it also identifies the partial.
    headers = {"Accept-Encoding": "identity", "User-Agent": "cassini-live-build/1.0"}
    if done:
        headers["Range"] = f"bytes={done}-"
        headers["If-Range"] = f'"{asset.zst_sha256}"'
    url = f"{DIST_URL}{asset.zst_sha256}/{asset.name}.zst"
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        if response.status == 206 and done:
            expected = f"bytes {done}-{asset.zst_size - 1}/{asset.zst_size}"
            if response.headers.get("Content-Range") != expected:
                raise RuntimeError(f"Invalid resume range for {asset.name}")
        elif response.status == 200:
            done = 0
        else:
            raise RuntimeError(f"Unexpected download status for {asset.name}: {response.status}")
        if response.headers.get("Content-Length") != str(asset.zst_size - done):
            raise RuntimeError(f"Unexpected download size for {asset.name}")
        with compressed.open("ab" if done else "wb") as out:
            while block := response.read(BLOCK):
                out.write(block)
                done += len(block)
    if done != asset.zst_size:
        raise RuntimeError(f"Download ended early for {asset.name}; run again to resume")


def install(asset, target, cache):
    if target.is_file() and target.stat().st_size == asset.size:
        if file_sha256(target) == asset.sha256:
            return
    cache.mkdir(parents=True, exist_ok=True)
    compressed = cache / f"{asset.zst_sha256}.zst"
    with compressed.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _download(asset, compressed)
        if file_sha256(compressed) != asset.zst_sha256:
            compressed.unlink()
            raise RuntimeError(f"Compressed checksum mismatch for {asset.name}")

        with tempfile.TemporaryDirectory(dir=target.parent) as staging:
            decoded = Path(staging) / asset.name
            digest = hashlib.sha256()
            size = 0
            decoder = zstandard.ZstdDecompressor()
            with (
                compressed.open("rb") as raw,
                decoder.stream_reader(raw, read_across_frames=True) as source,
                decoded.open("wb") as out,
            ):
                while block := source.read(BLOCK):
                    size += len(block)
                    if size > asset.size:
                        raise RuntimeError(f"Decoded size exceeds pin for {asset.name}")
                    digest.update(block)
                    out.write(block)
            if size != asset.size or digest.hexdigest() != asset.sha256:
                raise RuntimeError(f"Decoded checksum mismatch for {asset.name}")
            decoded.replace(target)
