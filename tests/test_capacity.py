import asyncio
import base64
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from cassini_live import capacity
from cassini_live.capacity import CapacityAssessment
from cassini_live.config import Config
from cassini_live.pool import WorkerPool
from cassini_live.resources import limits
from tests.test_app import make_app
from tests.test_streaming import FakeWorker


async def test_assessment_reports_measured_limit_and_keeps_live_counters(monkeypatch):
    pool = WorkerPool(3, worker_factory=FakeWorker)
    await pool.start()
    pool.dropped_chunks = 7

    async def scenario(pool, count):
        if count == 2:
            pool.note_drop()
        return {"speakers": count, "passed": count == 1}

    monkeypatch.setattr(capacity, "scenario", scenario)
    check = CapacityAssessment(pool)
    await check.run()
    assert check.result["state"] == "limited"
    assert check.result["testedSpeakers"] == 1
    assert len(check.result["scenarios"]) == 2
    assert check.result["checkedAt"]
    assert pool.dropped_chunks == 7
    await pool.close()


@pytest.mark.parametrize("mode,state", [("fail", "insufficient"), ("error", "error")])
async def test_assessment_never_claims_capacity_after_failure(monkeypatch, mode, state):
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()

    async def scenario(*_args):
        if mode == "error":
            raise RuntimeError("failed")
        return {"speakers": 1, "passed": False}

    monkeypatch.setattr(capacity, "scenario", scenario)
    check = CapacityAssessment(pool)
    await check.run()
    assert check.result["state"] == state
    assert check.result["testedSpeakers"] == 0
    await pool.close()


async def test_assessment_interruption_reclaims_workers_before_return(monkeypatch):
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    leased = asyncio.Event()

    async def scenario(pool, _count):
        worker = pool.acquire()
        leased.set()
        try:
            await asyncio.Future()
        finally:
            await pool.release(worker, healthy=False)

    monkeypatch.setattr(capacity, "scenario", scenario)
    check = CapacityAssessment(pool)
    check.start()
    await leased.wait()
    await check.interrupt()
    assert check.result["state"] == "interrupted"
    assert check.result["testedSpeakers"] == 0
    assert pool.idle.qsize() == 1
    await pool.close()


def test_capacity_endpoint_rejects_users_and_active_calls(headers):
    app, service = make_app()
    client = TestClient(app)
    assert client.post("/capacity/check", headers=headers).status_code == 403
    system = {**headers, "AUTHORIZATION-APP-API": base64.b64encode(b":test-exapp-secret").decode()}
    service.clients["busy"] = object()
    assert client.post("/capacity/check", headers=system).status_code == 409
    assert (
        client.get("/status", headers=headers).json()["capacityAssessment"]["state"] == "not_tested"
    )


def test_call_request_cannot_bypass_capacity_check(headers):
    app, service = make_app()
    check = app.state.assessment
    check.interrupt = AsyncMock()
    app.state.safety.state = "checking"
    response = TestClient(app).post(
        "/api/v1/call/transcribe",
        headers=headers,
        json={"roomToken": "room", "ncSessionId": "session"},
    )
    assert response.status_code == 503
    check.interrupt.assert_not_awaited()
    service.request.assert_not_awaited()


def test_cpu_quota_limits_default_workers(tmp_path, monkeypatch):
    (tmp_path / "cpu.max").write_text("150000 100000")
    (tmp_path / "memory.max").write_text("536870912")
    monkeypatch.setattr("os.sched_getaffinity", lambda _pid: set(range(32)))
    measured = limits(tmp_path)
    assert measured["effectiveCpus"] == 1.5
    assert measured["memoryLimitBytes"] == 536870912
    monkeypatch.setattr("cassini_live.config.limits", lambda: measured)
    monkeypatch.delenv("CASSINI_LIVE_WORKERS", raising=False)
    assert Config.from_env().workers == 1


async def test_real_calibration_cancellation_cleans_up_decoder_leases():
    from cassini_live.calibration import scenario

    class BlockedWorker(FakeWorker):
        async def receive(self, **_kwargs):
            await asyncio.Future()

    pool = WorkerPool(1, worker_factory=BlockedWorker)
    await pool.start()
    task = asyncio.create_task(scenario(pool, 1))
    async with asyncio.timeout(4):
        while pool.idle.qsize():
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pool.idle.qsize() == 1 and not pool.waiting
    assert pool.failures == 1
    await pool.close()


async def test_startup_check_blocks_calls_until_it_passes(monkeypatch, headers):
    from httpx import ASGITransport, AsyncClient

    from cassini_live.app import create_app
    from cassini_live.service import CaptionService
    from tests.test_safety import safe_factory

    class Nextcloud:
        @property
        def enabled_state(self):
            async def enabled():
                return True

            return enabled()

    gate = asyncio.Event()

    async def scenario(pool, count):
        await gate.wait()
        return {"passed": True, "speakers": count}

    monkeypatch.setattr(capacity, "scenario", scenario)
    pool = WorkerPool(1, worker_factory=FakeWorker)
    config = Config(None, 1, "secret", "")
    service = CaptionService(config)
    service.discover = AsyncMock()
    service.request = AsyncMock()
    app = create_app(config, pool, service, nc_factory=Nextcloud, safety_factory=safe_factory)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            request = {"roomToken": "room", "ncSessionId": "session"}
            assert (
                await client.post("/api/v1/call/transcribe", headers=headers, json=request)
            ).status_code == 503
            service.request.assert_not_awaited()
            gate.set()
            async with asyncio.timeout(2):
                while app.state.safety.state == "checking":
                    await asyncio.sleep(0.01)
            assert (
                await client.post("/api/v1/call/transcribe", headers=headers, json=request)
            ).status_code == 200
            service.request.assert_awaited_once()
    assert pool.closed
