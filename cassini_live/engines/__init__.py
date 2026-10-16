"""Speech engine backends behind one interface (engine-selection.md 6.1).

Whistle keeps its process-per-speaker WorkerPool. Nemotron engines share one engine
host process. Safety, capacity, diagnostics, the app and transcribers use only the
members below, so they never depend on the engine kind.
"""

import asyncio
import weakref
from collections import Counter
from pathlib import Path
from typing import Callable, Protocol

from .. import catalog


class Transcriber(Protocol):
    session_id: str
    language: str
    queue: asyncio.Queue
    gate: object
    pending: str
    lost_chunks: int
    lease_samples: int
    worker: object | None
    # Load signals (0.8.0-instant-admission.md): oldest undecoded chunk's created_at,
    # when the current lease was granted, and the governor's hold-off/shed marks.
    pending_since: float | None
    lease_at: float | None
    paused_until: float | None
    shed_reason: str | None

    def has_lease(self) -> bool: ...
    async def connect(self) -> None: ...
    async def start(self, stream) -> None: ...
    async def shutdown(self) -> None: ...
    async def set_language(self, language: str) -> None: ...
    async def abort(self) -> None: ...
    # Used by calibration only:
    async def _put_audio(self, samples) -> None: ...
    def _flush_gate(self) -> None: ...
    async def _decode(self) -> None: ...


class LagMeter(Protocol):
    """Per-request latency (capture to result), recorded by the transcribers."""

    def record(self, seconds: float) -> None: ...
    def last(self) -> float: ...
    def p95(self) -> float: ...


class Backend(Protocol):
    engine_id: str
    size: int
    admission_limit: int
    closed: bool
    resource_check: Callable[[], str | None] | None
    load_check: Callable[[], str | None] | None
    failure_circuit_open: bool
    failures: int
    capacity_waits: int
    dropped_chunks: int
    scheduling_yields: int
    last_pressure: float
    stream_ends: Counter
    chunks_processed: int
    audio_seconds: float
    finals_emitted: int
    partials_emitted: int
    workers: set
    leased: set
    lag: LagMeter
    transcribers: weakref.WeakSet  # every live Transcriber, registered on creation
    host_pid: int | None  # the shared engine host for duty sampling; Whistle has none

    @property
    def waiting(self) -> int: ...
    @property
    def busy(self) -> int: ...
    def is_idle(self) -> bool: ...
    def resize(self, size: int) -> None: ...
    def note_drop(self) -> None: ...
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    def status(self) -> dict: ...
    def transcriber(
        self, session_id, language, transcript_queue, *translation_hooks
    ) -> Transcriber: ...


def create_backend(
    engine_id: str,
    *,
    size: int,
    weights: str | None = None,
    model_dir: Path | None = None,
    threads: int | None = None,
) -> Backend:
    """whistle -> WorkerPool(size, weights); nemotron-* -> SharedEngine(engine, model_dir, size)."""
    engine = catalog.get(engine_id)
    if engine.kind == "worker_pool":
        from ..pool import WorkerPool

        return WorkerPool(size, weights)
    if model_dir is None:
        raise ValueError(f"{engine.name} needs a model directory")
    from .shared import SharedEngine

    return SharedEngine(engine, Path(model_dir), size, threads=threads)
