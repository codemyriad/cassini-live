import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from cassini_live import capacity
from cassini_live.capacity import CapacityAssessment
from cassini_live.pool import CapacityError, WorkerPool
from cassini_live.resources import MIB, budget_problem
from cassini_live.safety import ResourceRefused, SafetyController
from tests.test_streaming import FakeWorker

SAFE = {
    "pressureAvailable": True,
    "cpuPressure": 0,
    "cpuPressureRecent": 0,
    "hostCpuPressure": 0,
    "memoryPressure": 0,
    "monitoringAvailable": True,
    "cpuQuotaCpus": 4.0,
    "affinityCpus": 8,
    "effectiveCpus": 4.0,
    "memoryLimitBytes": 2048 * MIB,
    "memoryCurrentBytes": 410 * MIB,
    "memoryFileBytes": 0,
    "memoryShmemBytes": 0,
    "memoryStatAvailable": True,
    "cpuStallUsec": 0,
    "hostCpuStallUsec": 0,
    "swapLimitBytes": 0,
    "pidsLimit": 128,
    "hostMemoryBytes": 8192 * MIB,
    "hostAvailableBytes": 4096 * MIB,
    "memoryFraction": 0.2,
    "pidsFraction": 0.2,
    "cpuPeriods": 0,
    "throttledPeriods": 0,
    "oomKills": 0,
}


def safe_factory(pool, assessment, service=None):
    return SafetyController(pool, assessment, service, reader=lambda: dict(SAFE))


@pytest.mark.parametrize(
    "change",
    [
        {"monitoringAvailable": False},
        {"cpuQuotaCpus": None},
        {"memoryLimitBytes": None},
        {"pidsLimit": None},
        {"swapLimitBytes": 1},
        {"cpuQuotaCpus": 8},
        {"memoryLimitBytes": 8192 * MIB},
        {"pidsLimit": 500},
        {"hostAvailableBytes": 10 * MIB},
        {"memoryFraction": 0.85},
        {"pidsFraction": 0.9},
        {"hostAvailableBytes": None},
    ],
)
async def test_unsafe_installation_never_loads_a_model(change):
    pool = WorkerPool(2, worker_factory=FakeWorker)
    pool.start = AsyncMock()
    data = {**SAFE, **change}
    guard = SafetyController(pool, CapacityAssessment(pool), reader=lambda: data)
    await guard.start()
    assert guard.state in {"blocked", "recovering"}
    pool.start.assert_not_awaited()
    with pytest.raises(ResourceRefused):
        guard.require_ready()
    await guard.close()


def test_requested_workers_must_fit_budget():
    assert budget_problem(SAFE, 3)
    assert budget_problem({**SAFE, "memoryLimitBytes": 512 * MIB}, 2)
    assert budget_problem(SAFE, 2) is None


async def test_measured_speaking_limit_is_enforced_even_with_idle_workers(monkeypatch):
    async def scenario(pool, count):
        return {"passed": count == 1, "speakers": count}

    monkeypatch.setattr(capacity, "scenario", scenario)
    pool = WorkerPool(2, worker_factory=FakeWorker)
    guard = safe_factory(pool, CapacityAssessment(pool))
    try:
        await guard.start()
        with pytest.raises(ResourceRefused):
            guard.require_ready()
        async with asyncio.timeout(2):
            while guard.state == "checking":
                await asyncio.sleep(0.01)
        assert guard.state == "ready" and pool.admission_limit == 1
        one = pool.acquire()
        assert pool.idle.qsize() == 1
        with pytest.raises(CapacityError):
            await pool.acquire_wait(timeout=0.01)
        await pool.release(one)
        assert pool.available == 1
    finally:
        await guard.close()


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"memoryFraction": 0.9}, "memory budget"),
        ({"pidsFraction": 0.9}, "process/thread"),
        ({"hostAvailableBytes": MIB}, "Host memory"),
        ({"cpuQuotaCpus": 2}, "limits changed"),
        ({"oomKills": 1}, "memory limit"),
    ],
)
async def test_pressure_stops_rooms_and_workers_and_waits_before_recovery(
    monkeypatch, change, reason
):
    async def scenario(pool, count):
        return {"passed": True, "speakers": count}

    monkeypatch.setattr(capacity, "scenario", scenario)
    data = deepcopy(SAFE)
    pool = WorkerPool(1, worker_factory=FakeWorker)
    service = SimpleNamespace(stop_rooms=AsyncMock())
    guard = SafetyController(pool, CapacityAssessment(pool), service, reader=lambda: dict(data))
    await guard.start()
    async with asyncio.timeout(2):
        while guard.state == "checking":
            await asyncio.sleep(0.01)
    data.update(change)
    async with asyncio.timeout(2):
        while pool.workers or not service.stop_rooms.await_count:
            await asyncio.sleep(0.01)
    assert guard.state == "recovering" and reason in guard.reason
    assert not pool.workers
    await asyncio.sleep(0.01)
    service.stop_rooms.assert_awaited_once()
    data.update(SAFE)
    await asyncio.sleep(0.02)
    assert guard.state == "recovering"  # Cooldown still holds after pressure clears.
    await guard.close()


