"""On-demand model download, verification and atomic install.

Each model file is one Zstandard object on dist.gocassini.com. Nothing is trusted until its
pinned hash matches the catalog: the compressed object's before it is decoded, then the
decoded file's. Objects download into .partial/<engine>/ and resume with Range/If-Range,
decoding streams with bounded memory, and the final directory is replaced with one rename,
so a crash leaves either the old install or none.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import secrets
import shutil
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import zstandard

from . import __version__, catalog

REPO_ROOT = Path(__file__).resolve().parent.parent
BLOCK = 1024 * 1024
GIB = 1024**3
# Page cache counts toward the container's memory.current, which the 80% safety trip reads.
CACHE_DROP = 64 * BLOCK
# While calls have captions, hashing and decoding sleep this many times their CPU time, so
# they take about a quarter of a core from the shared quota instead of a whole one.
PACE_SLEEP = 3.0
log = logging.getLogger(__name__)

ModelStateName = Literal["bundled", "absent", "partial", "downloading", "verifying",
                         "installing", "ready", "corrupt", "error"]


@dataclass
class Progress:
    engine: str
    phase: Literal["downloading", "extracting", "verifying", "installing"]
    bytes_done: int = 0
    bytes_total: int = 0
    started_at: float = 0.0


@dataclass(frozen=True)
class ModelState:
    engine: str
    state: ModelStateName
    path: Path | None
    partial_bytes: int = 0
    error: str | None = None


class ModelError(RuntimeError):
    """code: disk_space | network | size_mismatch | hash_mismatch | cancelled | busy | storage"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _drop_cache(f, sync=False):
    """Release a model file's cached pages so a download cannot trip the memory guard."""
    with contextlib.suppress(OSError, AttributeError, ValueError):
        if sync:
            f.flush()
            os.fdatasync(f.fileno())
        os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)


class _Pacer:
    """Called after each block from the thread doing the work; measures that thread's CPU."""

    def __init__(self, busy=None):
        self.busy, self.mark = busy, time.thread_time()

    def __call__(self):
        used = time.thread_time() - self.mark
        if used < 0.05:
            return
        if self.busy is not None and self.busy():
            time.sleep(PACE_SLEEP * used)
        self.mark = time.thread_time()


