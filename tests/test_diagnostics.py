"""Diagnostic media must be bounded, decodable and independent of caption work."""

import asyncio
import time
from types import SimpleNamespace

import av
import pytest
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

from cassini_live.diagnostic_video import HEIGHT, WIDTH, DashboardTrack, history_column, render
from cassini_live.diagnostics import DiagnosticVideo, process_usage


async def test_sparse_frames_decode_and_have_bounded_cost():
    track = DashboardTrack()
    decoder = av.CodecContext.create("vp8", "r")
    started = time.process_time()
    try:
        for i in range(6):
            track.next_frame = 0  # accelerate pacing, preserve the real 2s video timestamps
            track.origin = time.monotonic() - i * 2
            packet = await track.recv()
            assert packet.size <= 32768
            frames = decoder.decode(packet)
            assert len(frames) == 1
            assert (frames[0].width, frames[0].height) == (WIDTH, HEIGHT)
        # Loose regression ceiling, distinct from the runtime 0.15 CPU governor.
        assert time.process_time() - started < 1.8
    finally:
        track.stop()


async def test_actual_webrtc_receiver_decodes_diagnostic_video():
    publisher = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    viewer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    received = asyncio.Future()
    track = DashboardTrack()
    publisher.addTrack(track)

    @viewer.on("track")
    def incoming(remote):
        async def read():
            received.set_result(await remote.recv())

        asyncio.create_task(read())

    try:
        await publisher.setLocalDescription(await publisher.createOffer())
        await viewer.setRemoteDescription(publisher.localDescription)
        await viewer.setLocalDescription(await viewer.createAnswer())
        await publisher.setRemoteDescription(
            RTCSessionDescription(sdp=viewer.localDescription.sdp, type="answer")
        )
        frame = await asyncio.wait_for(received, 15)
        assert frame.width == WIDTH and frame.height == HEIGHT
    finally:
        track.stop()
        await publisher.close()
        await viewer.close()


async def test_single_publisher_and_room_scoped_cleanup(monkeypatch):
    monkeypatch.setenv("CASSINI_LIVE_DEBUG_VIDEO", "1")
    manager = DiagnosticVideo(SimpleNamespace())
    stopped = asyncio.Event()

    async def run(token):
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(manager, "_run", run)
    manager.start("one")
    task = manager.task
    manager.start("two")
    assert manager.task is task and manager.owner == "one"
    await asyncio.sleep(0)
    await manager.stop("two")
    assert not task.done()
    await manager.stop("one")
    assert stopped.is_set() and manager.task is None


def test_disabled_by_default_and_no_child(monkeypatch):
    monkeypatch.delenv("CASSINI_LIVE_DEBUG_VIDEO", raising=False)
    manager = DiagnosticVideo(None)
    manager.start("room")
    assert manager.task is None and manager.state == "disabled"


def test_process_metrics_include_real_memory():
    import os

    cpu, rss = process_usage(os.getpid())
    assert cpu >= 0 and rss > 0


@pytest.mark.parametrize(
    "pressure, rss, state",
    [
        (0.71, 40 * 1024**2, "paused: caption resource pressure"),
        (0.2, 129 * 1024**2, "paused: diagnostic budget exceeded"),
    ],
)
async def test_pressure_stops_and_reaps_only_optional_child(monkeypatch, pressure, rss, state):
    import cassini_live.diagnostics as module

    monkeypatch.setenv("NEXTCLOUD_URL", "http://internal-nextcloud")

    class Pipe:
        def write(self, data):
            import json

            assert len(data) < 8192
            config = json.loads(data)
            if "backend" in config:
                assert config["backend"] == "https://public.example"

        async def drain(self):
            pass

    class Child:
        pid = 123
        returncode = None
        stdin = Pipe()
        stdout = None
        terminated = False

        def terminate(self):
            self.terminated = True

        async def wait(self):
            self.returncode = 0

    child = Child()

    async def spawn(*args, **kwargs):
        return child

    settings = SimpleNamespace(
        server="wss://example.invalid", model_dump=lambda: {"stunservers": [], "turnservers": []}
    )
    service = SimpleNamespace(
        settings=settings,
        admission=SimpleNamespace(state="ready"),
        clients={
            "room": SimpleNamespace(defunct=asyncio.Event(), backend_url="https://public.example")
        },
    )
    manager = DiagnosticVideo(service)
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(module, "process_usage", lambda pid: (0, rss))
    monkeypatch.setattr(
        module, "snapshot", lambda: {"memoryFraction": pressure, "memoryLimitBytes": 2 * 1024**3}
    )
    monkeypatch.setattr(manager, "_host_cpu", lambda: (100, 90))
    monkeypatch.setattr(manager, "_caption_cpu", lambda: 0)
    await manager._run("room")
    if pressure >= 0.7:
        assert manager.state == "paused: insufficient diagnostic headroom"
        assert not child.terminated  # no child was allocated
    else:
        assert manager.state == state
        assert child.terminated and child.returncode == 0
    assert service.admission.state == "ready"


def test_paused_state_is_not_overwritten_by_last_publisher_report():
    manager = DiagnosticVideo(None)
    manager.state = "paused: caption resource pressure"
    manager.metrics = {"state": "connected", "frames": 10}
    assert manager.status()["state"] == manager.state


