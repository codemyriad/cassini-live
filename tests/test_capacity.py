import asyncio
import base64
import http.client
import json
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


def test_a_call_waits_out_a_running_measurement(headers):
    app, service = make_app()
    check = app.state.assessment
    check.interrupt = AsyncMock()
    app.state.safety.state, app.state.safety.measuring = "ready", True
    response = TestClient(app).post(
        "/api/v1/call/transcribe",
        headers=headers,
        json={"roomToken": "room", "ncSessionId": "session"},
    )
    assert response.status_code == 503 and int(response.headers["Retry-After"]) >= 1
    check.interrupt.assert_not_awaited()
    service.request.assert_not_awaited()


def test_capacity_endpoint_delegates_to_the_manager(headers):
    app, _ = make_app()
    client = TestClient(app)
    system = {**headers, "AUTHORIZATION-APP-API": base64.b64encode(b":test-exapp-secret").decode()}
    manager = app.state.manager
    manager.measure = AsyncMock(return_value={"state": "checking", "testedSpeakers": 0})
    response = client.post("/capacity/check", headers=system)
    assert response.status_code == 200 and response.json() == {"state": "checking"}
    from cassini_live.engine_switch import SwitchRefused

    manager.measure = AsyncMock(side_effect=SwitchRefused(409, "busy", "Wait for the change"))
    assert client.post("/capacity/check", headers=system).status_code == 409


async def test_measure_cli_waits_for_the_result(monkeypatch, capsys):
    calls = []
    states = iter(["checking", "checking", "passed"])

    def installed_request(method, path):
        calls.append((method, path))
        if method == "POST":
            return {"state": "checking"}
        return {
            "capacityAssessment": {"state": next(states), "testedSpeakers": 2},
            "resourceSafety": {"state": "ready"},
        }

    async def no_sleep(_):
        pass

    monkeypatch.setattr(capacity, "installed_request", installed_request)
    monkeypatch.setattr(capacity.asyncio, "sleep", no_sleep)
    assert await capacity.measure_installed() == 0
    assert calls[0] == ("POST", "/capacity/check") and len(calls) == 4
    assert '"passed"' in capsys.readouterr().out


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


async def test_startup_load_blocks_calls_until_the_engine_is_ready(monkeypatch, headers):
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

    class SlowWorker(FakeWorker):
        async def start(self):
            await gate.wait()
            await super().start()

    pool = WorkerPool(1, worker_factory=SlowWorker)
    config = Config(None, 1, "secret", "")
    service = CaptionService(config)
    service.discover = AsyncMock()
    service.request = AsyncMock()
    app = create_app(config, pool, service, nc_factory=Nextcloud, safety_factory=safe_factory)
    request = {"roomToken": "room", "ncSessionId": "session"}
    entered, leave = asyncio.Event(), asyncio.Event()

    async def lifespan():
        async with app.router.lifespan_context(app):
            entered.set()
            await leave.wait()

    # The lifespan waits for the first outcome, so calls arrive while the engine loads.
    running = asyncio.create_task(lifespan())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with asyncio.timeout(2):
            while getattr(app.state, "safety", None) is None or app.state.safety.phase != "loading":
                await asyncio.sleep(0.01)
        refused = await client.post("/api/v1/call/transcribe", headers=headers, json=request)
        assert refused.status_code == 503 and int(refused.headers["Retry-After"]) >= 1
        service.request.assert_not_awaited()
        gate.set()
        async with asyncio.timeout(2):
            await entered.wait()
        assert app.state.safety.state == "ready"
        accepted = await client.post("/api/v1/call/transcribe", headers=headers, json=request)
        assert accepted.status_code == 200
        service.request.assert_awaited_once()
    leave.set()
    await running
    assert pool.closed


async def test_report_names_the_engine_and_requires_an_idle_backend(monkeypatch):
    from cassini_live import capacity
    from cassini_live.capacity import CapacityAssessment
    from tests.fakes_engine import FakeBackend

    async def scenario(pool, count):
        return {"passed": True, "speakers": count}

    monkeypatch.setattr(capacity, "scenario", scenario)
    backend = FakeBackend("nemotron-en", 2)
    assessment = CapacityAssessment(backend)
    await assessment.run()
    assert assessment.result["engine"] == "nemotron-en"
    assert assessment.result["state"] == "error"  # not started: is_idle() is false
    await backend.start()
    await assessment.run()
    assert assessment.result["state"] == "passed" and assessment.result["testedSpeakers"] == 2