def _hash_file(path: Path, cancelled=None, pace=None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        read = 0
        while block := f.read(BLOCK):
            if cancelled is not None and cancelled.is_set():
                raise ModelError("cancelled", "The model check was cancelled")
            if pace is not None:
                pace()
            digest.update(block)
            read += len(block)
            if read % CACHE_DROP < BLOCK:
                _drop_cache(f)
        _drop_cache(f)
    return digest.hexdigest()


def _fsync_dir(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _gib(value: int) -> str:
    return f"{value / GIB:.1f}"


class _PacedReader:
    """A verified .zst object as the decompressor reads it: paced, checked for cancel."""

    def __init__(self, f, cancelled, pace):
        self.f, self.cancelled, self.pace = f, cancelled, pace
        self.uncached = 0

    def read(self, size=-1):
        if self.cancelled.is_set():
            raise ModelError("cancelled", "The model download was cancelled")
        self.pace()
        data = self.f.read(BLOCK if size is None or size < 0 or size > BLOCK else size)
        self.uncached += len(data)
        if self.uncached >= CACHE_DROP or not data:
            self.uncached = 0
            _drop_cache(self.f)
        return data


class ModelStore:
    def __init__(self, root: Path | None, *, engines=catalog.ENGINES,
                 opener=urllib.request.urlopen, disk_usage=shutil.disk_usage,
                 clock=time.time, license_root: Path = REPO_ROOT, calls_active=None):
        self.engines, self.opener, self.disk_usage, self.clock = engines, opener, disk_usage, clock
        self.calls_active = calls_active  # hashing and decoding slow down while True
        self.license_root = Path(license_root)
        self.root = None
        self._busy: str | None = None
        self._progress: Progress | None = None
        self._cancel = threading.Event()
        self._discard = False
        self._errors: dict[str, str] = {}
        if root is not None:
            root = Path(root).absolute()
            if root.is_symlink():
                log.warning("Model storage %s is a symlink; downloads are disabled", root)
            else:
                self.root = root.resolve()
                self._cleanup()

    def _cleanup(self):
        try:
            leftovers = [p for p in self.root.iterdir() if p.is_dir() and not p.is_symlink()
                         and p.name.startswith((".staging-", ".old-"))]
        except OSError:
            return
        for path in leftovers:
            shutil.rmtree(path, ignore_errors=True)
        # Archives and sidecars left by the GitHub .tar.bz2 downloads of 0.8.2 and earlier.
        with contextlib.suppress(OSError):
            for path in (self.root / ".partial").glob("*.tar.bz2*"):
                if path.is_file():
                    path.unlink()

    def _engine(self, engine_id: str) -> catalog.Engine:
        return self.engines[engine_id]

    def model_dir(self, engine_id: str) -> Path:
        if self._engine(engine_id).asset is None:
            return self._bundled_dir()
        if self.root is None:
            raise ModelError("storage", "Persistent app storage is unavailable")
        return self.root / engine_id

    @staticmethod
    def _bundled_dir() -> Path:
        return Path(os.environ.get("CASSINI_LIVE_WEIGHTS", "/opt/whistle/whistle.cact")).parent

    def _partial_dir(self, engine_id: str) -> Path:
        return self.root / ".partial" / engine_id

    def _partial(self, engine_id: str, f: catalog.ModelFile) -> tuple[Path, Path]:
        partial = self._partial_dir(engine_id) / f"{f.name}.zst"
        return partial, partial.with_name(partial.name + ".json")

    def _partial_bytes(self, engine: catalog.Engine) -> int:
        total = 0
        for f in engine.asset.files:
            with contextlib.suppress(OSError):
                total += self._partial(engine.id, f)[0].stat().st_size
        return total

    def _discard_partial(self, engine_id: str):
        shutil.rmtree(self._partial_dir(engine_id), ignore_errors=True)

    def _manifest_ok(self, engine: catalog.Engine, final: Path) -> bool:
        """The installed files carry the pinned names, sizes and hashes. Where they came from
        is not compared, so installs from the old GitHub archives (same files, a manifest
        with "archive" instead of "source") stay ready."""
        try:
            manifest = json.loads((final / "manifest.json").read_text())
            pins = {f.name: {"sha256": f.sha256, "size": f.size} for f in engine.asset.files}
            return (manifest.get("engine") == engine.id
                    and manifest.get("files") == pins
                    and all((final / f.name).stat().st_size == f.size
                            and not (final / f.name).is_symlink() for f in engine.asset.files))
        except (OSError, ValueError, AttributeError):
            return False

    def state(self, engine_id: str) -> ModelState:
        engine = self._engine(engine_id)
        if engine.asset is None:
            return ModelState(engine_id, "bundled", self._bundled_dir())
        if self.root is None:
            return ModelState(engine_id, "absent", None,
                              error="Persistent app storage is unavailable")
        if self._busy == engine_id and self._progress is not None:
            name = {"extracting": "verifying"}.get(self._progress.phase, self._progress.phase)
            return ModelState(engine_id, name, None, self._progress.bytes_done
                              if self._progress.phase == "downloading" else 0)
        final = self.root / engine_id
        if final.is_dir() and not final.is_symlink():
            if self._manifest_ok(engine, final):
                return ModelState(engine_id, "ready", final)
            return ModelState(engine_id, "corrupt", None,
                              error="The installed model files are incomplete or changed")
        error = self._errors.get(engine_id)
        size = self._partial_bytes(engine)
        if size:
            return ModelState(engine_id, "partial", None, size, error)
        return ModelState(engine_id, "error" if error else "absent", None, error=error)

    async def verify(self, engine_id: str, *, deep: bool = True) -> ModelState:
        current = self.state(engine_id)
        if not deep or current.state not in ("ready", "corrupt"):
            return current
        return await asyncio.to_thread(self._verify_sync, engine_id)

    def _verify_sync(self, engine_id: str) -> ModelState:
        engine = self._engine(engine_id)
        final = self.root / engine_id
        if not self._manifest_ok(engine, final):
            return self.state(engine_id)
        for f in engine.asset.files:
            try:
                ok = _hash_file(final / f.name, pace=_Pacer(self.calls_active)) == f.sha256
            except OSError:
                ok = False
            if not ok:
                return ModelState(engine_id, "corrupt", None,
                                  error=f"{f.name} does not match its pinned checksum")
        return ModelState(engine_id, "ready", final)

    def progress(self, engine_id: str) -> Progress | None:
        return self._progress if self._busy == engine_id else None

    @property
    def busy(self) -> str | None:
        return self._busy

    def cancel(self, engine_id: str, *, discard: bool = False) -> bool:
        if self._busy == engine_id:
            self._discard = self._discard or discard
            self._cancel.set()
            return True
        asset = self._engine(engine_id).asset
        if discard and asset is not None and self.root is not None:
            self._discard_partial(engine_id)
        return False

    async def download(self, engine_id: str) -> Path:
        engine = self._engine(engine_id)
        if engine.asset is None:
            return self._bundled_dir()
        if self._busy is not None:
            raise ModelError("busy", "Another model download is already running")
        if self.root is None:
            raise ModelError("storage", "Persistent app storage is unavailable, so the model "
                             "cannot be downloaded")
        self._busy, self._discard = engine_id, False
        self._cancel = threading.Event()
        self._progress = Progress(engine_id, "downloading", 0, engine.asset.download_bytes,
                                  self.clock())
        self._errors.pop(engine_id, None)
        work = asyncio.ensure_future(asyncio.to_thread(self._download_sync, engine, self._cancel))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            # The thread must stop before the store accepts another download.
            self._cancel.set()
            with contextlib.suppress(BaseException):
                await asyncio.wait({work})
            raise
        except ModelError as error:
            if error.code != "cancelled":
                self._errors[engine_id] = str(error)
            raise
        finally:
            self._busy = self._progress = None

    def _download_sync(self, engine: catalog.Engine, cancelled: threading.Event) -> Path:
        asset = engine.asset
        try:
            self._writable()
            # A reinstall keeps the old directory until the final rename.
            replacing = _tree_bytes(self.root / engine.id)
            need = catalog.asset_disk_bytes(asset, self._partial_bytes(engine)) + replacing
            free = self.disk_usage(self.root).free
            if free < need:
                raise ModelError("disk_space", f"Downloading needs {_gib(need)} GiB of free "
                                 f"space; {_gib(free)} GiB is free.")
            before = 0
            for f in asset.files:
                self._fetch(engine.id, f, cancelled, before)
                before += f.zst_bytes
            pace = _Pacer(self.calls_active)
            self._check_objects(engine, cancelled, pace)
            staging = self.root / f".staging-{engine.id}-{secrets.token_hex(4)}"
            try:
                self._decode(engine, staging, cancelled, pace)
                self._progress.phase = "installing"
                final = self._install(engine, staging)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        except ModelError as error:
            if error.code == "hash_mismatch" or (error.code == "cancelled" and self._discard):
                self._discard_partial(engine.id)
            raise
        except OSError as error:
            raise ModelError("storage", f"Could not write the model files: {error.strerror}")
        self._discard_partial(engine.id)
        return final

    def _writable(self):
        partial = self.root / ".partial"
        try:
            partial.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise ModelError("storage", "Persistent app storage is not writable")
        if not os.access(self.root, os.W_OK) or not os.access(partial, os.W_OK):
            raise ModelError("storage", "Persistent app storage is not writable")

    def _fetch(self, engine_id: str, f: catalog.ModelFile, cancelled, before: int):
        """Download one .zst object; `before` counts the objects ahead of it, for progress."""
        partial, sidecar = self._partial(engine_id, f)
        partial.parent.mkdir(exist_ok=True)
        try:
            done = partial.stat().st_size
        except OSError:
            done = 0
        try:
            meta = json.loads(sidecar.read_text())
        except (OSError, ValueError):
            meta = {}
        validator = meta.get("etag") or meta.get("lastModified")
        if done > f.zst_bytes or meta.get("bytes") != f.zst_bytes:
            done = 0
        if done and not validator:
            done = 0
        self._progress.bytes_done = before + done
        if done == f.zst_bytes:
            return
        # identity: the object is already compressed; a transfer encoding would break Range.
        headers = {"User-Agent": f"cassini-live/{__version__}", "Accept-Encoding": "identity"}
        if done:
            headers |= {"Range": f"bytes={done}-", "If-Range": validator}
        request = urllib.request.Request(f.url, headers=headers)
        try:
            response = self.opener(request, timeout=30)
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise ModelError("network", f"Cannot download the model: {_why(error)}")
        with response:
            status = getattr(response, "status", None) or response.getcode()
            if status == 206 and done:
                first, total = _content_range(response.headers.get("Content-Range", ""))
                if total != f.zst_bytes:
                    raise _changed()
                if first != done:
                    raise ModelError("network", "The download server resumed at the wrong offset")
                mode = "ab"
            elif status == 200:
                if _int(response.headers.get("Content-Length")) != f.zst_bytes:
                    raise _changed()
                done, mode = 0, "wb"
                meta = {"url": f.url, "etag": response.headers.get("ETag"),
                        "lastModified": response.headers.get("Last-Modified"),
                        "bytes": f.zst_bytes}
                tmp = sidecar.with_name(sidecar.name + ".tmp")
                tmp.write_text(json.dumps(meta))
                os.replace(tmp, sidecar)
            else:
                raise ModelError("network", f"The download server answered with status {status}")
            self._progress.bytes_done = before + done
            with partial.open(mode) as out:
                try:
                    while True:
                        if cancelled.is_set():
                            raise ModelError("cancelled", "The model download was cancelled")
                        block = response.read(BLOCK)
                        if not block:
                            break
                        if done + len(block) > f.zst_bytes:
                            out.write(block[: f.zst_bytes - done])
                            raise _changed()
                        out.write(block)
                        done += len(block)
                        self._progress.bytes_done = before + done
                        if done % CACHE_DROP < len(block):
                            _drop_cache(out, sync=True)
                except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                    raise ModelError("network", f"The download was interrupted: {_why(error)}")
                finally:
                    out.flush()
                    os.fsync(out.fileno())
                    _drop_cache(out)
        if done != f.zst_bytes:
            raise ModelError("network", "The download ended early; it will resume next time")

    def _check_objects(self, engine: catalog.Engine, cancelled, pace):
        """Hash every downloaded object first, so the decoder never sees unpinned bytes. This
        also verifies resumed downloads."""
        self._progress.phase = "verifying"
        self._progress.bytes_done, self._progress.bytes_total = 0, engine.asset.download_bytes
        for f in engine.asset.files:
            if _hash_file(self._partial(engine.id, f)[0], cancelled, pace) != f.zst_sha256:
                raise ModelError("hash_mismatch", f"The downloaded {f.name} does not match its "
                                 "pinned checksum")
            self._progress.bytes_done += f.zst_bytes

    def _decode(self, engine: catalog.Engine, staging: Path, cancelled, pace):
        self._progress.phase = "extracting"
        self._progress.bytes_done = 0
        self._progress.bytes_total = engine.asset.extracted_bytes
        staging.mkdir(mode=0o750)
        for f in engine.asset.files:
            with self._partial(engine.id, f)[0].open("rb") as raw:
                # Each object is many independent frames, then a skippable seek-table frame.
                source = zstandard.ZstdDecompressor().stream_reader(
                    _PacedReader(raw, cancelled, pace), read_size=BLOCK,
                    read_across_frames=True)
                try:
                    self._copy(source, staging / f.name, f, cancelled)
                except zstandard.ZstdError as error:
                    raise ModelError("hash_mismatch", f"{f.name} cannot be decompressed: "
                                     f"{error}")

    def _copy(self, source, target: Path, pin: catalog.ModelFile, cancelled):
        digest = hashlib.sha256()
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        with os.fdopen(fd, "wb") as out:
            written = 0
            while block := source.read(BLOCK):
                if cancelled.is_set():
                    raise ModelError("cancelled", "The model download was cancelled")
                if written + len(block) > pin.size:
                    raise ModelError("hash_mismatch", f"{pin.name} is larger than pinned")
                digest.update(block)
                out.write(block)
                written += len(block)
                self._progress.bytes_done += len(block)
                if written % CACHE_DROP < len(block):
                    _drop_cache(out, sync=True)
            out.flush()
            os.fsync(out.fileno())
            _drop_cache(out)
        if written != pin.size or digest.hexdigest() != pin.sha256:
            raise ModelError("hash_mismatch", f"{pin.name} does not match its pinned checksum")

    def _install(self, engine: catalog.Engine, staging: Path) -> Path:
        license = engine.license
        shutil.copyfile(self.license_root / license.text_file, staging / "LICENSE.txt")
        if license.notice:
            (staging / "NOTICE.txt").write_text(license.notice + "\n")
        manifest = {
            "engine": engine.id,
            "source": catalog.DIST_URL,
            "model": engine.asset.model,
            "files": {f.name: {"sha256": f.sha256, "size": f.size} for f in engine.asset.files},
            "downloads": {f.name: {"url": f.url, "sha256": f.zst_sha256, "bytes": f.zst_bytes}
                          for f in engine.asset.files},
            "license": license.id,
            "licenseVersion": license.version,
            "installedAt": int(self.clock()),
        }
        tmp = staging / "manifest.json.tmp"
        with tmp.open("w") as out:
            json.dump(manifest, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, staging / "manifest.json")
        _fsync_dir(staging)
        final = self.root / engine.id
        old = None
        if final.exists() or final.is_symlink():
            old = self.root / f".old-{engine.id}-{secrets.token_hex(4)}"
            os.replace(final, old)
        try:
            os.replace(staging, final)
        except OSError:
            if old is not None:
                os.replace(old, final)
            raise
        _fsync_dir(self.root)
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
        return final

    async def delete(self, engine_id: str) -> None:
        engine = self._engine(engine_id)
        if engine.asset is None or self.root is None:
            return
        if self._busy == engine_id:
            raise ModelError("busy", "The model is being downloaded; cancel the download first")
        await asyncio.to_thread(self._delete_sync, engine)

    def _delete_sync(self, engine: catalog.Engine):
        final = self.root / engine.id
        if final.is_symlink():
            final.unlink()
        elif final.exists():
            old = self.root / f".old-{engine.id}-{secrets.token_hex(4)}"
            os.replace(final, old)
            shutil.rmtree(old, ignore_errors=True)
        self._discard_partial(engine.id)
        self._errors.pop(engine.id, None)


def _tree_bytes(path: Path) -> int:
    if path.is_symlink() or not path.is_dir():
        return 0
    total = 0
    for child in path.iterdir():
        with contextlib.suppress(OSError):
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
    return total


def _content_range(value: str) -> tuple[int | None, int | None]:
    try:
        unit, rest = value.split(" ", 1)
        span, total = rest.split("/")
        return int(span.split("-")[0]), int(total)
    except ValueError:
        return None, None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _changed() -> ModelError:
    return ModelError("size_mismatch", "The published model file changed; this version of "
                      "Cassini Live cannot verify it")


def _why(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP status {error.code}"
    reason = getattr(error, "reason", None) or error
    return str(reason) or type(error).__name__
