import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from cassini_live import __version__, capacity
from cassini_live.capacity import CapacityAssessment
from cassini_live.pool import WorkerPool
from cassini_live.recovery import RecoveryJournal, RecoveryPolicy
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
        raise AssertionError("0.8.0 never runs the capacity check automatically")

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


def oom(data):
    """The kernel killed something in the container: the one memory trip left."""
    data["oomKills"] += 1


async def test_a_crash_waits_out_only_its_cooldown(harness):
    guard, pool, data, clock = harness
    try:
        await guard.start()
        await until(lambda: guard.state == "ready")
        oom(data)
        await until(lambda: pool.closed)
        assert guard.state == "recovering" and not guard.admin_action
        await advance(guard, clock, 119)
        assert pool.closed
        # Pressure is no longer a gate: high PSI at the deadline does not delay the load.
        data.update(hostCpuPressure=90, cpuPressureRecent=90, memoryPressure=50)
        await advance(guard, clock, 1)
        await until(lambda: guard.state == "ready")
        # Only the trip counts; the successful reload does not add a failure.
        assert len(pool.workers) == 1 and guard.failures == 1
    finally:
        await guard.close()


async def test_a_start_that_does_not_fit_is_configuration_not_a_failure(harness):
    from cassini_live.resources import MIB

    guard, pool, data, clock = harness
    data["memoryCurrentBytes"] = data["memoryLimitBytes"] - 100 * MIB
    try:
        await guard.start()
        assert guard.state == "blocked" and guard.phase == "configuration"
        assert "Not enough memory to load Whistle" in guard.reason and guard.admin_action
        assert guard.failures == 0 and guard.next_attempt <= clock() and not pool.workers
        data.update(SAFE)
        await advance(guard, clock, 0)  # no cooldown: the next poll loads
        await until(lambda: guard.state == "ready")
        assert guard.failures == 0
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
    oom(data)
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
        # The interrupted attempt stays counted; the successful one is not.
        assert not saved["attemptInProgress"] and saved["failures"] == 2
    finally:
        await guard.close()


async def test_trip_reason_is_logged_and_survives_recovery(harness, tmp_path, caplog):
    guard, pool, data, clock = harness
    guard.journal = RecoveryJournal(tmp_path / "recovery.json")
    try:
        await guard.start()
        await until(lambda: guard.state == "ready")
        oom(data)
        await until(lambda: pool.closed)
        assert "The caption memory limit was exceeded" in caplog.text
        await advance(guard, clock, 120)
        await until(lambda: guard.state == "ready")
        trip = guard.status()["lastTrip"]
        assert trip["reason"] == "The caption memory limit was exceeded"
        assert guard.journal.read()["lastTrip"] == trip
    finally:
        await guard.close()
    restarted = SafetyController(
        WorkerPool(1, worker_factory=FakeWorker),
        CapacityAssessment(pool),
        reader=lambda: dict(data),
        journal=guard.journal,
        clock=clock,
        wall_clock=clock,
        policy=guard.policy,
    )
    try:
        await restarted.start()
        assert restarted.status()["lastTrip"] == trip
    finally:
        await restarted.close()


async def test_only_sustained_ready_resets_backoff(harness):
    guard, pool, _, clock = harness
    guard.journal.write(2, clock(), False, "Earlier trips")
    try:
        await guard.start()
        await until(lambda: guard.state == "ready")  # an elapsed cooldown loads at once
        assert guard.failures == 2
        await advance(guard, clock, 599)
        assert guard.failures == 2
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


async def test_unwritable_journal_never_allocates_workers(harness):
    guard, pool, _, _ = harness
    guard.journal.write = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("Read only"))
    pool.start = AsyncMock()
    try:
        await guard.start()
        assert guard.admin_action
        pool.start.assert_not_awaited()
    finally:
        await guard.close()