def test_track_reservations_are_atomic_and_released_once():
    pool = WorkerPool(1, worker_factory=FakeWorker)
    guard = safe_factory(pool, CapacityAssessment(pool))
    guard.state = "ready"
    leases = [guard.reserve_track() for _ in range(10)]
    with pytest.raises(ResourceRefused):
        guard.reserve_track()
    leases[0].release()
    leases[0].release()
    assert len(guard.tracks) == 9
    guard.reserve_track()
    assert len(guard.tracks) == 10


async def test_repeated_worker_failure_stops_replacement_loop():
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    for _ in range(3):
        worker = pool.acquire()
        await pool.release(worker, healthy=False)
    assert pool.failure_circuit_open and not pool.workers
    with pytest.raises(CapacityError):
        pool.acquire()
    await pool.close()


async def test_track_budget_refuses_before_peer_allocation(monkeypatch):
    from cassini_live.talk import client as transport
    from cassini_live.talk.types import HPBSettings

    settings = HPBSettings(server="ws://unused/spreed", stunservers=[], turnservers=[])
    client = transport.SpreedClient("room", settings, "en", None)
    pool = WorkerPool(1, worker_factory=FakeWorker)
    client.admission = safe_factory(pool, CapacityAssessment(pool))
    client.admission.state = "ready"
    for _ in range(10):
        client.admission.reserve_track()
    client.close = AsyncMock()

    def unexpected_allocation(*args, **kwargs):
        pytest.fail("Peer connection allocated beyond the track budget")

    monkeypatch.setattr(transport, "RTCPeerConnection", unexpected_allocation)
    await client.handle_offer({"message": {"sender": {"sessionid": "eleventh"}}})
    await client._close_task
    client.close.assert_awaited_once()
    assert not client.peer_connections


async def test_invalid_offer_releases_reserved_track():
    from cassini_live.talk.client import SpreedClient
    from cassini_live.talk.types import HPBSettings

    settings = HPBSettings(server="ws://unused/spreed", stunservers=[], turnservers=[])
    client = SpreedClient("room", settings, "en", None)
    pool = WorkerPool(1, worker_factory=FakeWorker)
    client.admission = safe_factory(pool, CapacityAssessment(pool))
    client.admission.state = "ready"
    with pytest.raises(ValueError):
        await client.handle_offer(
            {
                "message": {
                    "sender": {"sessionid": "speaker"},
                    "data": {"payload": {"sdp": "m=audio 9 RTP/AVP 0\nm=audio 9 RTP/AVP 0"}},
                }
            }
        )
    assert not client.admission.tracks and not client.peer_connections
    assert not client._accepting_offers


@pytest.mark.parametrize("cause", ["throttled", "failures"])
async def test_sustained_overload_trips_circuit(monkeypatch, cause):
    async def scenario(pool, count):
        return {"passed": True, "speakers": count}

    monkeypatch.setattr(capacity, "scenario", scenario)
    data = deepcopy(SAFE)
    pool = WorkerPool(1, worker_factory=FakeWorker)
    guard = SafetyController(pool, CapacityAssessment(pool), reader=lambda: dict(data))
    await guard.start()
    try:
        async with asyncio.timeout(2):
            while guard.state == "checking":
                await asyncio.sleep(0.01)
        if cause == "dropped":
            for _ in range(3):
                pool.note_drop()
        elif cause == "failures":
            pool.failure_circuit_open = True
        async with asyncio.timeout(5):
            while guard.state == "ready":
                if cause == "throttled":
                    data["cpuPeriods"] += 10
                    data["throttledPeriods"] += 10
                await asyncio.sleep(0.1)
        assert guard.state == "recovering"
    finally:
        await guard.close()


