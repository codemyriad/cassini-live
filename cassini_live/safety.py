"""Fail closed before allocating work; latch overload until an explicit recheck."""

import asyncio
import time
from contextlib import suppress

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
    def __init__(self, pool, assessment, service=None, reader=snapshot):
        self.pool, self.assessment, self.service = pool, assessment, service
        self.reader = reader
        self.state = "blocked"
        self.reason = "Capacity has not been established"
        self.last_refusal = None
        self.tracks = set()
        self.monitor = None
        self.lock = asyncio.Lock()
        self.initial = None
        self.max_tracks = 10  # The installation workload tests exactly ten input tracks.
        self.pool.resource_check = lambda: pressure_problem(self.reader())

    def status(self):
        return {
            "state": self.state,
            "reason": self.reason,
            "activeTracks": len(self.tracks),
            "maxTracks": self.max_tracks,
            "lastRefusal": self.last_refusal,
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
            await self._stop_monitor()
            await self.assessment.interrupt()
            self.state, self.reason = "blocked", "Checking resource limits"
            await self.pool.close()
            data = self.reader()
            if problem := budget_problem(data, self.pool.size):
                self.reason = problem
                return
            self.initial = data
            self.state, self.reason = (
                "checking",
                "Capacity check must pass before captions can start",
            )
            try:
                await self.pool.start()
            except Exception:
                self.state, self.reason = "blocked", "Recognition workers could not start safely"
                return
            self.assessment.start()
            self.monitor = asyncio.create_task(self._watch())

    async def _watch(self):
        previous = self.initial
        pressure_ticks = 0
        expected = time.monotonic() + 1
        dropped = self.pool.dropped_chunks
        drop_time = time.monotonic()
        try:
            while True:
                await asyncio.sleep(max(0, expected - time.monotonic()))
                now = time.monotonic()
                lag, expected = now - expected, now + 1
                current = self.reader()
                problem = pressure_problem(current)
                if signature(current) != signature(self.initial):
                    await self.trip("Resource limits changed; run the capacity check again")
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
                        await self.trip("Capacity check did not establish a safe speaking budget")
                        return
                    self.pool.admission_limit = report["testedSpeakers"]
                    self.state, self.reason = "ready", "Capacity check passed"
                    dropped, drop_time = self.pool.dropped_chunks, now
                elif self.state == "ready":
                    if self.pool.dropped_chunks - dropped >= 3:
                        await self.trip("Caption workload exceeds the measured capacity")
                        return
                    if now - drop_time >= 15:
                        dropped, drop_time = self.pool.dropped_chunks, now
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.trip("Resource monitoring failed; captions stopped")

    async def trip(self, reason):
        async with self.lock:
            self.state, self.reason = "blocked", reason
            # Close workers first: cancellation cannot spawn replacements or leave
            # native recognition chewing CPU during transport teardown.
            await self.pool.close()
            await self.assessment.interrupt()
            if self.service is not None:
                with suppress(Exception):
                    async with asyncio.timeout(5):
                        await self.service.stop_rooms()
            for lease in list(self.tracks):
                lease.release()

    async def _stop_monitor(self):
        if self.monitor and self.monitor is not asyncio.current_task():
            self.monitor.cancel()
            with suppress(asyncio.CancelledError):
                await self.monitor
        self.monitor = None

    async def close(self):
        await self._stop_monitor()
        await self.trip("Caption service stopped")