async def test_missing_psi_no_longer_blocks_captions(harness):
    guard, pool, data, _ = harness
    data.update(pressureAvailable=False, cpuPressure=None, memoryPressure=None)
    data.update(hostCpuPressure=None, cpuStallUsec=None, hostCpuStallUsec=None)
    try:
        await guard.start()
        await until(lambda: guard.state == "ready")
        assert not guard.admin_action and pool.workers
    finally:
        await guard.close()


async def test_a_measurement_holds_calls_and_never_reloads_or_counts(harness, monkeypatch):
    from cassini_live.safety import ResourceRefused

    gate = asyncio.Event()

    async def scenario(pool, count):
        await gate.wait()
        pool.capacity_waits += 5  # synthetic pressure must not reach the call counters
        pool.lag.record(9.0)
        return {"passed": True, "speakers": count}

    monkeypatch.setattr(capacity, "scenario", scenario)
    guard, pool, _, _ = harness
    try:
        await guard.start()
        await until(lambda: guard.state == "ready")
        generation, failures = pool.generation, guard.failures
        pool.lag.record(0.3)
        await guard.measure()
        status = guard.status()
        assert status["phase"] == "measuring" and status["retryAfterSeconds"] >= 1
        with pytest.raises(ResourceRefused, match="Measuring"):
            guard.require_ready()
        with pytest.raises(ResourceRefused, match="already running"):
            await guard.measure()
        assert guard.governor.measuring
        gate.set()
        await until(lambda: not guard.measuring)
        assert guard.assessment.result["state"] == "passed" and not guard.governor.measuring
        assert pool.capacity_waits == 0 and pool.lag.p95() == 0.3
        assert guard.state == "ready" and status["state"] == "ready"
        assert pool.generation == generation and guard.failures == failures
        guard.require_ready()
    finally:
        await guard.close()


async def test_a_measurement_needs_ready_captions_and_no_calls(harness):
    from types import SimpleNamespace

    from cassini_live.safety import ResourceRefused

    guard, pool, _, _ = harness
    with pytest.raises(ResourceRefused, match="not ready"):
        await guard.measure()
    try:
        await guard.start()
        await until(lambda: guard.state == "ready")
        with pytest.raises(ResourceRefused, match="captioned call"):
            await guard.measure(calls_starting=lambda: 1)
        guard.service = SimpleNamespace(clients={"room": object()}, stop_rooms=AsyncMock())
        with pytest.raises(ResourceRefused, match="captioned call"):
            await guard.measure()
        guard.service = None
        pool.acquire()  # a speaker holds a worker
        with pytest.raises(ResourceRefused, match="in use"):
            await guard.measure()
        assert not guard.measuring and guard.assessment.task is None
    finally:
        await guard.close()


@pytest.mark.parametrize(
    "change",
    [
        {"hostCpuPressure": 90},
        {"memoryFraction": 0.9, "pidsFraction": 0.9},
        {"hostAvailableBytes": 1},
        {"cpuQuotaCpus": 2.0, "effectiveCpus": 2.0},
    ],
)
async def test_load_and_limit_changes_never_trip_a_ready_service(harness, change):
    guard, pool, data, clock = harness
    try:
        await guard.start()
        await until(lambda: guard.state == "ready")
        data.update(change)
        await advance(guard, clock, 5)
        assert guard.state == "ready" and pool.workers and guard.failures == 0
    finally:
        await guard.close()


def restart(guard, data, clock):
    pool = WorkerPool(1, worker_factory=FakeWorker)
    return SafetyController(
        pool,
        CapacityAssessment(pool),
        reader=lambda: dict(data),
        journal=guard.journal,
        clock=clock,
        wall_clock=clock,
        policy=guard.policy,
        jitter=lambda: 0,
    )