async def test_monitor_read_failure_during_preload_reaps_worker():
    workers = []

    def factory(weights):
        worker = FakeWorker(weights)
        workers.append(worker)
        return worker

    def check():
        if workers:
            raise OSError("Counters became unreadable")
        return None

    pool = WorkerPool(1, worker_factory=factory)
    pool.resource_check = check
    with pytest.raises(OSError):
        await pool.start()
    assert workers[0].process.returncode is not None
    assert not pool.workers and pool.closed


@pytest.mark.parametrize(
    "usec,host,lag,expected",
    [
        (80000, 1, 0.01, None),
        (210000, 1, 0.01, "quota stalls"),
        (None, 1, 0.01, "severity unavailable"),
        (80000, 21, 0.01, "Host CPU pressure"),
        (80000, None, 0.01, "monitoring is unavailable"),
        (80000, 1, 0.6, "event loop"),
    ],
)
def test_cpu_guard_distinguishes_brief_quota_enforcement_from_pressure(usec, host, lag, expected):
    from cassini_live.safety import cpu_pressure_reason

    before = {"cpuPeriods": 0, "throttledPeriods": 0, "throttledUsec": 0}
    after = {
        "cpuPeriods": 10,
        "throttledPeriods": 10,
        "throttledUsec": usec,
        "hostCpuPressure": host,
    }
    result = cpu_pressure_reason(before, after, 1, lag)
    assert result is None if expected is None else expected in result


def test_throttle_severity_uses_actual_elapsed_time():
    from cassini_live.safety import cpu_pressure_reason

    before = {"cpuPeriods": 0, "throttledPeriods": 0, "throttledUsec": 0}
    after = {
        "cpuPeriods": 20,
        "throttledPeriods": 20,
        "throttledUsec": 300000,
        "hostCpuPressure": 0,
    }
    assert cpu_pressure_reason(before, after, 2, 0) is None


def engine_guard(pool, data):
    from dataclasses import replace

    from cassini_live.recovery import RecoveryPolicy

    return SafetyController(
        pool,
        CapacityAssessment(pool),
        reader=lambda: dict(data),
        policy=replace(RecoveryPolicy(), monitor=0.005, poll=0.005),
        jitter=lambda: 0,
    )


async def wait_for(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.002)


@pytest.fixture
def passing(monkeypatch):
    async def scenario(pool, count):
        return {"passed": True, "speakers": count}

    monkeypatch.setattr(capacity, "scenario", scenario)


async def test_replace_pool_only_while_stopped_and_outcomes(passing):
    from cassini_live import transcriber
    from tests.fakes_engine import FakeBackend

    data = {**SAFE, "memoryLimitBytes": 3072 * MIB}
    guard = engine_guard(FakeBackend("whistle", 1), data)
    await guard.start()
    assert await guard.wait_outcome(2) == (True, "Capacity check passed")
    with pytest.raises(RuntimeError):
        guard.replace_pool(FakeBackend("nemotron-en", 1))
    await guard.close(reason="Changing the speech engine")
    assert guard.reason == "Changing the speech engine"
    backend = FakeBackend("nemotron-en", 1)
    guard.replace_pool(backend)
    assert guard.engine_id == "nemotron-en" and transcriber.POOL is backend
    assert guard.assessment.pool is backend and backend.load_check() is None
    try:
        await guard.start(fresh=True)
        assert (await guard.wait_outcome(2))[0]
        data["memoryFraction"] = 0.9
        await wait_for(lambda: guard.state != "ready")
    finally:
        await guard.close()
        transcriber.POOL = None