async def test_scenario_reports_lag_fields_and_keeps_synthetic_requests_out_of_status():
    from cassini_live.calibration import scenario

    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    live = pool.lag
    live.record(0.3)
    try:
        result = await scenario(pool, 1, participants=1, speed=100)
    finally:
        await pool.close()
    for key in ("maxLagSeconds", "lagSustainedSeconds", "p95RequestSeconds"):
        assert isinstance(result[key], float) and result[key] >= 0
    assert result["speakers"] == 1 and "droppedChunks" in result
    assert pool.lag is live and list(live.requests) == [0.3]


async def test_watch_prints_one_json_line_per_second_from_status(monkeypatch, capsys):
    status = {
        "resourceSafety": {
            "state": "ready",
            "admission": {
                "seats": 1,
                "speaking": 1,
                "lagSeconds": 0.2,
                "lagSustainedSeconds": 0.1,
                "captionAgeSeconds": 0.4,
                "cpuStall": 0.0,
                "cpuPressureAvg10": 1.5,
                "engineDuty": 0.7,
                "memoryUsedMiB": 1500,
                "memoryHeadroomMiB": 1572,
                "lastRefusal": None,
                "lastShed": None,
                "tickMs": 0.3,
            },
            "cpuHealth": {"eventLoopLagMs": 0.4, "monitorTickMs": 1.7},
        },
        "p95RequestSeconds": 0.5,
        "droppedSpeechChunks": 0,
        "capacityWaits": 2,
        "hostRestarts": 0,
    }
    calls = []
    monkeypatch.setattr(capacity, "installed_request", lambda *a: calls.append(a) or status)
    monkeypatch.setattr(capacity, "WATCH_INTERVAL_S", 0.01)
    assert await capacity.watch(3) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 3 and calls == [("GET", "/status")] * 3
    assert list(lines[0]) == [
        "at", "state", "lagSeconds", "lagSustainedSeconds", "captionAgeSeconds", "cpuStall",
        "cpuPressureAvg10", "engineDuty", "memoryUsedMiB", "memoryHeadroomMiB",
        "p95RequestSeconds", "droppedSpeechChunks", "capacityWaits", "hostRestarts", "seats",
        "speaking", "lastRefusal", "lastShed", "tickMs", "monitorTickMs",
    ]  # fmt: skip
    assert lines[0]["engineDuty"] == 0.7 and lines[0]["hostRestarts"] == 0
    assert lines[0]["tickMs"] == 0.3 and lines[0]["monitorTickMs"] == 1.7
    status["resourceSafety"]["state"] = "recovering"
    del status["resourceSafety"]["admission"]  # an older service: the line still prints
    del status["resourceSafety"]["cpuHealth"]
    del status["hostRestarts"]  # Whistle has no host
    assert await capacity.watch(1) == 1
    (line,) = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert line["state"] == "recovering" and line["lagSeconds"] is None
    assert line["hostRestarts"] is None and line["p95RequestSeconds"] == 0.5
    assert line["monitorTickMs"] is None and "error" not in line


async def test_watch_survives_a_failed_read_and_keeps_its_cadence(monkeypatch, capsys):
    # A trip restarts the container mid-run; the seconds after it are the ones the
    # threshold derivation needs, so one failed read must not end the run.
    status = {"resourceSafety": {"state": "ready", "admission": {"lagSeconds": 0.1}}}
    failures = [
        ConnectionRefusedError(111, "Connection refused"),
        TimeoutError("timed out"),
        RuntimeError("GET /status returned HTTP 503"),
        json.JSONDecodeError("Expecting value", "", 0),
        http.client.RemoteDisconnected("Remote end closed connection without response"),
    ]
    reads = iter([status, *failures, status])

    def installed_request(method, path):
        result = next(reads)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(capacity, "installed_request", installed_request)
    monkeypatch.setattr(capacity, "WATCH_INTERVAL_S", 0.01)
    assert await capacity.watch(7) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 7 and lines[0]["state"] == lines[6]["state"] == "ready"
    assert [line["state"] for line in lines[1:6]] == [None] * 5
    assert [line["error"].split(":")[0] for line in lines[1:6]] == [
        "ConnectionRefusedError",
        "TimeoutError",
        "RuntimeError",
        "JSONDecodeError",
        "RemoteDisconnected",
    ]
    assert list(lines[1]) == [*lines[0], "error"] and lines[1]["lagSeconds"] is None

    # The exit status reports the last read: a service still down at the end is not ready.
    def refused(method, path):
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(capacity, "installed_request", refused)
    assert await capacity.watch(1) == 1
