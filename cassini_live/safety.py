"""One supervisor owns admission, shutdown and crash-only recovery.

The kernel's cgroup limits protect the call (docs/proposals/0.8.0-instant-admission.md):
a start is a memory fit check and a load, ready in load time; per-speaker admission and
shedding live in the load governor; only a real crash (an OOM kill or the backend's
failure circuit) stops captions and waits out a back-off.
"""

import asyncio
import logging
import os
import random
import time
from contextlib import suppress

from . import __version__, catalog
from .diagnostics import process_usage
from .load import THRESHOLDS, LoadGovernor, per_speaker_mib
from .pool import CapacityError
from .recovery import RecoveryJournal, RecoveryPolicy
from .resources import advice, snapshot, start_problem, static_problem

LOGGER = logging.getLogger(__name__)
# 0.7.x tripped when its automatic capacity check failed. That cooldown reflects the
# check, not a crash, and must not delay the first 0.8.0 start. Its ready reason,
# "Capacity check passed", is no trip: a process killed while serving stays a crash.
LEGACY_CHECK_TRIP = "Capacity check did not establish"
# Retry-After while an engine loads. Measured warm loads take 1.2-1.9 s (specs.md:48); a
# cold one can take up to start_timeout_s, so this only paces client retries.
LOADING_RETRY_S = 5


class ResourceRefused(RuntimeError):
    pass


class TrackLease:
    def __init__(self, owner):
        self.owner = owner
        self.released = False

    def release(self):
        if not self.released:
            self.owner.tracks.discard(self)
            self.released = True


def _legacy_check(saved):
    """A 0.7.x cooldown after its capacity check failed, and nothing worse since."""
    if saved.get("engineRunning") or saved.get("attemptInProgress"):
        return False  # a crash while serving or loading is honoured whatever the reason
    trip = saved.get("lastTrip")
    reasons = (saved.get("reason"), trip.get("reason") if isinstance(trip, dict) else None)
    return any(isinstance(r, str) and r.startswith(LEGACY_CHECK_TRIP) for r in reasons)


