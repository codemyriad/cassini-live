"""One supervisor owns admission, shutdown and bounded automatic recovery."""

import asyncio
import os
import random
import time
from contextlib import suppress

from .recovery import RecoveryJournal, RecoveryPolicy, recovery_problem
from .resources import budget_problem, pressure_problem, signature, snapshot


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
        self.pool.resource_check = lambda: pressure_problem(self.reader())

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
            "failures": self.failures,
            "adminActionRequired": self.admin_action,
            "automaticRecovery": self.desired_enabled,
            "retryAfterSeconds": max(1, int(retry)) if self.state != "ready" else 0,
            "nextAttemptAt": self.wall_clock() + retry if retry else None,
            "closingRooms": len(getattr(self.service, "closing", {})),
        }

    def require_ready(self):
        if self.state != "ready":
            raise ResourceRefused(self.reason)

    def allow_room(self, rooms):
        self.require_ready()
        if rooms >= min(2, self.assessment.result.get("testedSpeakers", 0)):
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

    async def start(self):
        async with self.lock:
            if self.monitor and not self.monitor.done():
                self.wake.set()
                return
            self.desired_enabled = True
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

    def _persist(self, pending):
        deadline = self.wall_clock() + max(0, self.next_attempt - self.clock())
        self.journal.write(self.failures, deadline, pending, self.reason)

    async def _supervise(self):
        try:
            await self.pool.close()
            # An unreadable journal is an installation problem, never permission
            # to forget the cooldown and load models repeatedly.
            while self.desired_enabled:
                try:
                    saved = self.journal.read()
                    self.failures = saved.get("failures", 0)
                    remaining = min(
                        self.policy.cap * 1.2 + 180,
                        max(0, saved.get("nextAttemptAt", 0) - self.wall_clock()),
                    )
                    if saved.get("attemptInProgress"):
                        remaining = max(remaining, self.policy.delay(self.failures, self.jitter))
                    self.next_attempt = self.clock() + remaining
                    self.must_settle = bool(remaining or self.failures)
                    if remaining:
                        self.reason = "Recovering after an interrupted caption process"
                    break
                except (OSError, ValueError, TypeError):
                    self.state, self.phase = "blocked", "configuration"
                    self.reason = "Recovery journal is unreadable; check persistent storage"
                    self.admin_action = True
                    self.first.set()
                    await self._pause(self.policy.poll)
            while self.desired_enabled:
                try:
                    data = self.reader()
                    if self.auto_size and self.pool.closed:
                        self.pool.size = min(4, max(1, int(data["effectiveCpus"] // 2)))
                        self.pool.idle = asyncio.Queue(maxsize=self.pool.size)
                    problem = budget_problem(data, self.pool.size)
                    if not problem and not data.get("pressureAvailable", False):
                        problem = (
                            "Linux PSI pressure statistics are unavailable; enable and expose PSI"
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
                    else:
                        self.admin_action = False
                        headroom = recovery_problem(data, self.pool.size)
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
                self.first.set()
                await self._pause(self.policy.poll)
        finally:
            self.first.set()
            await self._teardown()

    async def _attempt(self, data):
        self.failures = min(20, self.failures + 1)
        self.next_attempt = self.clock() + self.policy.delay(self.failures, self.jitter) + 180
        self.reason = "Checking caption capacity"
        self._persist(True)  # Must succeed before any expensive allocation.
        self.state, self.phase = "checking", "loading"
        self.initial = data
        try:
            # Generation fences and complete teardown are prerequisites for reuse.
            async with asyncio.timeout(30):
                await self.pool.start()
            self.assessment.start()
            self.phase = "calibrating"
            self.first.set()
            await self._watch()
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.trip("Recognition workers or resource monitoring failed")

    async def _watch(self):
        previous = self.initial
        pressure_ticks = 0
        expected = self.clock() + self.policy.monitor
        ready_since = None
        while self.desired_enabled:
            await asyncio.sleep(max(0, expected - self.clock()))
            now = self.clock()
            lag, expected = now - expected, now + self.policy.monitor
            current = self.reader()
            problem = pressure_problem(current)
            if signature(current) != signature(self.initial):
                await self.trip("Resource limits changed; reassessing capacity automatically")
                return
            if current["oomKills"] > previous["oomKills"]:
                await self.trip("The caption memory limit was exceeded")
                return
            periods = current["cpuPeriods"] - previous["cpuPeriods"]
            throttled = current["throttledPeriods"] - previous["throttledPeriods"]
            strained = lag > 0.5 or (periods > 0 and throttled / periods >= 0.8)
            pressure_ticks = pressure_ticks + 1 if strained else 0
            if problem or pressure_ticks >= 3 or self.pool.failure_circuit_open:
                await self.trip(problem or "Caption resource pressure is sustained")
                return
            previous = current
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
                self._persist(False)
                ready_since = now
            elif ready_since is not None and now - ready_since >= self.policy.stable_window:
                self.failures, self.next_attempt = 0, 0
                self._persist(False)
                ready_since = None
            # Excess demand is handled by bounded queues and admission, not a
            # service-wide restart. Real resource pressure remains monitored.

    async def trip(self, reason, *, insufficient=False):
        self.state, self.phase, self.reason = "recovering", "cooling_down", reason
        self.healthy_since = None
        self.must_settle = True
        self.failures = max(1, self.failures)
        delay = self.policy.delay(self.failures, self.jitter)
        if insufficient:
            delay = max(delay, self.policy.insufficient)
        self.next_attempt = self.clock() + delay
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

    async def close(self):
        task = asyncio.create_task(self._close())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.shield(task)
            raise

    async def _close(self):
        async with self.lock:
            self.desired_enabled = False
            self.state, self.phase = "disabled", "disabled"
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
            self.reason = "Caption service stopped"
            self.admin_action = False
            with suppress(OSError, ValueError):
                self._persist(False)
