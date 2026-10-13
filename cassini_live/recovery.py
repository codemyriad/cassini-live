"""Bounded retry policy and a tiny crash-safe journal (no call data)."""

import json
import math
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

from .resources import MIB, budget_problem


@dataclass(frozen=True)
class RecoveryPolicy:
    base: float = 120
    cap: float = 3600
    insufficient: float = 900
    headroom_window: float = 180
    stable_window: float = 1800
    poll: float = 15
    monitor: float = 1

    def delay(self, failures, jitter=random.random):
        # Positive jitter preserves the minimum cooldown and caps retry frequency.
        return min(self.cap, self.base * 2 ** min(max(failures - 1, 0), 10)) * (1 + 0.2 * jitter())


def recovery_problem(data, workers):
    if problem := budget_problem(data, workers):
        return problem
    if data["memoryFraction"] >= 0.6 or data["pidsFraction"] >= 0.6:
        return "Waiting for memory and process headroom"
    reserve = max(256 * MIB, data["hostMemoryBytes"] * 0.1)
    if data["hostAvailableBytes"] - workers * 192 * MIB < reserve * 1.5:
        return "Waiting for sustained host memory headroom"
    # PSI measures time stalled, including contention outside this container.
    if (data.get("cpuPressure") is None or data.get("memoryPressure") is None
            or data["cpuPressure"] >= 20 or data["memoryPressure"] >= 1):
        return "Waiting for host CPU and memory pressure to settle"
    return None


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

    def write(self, failures, next_attempt, pending, reason):
        data = dict(
            failures=failures,
            nextAttemptAt=next_attempt,
            attemptInProgress=pending,
            reason=reason,
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