class SafetyController:
    def __init__(
        self,
        pool,
        assessment,
        service=None,
        reader=snapshot,
        *,
        policy=None,
        journal=None,
        clock=time.monotonic,
        wall_clock=time.time,
        jitter=random.random,
        fatal_exit=os._exit,
        thresholds=THRESHOLDS,
    ):
        self.pool, self.assessment, self.service = pool, assessment, service
        self.thresholds = thresholds
        self.reader, self.clock, self.wall_clock, self.jitter = reader, clock, wall_clock, jitter
        self.policy = policy or RecoveryPolicy()
        self.fatal_exit = fatal_exit
        self.journal = journal or RecoveryJournal()
        self.state, self.reason = "blocked", "Caption service has not started"
        self.phase = "idle"
        self.last_refusal = None
        self.last_trip = None
        self.cpu_health = {}
        self.advice = []
        self.tracks = set()
        self.monitor = None
        self.lock = asyncio.Lock()
        self.initial = None
        self.max_tracks = 10
        self.desired_enabled = False
        self.auto_size = False
        self.failures = 0
        self.next_attempt = 0
        self.admin_action = False
        self.first = asyncio.Event()
        self.wake = asyncio.Event()
        self.fresh = False
        self.attempt_started = None
        # An administrator's capacity measurement holds the service; calls wait it out.
        self.measuring = False
        self.on_measured = None  # notified when a measurement ends, e.g. to republish
        # oomKills when the engine last became ready: a restart past a later kill is refused.
        self.oom_baseline = None
        self._outcome = None
        self.governor = None
        self._engine_cpu = None  # (pid, cpu seconds, clock) at the previous tick
        self._hook(pool)

    @property
    def engine_id(self):
        return getattr(self.pool, "engine_id", "whistle")

    def _hook(self, pool):
        pool.start_check = self._start_check
        # Observe-only while the thresholds are provisional: transcribers ask admit() and
        # are always admitted, and /status shows what would have been refused or shed.
        self.governor = LoadGovernor(
            ceiling=getattr(pool, "size", 0),
            per_speaker_mib=per_speaker_mib(self.engine_id),
            thresholds=self.thresholds,
            transcribers=getattr(pool, "transcribers", None),
            clock=self.clock,
            wall_clock=self.wall_clock,
        )
        self.governor.on_reopen = getattr(pool, "kick", None)
        pool.governor = self.governor
        self._engine_cpu = None

    def _start_check(self):
        """Why the backend must not spawn an engine process now, or None. Consulted before
        every spawn, restarts included: one model load inside a container the kernel has
        just OOM-killed would only be killed again, and the next one must fit."""
        data = self.reader()
        if self.oom_baseline is not None and data["oomKills"] > self.oom_baseline:
            return "The caption memory limit was exceeded"
        return start_problem(data, self.engine_id, getattr(self.pool, "rss_mib", None))

    def replace_pool(self, pool):
        # Only a stopped controller may swap engines; the monitor owns the running pool.
        if self.monitor and not self.monitor.done():
            raise RuntimeError("Stop caption supervision before replacing the speech engine")
        from . import transcriber

        self.pool = self.assessment.pool = pool
        self._hook(pool)
        transcriber.POOL = pool

    def _resolve(self, ok, reason):
        if self._outcome is not None and not self._outcome.done():
            self._outcome.set_result((ok, reason))

    async def wait_outcome(self, timeout):
        """The first result of the attempt launched by start(): ready, tripped or blocked."""
        if self._outcome is None:
            return (self.state == "ready", self.reason)
        try:
            return await asyncio.wait_for(asyncio.shield(self._outcome), timeout)
        except TimeoutError:
            return (False, "The speech engine did not load in time")

    def _retry_after(self):
        if self.state == "ready" and not self.measuring:
            return 0
        if self.measuring:
            remaining = getattr(self.assessment, "remaining", lambda: self.policy.poll)
            return max(1, int(remaining()))
        if self.phase == "cooling_down":
            return max(1, int(self.next_attempt - self.clock()))
        if self.phase == "loading":
            return LOADING_RETRY_S
        return int(self.policy.poll)

    def status(self):
        retry = self._retry_after()
        return {
            "state": self.state,
            "phase": "measuring" if self.measuring else self.phase,
            "reason": "Measuring caption capacity" if self.measuring else self.reason,
            "activeTracks": len(self.tracks),
            "maxTracks": self.max_tracks,
            "lastRefusal": self.last_refusal,
            "lastTrip": self.last_trip,
            "failures": self.failures,
            "adminActionRequired": self.admin_action,
            "automaticRecovery": self.desired_enabled,
            "retryAfterSeconds": retry,
            "nextAttemptAt": self.wall_clock() + retry if retry else None,
            "closingRooms": len(getattr(self.service, "closing", {})),
            "cpuHealth": self.cpu_health,
            "advice": list(self.advice),
            "admission": self.governor.status(),
        }

    def require_ready(self):
        if self.state != "ready":
            raise ResourceRefused(self.reason)
        if self.measuring:
            raise ResourceRefused("Measuring caption capacity")

    def allow_room(self, rooms):
        # Load is decided per speaker when they speak (LoadGovernor.admit), not per room:
        # a room opens while admission is closed and its first speaker hears why.
        self.require_ready()
        if len(self.tracks) >= self.max_tracks:
            self.last_refusal = "Caption audio-track budget reached"
            raise ResourceRefused(self.last_refusal)

    def reserve_track(self):
        self.require_ready()
        if len(self.tracks) >= self.max_tracks:
            self.last_refusal = "Caption audio-track budget reached"
            raise ResourceRefused(self.last_refusal)
        lease = TrackLease(self)
        self.tracks.add(lease)
        return lease

    async def start(self, *, fresh=False):
        async with self.lock:
            if self.monitor and not self.monitor.done():
                self.wake.set()
                return
            self.desired_enabled = True
            # A deliberate engine switch is bounded by its manager, not the cooldown.
            self.fresh = fresh
            self._outcome = asyncio.get_running_loop().create_future()
            self.first.clear()
            self.monitor = asyncio.create_task(self._supervise())
        # Do not hold the lifecycle lock during model loading: disable must cancel it.
        await self.first.wait()

    async def measure(self, calls_starting=lambda: 0):
        """Run an administrator's capacity measurement on the idle, ready engine. Calls are
        refused from the moment it is claimed: `measuring` is set before the rooms and the
        calls being set up are checked, with no await between, so a call either sees it or
        is seen. Its result is displayed, never used as a gate."""
        if self.state != "ready":
            raise ResourceRefused(f"Captions are not ready: {self.reason}")
        if self.measuring:
            raise ResourceRefused("A capacity measurement is already running")
        self.measuring = True
        problem = None
        if calls_starting() or getattr(self.service, "clients", None):
            problem = "Measuring needs every captioned call to end first"
        elif not self.pool.is_idle():
            problem = "Captions are still in use"
        if problem:
            self.measuring = False
            raise ResourceRefused(problem)
        self.governor.measuring = True
        self.assessment.start()
        self.assessment.task.add_done_callback(self._measured)

    def _measured(self, task):
        self.measuring = False
        self.governor.measuring = False
        if not task.cancelled() and self.on_measured is not None:
            self.on_measured()

    async def _pause(self, seconds):
        try:
            await asyncio.wait_for(self.wake.wait(), seconds)
        except TimeoutError:
            pass
        self.wake.clear()

    def _persist(self, pending, running=False):
        deadline = self.wall_clock() + max(0, self.next_attempt - self.clock())
        self.journal.write(
            self.failures, deadline, pending, self.reason, self.last_trip, running=running
        )

    def _restore(self, saved):
        """Apply the journal at boot. A crash is counted once and waited out; neither a
        0.7.x capacity-check cooldown nor an upgrade that stopped a load is a crash."""
        self.failures = saved.get("failures", 0)
        if isinstance(saved.get("lastTrip"), dict):
            self.last_trip = saved["lastTrip"]
        remaining = min(
            self.policy.cap * 1.2,
            max(0, saved.get("nextAttemptAt", 0) - self.wall_clock()),
        )
        crashed = saved.get("engineRunning") and not saved.get("attemptInProgress")
        interrupted = bool(saved.get("attemptInProgress"))
        clean = False
        if _legacy_check(saved):
            LOGGER.info("Clearing a 0.7.x capacity-check cooldown from the recovery journal")
            self.failures, remaining, crashed, interrupted, clean = 0, 0, False, False, True
        elif interrupted and saved.get("appVersion") != __version__:
            # The container stopped mid-load for an upgrade, not because the load crashed.
            LOGGER.info("Starting cleanly after an upgrade interrupted the previous load")
            interrupted, clean = False, True
        if crashed:
            # The ready engine died without a clean stop; its run was uncounted.
            self.failures = min(20, self.failures + 1)
        if interrupted or crashed:
            remaining = max(remaining, self.policy.delay(self.failures, self.jitter))
        self.next_attempt = self.clock() + remaining
        if remaining:
            self.reason = "Recovering after an interrupted caption process"
        if self.fresh:
            self.fresh = False
            self.failures, self.next_attempt = 0, 0
            self._persist(False)
        elif crashed or clean:
            self._persist(False)  # Count this crash once, even if boot dies again.

    async def _supervise(self):
        try:
            await self.pool.close()
            # An unreadable journal is an installation problem, never permission
            # to forget the cooldown and load models repeatedly.
            while self.desired_enabled:
                try:
                    self._restore(self.journal.read())
                    break
                except (OSError, ValueError, TypeError):
                    self.state, self.phase = "blocked", "configuration"
                    self.reason = "Recovery journal is unreadable; check persistent storage"
                    self.admin_action = True
                    self._resolve(False, self.reason)
                    self.first.set()
                    await self._pause(self.policy.poll)
            while self.desired_enabled:
                pause = self.policy.poll
                try:
                    data = self.reader()
                    if self.auto_size and self.pool.closed:
                        self.pool.resize(
                            catalog.worker_ceiling(self.engine_id, data["effectiveCpus"])
                        )
                    self.advice = advice(data)
                    problem = static_problem(data, self.pool.size, self.engine_id)
                    if not problem:
                        whistle = catalog.get(self.engine_id).kind == "worker_pool"
                        problem = start_problem(
                            data,
                            self.engine_id,
                            getattr(self.pool, "rss_mib", None),
                            workers=self.pool.size if whistle else 1,
                        )
                    if problem:
                        # Configuration, not a failure: nothing was loaded, nothing counts.
                        self.state, self.phase = "blocked", "configuration"
                        self.reason, self.admin_action = problem, True
                        self._resolve(False, problem)
                    elif self.clock() < self.next_attempt:
                        self.state, self.phase = "recovering", "cooling_down"
                        self.admin_action = False
                        # Retry at the deadline itself, not at the next poll after it.
                        pause = min(pause, max(0.01, self.next_attempt - self.clock()))
                    else:
                        self.admin_action = False
                        await self._load(data)
                        continue
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.state, self.phase = "blocked", "configuration"
                    self.reason = "Resource monitoring or recovery storage is unavailable"
                    self.admin_action = True
                    self._resolve(False, self.reason)
                self.first.set()
                await self._pause(pause)
        finally:
            self.first.set()
            await self._teardown()

    async def _load(self, data):
        self.failures = min(20, self.failures + 1)
        # Crash guard: an OOM restart during the load waits this out.
        self.next_attempt = self.clock() + self.policy.delay(self.failures, self.jitter)
        self.reason = f"Loading {catalog.get(self.engine_id).name}"
        self._persist(True)  # Must succeed before any expensive allocation.
        self.attempt_started = self.clock()
        self.state, self.phase = "recovering", "loading"
        self.oom_baseline = data["oomKills"]
        try:
            # Generation fences and complete teardown are prerequisites for reuse.
            async with asyncio.timeout(catalog.get(self.engine_id).requirements.start_timeout_s):
                await self.pool.start()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.warning("Speech engine did not load (%s)", type(error).__name__)
            # A refused load names its reason; an administrator's rollback message shows it.
            await self.trip(
                str(error)
                if isinstance(error, CapacityError) and str(error)
                else f"{catalog.get(self.engine_id).name} could not be loaded"
            )
            return
        self.initial = self.reader()
        self.oom_baseline = self.initial["oomKills"]
        self.governor.ceiling = self.pool.size  # auto_size may have resized the backend
        self.state, self.phase, self.reason = "ready", "ready", "Captions are ready"
        # The attempt was counted as a failure in case it crashed; it did not.
        self.failures, self.next_attempt = max(0, self.failures - 1), 0
        self._persist(False, running=True)
        self._resolve(True, self.reason)
        self.first.set()
        try:
            await self._watch()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.warning("Caption supervision failed (%s)", type(error).__name__)
            await self.trip("Recognition workers or resource monitoring failed")

    async def _watch(self):
        previous = self.initial
        previous_now = self.clock()
        expected = self.clock() + self.policy.monitor
        ready_since = previous_now
        while self.desired_enabled:
            await asyncio.sleep(max(0, expected - self.clock()))
            now = self.clock()
            lag, expected = now - expected, now + self.policy.monitor
            started = time.perf_counter()
            current = self.reader()
            self._observe(current, now)
            tick_ms = round((time.perf_counter() - started) * 1000, 2)
            if current["oomKills"] > previous["oomKills"]:
                await self.trip("The caption memory limit was exceeded")
                return
            elapsed = max(0.001, now - previous_now)
            before, after = previous.get("throttledUsec"), current.get("throttledUsec")
            self.cpu_health = {
                "eventLoopLagMs": round(lag * 1000, 1),
                "hostPressureAvg10": current.get("hostCpuPressure"),
                "throttledMsPerSecond": (
                    round(max(0, after - before) / 1000 / elapsed, 1)
                    if before is not None and after is not None
                    else None
                ),
                # Snapshot, duty sample and governor tick; the design budgets 5 ms.
                "monitorTickMs": tick_ms,
            }
            if self.pool.failure_circuit_open:
                await self.trip("Repeated recognition worker failures")
                return
            previous, previous_now = current, now
            if ready_since is not None and now - ready_since >= self.policy.stable_window:
                self.failures, self.next_attempt = 0, 0
                self._persist(False, running=True)  # Still running: a later crash must count.
                ready_since = None
            # CPU pressure, limit changes and lag are not trips: the governor holds and
            # sheds speakers on them, and the next tick simply reads the new numbers.

    def _observe(self, current, now):
        """Feed the governor one sample: the snapshot plus the engine host's duty."""
        self.governor.measuring = self.measuring
        self.governor.tick({**current, "engineDuty": self._engine_duty(now)}, now)

    def _engine_duty(self, now):
        """CPU of the shared engine host per decode thread since the previous tick (the
        harness reports 0.98 at saturation); None for Whistle, before the first delta and
        across a host restart. One /proc read, well under the tick budget."""
        pid = getattr(self.pool, "host_pid", None)
        if pid is None:
            self._engine_cpu = None
            return None
        try:
            cpu, _ = process_usage(pid)
        except (OSError, ValueError, IndexError):
            self._engine_cpu = None
            return None
        previous, self._engine_cpu = self._engine_cpu, (pid, cpu, now)
        if previous is None or previous[0] != pid:
            return None
        threads = getattr(self.pool, "threads", 1)
        return round(max(0.0, cpu - previous[1]) / max(0.001, now - previous[2]) / threads, 3)

    async def trip(self, reason):
        if self.state == "ready":
            self.failures = min(20, self.failures + 1)  # A running engine's attempt was uncounted.
        self.state, self.phase, self.reason = "recovering", "cooling_down", reason
        self.failures = max(1, self.failures)
        delay = self.policy.delay(self.failures, self.jitter)
        self.next_attempt = self.clock() + delay
        self._resolve(False, reason)
        # Rooms close silently otherwise; the reason must outlive recovery.
        self.last_trip = {"reason": reason, "at": round(self.wall_clock()), **self.cpu_health}
        LOGGER.warning(
            "Captions paused, closing all rooms: %s; retrying in %d s (CPU health: %s)",
            reason,
            delay,
            self.cpu_health or "unavailable",
        )
        # Persist first so a teardown crash cannot reset the backoff.
        try:
            self._persist(False)
        finally:
            await self._teardown()

    async def _teardown(self):
        await self.pool.close()
        await self.assessment.interrupt()
        self.measuring = False
        if self.service is not None:
            # Shielded, tracked room teardown must finish before workers reopen.
            try:
                async with asyncio.timeout(30):
                    await self.service.stop_rooms()
            except Exception:
                # The journal survives container restarts. Exiting reaps leaked
                # decoders; reopening a pool while teardown failed is forbidden.
                self.fatal_exit(75)
                raise RuntimeError("Caption transport teardown failed")
        for lease in list(self.tracks):
            lease.release()

    async def close(self, reason=None):
        task = asyncio.create_task(self._close(reason or "Caption service stopped"))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise

    async def _close(self, reason):
        async with self.lock:
            if self.phase == "loading":
                # A stop is not a crash: undo the attempt's provisional failure and deadline.
                self.failures, self.next_attempt = max(0, self.failures - 1), 0
            self.desired_enabled = False
            self.state, self.phase = "disabled", "disabled"
            self._resolve(False, reason)
            with suppress(OSError, ValueError):
                self._persist(False)
            if self.monitor:
                self.monitor.cancel()
                with suppress(asyncio.CancelledError):
                    await self.monitor
                self.monitor = None
            else:
                await self._teardown()
            self.state, self.phase = "disabled", "disabled"
            self.reason = reason
            self.admin_action = False
            with suppress(OSError, ValueError):
                self._persist(False)
