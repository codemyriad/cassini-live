import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from cassini_live import capacity
from cassini_live.capacity import CapacityAssessment
from cassini_live.pool import WorkerPool
from cassini_live.recovery import RecoveryJournal, RecoveryPolicy, recovery_problem
from cassini_live.safety import SafetyController
from tests.test_safety import SAFE
from tests.test_streaming import FakeWorker


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.002)


@pytest.fixture
def harness(monkeypatch):
    async def scenario(pool, count):
        return {"passed": True, "speakers": count}

    monkeypatch.setattr(capacity, "scenario", scenario)
    data, clock = dict(SAFE), Clock()
    pool = WorkerPool(1, worker_factory=FakeWorker)
    policy = replace(RecoveryPolicy(), monitor=0.005, poll=0.005)
    guard = SafetyController(
        pool,
        CapacityAssessment(pool),
        reader=lambda: dict(data),
        clock=clock,
        wall_clock=clock,
        policy=policy,
        jitter=lambda: 0,
    )
    return guard, pool, data, clock


async def advance(guard, clock, seconds):
    clock.now += seconds
    guard.wake.set()
    await asyncio.sleep(0.025)


def test_retry_budget_grows_caps_and_jitter_never_shortens_minimum():
    policy = RecoveryPolicy()
    assert [policy.delay(i, lambda: 0) for i in range(1, 8)] == [
        120,
        240,
        480,
        960,
        1920,
        3600,
        3600,
    ]
    assert policy.delay(20, lambda: 1) == 4320
    assert policy.insufficient == 900


@pytest.mark.parametrize("pressure", ["cpuPressure", "hostCpuPressure"])
async def test_automatic_recovery_requires_cooldown_and_continuous_headroom(harness, pressure):
    guard, pool, data, clock = harness
    try:
        await guard.start()
        await until(lambda: guard.state == "ready")
        data["memoryFraction"] = 0.9
        await until(lambda: pool.closed)
        assert guard.state == "recovering" and not guard.admin_action
        data.update(SAFE)
        await advance(guard, clock, 120)
        assert pool.closed  # Cooldown elapsed, but sustained headroom is still required.
        await advance(guard, clock, 179)
        assert pool.closed
        data[pressure] = 30
        await advance(guard, clock, 1)
        data.update(SAFE)
        await advance(guard, clock, 1)
        await advance(guard, clock, 179)
        assert pool.closed  # Pressure spike reset the healthy window.
        await advance(guard, clock, 2)
        await until(lambda: guard.state == "ready")
        assert len(pool.workers) == 1 and guard.failures == 2
    finally:
        await guard.close()


async def test_failed_calibration_uses_long_backoff_and_recovers(harness, monkeypatch):
    guard, pool, _, clock = harness
    passed = False

    async def scenario(pool, count):
        return {"passed": passed, "speakers": count}

    monkeypatch.setattr(capacity, "scenario", scenario)
    try:
        await guard.start()
        await until(lambda: guard.state == "recovering")
        assert guard.next_attempt - clock() == 900
        passed = True
        await until(lambda: guard.healthy_since is not None)
        await advance(guard, clock, 899)
        assert pool.closed
        await advance(guard, clock, 2)
        await until(lambda: guard.state == "ready")
    finally:
        await guard.close()


async def test_disable_cancels_preload_and_never_resurrects():
    entered = asyncio.Event()

    class SlowWorker(FakeWorker):
        async def start(self):
            await super().start()
            entered.set()
            await asyncio.Future()

    pool = WorkerPool(1, worker_factory=SlowWorker)
    guard = SafetyController(pool, CapacityAssessment(pool), reader=lambda: dict(SAFE))
    start = asyncio.create_task(guard.start())
    await entered.wait()
    await guard.close()
    await start
    assert guard.state == "disabled" and not guard.desired_enabled
    assert not pool.workers and guard.monitor is None


async def test_disable_during_cooldown_and_reenable_preserves_backoff(harness):
    guard, pool, data, clock = harness
    await guard.start()
    await until(lambda: guard.state == "ready")
    data["memoryFraction"] = 0.9
    await until(lambda: pool.closed)
    await guard.close()
    data.update(SAFE)
    await advance(guard, clock, 1000)
    assert guard.state == "disabled" and not pool.workers