async def test_clean_restart_and_reenable_attempt_without_waiting(harness, tmp_path):
    # The clock never advances: any cooldown or settle window would block readiness.
    guard, pool, data, clock = harness
    guard.journal = RecoveryJournal(tmp_path / "recovery.json")
    await guard.start()
    await until(lambda: guard.state == "ready")
    await guard.close()
    try:
        await guard.start()
        await until(lambda: guard.state == "ready")
    finally:
        await guard.close()
    saved = guard.journal.read()
    assert saved["failures"] == 0 and not saved["attemptInProgress"]
    restarted = restart(guard, data, clock)
    try:
        await restarted.start()
        await until(lambda: restarted.state == "ready")
        assert restarted.failures == 0 and restarted.status()["retryAfterSeconds"] == 0
    finally:
        await restarted.close()


async def test_trips_while_ready_back_off_from_the_first_delay(harness):
    guard, pool, data, clock = harness
    try:
        await guard.start()
        await until(lambda: guard.state == "ready")
        assert guard.failures == 0 and guard.next_attempt == 0
        for failures in (1, 2):
            oom(data)
            await until(lambda: pool.closed)
            delay = guard.policy.delay(failures, lambda: 0)
            assert guard.failures == failures and guard.next_attempt - clock() == delay
            await advance(guard, clock, delay - 1)
            assert pool.closed
            await advance(guard, clock, 1)
            await until(lambda: guard.state == "ready")
            assert guard.failures == failures
    finally:
        await guard.close()


async def test_crash_during_load_waits_out_the_backoff_after_restart(harness, tmp_path):
    guard, pool, data, clock = harness
    guard.journal = RecoveryJournal(tmp_path / "recovery.json")
    entered = asyncio.Event()

    class HangingWorker(FakeWorker):
        async def start(self):
            await super().start()
            entered.set()
            await asyncio.Future()

    pool.worker_factory = HangingWorker
    start = asyncio.create_task(guard.start())
    await entered.wait()
    crashed = guard.journal.path.read_bytes()  # As an OOM kill would leave it.
    await guard.close()
    await start
    guard.journal.path.write_bytes(crashed)
    assert guard.journal.read()["attemptInProgress"]
    restarted = restart(guard, data, clock)
    try:
        await restarted.start()
        assert restarted.failures == 1 and not restarted.pool.workers
        await advance(restarted, clock, guard.policy.delay(1, lambda: 0) - 1)
        assert restarted.pool.closed
        await advance(restarted, clock, 2)
        await until(lambda: restarted.state == "ready")
        assert restarted.failures == 1
    finally:
        await restarted.close()


async def test_process_death_while_ready_backs_off_after_restart(harness, tmp_path):
    guard, pool, data, clock = harness
    guard.journal = RecoveryJournal(tmp_path / "recovery.json")
    current = guard
    try:
        for failures in (1, 2):
            await current.start()
            await until(lambda: current.state == "ready")
            assert current.journal.read()["engineRunning"]
            crashed = current.journal.path.read_bytes()  # As a container kill would leave it.
            await current.close()
            assert not current.journal.read()["engineRunning"]
            current.journal.path.write_bytes(crashed)
            current = restart(current, data, clock)
            await current.start()
            delay = guard.policy.delay(failures, lambda: 0)
            assert current.failures == failures and current.next_attempt - clock() == delay
            assert current.journal.read()["failures"] == failures
            await advance(current, clock, delay - 1)
            assert current.pool.closed
            await advance(current, clock, 2)
            await until(lambda: current.state == "ready")
            assert current.failures == failures  # The crash count grows across kills.
            await current.close()
    finally:
        await current.close()


async def test_disable_during_load_does_not_leave_a_cooldown(harness, tmp_path):
    guard, pool, data, clock = harness
    guard.journal = RecoveryJournal(tmp_path / "recovery.json")
    entered = asyncio.Event()

    class HangingWorker(FakeWorker):
        async def start(self):
            await super().start()
            entered.set()
            await asyncio.Future()

    pool.worker_factory = HangingWorker
    start = asyncio.create_task(guard.start())
    await entered.wait()
    assert guard.phase == "loading" and guard.failures == 1
    await guard.close()
    await start
    saved = guard.journal.read()
    assert saved["failures"] == 0 and saved["nextAttemptAt"] == clock()
    assert not saved["attemptInProgress"] and not saved["engineRunning"]
    # The clock never advances: a re-enable or clean restart attempts at once.
    restarted = restart(guard, data, clock)
    try:
        await restarted.start()
        await until(lambda: restarted.state == "ready")
        assert restarted.failures == 0
    finally:
        await restarted.close()