async def test_cancelled_stop_keeps_reaping_and_blocks_a_second_publisher(monkeypatch):
    monkeypatch.setenv("CASSINI_LIVE_DEBUG_VIDEO", "1")
    manager = DiagnosticVideo(None)
    reaping = asyncio.Event()
    release = asyncio.Event()

    async def run(token):
        try:
            await asyncio.Event().wait()
        finally:
            reaping.set()
            await release.wait()

    monkeypatch.setattr(manager, "_run", run)
    manager.start("one")
    original = manager.task
    await asyncio.sleep(0)
    closing = asyncio.create_task(manager.stop())
    await reaping.wait()
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    manager.start("two")
    assert manager.task is original
    release.set()
    await manager.stop()
    assert manager.task is None and manager._stop_task is None


async def test_diagnostic_publisher_sends_bye_on_stop(monkeypatch):
    import json
    import os
    import sys

    from websockets.asyncio.server import serve

    received = []
    offered = asyncio.Event()
    byed = asyncio.Event()

    async def hpb(socket):
        async for raw in socket:
            msg = json.loads(raw)
            received.append(msg)
            if msg.get("type") == "hello":
                await socket.send(json.dumps({"type": "hello", "hello": {"sessionid": "diag-sid"}}))
            elif msg.get("type") == "message" and msg["message"]["data"].get("type") == "offer":
                offered.set()
            elif msg.get("type") == "bye":
                byed.set()

    async with serve(hpb, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "cassini_live.diagnostic_video",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "CASSINI_TALK_SIGNALING_INTERNAL_SECRET": "test-hpb",
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
            },
        )
        config = {
            "room": "room-1",
            "backend": "https://cloud.example",
            "server": f"ws://127.0.0.1:{port}/spreed",
            "stunservers": [],
            "turnservers": [],
        }
        proc.stdin.write((json.dumps(config) + "\n").encode())
        await proc.stdin.drain()
        await asyncio.wait_for(offered.wait(), 10)
        proc.stdin.close()
        proc.terminate()
        await asyncio.wait_for(proc.wait(), 5)
        await asyncio.wait_for(byed.wait(), 2)

    incalls = [
        m["internal"]["incall"]["incall"]
        for m in received
        if m.get("type") == "internal" and m.get("internal", {}).get("type") == "incall"
    ]
    assert incalls == [5, 0]
    assert received[-1]["type"] == "bye"
    assert proc.returncode == 0


def test_history_columns_turn_counters_into_lane_states():
    previous = {
        "finals": 4,
        "drops": 1,
        "lanes": [
            {"id": 1, "speech": 10, "lost": 0},
            {"id": 2, "speech": 5, "lost": 1},
            {"id": 3, "speech": 7, "lost": 0},
            {"id": 4, "speech": 9, "lost": 0},
        ],
    }
    data = {
        "finals": 6,
        "drops": 2,
        "lanes": [
            {"id": 1, "speech": 12, "lost": 0},
            {"id": 2, "speech": 6, "lost": 2, "waiting": True},
            {"id": 3, "speech": 7, "lost": 0, "waiting": True},
            {"id": 4, "speech": 1, "lost": 0},  # gate rebuilt after a language change
            {"id": 5, "speech": 0, "lost": 0},
        ],
    }
    column = history_column(previous, data, 10.0)
    assert column == {
        "t": 10.0,
        "lanes": {1: "speech", 2: "lost", 3: "waiting", 4: "speech"},
        "finals": 2,
        "drops": 1,
    }


def test_lane_samples_are_anonymous_and_reuse_scribe_numbers(monkeypatch):
    monkeypatch.setenv("CASSINI_LIVE_DEBUG_VIDEO", "1")
    video = DiagnosticVideo(SimpleNamespace())
    workers = [object(), object()]
    pool = SimpleNamespace(workers=set(workers), waiting=1)

    class Transcriber:  # Real transcribers are weakly referenced.
        def __init__(self, worker, queued=0):
            self.session_id = "secret-session"
            self.gate = SimpleNamespace(active=True, chunks_emitted=3)
            self.lost_chunks = 0
            self.worker = worker
            self.queue = SimpleNamespace(qsize=lambda: queued)

    transcriber = Transcriber
    first, second, waiter = transcriber(workers[0]), transcriber(workers[1]), transcriber(None, 2)
    samples = [video._lane_sample(pool, t) for t in (first, second, waiter)]
    assert [s["scribe"] for s in samples] == [1, 2, 0]
    assert [s["waiting"] for s in samples] == [False, False, True]
    assert set(samples[0]) == {"id", "active", "speech", "lost", "scribe", "waiting"}
    assert "secret-session" not in str(samples)
    # A replaced worker frees its number for the next one.
    pool.workers = {workers[1], replacement := object()}
    assert video._lane_sample(pool, transcriber(replacement))["scribe"] == 1
    assert video._lane_sample(pool, first)["id"] == samples[0]["id"]


def test_lanes_render_with_history_and_without_data():
    data = {"capacity": 2, "spare": 1, "busy": 1, "vadActive": 1, "lanes": [{"id": 1, "scribe": 1}]}
    history = [{"t": time.monotonic(), "lanes": {1: "speech"}, "finals": 1, "drops": 0}]
    assert render(data, history).size == (WIDTH, HEIGHT)
    assert render({}).size == (WIDTH, HEIGHT)


async def test_publisher_end_is_logged_with_its_reason(monkeypatch, caplog):
    from cassini_live import diagnostics

    caplog.set_level("INFO", logger="cassini_live.diagnostics")
    monkeypatch.setenv("CASSINI_LIVE_DEBUG_VIDEO", "1")
    monkeypatch.setattr(diagnostics, "snapshot", lambda: {"memoryLimitBytes": 0})
    manager = DiagnosticVideo(SimpleNamespace(clients={}, admission=None))
    manager.start("room")
    await manager.task
    assert "Diagnostic video ended (paused: insufficient diagnostic headroom)" in caplog.text