async def test_persisted_interrupted_attempt_waits_after_process_restart(harness, tmp_path):
    guard, pool, _, clock = harness
    journal = RecoveryJournal(tmp_path / "recovery.json")
    journal.write(2, clock() + 400, True, "Loading workers")
    guard.journal = journal
    try:
        await guard.start()
        assert pool.closed and not pool.workers
        assert guard.failures == 2 and guard.next_attempt >= clock() + 400
        await advance(guard, clock, 399)
        assert pool.closed
        await advance(guard, clock, 2)
        await until(lambda: guard.state == "ready")
        saved = journal.read()
        assert not saved["attemptInProgress"] and saved["failures"] == 3
    finally:
        await guard.close()


async def test_only_sustained_ready_resets_backoff(harness):
    guard, pool, _, clock = harness
    try:
        await guard.start()
        await until(lambda: guard.state == "ready")
        assert guard.failures == 1
        await advance(guard, clock, 1799)
        assert guard.failures == 1
        await advance(guard, clock, 2)
        await until(lambda: guard.failures == 0)
        assert guard.state == "ready" and not pool.closed
    finally:
        await guard.close()


async def test_configuration_fix_recalculates_default_workers(harness):
    guard, pool, data, clock = harness
    pool.size = 4
    guard.auto_size = True
    data["cpuQuotaCpus"] = None
    data["effectiveCpus"] = 8
    try:
        await guard.start()
        assert guard.admin_action and not pool.workers
        data.update(SAFE)
        await advance(guard, clock, 1)
        await advance(guard, clock, 181)
        await until(lambda: guard.state == "ready")
        assert pool.size == 2 and len(pool.workers) == 2
    finally:
        await guard.close()


async def test_old_worker_release_cannot_contaminate_new_generation():
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    stale = pool.acquire()
    await pool.close()
    await pool.start()
    await pool.release(stale, healthy=False)
    assert len(pool.workers) == 1 and pool.failures == 0 and pool.available == 1
    await pool.close()


async def test_excess_demand_does_not_trip_resource_safety(harness):
    guard, pool, _, _ = harness
    try:
        await guard.start()
        await until(lambda: guard.state == "ready")
        for _ in range(100):
            pool.note_drop()
        await asyncio.sleep(0.025)
        assert guard.state == "ready" and pool.status()["recentCapacityPressure"]
    finally:
        await guard.close()


def test_unknown_or_high_pressure_prevents_reload():
    assert recovery_problem({**SAFE, "hostCpuPressure": 30}, 1)
    assert recovery_problem({**SAFE, "hostCpuPressure": None}, 1)
    assert recovery_problem({**SAFE, "cpuPressure": 30}, 1)
    assert recovery_problem({**SAFE, "memoryPressure": 2}, 1)
    missing = dict(SAFE)
    missing.pop("cpuPressure")
    assert recovery_problem(missing, 1)


async def test_unwritable_journal_never_allocates_workers(harness):
    guard, pool, _, _ = harness
    guard.journal.write = lambda *args: (_ for _ in ()).throw(OSError("Read only"))
    pool.start = AsyncMock()
    try:
        await guard.start()
        assert guard.admin_action
        pool.start.assert_not_awaited()
    finally:
        await guard.close()


async def test_missing_psi_is_reported_as_configuration_error(harness):
    guard, pool, data, _ = harness
    data.update(pressureAvailable=False, cpuPressure=None, memoryPressure=None)
    try:
        await guard.start()
        assert guard.state == "blocked" and guard.admin_action
        assert "PSI" in guard.reason and not pool.workers
    finally:
        await guard.close()


async def test_status_request_does_not_recalibrate_or_raise_backoff(harness):
    guard, pool, _, _ = harness
    try:
        await guard.start()
        await until(lambda: guard.state == "ready")
        generation, failures = pool.generation, guard.failures
        await guard.recheck()
        await asyncio.sleep(0.03)
        assert guard.state == "ready"
        assert pool.generation == generation and guard.failures == failures
    finally:
        await guard.close()


@pytest.mark.parametrize("host", [None, 25])
async def test_host_pressure_prevents_initial_worker_allocation(harness, host):
    guard, pool, data, clock = harness
    data["hostCpuPressure"] = host
    pool.start = AsyncMock()
    try:
        await guard.start()
        await advance(guard, clock, 1000)
        pool.start.assert_not_awaited()
        assert guard.state == ("blocked" if host is None else "recovering")
        assert "host CPU" in guard.reason or "Host CPU" in guard.reason
    finally:
        await guard.close()