async def test_process_death_after_the_stable_window_still_backs_off(harness, tmp_path):
    guard, pool, data, clock = harness
    guard.journal = RecoveryJournal(tmp_path / "j.json")
    await guard.start()
    await until(lambda: guard.state == "ready")
    clock.now += 601  # stable window elapses while the engine runs
    await asyncio.sleep(0.05)
    killed = guard.journal.path.read_bytes()  # Simulated SIGKILL or OOM: the journal as it was
    await guard.close()
    guard.journal.path.write_bytes(killed)

    pool2 = WorkerPool(1, worker_factory=FakeWorker)
    g2 = SafetyController(
        pool2,
        CapacityAssessment(pool2),
        reader=lambda: dict(data),
        clock=clock,
        wall_clock=clock,
        policy=guard.policy,
        jitter=lambda: 0,
    )
    g2.journal = RecoveryJournal(guard.journal.path)
    try:
        await g2.start()
        await asyncio.sleep(0.05)
        assert g2.failures >= 1 and g2.next_attempt - clock() >= 120, "crash not backed off"
    finally:
        await g2.close()


def write_07x(journal, *args, **kwargs):
    """A journal as 0.7.x wrote it: no appVersion field."""
    import json

    journal.write(*args, **kwargs)
    saved = journal.read()
    saved.pop("appVersion")
    journal.path.write_text(json.dumps(saved))


LEGACY_CHECK = "Capacity check did not establish a safe speaking budget"
# Every 0.7.x trip 0.8.0 no longer has (review 0.8.0: not only the capacity check).
REMOVED_TRIPS = [
    LEGACY_CHECK,
    "Caption CPU quota stalls are sustained",
    "Caption CPU quota pressure (severity unavailable)",
    "Host CPU pressure is sustained",
    "Host CPU pressure monitoring is unavailable",
    "Caption event loop is falling behind",
    "Resource limits changed; reassessing capacity automatically",
    "Caption memory budget is nearly exhausted",
    "Caption process/thread budget is nearly exhausted",
    "Host memory is under pressure",
    "Host memory headroom cannot be established",
    "Resource pressure counters cannot be read safely",
    "Not enough free container memory to load Nemotron English",
]
KEPT_TRIPS = [
    "The caption memory limit was exceeded",
    "Repeated recognition worker failures",
    "Recognition workers or resource monitoring failed",
    "Recovering after an interrupted caption process",
]


@pytest.mark.parametrize("reason", REMOVED_TRIPS)
async def test_a_07x_cooldown_from_a_removed_trip_does_not_delay_the_first_start(
    harness, tmp_path, reason
):
    guard, pool, data, clock = harness
    guard.journal = RecoveryJournal(tmp_path / "recovery.json")
    write_07x(guard.journal, 3, clock() + 300, False, reason, {"reason": reason, "at": 1})
    try:
        await guard.start()
        await until(lambda: guard.state == "ready")
        assert guard.failures == 0 and guard.last_trip is None
        saved = guard.journal.read()
        assert saved["failures"] == 0 and saved["engineRunning"] and saved["lastTrip"] is None
    finally:
        await guard.close()


@pytest.mark.parametrize("reason", KEPT_TRIPS)
async def test_a_07x_crash_cooldown_is_honoured(harness, tmp_path, reason):
    guard, pool, data, clock = harness
    guard.journal = RecoveryJournal(tmp_path / "recovery.json")
    # lastTrip may still name an older removed trip; only the current reason counts.
    write_07x(guard.journal, 2, clock() + 300, False, reason, {"reason": LEGACY_CHECK, "at": 1})
    try:
        await guard.start()
        assert guard.state == "recovering" and guard.phase == "cooling_down"
        assert guard.failures == 2 and guard.next_attempt - clock() == 300
    finally:
        await guard.close()


