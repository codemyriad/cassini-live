import asyncio

import numpy as np
import pytest

from cassini_live import catalog
from cassini_live.engines import create_backend
from cassini_live.engines.shared import EngineFailed, EngineStreamError, SharedEngine
from cassini_live.pool import CapacityError, WorkerPool
from tests.fakes_engine import CHUNK, FakeHostProcess

SPEECH = np.full(CHUNK, 0.1, dtype="<f4")


class Hosts(list):
    """process_factory that records every host it creates."""

    def __init__(self, **options):
        super().__init__()
        self.options = options

    def __call__(self):
        host = FakeHostProcess(**self.options)
        self.append(host)
        return host


def engine(size=2, engine_id="nemotron-en", hosts=None, **kwargs):
    hosts = hosts if hosts is not None else Hosts()
    shared = SharedEngine(
        catalog.get(engine_id), "/models/x", size, process_factory=hosts, **kwargs
    )
    return shared, hosts


async def wait_for(condition):
    async with asyncio.timeout(2):
        while not condition():
            await asyncio.sleep(0.005)


def test_create_backend_dispatches_on_engine_kind():
    assert isinstance(create_backend("whistle", size=2), WorkerPool)
    shared = create_backend("nemotron-multilingual", size=3, model_dir="/m", threads=2)
    assert isinstance(shared, SharedEngine) and shared.size == 3 and shared.threads == 2
    with pytest.raises(ValueError):
        create_backend("nemotron-en", size=1)
    with pytest.raises(KeyError):
        create_backend("unknown", size=1)


async def test_start_spawns_one_host_with_its_environment():
    shared, hosts = engine()
    await shared.start()
    (host,) = hosts
    assert host.argv[1:5] == ["-m", "cassini_live.engine_host", "--engine", "nemotron-en"]
    assert "--model-dir" in host.argv and "/models/x" in host.argv
    assert host.env["OMP_NUM_THREADS"] == "1" and host.env["ORT_DISABLE_TELEMETRY"] == "1"
    assert shared.is_idle() and shared.rss_mib == 900
    status = shared.status()
    assert status["engine"] == "nemotron-en" and status["workers"] == 1
    assert {
        "capacity",
        "available",
        "admittedSpeakers",
        "openStreams",
        "hostRestarts",
        "speechGating",
        "streamEnds",
        "waitingSpeakers",
        "lagSeconds",
        "p95RequestSeconds",
    } <= status.keys()
    await shared.close()
    assert host.closed and shared.status()["workers"] == 0


def test_lag_meter_reports_last_and_p95():
    from cassini_live.pool import _LagMeter

    meter = _LagMeter()
    assert meter.last() == 0.0 and meter.p95() == 0.0
    for seconds in range(100, 0, -1):
        meter.record(seconds / 100)
    assert meter.last() == 0.01 and meter.p95() == 0.95
    for _ in range(meter.WINDOW):
        meter.record(2.0)
    assert meter.p95() == 2.0  # old samples age out of the window


async def test_status_reports_recorded_lag_and_last_message_time():
    clock = [100.0]
    shared, _ = engine(clock=lambda: clock[0])
    await shared.start()
    # The ready message is read by the spawn, not by the reader.
    assert shared.last_message_at is None
    stream = await shared.open_stream("en")
    assert shared.last_message_at == 100.0
    clock[0] = 103.5
    await stream.audio(SPEECH)
    assert shared.last_message_at == 103.5
    shared.lag.record(0.4)
    shared.lag.record(0.2)
    status = shared.status()
    assert status["lagSeconds"] == 0.2 and status["p95RequestSeconds"] == 0.4
    assert set(shared.transcribers) == set()
    track = shared.transcriber("s", "en", asyncio.Queue())
    assert set(shared.transcribers) == {track}
    await shared.close()


async def test_slot_fifo_handoff_cannot_be_stolen():
    shared, _ = engine(size=1)
    await shared.start()
    held = await shared.acquire_slot()
    assert held.index == 1 and shared.busy == 1
    first = asyncio.create_task(shared.acquire_slot())
    second = asyncio.create_task(shared.acquire_slot())
    await asyncio.sleep(0)
    shared.release_slot(held)
    with pytest.raises(CapacityError):
        await shared.acquire_slot(timeout=0.001)
    granted = await first
    assert not second.done()
    shared.release_slot(granted)
    shared.release_slot(await second)
    assert shared.busy == 0 and not shared.waiting
    await shared.close()


async def test_cancelled_waiter_returns_slot_even_during_handoff():
    shared, _ = engine(size=1)
    await shared.start()
    held = await shared.acquire_slot()
    waiting = asyncio.create_task(shared.acquire_slot())
    await asyncio.sleep(0)
    shared.release_slot(held)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert shared.busy == 0
    assert await shared.acquire_slot(timeout=0.01)
    await shared.close()


