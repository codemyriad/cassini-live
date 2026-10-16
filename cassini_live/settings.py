"""The administrator's engine choice, persisted atomically next to the recovery journal."""

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from . import catalog

MAX_BYTES = 4096


@dataclass
class EngineSettings:
    engine: str = catalog.DEFAULT_ENGINE
    pending: str | None = None
    last_error: dict | None = None
    updated_at: float = 0


def _engine(value, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or value not in catalog.ENGINES:
        raise ValueError("Unknown speech engine in settings")
    return value


def _time(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid time in settings")
    return float(value)


def _parse(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid engine settings")
    # 0.7.0 saved license acknowledgements; that step is gone, so the key is ignored in any
    # shape and dropped on the next write.
    error = data.get("lastError")
    if error is not None and not (
        isinstance(error, dict) and all(isinstance(v, (str, int, float)) for v in error.values())
    ):
        raise ValueError("Invalid last error")
    return EngineSettings(
        engine=_engine(data.get("engine", catalog.DEFAULT_ENGINE)),
        pending=_engine(data.get("pending"), optional=True),
        last_error=error,
        updated_at=_time(data.get("updatedAt", 0)),
    )


def _dump(settings):
    return {
        "engine": settings.engine,
        "pending": settings.pending,
        "lastError": settings.last_error,
        "updatedAt": settings.updated_at,
    }


class SettingsStore:
    def __init__(self, path: Path | None):
        self.path = Path(path) if path else None
        self.data = None

    def read(self) -> EngineSettings:
        if self.path is None:
            return _parse(self.data) if self.data is not None else EngineSettings()
        if not self.path.exists():
            return EngineSettings()
        if self.path.stat().st_size > MAX_BYTES:
            raise ValueError("Oversized engine settings")
        try:
            return _parse(json.loads(self.path.read_text()))
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as error:
            raise ValueError("Unreadable engine settings") from error

    def write(self, settings: EngineSettings) -> None:
        data = _dump(settings)
        encoded = json.dumps(data)
        if len(encoded.encode()) > MAX_BYTES:
            raise ValueError("Engine settings exceed the size cap")
        _parse(data)
        if self.path is None:
            self.data = data
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)
