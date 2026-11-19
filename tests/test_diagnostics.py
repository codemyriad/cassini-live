"""Diagnostic samples must be anonymous, bounded and independent of caption work."""

import time
import weakref
from types import SimpleNamespace

from cassini_live.diagnostic_history import history_column
from cassini_live.diagnostics import NAME_BYTES, DiagnosticSampler, clip_name


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


def test_history_columns_load_nothing_heavy():
    """The caption service can derive columns without the service's web and Nextcloud
    stack or the media libraries."""
    import subprocess
    import sys

    heavy = {
        "av",
        "aiortc",
        "fastapi",
        "nc_py_api",
        "cassini_live.diagnostics",
    }
    code = (
        f"import sys, cassini_live.diagnostic_history; print(sorted({heavy!r} & set(sys.modules)))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_one_sample_per_second_over_two_minutes():
    from cassini_live import diagnostic_history, diagnostics

    assert diagnostics.SAMPLE_S == 1.0
    # A lane lasts as long as the history it is drawn against.
    assert diagnostic_history.COLUMNS * diagnostics.SAMPLE_S == diagnostics.LANE_WINDOW_S


def test_lane_samples_are_anonymous_and_reuse_scribe_numbers():
    sampler = DiagnosticSampler(SimpleNamespace())
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
    samples = [sampler._lane_sample(pool, t) for t in (first, second, waiter)]
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
    assert sampler._lane_sample(pool, transcriber(replacement))["scribe"] == 1
    assert sampler._lane_sample(pool, first)["id"] == samples[0]["id"]


def test_names_leave_the_sampler_for_the_owning_room_only():
    sampler = DiagnosticSampler(SimpleNamespace())
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
    speakers, labels = sampler._speakers(owner, [owner, other])
    lanes, silent, hidden = sampler._lane_samples(pool, owner, speakers, labels, time.monotonic())
    assert (silent, hidden) == (0, 0)
    by_id = {lane["id"]: lane for lane in lanes}
    assert by_id[sampler._lane(mine)]["name"] == "Ana Ferreira"
    assert by_id[sampler._lane(unknown)]["name"] == "Participant"
    assert by_id[sampler._lane(theirs)] == {**by_id[sampler._lane(theirs)], "other": True}
    assert "name" not in by_id[sampler._lane(theirs)]
    assert "Bob Secret" not in str(lanes) and "Bob Secret" not in str(labels)
    # With more than ten speakers, the owning room's lanes are kept.
    crowd = [SimpleNamespace(transcribers={f"x{i}": Transcriber()}) for i in range(12)]
    speakers, labels = sampler._speakers(owner, [*crowd, owner])
    lanes, silent, hidden = sampler._lane_samples(pool, owner, speakers, labels, time.monotonic())
    assert len(lanes) == 10 and sum("name" in lane for lane in lanes) == 2
    assert (silent, hidden) == (0, 4)
    assert [lane.get("name") for lane in lanes[:2]] == ["Ana Ferreira", "Participant"]


class Mic:
    """A transcriber whose gate the test drives: speak() moves its chunk counter."""

    def __init__(self):
        self.gate = SimpleNamespace(active=False, chunks_emitted=0)
        self.lost_chunks, self.worker, self.paused_until = 0, None, None
        self.pending_since = None
        self.queue = SimpleNamespace(qsize=lambda: 0)

    def speak(self):
        self.gate.chunks_emitted += 1


def crowd_room(sampler, n):
    mics = [Mic() for _ in range(n)]
    owner = SimpleNamespace(
        transcribers={f"s{i}": mic for i, mic in enumerate(mics)},
        speaker_name=lambda sid: f"Person {sid[1:]}",
    )
    pool = SimpleNamespace(workers=set(), waiting=0)

    def sample(now):
        speakers, labels = sampler._speakers(owner, [owner])
        lanes, silent, hidden = sampler._lane_samples(pool, owner, speakers, labels, now)
        return [lane["name"] for lane in lanes], silent, hidden

    return mics, sample


def test_lanes_show_the_most_recent_speakers_in_a_stable_order():
    sampler = DiagnosticSampler(SimpleNamespace())
    mics, sample = crowd_room(sampler, 25)
    # A first sighting is only a baseline: nobody has a lane yet.
    assert sample(1000.0) == ([], 25, 0)
    for i in range(12):  # twelve people speak in turn, one per second
        mics[i].speak()
        sample(1001.0 + i)
    names, silent, hidden = sample(1013.0)
    # Ten lanes for the ten most recent speakers, in the order they first spoke.
    assert names == [f"Person {i}" for i in range(2, 12)]
    assert (silent, hidden) == (13, 2)
    # Someone already shown speaking again keeps their row.
    mics[7].gate.active = True
    assert sample(1014.0) == (names, 13, 2)
    mics[7].gate.active = False
    # A silent microphone that starts talking appears last and the oldest speaker leaves.
    mics[20].speak()
    names, silent, hidden = sample(1015.0)
    assert names == [f"Person {i}" for i in range(3, 12)] + ["Person 20"]
    assert (silent, hidden) == (12, 3)


def test_a_speaker_silent_for_two_minutes_loses_the_lane():
    sampler = DiagnosticSampler(SimpleNamespace())
    mics, sample = crowd_room(sampler, 3)
    sample(0.0)
    mics[0].speak()
    mics[1].speak()
    assert sample(1.0) == (["Person 0", "Person 1"], 1, 0)
    mics[1].speak()
    assert sample(60.0) == (["Person 0", "Person 1"], 1, 0)
    assert sample(121.0) == (["Person 0", "Person 1"], 1, 0)  # exactly two minutes
    assert sample(121.5) == (["Person 1"], 2, 0)
    assert sample(180.5) == ([], 3, 0)
    # Back after the window: a new appearance, after anyone already shown.
    mics[2].speak()
    sample(181.0)
    mics[0].gate.active = True
    assert sample(182.0) == (["Person 2", "Person 0"], 1, 0)


def test_engaged_speakers_always_have_a_lane():
    sampler = DiagnosticSampler(SimpleNamespace())
    mics, sample = crowd_room(sampler, 14)
    sample(0.0)
    for mic in mics[:12]:
        mic.speak()
    sample(1.0)
    # Silent at the gate, but holding a slot or paused: kept over more recent speakers.
    mics[12].worker, mics[13].paused_until = object(), 1e9
    for mic in mics[:12]:
        mic.speak()
    names, silent, hidden = sample(2.0)
    assert "Person 12" in names and "Person 13" in names
    assert (len(names), silent, hidden) == (10, 0, 4)


def test_decision_line_names_only_the_owning_room():
    from cassini_live.load import THRESHOLDS

    sampler = DiagnosticSampler(SimpleNamespace())

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
    decision = sampler._decision(governor, None, labels)
    assert decision["verb"] == "hold" and decision["who"] == "someone in another call"
    assert "lag 1.3 s ≥ admit 1.0" in decision["text"]
    governor.last_decision = {**governor.last_decision, "reason": None, "at": 11.0}
    governor.last_decision_ref = weakref.ref(mine)
    decision = sampler._decision(governor, None, labels)
    assert decision["verb"] == "admit" and decision["who"] == "Ana Ferreira"
    assert decision["text"].startswith("seat 2 of 4 · lag 1.3 s < 1.0 · stall 5%")
    governor.last_shed, governor.last_shed_ref = {"at": 12.0, "lag": 2.3}, weakref.ref(mine)
    decision = sampler._decision(governor, None, labels)
    assert decision["verb"] == "pause" and "shed 2.0" in decision["text"]


def test_names_are_cut_by_the_bytes_they_cost_encoded():
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


# Every field a sample carries for the diagnostics page to render.
SAMPLE_KEYS = {
    "engine",
    "state",
    "rooms",
    "tracks",
    "maxTracks",
    "busy",
    "capacity",
    "spare",
    "vadActive",
    "vadPeak",
    "vadChunks",
    "queuedChunks",
    "sttChunks",
    "sttAudioSec",
    "leasedSec",
    "yields",
    "endSilence",
    "endDuration",
    "endSched",
    "endFlush",
    "targets",
    "previewing",
    "finals",
    "partials",
    "hostCpu",
    "hostAvailableGiB",
    "hostGiB",
    "captionCpu",
    "quota",
    "captionMiB",
    "limitMiB",
    "drops",
    "waiting",
    "lanes",
    "silentMics",
    "hiddenSpeakers",
    "admission",
    "decision",
    "p95",
    "threads",
    "segmentSeconds",
    "cpuWeight",
    "oomKills",
    "engineRestarts",
    "samplerMs",
}
SNAPSHOT = {
    "memoryFraction": 0.25,
    "memoryLimitBytes": 4 * 1024**3,
    "hostAvailableBytes": 8 * 1024**3,
    "hostMemoryBytes": 16 * 1024**3,
    "cpuQuotaCpus": 4.0,
}


def sampling_service():
    """A ready service with two rooms: the owner's, with one speaker holding a slot, and
    another with an open microphone."""
    from cassini_live.load import LoadGovernor

    worker, speaking = object(), Mic()
    speaking.worker = worker
    owner = SimpleNamespace(transcribers={"s1": speaking}, speaker_name={"s1": "Ana"}.get)
    other = SimpleNamespace(transcribers={"s2": Mic()})
    pool = SimpleNamespace(busy=1, admission_limit=2, waiting=0, workers={worker}, dropped_chunks=0)
    admission = SimpleNamespace(
        state="ready", pool=pool, governor=LoadGovernor(ceiling=2), tracks=[None] * 2, max_tracks=8
    )
    return SimpleNamespace(admission=admission, clients={"a": owner, "b": other}), owner


def test_a_sample_carries_every_field_the_page_renders(monkeypatch):
    service, owner = sampling_service()
    sampler = DiagnosticSampler(service)
    monkeypatch.setattr(sampler, "_host_cpu", lambda: (100, 90))
    monkeypatch.setattr(sampler, "_caption_cpu", lambda: 0)
    monkeypatch.setattr(sampler, "_cpu_weight", lambda: 100)
    sampler.prime(10.0)
    sample = sampler.sample(owner, SNAPSHOT, 11.0)
    assert set(sample) == SAMPLE_KEYS
    assert [lane.get("name") for lane in sample["lanes"]] == ["Ana"]
    assert (sample["rooms"], sample["silentMics"], sample["hiddenSpeakers"]) == (2, 1, 0)


def test_cpu_figures_are_deltas_against_the_samplers_previous_sample(monkeypatch):
    service, owner = sampling_service()
    sampler = DiagnosticSampler(service)
    # /proc/stat (total, idle) jiffies and cgroup CPU seconds at prime() and two samples.
    hosts = iter([(1000, 900), (1100, 950), (1300, 1000)])
    captions = iter([10.0, 10.5, 12.0])
    weights = iter([100, 50])
    monkeypatch.setattr(sampler, "_host_cpu", lambda: next(hosts))
    monkeypatch.setattr(sampler, "_caption_cpu", lambda: next(captions))
    monkeypatch.setattr(sampler, "_cpu_weight", lambda: next(weights))
    sampler.prime(100.0)
    first = sampler.sample(owner, SNAPSHOT, 101.0)
    second = sampler.sample(owner, SNAPSHOT, 103.0)
    # Since prime(): 50 of 100 jiffies idle, 0.5 CPU seconds in 1 s.
    assert (first["hostCpu"], first["captionCpu"]) == (50.0, 50.0)
    # Since the first sample, not prime(): 50 of 200 idle, 1.5 CPU seconds in 2 s.
    assert (second["hostCpu"], second["captionCpu"]) == (75.0, 75.0)
    # The cgroup's CPU weight is read once per run of samples.
    assert first["cpuWeight"] == second["cpuWeight"] == 100