async def test_timeout_and_close_leave_no_orphan_waiters():
    shared, _ = engine(size=1)
    await shared.start()
    held = await shared.acquire_slot()
    with pytest.raises(CapacityError):
        await shared.acquire_slot(timeout=0.001)
    assert not shared.waiting
    task = asyncio.create_task(shared.acquire_slot())
    await asyncio.sleep(0)
    await shared.close()
    with pytest.raises(CapacityError):
        await task
    shared.release_slot(held)
    assert not shared.waiting and shared.busy == 0


async def test_admission_limit_bounds_slots():
    shared, _ = engine(size=3)
    await shared.start()
    shared.admission_limit = 1
    await shared.acquire_slot()
    with pytest.raises(CapacityError):
        await shared.acquire_slot(timeout=0.01)
    assert shared.status()["available"] == 0
    await shared.close()


async def test_load_check_refusal_prevents_the_spawn():
    shared, hosts = engine()
    shared.load_check = lambda: "Not enough free container memory"
    with pytest.raises(CapacityError, match="free container memory"):
        await shared.start()
    assert hosts == [] and shared.closed


async def test_resource_check_after_load_closes_the_host():
    shared, hosts = engine()
    shared.resource_check = lambda: "pressure"
    with pytest.raises(CapacityError):
        await shared.start()
    assert hosts[0].closed and shared.process is None


async def test_ready_failure_and_timeout():
    shared, hosts = engine(hosts=Hosts(ready_type="error"))
    with pytest.raises(RuntimeError):
        await shared.start()
    assert hosts[0].closed
    shared, hosts = engine(hosts=Hosts(ready=False), ready_timeout=0.05)
    with pytest.raises(TimeoutError):
        await shared.start()
    assert hosts[0].closed


async def test_close_during_start_wins():
    shared, hosts = engine(hosts=Hosts(ready=False))
    task = asyncio.create_task(shared.start())
    await wait_for(lambda: hosts)
    await shared.close()
    with pytest.raises(Exception):
        await task
    assert hosts[0].closed and shared.process is None and shared.closed


async def test_streams_open_commit_finish_and_language_mapping():
    shared, hosts = engine(engine_id="nemotron-multilingual")
    await shared.start()
    stream = await shared.open_stream("de")
    auto = await shared.open_stream("auto")
    assert [m["language"] for m in hosts[0].sent] == ["de", "auto"]
    assert (await stream.audio(SPEECH))["pending"] == "w1"
    assert (await stream.commit())["text"] == "w1"
    assert stream.alive and shared.status()["openStreams"] == 2
    assert (await auto.audio(SPEECH)) == {"text": "", "pending": "w1", "language": "de"}
    assert (await auto.finish())["text"] == "w1"
    assert not auto.alive
    await stream.cancel()
    assert shared.streams == {} and shared.is_idle()
    await shared.close()


async def test_english_engine_maps_every_language_to_english():
    shared, hosts = engine()
    await shared.start()
    await shared.open_stream("de")
    assert hosts[0].sent[0]["language"] == "en"
    await shared.close()


async def test_max_streams():
    shared, _ = engine(max_streams=1)
    await shared.start()
    await shared.open_stream("en")
    with pytest.raises(CapacityError):
        await shared.open_stream("en")
    assert shared.status()["openStreams"] == 1
    await shared.close()


async def test_per_stream_error_does_not_restart_the_host():
    shared, hosts = engine()
    await shared.start()
    stream = await shared.open_stream("en")
    with pytest.raises(EngineStreamError):
        await stream.audio(np.zeros(16001, dtype="<f4"))
    assert not stream.alive and shared.streams == {}
    with pytest.raises(EngineStreamError):
        await stream.commit()
    assert len(hosts) == 1 and shared.failures == 0 and shared.ready
    await shared.close()


async def test_host_exit_fails_streams_and_restarts():
    shared, hosts = engine()
    await shared.start()
    stream = await shared.open_stream("en")
    slot = await shared.acquire_slot()
    hosts[0].crash()
    await wait_for(lambda: shared.restarts == 1)
    assert not stream.alive and shared.failures == 1 and len(hosts) == 2
    with pytest.raises(EngineStreamError):
        await stream.audio(SPEECH)
    # Slots are independent of the host.
    assert slot in shared.leased
    assert (await (await shared.open_stream("en")).audio(SPEECH))["pending"] == "w1"
    assert shared.status()["hostRestarts"] == 1
    await shared.close()