async def test_outcome_reports_blocked_trip_and_close(passing):
    from tests.fakes_engine import FakeBackend

    guard = engine_guard(FakeBackend("nemotron-en", 1), SAFE)  # 2 GiB: below the floor
    await guard.start()
    ok, why = await guard.wait_outcome(2)
    assert not ok and "memory budget is too small for Nemotron English" in why
    await guard.close()
    data = {**SAFE, "memoryLimitBytes": 3072 * MIB}
    guard = engine_guard(FakeBackend("nemotron-en", 1, start_error=RuntimeError("x")), data)
    await guard.start()
    ok, why = await guard.wait_outcome(2)
    assert not ok and guard.state == "recovering"
    assert why == "Recognition workers or resource monitoring failed"
    await guard.close()
    refused = CapacityError("Not enough free container memory to load Nemotron English")
    guard = engine_guard(FakeBackend("nemotron-en", 1, start_error=refused), data)
    await guard.start()
    assert await guard.wait_outcome(2) == (False, str(refused))
    await guard.close()
    guard = engine_guard(FakeBackend("nemotron-en", 1, start_gate=asyncio.Event()), data)
    starting = asyncio.create_task(guard.start())  # Blocks while the model loads.
    await wait_for(lambda: guard.phase == "loading")
    waiter = asyncio.create_task(guard.wait_outcome(2))
    await guard.close()
    assert await waiter == (False, "Caption service stopped")
    await starting
    assert await engine_guard(FakeBackend(), data).wait_outcome(0.01) == (
        False,
        "Capacity has not been established",
    )


async def test_fresh_start_ignores_cooldown_but_not_budget(passing):
    from tests.fakes_engine import FakeBackend

    data = {**SAFE, "memoryLimitBytes": 3072 * MIB}
    guard = engine_guard(FakeBackend("whistle", 1), data)
    guard.journal.write(3, guard.wall_clock() + 3600, False, "Tripped")
    await guard.start()
    assert guard.state == "recovering" and guard.phase == "cooling_down"
    await guard.close()
    await guard.start(fresh=True)
    assert (await guard.wait_outcome(2))[0] and guard.failures <= 1
    await guard.close()
    guard.journal.write(3, guard.wall_clock() + 3600, False, "Tripped")
    data["memoryLimitBytes"] = 256 * MIB
    await guard.start(fresh=True)
    assert not (await guard.wait_outcome(2))[0] and guard.state == "blocked"
    await guard.close()


async def test_auto_size_resizes_through_the_backend(passing):
    from tests.fakes_engine import FakeBackend

    data = {**SAFE, "memoryLimitBytes": 3072 * MIB}
    backend = FakeBackend("nemotron-en", 1)
    guard = engine_guard(backend, data)
    guard.auto_size = True
    await guard.start()
    await guard.wait_outcome(2)
    assert backend.size == 4  # (4 CPUs - 0.25) / 0.5, capped at four
    await guard.close()


@pytest.mark.parametrize(
    "engine_id, tested, rooms",
    [("whistle", 4, 2), ("whistle", 1, 1), ("nemotron-en", 4, 4), ("nemotron-multilingual", 3, 3)],
)
def test_room_cap_is_two_only_for_whistle(engine_id, tested, rooms):
    assessment = SimpleNamespace(result={"testedSpeakers": tested})
    guard = SafetyController(SimpleNamespace(engine_id=engine_id), assessment)
    guard.state = "ready"
    for open_rooms in range(rooms):
        guard.allow_room(open_rooms)
    with pytest.raises(ResourceRefused):
        guard.allow_room(rooms)


# 0.7.3 observes only (0.8.0-instant-admission.md, step 4): the governor is fed from the
# monitor tick and reported, and nothing a caller sees changes. The trip tests above are
# the behaviour baseline and stay as they are.

ADMISSION_KEYS = {
    "open", "reason", "seats", "ceiling", "speaking", "paused", "lagSeconds",
    "lagSustainedSeconds", "captionAgeSeconds", "cpuStall", "cpuPressureAvg10",
    "memoryHeadroomMiB", "memoryUsedMiB", "memoryLimitMiB", "memoryStatAvailable",
    "pressureAvailable", "engineDuty", "lastRefusal", "lastShed", "shedEvents",
    "measuring", "tickMs", "observeOnly",
}  # fmt: skip


