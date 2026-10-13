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
    "monitoringAvailable": True,
    "cpuQuotaCpus": 4.0,
    "affinityCpus": 8,
    "effectiveCpus": 4.0,
    "memoryLimitBytes": 2048 * MIB,
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
    assert guard.state == "blocked"
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
async def test_pressure_stops_rooms_and_workers_and_requires_recheck(monkeypatch, change, reason):
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
    assert guard.state == "blocked" and reason in guard.reason
    assert not pool.workers
    await asyncio.sleep(0.01)
    service.stop_rooms.assert_awaited_once()
    data.update(SAFE)
    await asyncio.sleep(0.02)
    assert guard.state == "blocked"  # No restart storm when pressure fluctuates.
    await guard.start()
    assert guard.state == "checking"
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


@pytest.mark.parametrize("cause", ["dropped", "throttled", "failures"])
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
        assert guard.state == "blocked"
    finally:
        await guard.close()
