"""The administrator's engine choice, persisted atomically next to the recovery journal."""

import json
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import catalog

MAX_BYTES = 4096


@dataclass
class Acknowledgement:
    version: str
    user: str
    at: float


@dataclass
class EngineSettings:
    engine: str = catalog.DEFAULT_ENGINE
    pending: str | None = None
    acknowledgements: dict[str, Acknowledgement] = field(default_factory=dict)
    last_error: dict | None = None
    updated_at: float = 0


def _engine(value, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or value not in catalog.ENGINES:
        raise ValueError("Unknown speech engine in settings")
    return value


def _text(value, limit=256):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("Invalid text in settings")
    return value


def _time(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid time in settings")
    return float(value)


def _parse(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid engine settings")
    acks = data.get("acknowledgements", {})
    if not isinstance(acks, dict):
        raise ValueError("Invalid license acknowledgements")
    error = data.get("lastError")
    if error is not None and not (
        isinstance(error, dict) and all(isinstance(v, (str, int, float)) for v in error.values())
    ):
        raise ValueError("Invalid last error")
    acknowledgements = {}
    for engine, ack in acks.items():
        if not isinstance(ack, dict):
            raise ValueError("Invalid license acknowledgement")
        acknowledgements[_engine(engine)] = Acknowledgement(
            _text(ack.get("version")), _text(ack.get("user")), _time(ack.get("at"))
        )
    return EngineSettings(
        engine=_engine(data.get("engine", catalog.DEFAULT_ENGINE)),
        pending=_engine(data.get("pending"), optional=True),
        acknowledgements=acknowledgements,
        last_error=error,
        updated_at=_time(data.get("updatedAt", 0)),
    )


def _dump(settings):
    return {
        "engine": settings.engine,
        "pending": settings.pending,
        "acknowledgements": {k: asdict(v) for k, v in settings.acknowledgements.items()},
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
