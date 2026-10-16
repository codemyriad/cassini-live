"""Diagnostic media must be bounded, decodable and independent of caption work."""

import asyncio
import time
import weakref
from types import SimpleNamespace

import av
import pytest
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription

from cassini_live.diagnostic_video import (
    FRAME_CAP,
    HEIGHT,
    QUANTIZER,
    QUANTIZER_MAX,
    WIDTH,
    DashboardTrack,
    history_column,
    render,
)
from cassini_live.diagnostics import NAME_BYTES, DiagnosticVideo, clip_name, process_usage


async def test_sparse_frames_decode_and_have_bounded_cost():
    track = DashboardTrack()
    decoder = av.CodecContext.create("vp8", "r")
    started = time.process_time()
    try:
        for i in range(6):
            track.next_frame = 0  # accelerate pacing, preserve the real 2s video timestamps
            track.origin = time.monotonic() - i * 2
            packet = await track.recv()
            assert packet.size <= FRAME_CAP
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

    from cassini_live.diagnostic_video import LINE_LIMIT

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
            "-c",
            # The real entry point, without nice 19: on a loaded machine the child would
            # miss its one-second shutdown sends and the test would flake.
            "from cassini_live.diagnostic_video import run; run(nice=0)",
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
        # Bounded but generous: importing av and aiortc takes seconds on a busy machine.
        await asyncio.wait_for(offered.wait(), 30)
        # A metrics line over the child's limit is skipped and still answered; the next
        # one is read normally.
        proc.stdin.write(json.dumps({"lanes": [], "pad": "x" * 3 * LINE_LIMIT}).encode() + b"\n")
        await proc.stdin.drain()
        report = json.loads(await asyncio.wait_for(proc.stdout.readline(), 15))
        assert report["skippedSamples"] == 1 and report["state"] != "failed"
        proc.stdin.write(b'{"vadActive": 1}\n')
        await proc.stdin.drain()
        report = json.loads(await asyncio.wait_for(proc.stdout.readline(), 15))
        assert report["skippedSamples"] == 1
        proc.stdin.close()
        proc.terminate()
        await asyncio.wait_for(proc.wait(), 15)
        await asyncio.wait_for(byed.wait(), 5)

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
        "lag": None,
        "shed": False,
    }


def test_history_columns_carry_lag_sheds_and_pauses():
    previous = {"lanes": [{"id": 1, "speech": 3}], "admission": {"shedEvents": 2}}
    data = {
        "lanes": [{"id": 1, "speech": 4, "paused": True}],
        "admission": {"shedEvents": 3, "lagSeconds": 2.4},
    }
    column = history_column(previous, data, 5.0)
    assert column["lanes"] == {1: "paused"}
    assert column["lag"] == 2.4 and column["shed"] is True
    assert history_column(data, data, 6.0)["shed"] is False


def test_one_frame_and_one_sample_per_second_over_two_minutes():
    from cassini_live import diagnostic_video, diagnostics

    assert diagnostic_video.FRAME_S == diagnostics.SAMPLE_S == 1.0
    assert diagnostic_video.COLUMNS * diagnostic_video.FRAME_S == 120
    assert diagnostic_video.COLUMNS * diagnostic_video.COLUMN_W == diagnostic_video.LANES_W
    # The CPU stop rule still means six seconds above 15 % of one CPU.
    assert diagnostics.HIGH_SAMPLES * diagnostics.SAMPLE_S == 6


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
    assert set(samples[0]) == {
        "id",
        "active",
        "speech",
        "lost",
        "scribe",
        "waiting",
        "paused",
        "lag",
    }
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


ADMISSION = {
    "open": False,
    "reason": "lag",
    "seats": 2,
    "ceiling": 4,
    "lagSeconds": 1.3,
    "cpuStall": 0.06,
    "engineDuty": 0.97,
    "shedEvents": 1,
    "lagAdmit": 1.0,
    "lagShed": 2.0,
    "cpuStallAdmit": 0.2,
    "holdOff": 10.0,
}
FULL = {
    "engine": "nemotron-multilingual",
    "quota": 4.0,
    "limitMiB": 3072,
    "captionMiB": 1340,
    "vadActive": 3,
    "capacity": 4,
    "threads": 3,
    "p95": 1.12,
    "cpuWeight": 10,
    "segmentSeconds": 8,
    "admission": ADMISSION,
    "decision": {"at": time.time(), "verb": "hold", "who": "Ana", "text": "lag 1.3 s"},
    "lanes": [
        {"id": 1, "name": "Ana Ferreira", "scribe": 1, "lag": 1.2},
        {"id": 2, "name": "x" * 200, "paused": True},
        {"id": 3, "waiting": True, "lag": 1.6},
        {"id": 4, "other": True},
    ],
}


