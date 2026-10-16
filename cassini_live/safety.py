"""One supervisor owns admission, shutdown and bounded automatic recovery."""

import asyncio
import logging
import os
import random
import time
from contextlib import suppress

from . import catalog
from .diagnostics import process_usage
from .load import LoadGovernor, per_speaker_mib
from .pool import CapacityError
from .recovery import RecoveryJournal, RecoveryPolicy, recovery_problem
from .resources import budget_problem, load_problem, pressure_problem, signature, snapshot

LOGGER = logging.getLogger(__name__)


def cpu_pressure_reason(previous, current, elapsed, lag):
    """Quota frequency alone cannot distinguish tiny stalls from overload."""
    if lag > 0.5:
        return "Caption event loop is falling behind"
    host = current.get("hostCpuPressure")
    if host is None:
        return "Host CPU pressure monitoring is unavailable"
    if host >= 20:
        return "Host CPU pressure is sustained"
    periods = current["cpuPeriods"] - previous["cpuPeriods"]
    throttled = current["throttledPeriods"] - previous["throttledPeriods"]
    if periods > 0 and throttled / periods >= 0.8:
        before, after = previous.get("throttledUsec"), current.get("throttledUsec")
        if before is None or after is None or after < before:
            return "Caption CPU quota pressure (severity unavailable)"
        if (after - before) / 1e6 / max(0.001, elapsed) >= 0.2:
            return "Caption CPU quota stalls are sustained"
    return None


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
    ):
        self.pool, self.assessment, self.service = pool, assessment, service
        self.reader, self.clock, self.wall_clock, self.jitter = reader, clock, wall_clock, jitter
        self.policy = policy or RecoveryPolicy()
        self.fatal_exit = fatal_exit
        self.journal = journal or RecoveryJournal()
        self.state, self.reason = "blocked", "Capacity has not been established"
        self.phase = "idle"
        self.last_refusal = None
        self.last_trip = None
        self.cpu_health = {}
        self.tracks = set()
        self.monitor = None
        self.lock = asyncio.Lock()
        self.initial = None
        self.max_tracks = 10
        self.desired_enabled = False
        self.auto_size = False
        self.must_settle = False
        self.failures = 0
        self.next_attempt = 0
        self.healthy_since = None
        self.admin_action = False
        self.first = asyncio.Event()
        self.wake = asyncio.Event()
        self.fresh = False
        self.attempt_started = None
        self._outcome = None
        self.governor = None
        self._engine_cpu = None  # (pid, cpu seconds, clock) at the previous tick
        self._hook(pool)

    @property
    def engine_id(self):
        return getattr(self.pool, "engine_id", "whistle")

    def _hook(self, pool):
        pool.resource_check = lambda: pressure_problem(self.reader())
        pool.load_check = lambda: load_problem(self.reader(), self.engine_id)
        # 0.7.3 observes only (0.8.0-instant-admission.md, step 4): the governor reads the
        # backend's live transcribers from the 1 s tick and reports what it would decide;
        # nothing consults admit() and no transcriber is marked. The seat ceiling is the
        # backend's structural size until step 6 re-derives it from the engine threads.
        self.governor = LoadGovernor(
            ceiling=getattr(pool, "size", 0),
            per_speaker_mib=per_speaker_mib(self.engine_id),
            observe_only=True,
            transcribers=getattr(pool, "transcribers", None),
            clock=self.clock,
            wall_clock=self.wall_clock,
        )
        self._engine_cpu = None

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
            return (False, "The capacity check did not finish in time")

    def status(self):
        retry = max(0, self.next_attempt - self.clock())
        if self.state in {"blocked", "recovering"}:
            settle = self.policy.headroom_window if self.must_settle else 0
            if self.healthy_since is not None:
                settle = max(0, settle - (self.clock() - self.healthy_since))
            retry = max(retry, settle, self.policy.poll)
        else:
            retry = 0
        return {
            "state": self.state,
            "phase": self.phase,
            "reason": self.reason,
            "activeTracks": len(self.tracks),
            "maxTracks": self.max_tracks,
            "lastRefusal": self.last_refusal,
            "lastTrip": self.last_trip,
            "failures": self.failures,
            "adminActionRequired": self.admin_action,
            "automaticRecovery": self.desired_enabled,
            "retryAfterSeconds": max(1, int(retry)) if self.state != "ready" else 0,
            "nextAttemptAt": self.wall_clock() + retry if retry else None,
            "closingRooms": len(getattr(self.service, "closing", {})),
            "cpuHealth": self.cpu_health,
            "admission": self.governor.status(),
        }

    def require_ready(self):
        if self.state != "ready":
            raise ResourceRefused(self.reason)

    def allow_room(self, rooms):
        self.require_ready()
        speakers = self.assessment.result.get("testedSpeakers", 0)
        # Whistle's two-room cap predates the shared engine, which serves every tested speaker.
        if catalog.get(self.engine_id).kind == "worker_pool":
            speakers = min(2, speakers)
        if rooms >= speakers:
            self.last_refusal = "Caption room budget reached"
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

    async def recheck(self):
        # A manual request cannot bypass the persisted cooldown or race a load.
        if not self.desired_enabled:
            raise ResourceRefused("Caption service is disabled")
        self.wake.set()

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

    async def _supervise(self):
        try:
            await self.pool.close()
            # An unreadable journal is an installation problem, never permission
            # to forget the cooldown and load models repeatedly.
            while self.desired_enabled:
                try:
                    saved = self.journal.read()
                    self.failures = saved.get("failures", 0)
                    if isinstance(saved.get("lastTrip"), dict):
                        self.last_trip = saved["lastTrip"]
                    remaining = min(
                        self.policy.cap * 1.2,
                        max(0, saved.get("nextAttemptAt", 0) - self.wall_clock()),
                    )
                    crashed = saved.get("engineRunning") and not saved.get("attemptInProgress")
                    if crashed:
                        # The ready engine died without a clean stop; its run was uncounted.
                        self.failures = min(20, self.failures + 1)
                    if saved.get("attemptInProgress") or crashed:
                        remaining = max(remaining, self.policy.delay(self.failures, self.jitter))
                    self.next_attempt = self.clock() + remaining
                    self.must_settle = bool(remaining or self.failures)
                    if remaining:
                        self.reason = "Recovering after an interrupted caption process"
                    if self.fresh:
                        self.fresh = False
                        self.failures, self.next_attempt, self.must_settle = 0, 0, False
                        self._persist(False)
                    elif crashed:
                        self._persist(False)  # Count this crash once, even if boot dies again.
                    break
                except (OSError, ValueError, TypeError):
                    self.state, self.phase = "blocked", "configuration"
                    self.reason = "Recovery journal is unreadable; check persistent storage"
                    self.admin_action = True
                    self._resolve(False, self.reason)
                    self.first.set()
                    await self._pause(self.policy.poll)
            while self.desired_enabled:
                try:
                    data = self.reader()
                    if self.auto_size and self.pool.closed:
                        self.pool.resize(
                            catalog.worker_ceiling(self.engine_id, data["effectiveCpus"])
                        )
                    problem = budget_problem(data, self.pool.size, self.engine_id)
                    if not problem and not data.get("pressureAvailable", False):
                        problem = (
                            "Linux PSI pressure statistics are unavailable; enable and expose PSI"
                        )
                    if not problem and data.get("hostCpuPressure") is None:
                        problem = (
                            "Host CPU PSI statistics are unavailable; expose /proc/pressure/cpu"
                        )
                    if problem:
                        transient = problem == pressure_problem(data) and data.get(
                            "monitoringAvailable"
                        )
                        self.state = "recovering" if transient else "blocked"
                        self.phase = "waiting_headroom" if transient else "configuration"
                        self.reason, self.admin_action = problem, not transient
                        self.healthy_since = None
                        self.must_settle = True
                        if not transient:
                            self._resolve(False, problem)
                    else:
                        self.admin_action = False
                        headroom = recovery_problem(data, self.pool.size, self.engine_id)
                        if headroom:
                            self.healthy_since = None
                            self.must_settle = True
                        elif self.healthy_since is None:
                            self.healthy_since = self.clock()
                        window = self.policy.headroom_window if self.must_settle else 0
                        waiting = (
                            headroom
                            or self.clock() < self.next_attempt
                            or self.clock()
                            - (
                                self.healthy_since
                                if self.healthy_since is not None
                                else self.clock()
                            )
                            < window
                        )
                        if waiting:
                            self.state, self.phase = "recovering", "waiting_headroom"
                            if headroom:
                                self.reason = headroom
                            elif self.clock() < self.next_attempt:
                                self.phase = "cooling_down"
                            else:
                                self.reason = "Waiting for sustained resource headroom"
                        else:
                            await self._attempt(data)
                            continue
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.state, self.phase = "blocked", "configuration"
                    self.reason = "Resource monitoring or recovery storage is unavailable"
                    self.admin_action = True
                    self.healthy_since = None
                    self._resolve(False, self.reason)
                self.first.set()
                await self._pause(self.policy.poll)
        finally:
            self.first.set()
            await self._teardown()

    async def _attempt(self, data):
        self.failures = min(20, self.failures + 1)
        # Crash guard: an OOM restart during the load waits this out.
        self.next_attempt = self.clock() + self.policy.delay(self.failures, self.jitter)
        self.reason = "Checking caption capacity"
        self._persist(True)  # Must succeed before any expensive allocation.
        self.attempt_started = self.clock()
        self.state, self.phase = "checking", "loading"
        self.initial = data
        try:
            # Generation fences and complete teardown are prerequisites for reuse.
            async with asyncio.timeout(catalog.get(self.engine_id).requirements.start_timeout_s):
                await self.pool.start()
            self.governor.ceiling = self.pool.size  # auto_size may have resized the backend
            self.assessment.start()
            self.phase = "calibrating"
            self.first.set()
            await self._watch()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.warning("Caption supervision failed (%s)", type(error).__name__)
            # A refused load names its reason; an administrator's rollback message shows it.
            await self.trip(
                str(error)
                if isinstance(error, CapacityError) and str(error)
                else "Recognition workers or resource monitoring failed"
            )

    async def _watch(self):
        previous = self.initial
        previous_now = self.clock()
        pressure_ticks = 0
        expected = self.clock() + self.policy.monitor
        ready_since = None
        while self.desired_enabled:
            await asyncio.sleep(max(0, expected - self.clock()))
            now = self.clock()
            lag, expected = now - expected, now + self.policy.monitor
            started = time.perf_counter()
            current = self.reader()
            self._observe(current, now)
            tick_ms = round((time.perf_counter() - started) * 1000, 2)
            problem = pressure_problem(current)
            if signature(current) != signature(self.initial):
                await self.trip("Resource limits changed; reassessing capacity automatically")
                return
            if current["oomKills"] > previous["oomKills"]:
                await self.trip("The caption memory limit was exceeded")
                return
            elapsed = max(0.001, now - previous_now)
            cpu_problem = cpu_pressure_reason(previous, current, elapsed, lag)
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
            pressure_ticks = pressure_ticks + 1 if cpu_problem else 0
            if problem or pressure_ticks >= 3 or self.pool.failure_circuit_open:
                await self.trip(problem or cpu_problem or "Repeated recognition worker failures")
                return
            previous, previous_now = current, now
            if self.state == "checking" and self.assessment.task.done():
                report = self.assessment.result
                if report["state"] not in {"passed", "limited"} or not report["testedSpeakers"]:
                    await self.trip(
                        "Capacity check did not establish a safe speaking budget", insufficient=True
                    )
                    return
                self.pool.admission_limit = report["testedSpeakers"]
                self.state, self.phase = "ready", "ready"
                self.reason = "Capacity check passed"
                # The attempt was counted as a failure in case it crashed; it did not.
                self.failures, self.next_attempt = max(0, self.failures - 1), 0
                self._persist(False, running=True)
                self._resolve(True, self.reason)
                ready_since = now
            elif ready_since is not None and now - ready_since >= self.policy.stable_window:
                self.failures, self.next_attempt = 0, 0
                self._persist(False, running=True)  # Still running: a later crash must count.
                ready_since = None
            # Excess demand is handled by bounded queues and admission, not a
            # service-wide restart. Real resource pressure remains monitored.

    def _observe(self, current, now):
        """Feed the governor one sample: the snapshot plus the engine host's duty. In
        observe-only mode this changes nothing a caller sees beyond /status."""
        task = getattr(self.assessment, "task", None)
        self.governor.measuring = bool(task) and not task.done()
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

    async def trip(self, reason, *, insufficient=False):
        if self.state == "ready":
            self.failures = min(20, self.failures + 1)  # A running engine's attempt was uncounted.
        self.state, self.phase, self.reason = "recovering", "cooling_down", reason
        self.healthy_since = None
        self.must_settle = True
        self.failures = max(1, self.failures)
        delay = self.policy.delay(self.failures, self.jitter)
        if insufficient:
            delay = max(delay, self.policy.insufficient)
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
            if self.state == "checking":
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
