"""Per-speaker admission and continuous shedding driven by live transcription lag.

The kernel's cgroup limits protect the call (docs/proposals/0.8.0-instant-admission.md);
this module only decides whether one more speaker fits and which stream to pause when
captions fall behind. Pure and in-memory: `admit()` reads the sample cached by the last
tick plus transcriber fields, never a file. Until the staging lag run fixes the
thresholds (`Thresholds.provisional`) the governor observes: it records every decision
it would have taken and changes nothing.
"""

import logging
import time
import weakref
from collections import deque
from dataclasses import dataclass
from typing import Protocol

from . import catalog
from .resources import MIB

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Thresholds:
    """Admission and shedding limits. Provenance per field; the design's thresholds table
    carries the staging measurement plan that replaces each provisional value."""

    # Refuse a further speaker above this control lag. Derived from the harness at 1.3x:
    # 0.0 s below the structural ceiling, 0.19-0.44 s at it, 1.86 s one speaker past it
    # (results.md:123-158); real speech on staging CPU untested.
    lag_admit: float = 1.0
    # Pause the newest stream above this sustained lag. Bounded under the 4 s stale cut
    # and the 5 s request timeout: lag grows about 0.3 s/s at the cliff, so one 5 s
    # window adds about 1.5 s and 2.0 + 1.5 stays under 4.
    lag_shed: float = 2.0
    # Container CPU stall (fraction of the last second) that closes admission. Unjustified:
    # the inherited 20 % had none and the harness shows it will not fire at engine
    # saturation (0.19 s throttled at 0.98 duty, results.md:120); a stress-ng run on
    # staging sets it or drops the input if lag always leads.
    cpu_stall_admit: float = 0.20
    # Derived: one sustained window, so the previous shed's effect is visible first.
    shed_interval: float = 5.0
    # Derived: two sustained windows; shed/re-admit flaps per minute on staging may
    # lengthen it.
    hold_off: float = 10.0
    # Unjustified; the lower bound is the 8 s segment cap so a seat survives one segment
    # gap. Measured turn gaps in a 3-speaker call must never lose a seat mid-conversation.
    seat_idle_s: float = 15.0
    # Unjustified; checked in the Talk UI.
    notice_interval: float = 60.0
    # True until the staging run writes measured values; the governor then leaves
    # observe-only mode (CI asserts measured_on is set before provisional is cleared).
    provisional: bool = True
    measured_on: str | None = None


THRESHOLDS = Thresholds()


def per_speaker_mib(engine_id):
    """Memory one admitted speaker adds on a shared host: its stream plus a batch slot
    (stream_mib provisional, catalog.py:191). Whistle preloads its workers, so admission
    never binds on memory there."""
    engine = catalog.get(engine_id)
    if engine.kind == "worker_pool":
        return 0
    return engine.requirements.stream_mib + engine.requirements.batch_mib


class Seatable(Protocol):
    """What the governor reads from a transcriber; all stamps are monotonic seconds."""

    pending_since: float | None  # oldest undecoded chunk's created_at
    lease_at: float | None  # when the current slot or worker lease was acquired
    paused_until: float | None  # hold-off after a shed or a lag/cpu refusal
    shed_reason: str | None  # written by tick(); the transcriber acts on it

    def has_lease(self) -> bool: ...


@dataclass(frozen=True)
class Decision:
    admitted: bool
    # The rule that fired: ceiling, memory, cpu, lag or shed; None when none did. In
    # observe-only mode `admitted` is True while `reason` still names the refusal.
    reason: str | None = None


class LagMeter:
    """Rolling lag statistics. `observe()` keeps per-second maxima of the control lag and
    `sustained()` is their rolling minimum over the window (the harness definition,
    rt_common.py: momentary decode latency averages out, a growing backlog does not).
    `record()` keeps recent per-request latencies for a p95."""

    WINDOW_S = 5  # rt_common.py SUSTAIN_WINDOW; the shed and hold-off derivations use it
    BUCKETS = 10  # two windows of history (design); only the latest window is read
    # Bounds the status cost; about 20 s of 12 streams at one request per second.
    # Unjustified: the staging request rate decides whether p95 needs more.
    REQUESTS = 256

    def __init__(self):
        self.maxima = {}
        self.requests = deque(maxlen=self.REQUESTS)

    def observe(self, lag, now):
        second = int(now)
        self.maxima[second] = max(self.maxima.get(second, 0.0), lag)
        for stale in [s for s in self.maxima if s <= second - self.BUCKETS]:
            del self.maxima[stale]

    def sustained(self, now):
        # A second without a tick counts as no lag: shedding needs a full window of evidence.
        second = int(now)
        return min(self.maxima.get(s, 0.0) for s in range(second - self.WINDOW_S + 1, second + 1))

    def record(self, seconds):
        self.requests.append(seconds)

    def p95(self):
        if not self.requests:
            return None
        ordered = sorted(self.requests)
        return ordered[int(0.95 * (len(ordered) - 1))]