@pytest.mark.parametrize("view", ["lanes", "pipeline"])
@pytest.mark.parametrize(
    "data",
    [
        FULL,
        {**FULL, "admission": {**ADMISSION, "observeOnly": True}},
        {**FULL, "admission": {}, "decision": None, "lanes": []},
        {},
    ],
    ids=["full", "observe", "no-governor", "empty"],
)
def test_both_views_render_full_observe_and_empty_data(data, view):
    now = time.monotonic()
    history = [
        {
            "t": now - 120 + i,
            "lanes": {1: "speech", 2: "paused", 3: "waiting"},
            "finals": i % 7 == 0,
            "drops": 0,
            "lag": 0.1 * (i % 30),
            "shed": i == 90,
        }
        for i in range(130)
    ]
    assert render(data, history, view).size == (WIDTH, HEIGHT)


def test_observe_only_decisions_are_worded_as_would(monkeypatch):
    from cassini_live import diagnostic_video

    drawn = []
    real = diagnostic_video.ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        drawn.append(text)
        return real(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(diagnostic_video.ImageDraw.ImageDraw, "text", spy)
    render({**FULL, "admission": {**ADMISSION, "observeOnly": True}}, [], "lanes")
    assert "Would hold new voices" in drawn and "would hold Ana" in drawn
    assert "would hold: lag" in drawn
    drawn.clear()
    render(FULL, [], "lanes")
    assert "Holding new voices" in drawn and "hold Ana" in drawn


def test_names_cross_the_pipe_for_the_owning_room_only(monkeypatch):
    monkeypatch.setenv("CASSINI_LIVE_DEBUG_VIDEO", "1")
    video = DiagnosticVideo(SimpleNamespace())
    pool = SimpleNamespace(workers=set(), waiting=0)

    class Transcriber:
        def __init__(self):
            self.gate = SimpleNamespace(active=True, chunks_emitted=1)
            self.lost_chunks, self.worker, self.paused_until = 0, None, None
            self.pending_since = None
            self.queue = SimpleNamespace(qsize=lambda: 0)

    mine, theirs, unknown = Transcriber(), Transcriber(), Transcriber()
    owner = SimpleNamespace(
        transcribers={"s1": mine, "s3": unknown},
        speaker_name={"s1": "Ana Ferreira"}.get,
    )
    other = SimpleNamespace(transcribers={"s2": theirs}, speaker_name={"s2": "Bob Secret"}.get)
    speakers, labels = video._speakers(owner, [owner, other])
    lanes = video._lane_samples(pool, owner, speakers, labels, time.monotonic())
    by_id = {lane["id"]: lane for lane in lanes}
    assert by_id[video._lane(mine)]["name"] == "Ana Ferreira"
    assert by_id[video._lane(unknown)]["name"] == "Participant"
    assert by_id[video._lane(theirs)] == {**by_id[video._lane(theirs)], "other": True}
    assert "name" not in by_id[video._lane(theirs)]
    assert "Bob Secret" not in str(lanes) and "Bob Secret" not in str(labels)
    # With more than ten speakers, the owning room's lanes are kept.
    crowd = [SimpleNamespace(transcribers={f"x{i}": Transcriber()}) for i in range(12)]
    speakers, labels = video._speakers(owner, [*crowd, owner])
    lanes = video._lane_samples(pool, owner, speakers, labels, time.monotonic())
    assert len(lanes) == 10 and sum("name" in lane for lane in lanes) == 2


def test_decision_line_names_only_the_owning_room():
    from cassini_live.load import THRESHOLDS

    video = DiagnosticVideo(SimpleNamespace())

    class Box:  # SimpleNamespace is not weakly referenceable
        pass

    mine, theirs = Box(), Box()
    governor = SimpleNamespace(
        thresholds=THRESHOLDS,
        last_decision={
            "at": 10.0,
            "reason": "lag",
            "seats": 2,
            "ceiling": 4,
            "lag": 1.3,
            "cpuStall": 0.05,
            "headroomMiB": 1800,
        },
        last_decision_ref=weakref.ref(theirs),
        last_shed=None,
        last_shed_ref=None,
    )
    labels = {mine: "Ana Ferreira"}
    decision = video._decision(governor, None, labels)
    assert decision["verb"] == "hold" and decision["who"] == "someone in another call"
    assert "lag 1.3 s ≥ admit 1.0" in decision["text"]
    governor.last_decision = {**governor.last_decision, "reason": None, "at": 11.0}
    governor.last_decision_ref = weakref.ref(mine)
    decision = video._decision(governor, None, labels)
    assert decision["verb"] == "admit" and decision["who"] == "Ana Ferreira"
    assert decision["text"].startswith("seat 2 of 4 · lag 1.3 s < 1.0 · stall 5%")
    governor.last_shed, governor.last_shed_ref = {"at": 12.0, "lag": 2.3}, weakref.ref(mine)
    decision = video._decision(governor, None, labels)
    assert decision["verb"] == "pause" and "shed 2.0" in decision["text"]


async def test_publisher_end_is_logged_with_its_reason(monkeypatch, caplog):
    from cassini_live import diagnostics

    caplog.set_level("INFO", logger="cassini_live.diagnostics")
    monkeypatch.setenv("CASSINI_LIVE_DEBUG_VIDEO", "1")
    monkeypatch.setattr(diagnostics, "snapshot", lambda: {"memoryLimitBytes": 0})
    manager = DiagnosticVideo(SimpleNamespace(clients={}, admission=None))
    manager.start("room")
    await manager.task
    assert "Diagnostic video ended (paused: insufficient diagnostic headroom)" in caplog.text


def busy_history(lanes=10, period=2, paused=(1, 4, 7)):
    """Ten lanes changing state every `period` seconds, several paused stretches and an
    uneven lag graph: the busiest lanes view the reviewers measured (41-61 KB at 0.7.4)."""
    now = time.monotonic()
    states = ["speech", "lost", "paused", "waiting", None]
    return [
        {
            "t": now - 120 + j,
            "lanes": {
                i + 1: (
                    "paused"
                    if i in paused and (j // 10) % 2
                    else states[(i + j // period) % len(states)]
                )
                for i in range(lanes)
            },
            "finals": j % 3 == 0,
            "drops": 0,
            "lag": (j * 7919 % 251) / 100,
            "shed": j % 17 == 0,
        }
        for j in range(120)
    ]


def busy_data(name="W" * 40):
    return {
        **FULL,
        "admission": {**ADMISSION, "observeOnly": True, "seats": 8, "ceiling": 8},
        "decision": {"at": time.time(), "verb": "keep paused", "who": name, "text": "x" * 60},
        "lanes": [
            {
                "id": i + 1,
                "name": name,
                "scribe": i % 4 + 1 if i % 3 == 0 else 0,
                "waiting": i % 3 == 1,
                "paused": i % 3 == 2,
                "lag": 2.71,
            }
            for i in range(10)
        ],
    }


@pytest.mark.parametrize("name", ["W" * 40, "\U0001f600" * 20, "Ж" * 40])
async def test_busiest_frames_stay_under_the_cap_and_never_raise(name):
    track = DashboardTrack()
    decoder = av.CodecContext.create("vp8", "r")
    try:
        track.update(busy_data(name))
        for column in busy_history():
            track.history.append(column)
        for i in range(KEYFRAMES := 6):
            track.next_frame = 0
            track.origin = time.monotonic() - i  # stay within the lanes view
            packet = await track.recv()
            assert packet.size <= FRAME_CAP
            assert len(decoder.decode(packet)) == 1
            track.history.append(busy_history()[i])
        assert track.frames == KEYFRAMES and track.skipped == 0
        assert QUANTIZER <= track.quantizer <= QUANTIZER_MAX
    finally:
        track.stop()


async def test_a_frame_too_big_even_when_coarse_is_skipped_then_a_keyframe_follows(monkeypatch):
    from cassini_live import diagnostic_video

    track = DashboardTrack()
    decoder = av.CodecContext.create("vp8", "r")
    image = render(busy_data(), busy_history())
    try:
        first = track._encode(image, 0, True)
        assert first is not None and len(decoder.decode(first)) == 1
        monkeypatch.setattr(diagnostic_video, "FRAME_CAP", 500)
        assert track._encode(image, 90000, False) is None
        assert track.skipped == 1 and track.force_key and track.quantizer == QUANTIZER_MAX
        monkeypatch.undo()
        # The next frame is a keyframe even off the keyframe schedule: the encoder's
        # reference was never sent, and the viewer still holds the first picture.
        track.frames, track.view = 1, "lanes"
        track.next_frame, track.origin = 0, time.monotonic()
        packet = await track.recv()
        assert packet.is_keyframe and not track.force_key
        assert len(decoder.decode(packet)) == 1
    finally:
        track.stop()


async def test_a_view_change_returns_to_the_finest_quantizer():
    track = DashboardTrack()
    try:
        track.update(busy_data())
        for column in busy_history():
            track.history.append(column)
        track.next_frame, track.origin = 0, time.monotonic()
        await track.recv()
        assert track.quantizer > QUANTIZER  # the busy lanes view needed a coarser one
        track.next_frame, track.origin = 0, time.monotonic() - 20  # pipeline view
        await track.recv()
        assert track.quantizer == QUANTIZER and track.view == "pipeline"
    finally:
        track.stop()


async def test_oversized_lines_are_dropped_whole():
    from cassini_live.diagnostic_video import read_line

    reader = asyncio.StreamReader(limit=64)
    reader.feed_data(b"a" * 200 + b"\n" + b"ok\n" + b"b" * 70)
    reader.feed_data(b"c" * 70 + b"\n" + b"next\n")
    reader.feed_eof()
    assert await read_line(reader) is None
    assert await read_line(reader) == b"ok\n"
    assert await read_line(reader) is None
    assert await read_line(reader) == b"next\n"
    assert await read_line(reader) == b""


def test_names_are_cut_by_the_bytes_they_cost_in_the_line():
    import json

    assert clip_name("x" * 200) == "x" * 40
    assert clip_name("Ж" * 40) == "Ж" * 40  # 6 bytes escaped: 240
    assert clip_name("\U0001f600" * 40) == "\U0001f600" * 20  # 12 bytes escaped
    for name in ("x" * 200, "Ж" * 80, "\U0001f600" * 40, "Guest · " + "\U0001f600" * 40):
        assert len(json.dumps(clip_name(name))) - 2 <= NAME_BYTES


def widest(value):
    """The widest JSON a status value of this type can plausibly take."""
    if isinstance(value, bool):
        return False
    if isinstance(value, dict):
        return {key: widest(item) for key, item in value.items()}
    if isinstance(value, int):
        return -(10**12)
    if isinstance(value, str):
        return "x" * 24
    return -1.2345678901234567e-300  # floats and None (numbers not yet measured)


@pytest.mark.parametrize("reason", [None, "lag", "cpu", "memory", "ceiling", "hold", "shed"])
async def test_worst_case_sample_line_fits_the_child_limit(monkeypatch, reason):
    import json

    import cassini_live.diagnostics as module
    from cassini_live.diagnostic_video import LINE_LIMIT
    from cassini_live.load import LoadGovernor

    monkeypatch.setenv("CASSINI_LIVE_DEBUG_VIDEO", "1")
    lines, shown = [], []

    class Pipe:
        def write(self, data):
            lines.append(data)

        async def drain(self):
            pass

        def close(self):
            pass

    class Out:
        async def readline(self):
            return b'{"state": "failed"}\n'

    class Child:
        pid, returncode, stdin, stdout = 123, None, Pipe(), Out()

        def terminate(self):
            self.returncode = 0

        async def wait(self):
            return 0

    async def spawn(*args, **kwargs):
        return Child()

    class Transcriber:
        SEGMENT_SECONDS = 15

        def __init__(self, worker):
            big = 10**12
            self.gate = SimpleNamespace(
                active=True, chunks_emitted=big, last_probability=0.123456789
            )
            self.queue = SimpleNamespace(qsize=lambda: big)
            self.lost_chunks = self.lease_samples = big
            self.worker, self.paused_until, self.pending_since = worker, 1e18, -1e9
            self.pending = "x"
            self.has_lease = lambda: False

    workers = [object() for _ in range(12)]
    mine = {f"s{i}": Transcriber(workers[i] if i % 2 else None) for i in range(12)}
    # 40 emoji: 480 bytes once escaped, before the cut.
    owner = SimpleNamespace(
        transcribers=mine,
        speaker_name=lambda sid: "\U0001f600" * 40,
        defunct=asyncio.Event(),
        backend_url="https://cloud.example",
        show_names=shown.append,
    )
    other = SimpleNamespace(transcribers={"o": Transcriber(None)}, defunct=asyncio.Event())
    governor = LoadGovernor(ceiling=8)
    status = {key: widest(value) for key, value in governor.status().items()}
    status.update(
        lastRefusal={"at": -1.2345678901234567e-300, "reason": "memory"},
        lastShed={"at": -1.2345678901234567e-300, "lag": -1.2345678901234567e-300},
    )
    governor.status = lambda: dict(status)
    first = next(iter(mine.values()))
    governor.last_decision = {
        "at": 1.0,
        "reason": None if reason == "shed" else reason,
        "seats": -(10**12),
        "ceiling": -(10**12),
        "lag": -1.2345678901234567e30,
        "cpuStall": -1.2345678901234567e30,
        "headroomMiB": -(10**12),
    }
    governor.last_decision_ref = weakref.ref(first)
    if reason == "shed":
        governor.last_shed = {"at": 2.0, "lag": -1.2345678901234567e30}
        governor.last_shed_ref = weakref.ref(first)
    big = 10**12
    pool = SimpleNamespace(
        busy=big,
        admission_limit=big,
        waiting=big,
        workers=set(workers),
        dropped_chunks=big,
        chunks_processed=big,
        audio_seconds=1.2345678901234567e30,
        scheduling_yields=big,
        stream_ends={},
        finals_emitted=big,
        partials_emitted=big,
        engine_id="nemotron-multilingual",
        threads=big,
        restarts=big,
        lag=SimpleNamespace(p95=lambda: -1.2345678901234567e-300),
    )
    settings = SimpleNamespace(
        server="wss://example.invalid", model_dump=lambda: {"stunservers": [], "turnservers": []}
    )
    service = SimpleNamespace(
        settings=settings,
        admission=SimpleNamespace(
            state="ready", pool=pool, governor=governor, tracks=[None] * 99, max_tracks=big
        ),
        clients={"room": owner, **{f"r{i}": other for i in range(9)}},
    )
    manager = DiagnosticVideo(service)
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(module, "process_usage", lambda pid: (0, 1))
    monkeypatch.setattr(
        module,
        "snapshot",
        lambda: {
            "memoryFraction": 0.2,
            "memoryLimitBytes": 2 * 1024**3,
            "hostAvailableBytes": 10**18,
            "hostMemoryBytes": 10**18,
            "cpuQuotaCpus": -1.2345678901234567e-300,
            "oomKills": big,
        },
    )
    monkeypatch.setattr(manager, "_host_cpu", lambda: (100, 90))
    monkeypatch.setattr(manager, "_caption_cpu", lambda: 0)
    monkeypatch.setattr(manager, "_cpu_weight", lambda: 10000)
    await manager._run("room")
    sample = json.loads(lines[-1])
    assert len(sample["lanes"]) == 10 and all(len(lane["name"]) == 20 for lane in sample["lanes"])
    assert sample["decision"]["who"] == "\U0001f600" * 20
    # Measured: 6401 bytes at most (names 11 x 240, every number 24 characters wide);
    # at least 1.5 KiB must stay free for fields added later.
    assert len(lines[-1]) <= LINE_LIMIT - 1536
    # Names were read only while the video ran.
    assert shown == [True, False]


@pytest.mark.parametrize(
    "segment, label", [(None, "per piece"), (0, "per piece"), (8, "per piece, 8 s max")]
)
def test_pipeline_names_no_segment_maximum_without_a_microphone(monkeypatch, segment, label):
    from cassini_live import diagnostic_video

    drawn = []
    real = diagnostic_video.ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        drawn.append(text)
        return real(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(diagnostic_video.ImageDraw.ImageDraw, "text", spy)
    render({**FULL, "lanes": [], "segmentSeconds": segment}, [], "pipeline")
    assert any(label == text or label in text.split("\n") for text in drawn)
    assert not any("None" in text for text in drawn)