async def test_pending_request_fails_when_the_host_dies():
    shared, hosts = engine(hosts=Hosts(mute=False))
    await shared.start()
    stream = await shared.open_stream("en")
    hosts[0].mute = True
    request = asyncio.create_task(stream.audio(SPEECH))
    await asyncio.sleep(0.01)
    hosts[0].crash()
    with pytest.raises(EngineFailed):
        await request
    await shared.close()


async def test_three_exits_within_a_minute_open_the_circuit():
    clock = [0.0]
    shared, hosts = engine(clock=lambda: clock[0])
    await shared.start()
    for expected in (1, 2):
        hosts[-1].crash()
        clock[0] += 10
        await wait_for(lambda expected=expected: shared.restarts == expected)
    hosts[-1].crash()
    await wait_for(lambda: shared.failure_circuit_open)
    await asyncio.sleep(0.01)
    assert len(hosts) == 3 and hosts[-1].closed
    await shared.close()


async def test_spaced_exits_do_not_open_the_circuit():
    clock = [0.0]
    shared, hosts = engine(clock=lambda: clock[0])
    await shared.start()
    for expected in (1, 2, 3):
        hosts[-1].crash()
        clock[0] += 40
        await wait_for(lambda expected=expected: shared.restarts == expected)
    assert not shared.failure_circuit_open
    await shared.close()


async def test_restart_load_check_failure_opens_the_circuit():
    shared, hosts = engine()
    await shared.start()
    shared.load_check = lambda: "no memory"
    hosts[0].crash()
    await wait_for(lambda: shared.failure_circuit_open)
    assert len(hosts) == 1
    await shared.close()


async def test_request_timeout_counts_as_host_failure():
    shared, hosts = engine(request_timeout=0.05)
    await shared.start()
    stream = await shared.open_stream("en")
    hosts[0].mute = True
    with pytest.raises(EngineFailed):
        await stream.audio(SPEECH)
    assert shared.failures == 1 and not stream.alive
    await wait_for(lambda: shared.restarts == 1)
    assert hosts[0].closed
    await shared.close()


async def test_cancel_never_raises_on_a_dead_host():
    shared, hosts = engine()
    await shared.start()
    stream = await shared.open_stream("en")
    await shared.close()
    await stream.cancel()
    with pytest.raises(CapacityError):
        await shared.open_stream("en")


async def test_resize_only_while_closed():
    shared, _ = engine()
    shared.closed = True
    shared.resize(4)
    assert shared.size == 4
    await shared.start()
    with pytest.raises(RuntimeError):
        shared.resize(1)
    with pytest.raises(RuntimeError):
        await shared.start()
    await shared.close()


async def test_is_idle_tracks_slots_and_streams():
    shared, _ = engine()
    assert not shared.is_idle()
    await shared.start()
    slot = await shared.acquire_slot()
    assert not shared.is_idle()
    shared.release_slot(slot)
    stream = await shared.open_stream("en")
    assert not shared.is_idle()
    await stream.cancel()
    assert shared.is_idle()
    await shared.close()


class SlowHost(FakeHostProcess):
    """Takes a while to exit, like a real host answering quit."""

    def __init__(self):
        super().__init__()
        self.exit = asyncio.Event()
        self.exited = False

    async def close(self):
        await self.exit.wait()
        await super().close()
        self.exited = True


async def test_cancelled_close_still_stops_the_host():
    # A tripped monitor is cancelled mid-teardown; the old model must not outlive it.
    hosts = []
    shared = SharedEngine(
        catalog.get("nemotron-en"),
        "/m",
        1,
        process_factory=lambda: hosts.append(SlowHost()) or hosts[-1],
    )
    await shared.start()
    closing = asyncio.create_task(shared.close())
    await asyncio.sleep(0.01)
    closing.cancel()
    await asyncio.sleep(0.01)
    assert not closing.done()  # Still waiting for the host, not abandoning it.
    hosts[0].exit.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert hosts[0].exited and shared.process is None


async def test_host_process_is_killed_when_its_close_is_cancelled():
    import sys

    from cassini_live.engines.shared import EngineHostProcess

    host = EngineHostProcess()
    stubborn = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    await host.start([sys.executable, "-c", stubborn], None)
    closing = asyncio.create_task(host.close())
    await asyncio.sleep(0.2)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    async with asyncio.timeout(5):
        assert await host.process.wait() == -9


async def test_cancelled_open_frees_the_host_stream(tmp_path):
    engine = SharedEngine(
        catalog.get("nemotron-en"), tmp_path, 2, max_streams=2, process_factory=FakeHostProcess
    )
    await engine.start()
    try:
        task = asyncio.create_task(engine.open_stream("en"))
        await asyncio.sleep(0)  # request sent, reply not yet read
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.1)
        await engine.open_stream("en")
        await engine.open_stream("en")  # the host must not still hold the cancelled one
        assert len(engine.streams) == 2
    finally:
        await engine.close()
