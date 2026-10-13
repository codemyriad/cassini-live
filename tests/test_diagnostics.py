"""Diagnostic media must be bounded, decodable and independent of caption work."""

import asyncio
import time
from types import SimpleNamespace

import av
import pytest
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

from cassini_live.diagnostic_video import HEIGHT, WIDTH, DashboardTrack
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

    class Pipe:
        def write(self, data):
            assert len(data) < 8192

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
        clients={"room": SimpleNamespace(defunct=asyncio.Event())},
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
