"""Bounded retry policy and a tiny crash-safe journal (no call data)."""

import json
import math
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

from . import __version__


@dataclass(frozen=True)
class RecoveryPolicy:
    # Shortened waits approved by Silvio (2026-10-15). Only a crash waits now: a load is
    # attempted as soon as the limits allow it, with no headroom or capacity windows.
    base: float = 120
    cap: float = 3600
    stable_window: float = 600  # unjustified; resets the failure count after a calm run
    poll: float = 15
    monitor: float = 1

    def delay(self, failures, jitter=random.random):
        # Positive jitter preserves the minimum cooldown and caps retry frequency.
        return min(self.cap, self.base * 2 ** min(max(failures - 1, 0), 10)) * (1 + 0.2 * jitter())


class RecoveryJournal:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.data = {}

    def read(self):
        if self.path is None:
            return dict(self.data)
        if not self.path.exists():
            return {}
        if self.path.stat().st_size > 4096:
            raise ValueError("Oversized recovery journal")
        data = json.loads(self.path.read_text())
        if not isinstance(data, dict):
            raise ValueError("Invalid recovery journal")
        if not isinstance(data.get("failures"), int) or not 0 <= data["failures"] <= 20:
            raise ValueError("Invalid failure count")
        if not isinstance(data.get("nextAttemptAt"), (int, float)) or not math.isfinite(
            data["nextAttemptAt"]
        ):
            raise ValueError("Invalid retry time")
        return data

    def write(self, failures, next_attempt, pending, reason, last_trip=None, running=False):
        data = dict(
            failures=failures,
            nextAttemptAt=next_attempt,
            attemptInProgress=pending,
            # Only a clean stop or a trip clears it: a boot that finds it
            # set follows a process death while the engine ran, which counts as a failure.
            engineRunning=running,
            # Older readers ignore it; a boot of another version clears an interrupted load.
            appVersion=__version__,
            reason=reason,
            lastTrip=last_trip,
            updatedAt=time.time(),
        )
        self.data = data
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        # A fixed-size document, atomically replaced; never audio or identifiers.
        with temporary.open("w") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)