def _headroom_mib(sample):
    """memory.max minus non-reclaimable charge: the kernel reclaims file pages before an
    OOM. Without memory.stat the file figure is 0 and headroom is pessimistic."""
    limit = sample.get("memoryLimitBytes")
    if not limit:
        return None, None
    current = sample.get("memoryCurrentBytes")
    if current is None:
        current = sample.get("memoryFraction", 0.0) * limit
    used = current - (sample.get("memoryFileBytes") or 0)
    return (limit - used) / MIB, used / MIB


class LoadGovernor:
    def __init__(
        self,
        *,
        ceiling,
        per_speaker_mib=0,
        thresholds=THRESHOLDS,
        observe_only=None,
        clock=time.monotonic,
        wall_clock=time.time,
    ):
        self.ceiling = ceiling
        self.per_speaker_mib = per_speaker_mib
        self.thresholds = thresholds
        self.observe_only = thresholds.provisional if observe_only is None else observe_only
        self.clock, self.wall_clock = clock, wall_clock
        self.seats = weakref.WeakKeyDictionary()  # transcriber -> admitted_at
        self._active_at = weakref.WeakKeyDictionary()  # transcriber -> last tick with a lease
        # Every transcriber admit() has seen, for caption age; a closed track drops out.
        self.transcribers = weakref.WeakSet()
        self.lag = LagMeter()
        self.sample = {}
        self.lag_now = self.lag_sustained = self.caption_age = 0.0
        self.cpu_stall = None
        self._stall_usec = self._tick_at = None
        self.closed_reason = None
        self._calm_since = None
        self.last_refusal = None
        self.last_shed = None
        self.last_shed_at = None
        self.shed_events = 0
        self.measuring = False
        self.tick_ms = 0.0

    @property
    def closed(self):
        return self.closed_reason is not None

    def admit(self, transcriber, now=None):
        """Decision at the start of a gate segment, before the slot or worker wait."""
        now = self.clock() if now is None else now
        self.transcribers.add(transcriber)
        reason = self._refusal(transcriber, now)
        if reason is None:
            self._seat(transcriber, now)
            return Decision(True)
        self.last_refusal = {"at": self.wall_clock(), "reason": reason}
        if reason in {"lag", "cpu"} and not self.closed:
            self.closed_reason, self._calm_since = reason, None
            LOGGER.info("Caption admission closed (%s)", reason)
        if not self.observe_only:
            return Decision(False, reason)
        # Observing: the speaker goes on, so the seat count must keep matching reality.
        self._seat(transcriber, now)
        return Decision(True, reason)

    def _seat(self, transcriber, now):
        self.seats.setdefault(transcriber, now)
        self._active_at.setdefault(transcriber, now)

    def _refusal(self, transcriber, now):
        if transcriber.paused_until is not None and transcriber.paused_until > now:
            return "shed"
        if transcriber in self.seats or not self.seats:
            return None
        if len(self.seats) >= self.ceiling:
            return "ceiling"
        headroom, _ = _headroom_mib(self.sample)
        # One allowance for this speaker and one for growth before the next sample.
        if headroom is not None and headroom < 2 * self.per_speaker_mib:
            return "memory"
        if self.closed:
            return self.closed_reason
        if self.cpu_stall is not None and self.cpu_stall >= self.thresholds.cpu_stall_admit:
            return "cpu"
        if self._lag_now(now) >= self.thresholds.lag_admit:
            return "lag"
        return None

    def pause(self, transcriber, now=None):
        """Hold a speaker off after a shed or a lag/cpu refusal timed out; the seat goes."""
        now = self.clock() if now is None else now
        transcriber.paused_until = now + self.thresholds.hold_off
        self.release(transcriber)

    def release(self, transcriber):
        self.seats.pop(transcriber, None)
        self._active_at.pop(transcriber, None)

    def _lag_now(self, now):
        # Control lag excludes the FIFO wait: a speaker parked on a slot is not engine lag.
        lag = 0.0
        for transcriber in self.transcribers:
            since, lease_at = transcriber.pending_since, transcriber.lease_at
            if since is not None and lease_at is not None and transcriber.has_lease():
                lag = max(lag, now - max(since, lease_at))
        return lag

    def tick(self, sample, now=None):
        """Once per second from the safety monitor: refresh the inputs, expire idle seats,
        reopen after a calm window and shed when the backlog is sustained."""
        started = time.perf_counter()
        now = self.clock() if now is None else now
        self.sample = sample
        stall_usec = sample.get("cpuStallUsec")
        if stall_usec is not None and self._stall_usec is not None and self._tick_at is not None:
            elapsed = max(0.001, now - self._tick_at)
            self.cpu_stall = min(1.0, max(0, stall_usec - self._stall_usec) / 1e6 / elapsed)
        else:
            self.cpu_stall = None
        self._stall_usec, self._tick_at = stall_usec, now
        self.lag_now = self._lag_now(now)
        self.caption_age = max(
            (now - t.pending_since for t in self.transcribers if t.pending_since is not None),
            default=0.0,
        )
        self.lag.observe(self.lag_now, now)
        self.lag_sustained = self.lag.sustained(now)
        for transcriber in list(self.seats):
            if transcriber.has_lease():
                self._active_at[transcriber] = now
            elif now - self._active_at.get(transcriber, now) >= self.thresholds.seat_idle_s:
                self.release(transcriber)
        if self.closed:
            self._reopen(now)
        if not self.measuring:
            self._shed(now)
        self.tick_ms = round((time.perf_counter() - started) * 1000, 2)

    def _reopen(self, now):
        # Hysteresis: half the admission thresholds for one full window, so a reopened
        # governor does not refuse the next speaker within the same backlog.
        calm = self.lag_sustained < self.thresholds.lag_admit / 2 and (
            self.cpu_stall is None or self.cpu_stall < self.thresholds.cpu_stall_admit / 2
        )
        if not calm:
            self._calm_since = None
        elif self._calm_since is None:
            self._calm_since = now
        elif now - self._calm_since >= LagMeter.WINDOW_S:
            LOGGER.info("Caption admission reopened after %s", self.closed_reason)
            self.closed_reason, self._calm_since = None, None

    def _shed(self, now):
        if self.lag_sustained < self.thresholds.lag_shed:
            return
        if (
            self.last_shed_at is not None
            and now - self.last_shed_at < self.thresholds.shed_interval
        ):
            return
        leased = [t for t in self.seats if t.has_lease()]
        # A single seat holder is never shed; only a lease can be releasing the backlog.
        if len(leased) < 2:
            return
        newest = max(leased, key=self.seats.__getitem__)
        seats = len(self.seats)
        # The interval applies to would-be sheds too, or observing would log every tick.
        self.last_shed_at = now
        self.last_shed = {"at": self.wall_clock(), "lag": round(self.lag_sustained, 2)}
        self.shed_events += 1
        if self.observe_only:
            LOGGER.info(
                "Caption lag %.1f s sustained with %d seats; observing, no stream paused",
                self.lag_sustained,
                seats,
            )
            return
        self.release(newest)
        newest.shed_reason = "lag"
        LOGGER.warning(
            "Caption lag %.1f s sustained with %d seats; pausing the newest stream (lag)",
            self.lag_sustained,
            seats,
        )

    def status(self):
        now = self.clock()
        headroom, used = _headroom_mib(self.sample)
        limit = self.sample.get("memoryLimitBytes")
        return {
            "open": not self.closed,
            "reason": self.closed_reason,
            "seats": len(self.seats),
            "ceiling": self.ceiling,
            "speaking": sum(1 for t in self.seats if t.has_lease()),
            "paused": sum(
                1 for t in self.transcribers if t.paused_until is not None and t.paused_until > now
            ),
            "lagSeconds": round(self.lag_now, 3),
            "lagSustainedSeconds": round(self.lag_sustained, 3),
            "captionAgeSeconds": round(self.caption_age, 3),
            "cpuStall": None if self.cpu_stall is None else round(self.cpu_stall, 3),
            "cpuPressureAvg10": self.sample.get("cpuPressureRecent"),
            "memoryHeadroomMiB": None if headroom is None else int(headroom),
            "memoryUsedMiB": None if used is None else int(used),
            "memoryLimitMiB": None if not limit else int(limit / MIB),
            "memoryStatAvailable": self.sample.get("memoryStatAvailable"),
            "pressureAvailable": self.sample.get("pressureAvailable"),
            "engineDuty": self.sample.get("engineDuty"),
            "lastRefusal": self.last_refusal,
            "lastShed": self.last_shed,
            "shedEvents": self.shed_events,
            "measuring": self.measuring,
            "tickMs": self.tick_ms,
            "observeOnly": self.observe_only,
        }