def test_status_carries_the_observing_governor_before_any_tick():
    pool = WorkerPool(2, worker_factory=FakeWorker)
    guard = safe_factory(pool, CapacityAssessment(pool))
    admission = guard.status()["admission"]
    assert set(admission) == ADMISSION_KEYS
    assert admission["observeOnly"] and admission["open"] and admission["seats"] == 0
    assert admission["ceiling"] == 2 and admission["engineDuty"] is None
    assert guard.governor.transcribers is pool.transcribers


async def test_monitor_feeds_the_governor_each_tick_without_changing_admission(monkeypatch):
    async def scenario(pool, count):
        return {"passed": True, "speakers": count}

    monkeypatch.setattr(capacity, "scenario", scenario)
    data = deepcopy(SAFE)
    reads = 0

    def reader():
        nonlocal reads
        reads += 1
        # 50 ms of stall per read: a real tick converts it into a fraction of the second.
        data["cpuStallUsec"] += 50_000
        return dict(data)

    pool = WorkerPool(2, worker_factory=FakeWorker)
    guard = SafetyController(pool, CapacityAssessment(pool), reader=reader)
    try:
        await guard.start()
        async with asyncio.timeout(2):
            while guard.state == "checking":
                await asyncio.sleep(0.01)
        ticks = reads
        async with asyncio.timeout(3):
            while guard.governor.cpu_stall is None:
                await asyncio.sleep(0.05)
        assert reads > ticks  # the monitor kept reading after ready
        status = guard.status()
        admission = status["admission"]
        assert set(admission) == ADMISSION_KEYS
        assert 0 < admission["cpuStall"] <= 1.0 and admission["cpuPressureAvg10"] == 0
        assert admission["memoryLimitMiB"] == 2048 and admission["memoryUsedMiB"] == 410
        assert admission["memoryHeadroomMiB"] == 2048 - 410
        assert admission["observeOnly"] and not admission["measuring"]
        assert "engineDuty" in guard.governor.sample and admission["engineDuty"] is None
        assert isinstance(status["cpuHealth"]["monitorTickMs"], float)
        assert admission["tickMs"] <= status["cpuHealth"]["monitorTickMs"]
        # Nothing consults admit() in 0.7.3: no seats, no refusal, the measured limit rules.
        assert admission["seats"] == 0 and admission["lastRefusal"] is None
        assert guard.state == "ready" and pool.admission_limit == 2
        assert guard.last_refusal is None
    finally:
        await guard.close()


def test_governor_is_marked_measuring_while_the_assessment_runs():
    pool = WorkerPool(1, worker_factory=FakeWorker)
    guard = safe_factory(pool, CapacityAssessment(pool))
    guard._observe(dict(SAFE), 10.0)
    assert guard.governor.measuring is False
    guard.assessment.task = SimpleNamespace(done=lambda: False)
    guard._observe(dict(SAFE), 11.0)
    assert guard.governor.measuring is True and guard.status()["admission"]["measuring"]


def test_engine_duty_is_the_host_cpu_delta_per_thread(monkeypatch):
    readings = {4242: [(10.0, 0), (10.0, 0), (11.6, 0)]}

    def process_usage(pid):
        if not readings.get(pid):
            raise FileNotFoundError(pid)
        return readings[pid].pop(0)

    monkeypatch.setattr("cassini_live.safety.process_usage", process_usage)
    pool = WorkerPool(1, worker_factory=FakeWorker)
    pool.host_pid, pool.threads = 4242, 2
    guard = safe_factory(pool, CapacityAssessment(pool))
    assert guard._engine_duty(100.0) is None  # the first reading has no delta
    assert guard._engine_duty(101.0) == 0.0
    assert guard._engine_duty(102.0) == pytest.approx(0.8)  # 1.6 s over 1 s on 2 threads
    assert guard._engine_duty(103.0) is None  # the host went away; the tick survives
    assert guard._engine_cpu is None
    pool.host_pid = None
    assert guard._engine_duty(104.0) is None  # Whistle never samples


def test_governor_ceiling_follows_a_resized_backend(monkeypatch):
    pool = WorkerPool(1, worker_factory=FakeWorker)
    guard = safe_factory(pool, CapacityAssessment(pool))
    replacement = WorkerPool(3, worker_factory=FakeWorker)
    guard.replace_pool(replacement)
    assert guard.governor.ceiling == 3 and guard.governor.transcribers is replacement.transcribers