async def test_a_07x_engine_killed_while_serving_still_counts(harness, tmp_path):
    # 0.7.x wrote "Capacity check passed" while ready; a kill then is a crash, not a check.
    guard, pool, data, clock = harness
    guard.journal = RecoveryJournal(tmp_path / "recovery.json")
    trip = {"reason": LEGACY_CHECK, "at": 1}
    write_07x(guard.journal, 1, clock(), False, "Capacity check passed", trip, running=True)
    try:
        await guard.start()
        assert guard.state == "recovering" and guard.phase == "cooling_down"
        assert guard.failures == 2
        assert guard.next_attempt - clock() == guard.policy.delay(2, lambda: 0)
    finally:
        await guard.close()


async def test_a_second_boot_during_a_real_crash_cooldown_still_waits(harness, tmp_path):
    # Review 0.8.0: the first boot counted a crash but lastTrip still named the 0.7.x
    # capacity check, and a second boot (the container restarted again) forgave it.
    guard, pool, data, clock = harness
    guard.journal = RecoveryJournal(tmp_path / "recovery.json")
    trip = {"reason": LEGACY_CHECK, "at": 1}
    write_07x(guard.journal, 1, clock(), False, "Capacity check passed", trip, running=True)
    await guard.start()
    assert guard.phase == "cooling_down"
    deadline = guard.next_attempt
    saved = guard.journal.read()
    await guard.close()
    guard.journal.path.write_text(__import__("json").dumps(saved))  # died, not stopped
    await guard.start()
    try:
        assert guard.phase == "cooling_down" and guard.failures == 2
        assert guard.next_attempt == deadline
    finally:
        await guard.close()


async def test_a_language_change_keeps_the_seat():
    from cassini_live.load import LoadGovernor, Thresholds
    from tests.fakes_engine import FakeBackend

    backend = FakeBackend("nemotron-en", 2)
    backend.governor = LoadGovernor(
        ceiling=2,
        thresholds=Thresholds(provisional=False, measured_on="2026-10-16"),
        transcribers=backend.transcribers,
    )
    track = backend.transcriber("s", "en", asyncio.Queue())

    class Ended:
        async def receive(self):
            from aiortc.mediastreams import MediaStreamError

            raise MediaStreamError

        def stop(self):
            pass

    track.stream = Ended()
    backend.governor.admit(track)
    track.keep_track = True  # what set_language sets around the restart
    await track._run()
    assert track in backend.governor.seats
    track.keep_track = False
    await track._run()
    assert track not in backend.governor.seats


@pytest.mark.parametrize("version,waits", [("0.7.2", False), (None, False), (__version__, True)])
async def test_an_upgrade_that_stopped_a_load_starts_cleanly(harness, tmp_path, version, waits):
    guard, pool, data, clock = harness
    path = tmp_path / "recovery.json"
    guard.journal = RecoveryJournal(path)
    guard.journal.write(1, clock(), True, "Checking caption capacity" if not waits else "Loading")
    saved = guard.journal.read()
    if version is None:
        saved.pop("appVersion")
    else:
        saved["appVersion"] = version
    import json

    path.write_text(json.dumps(saved))
    try:
        await guard.start()
        if waits:
            # Same version: the load itself died (an OOM kill), so the back-off holds.
            assert guard.state == "recovering" and guard.phase == "cooling_down"
            assert guard.next_attempt - clock() == guard.policy.delay(1, lambda: 0)
        else:
            await until(lambda: guard.state == "ready")
            assert not guard.journal.read()["attemptInProgress"]
    finally:
        await guard.close()


def test_the_journal_records_the_app_version(tmp_path):
    journal = RecoveryJournal(tmp_path / "recovery.json")
    journal.write(0, 0, False, "Captions are ready")
    assert journal.read()["appVersion"] == __version__
