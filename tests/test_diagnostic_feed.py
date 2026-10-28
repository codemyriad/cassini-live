"""The diagnostics page's feed: presence-driven sampling, history, owner room and names."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from cassini_live import diagnostic_feed
from cassini_live.diagnostic_feed import DiagnosticFeed
from cassini_live.diagnostic_history import COLUMNS
from cassini_live.diagnostics import DiagnosticVideo, NameHolds
from cassini_live.load import LoadGovernor
from cassini_live.pool import WorkerPool
from tests.test_diagnostics import SAMPLE_KEYS, SNAPSHOT, Mic


class Room:
    """A caption room with one microphone per speaker. Like the Talk client, it knows its
    participants' names only while they are shown."""

    def __init__(self, *names, sessions="session"):
        self.transcribers = {f"{sessions}-{i}": Mic() for i in range(len(names))}
        self.names = dict(zip(self.transcribers, names, strict=True))
        self.defunct = asyncio.Event()
        self.backend_url = "https://cloud.example"
        self.shown = []

    def show_names(self, shown):
        self.shown.append(shown)

    def speaker_name(self, session_id):
        return self.names[session_id] if self.shown and self.shown[-1] else None

    def speak(self):
        for mic in self.transcribers.values():
            mic.speak()

    async def close(self):
        self.defunct.set()


def service_with(**clients):
    pool = SimpleNamespace(
        busy=0, admission_limit=2, waiting=0, workers=set(), dropped_chunks=0, finals_emitted=0
    )
    admission = SimpleNamespace(
        state="ready", pool=pool, governor=LoadGovernor(ceiling=2), tracks=set(), max_tracks=8
    )
    video = SimpleNamespace(task=None, owner=None)
    return SimpleNamespace(clients=dict(clients), admission=admission, diagnostics=video)


def quiet(monkeypatch, sampler, data=SNAPSHOT):
    """Fixed resource readings: the sampler's CPU counters and the cgroup snapshot."""
    monkeypatch.setattr(sampler, "_host_cpu", lambda: (100, 90))
    monkeypatch.setattr(sampler, "_caption_cpu", lambda: 0)
    monkeypatch.setattr(sampler, "_cpu_weight", lambda: 100)
    monkeypatch.setattr(diagnostic_feed, "snapshot", lambda: dict(data))


def make_feed(monkeypatch, service, names=None, data=SNAPSHOT):
    feed = DiagnosticFeed(service, names or NameHolds())
    quiet(monkeypatch, feed.sampler, data)
    feed.sampler.prime(0.0)
    return feed


def without_ticker(feed):
    """For tests that step the feed at their own times: polls record presence but start no
    ticker, whose real clock would find them long gone and idle the feed."""
    feed.task = asyncio.get_running_loop().create_future()
    return feed


async def wait_for(condition, keep_watching=None):
    async with asyncio.timeout(5):
        while not condition():
            if keep_watching:
                keep_watching()
            await asyncio.sleep(0.005)


def test_first_read_backfills_and_later_reads_return_only_new_columns(monkeypatch):
    room = Room("Ana")
    feed = make_feed(monkeypatch, service_with(**{"room-a": room}))
    for now in (1.0, 2.0, 3.0, 4.0):
        room.speak()
        feed._step(now)
    body = feed.read(0)
    assert [column["t"] for column in body["columns"]] == [2.0, 3.0, 4.0]
    assert body["head"] == 4.0 and body["sample"] is feed.latest and body["error"] is None
    # A first sighting is only a baseline: the lane starts with the second sample.
    assert [sorted(set(c["lanes"].values())) for c in body["columns"]] == [
        [],
        ["speech"],
        ["speech"],
    ]
    assert set(body["sample"]) == SAMPLE_KEYS
    assert [column["t"] for column in feed.read(3.0)["columns"]] == [4.0]
    assert feed.read(4.0)["columns"] == []
    # Two minutes at most.
    for i in range(COLUMNS + 30):
        feed._step(5.0 + i)
    columns = feed.read(0)["columns"]
    assert len(columns) == COLUMNS and columns[-1]["t"] == 5.0 + COLUMNS + 29


def test_times_are_rounded_as_the_page_sends_them_back(monkeypatch):
    feed = make_feed(monkeypatch, service_with())
    for now in (5000.1234567, 5001.1236, 5002.9876):
        feed._step(now)
    body = json.loads(json.dumps(feed.read(0)))
    assert [column["t"] for column in body["columns"]] == [5001.124, 5002.988]
    assert body["head"] == 5002.988 and body["now"] == round(body["now"], 3)
    # The head as the page parses it and sends it back excludes exactly what it has seen.
    assert feed.read(float(str(body["head"])))["columns"] == []
    assert [c["t"] for c in feed.read(float(str(body["columns"][0]["t"])))["columns"]] == [
        5002.988
    ]


@pytest.mark.parametrize(
    "admission",
    [
        None,  # before the app wires the supervisor in
        SimpleNamespace(state="disabled", pool=None, governor=None, tracks=(), max_tracks=0),
        SimpleNamespace(state="blocked", pool=WorkerPool(2), tracks=set(), max_tracks=24),
    ],
)
def test_samples_without_rooms_engine_or_memory_limit(monkeypatch, admission):
    service = service_with()
    service.admission = admission
    unlimited = {**SNAPSHOT, "memoryLimitBytes": None, "hostAvailableBytes": None}
    feed = make_feed(monkeypatch, service, data=unlimited)
    feed._step(1.0)
    feed._step(2.0)
    body = feed.read(0)
    sample = body["sample"]
    assert set(sample) == SAMPLE_KEYS and len(body["columns"]) == 1
    assert sample["state"] == getattr(admission, "state", "blocked")
    assert (sample["rooms"], sample["lanes"], sample["decision"]) == (0, [], None)
    assert (sample["limitMiB"], sample["captionMiB"], sample["hostAvailableGiB"]) == (None,) * 3
    assert sample["hostGiB"] == 16.0 and sample["quota"] == 4.0
    assert feed.names_token() is None and feed.owner_token() is None


def test_owner_is_the_video_room_else_the_oldest_live_room_else_none(monkeypatch):
    first, second, third = Room("Ana"), Room("Bo"), Room("Cy")
    service = service_with(**{"room-1": first, "room-2": second, "room-3": third})
    feed = make_feed(monkeypatch, service)
    assert feed.owner_token() == "room-1"
    first.defunct.set()
    assert feed.owner_token() == "room-2"
    service.diagnostics.task, service.diagnostics.owner = object(), "room-3"
    assert feed.owner_token() == "room-3"
    # A video still stopping for a room that is gone, or no video at all.
    service.diagnostics.owner = "room-gone"
    assert feed.owner_token() == "room-2"
    service.diagnostics.task, service.diagnostics.owner = None, "room-3"
    assert feed.owner_token() == "room-2"
    second.defunct.set()
    assert feed.owner_token() == "room-3"
    third.defunct.set()
    assert feed.owner_token() is None
    # The room sampled last is the one whose names a sample may carry.
    third.defunct.clear()
    feed._step(1.0)
    assert feed.names_token() == "room-3"
    service.clients.clear()
    feed._step(2.0)
    assert feed.names_token() is None and feed.read(0)["sample"]["lanes"] == []


async def test_names_are_read_only_while_a_member_watches(monkeypatch):
    room, other = Room("Ana Ferreira"), Room("Bob Secret")
    feed = without_ticker(make_feed(monkeypatch, service_with(**{"room-a": room, "room-b": other})))
    feed.touch(None, now=10.0)
    feed._step(10.5)
    room.speak()
    other.speak()
    feed._step(11.5)
    # A poll by an administrator outside the call never asks Talk for names.
    assert room.shown == [] and other.shown == []
    lanes = feed.read(0)["sample"]["lanes"]
    assert [lane.get("name") for lane in lanes] == ["Participant", None]
    assert lanes[1]["other"] is True
    feed.touch("room-a", now=12.0)
    feed._step(12.5)
    feed._step(13.5)
    assert room.shown == [True] and other.shown == []
    assert [lane.get("name") for lane in feed.read(0)["sample"]["lanes"]] == ["Ana Ferreira", None]
    # Non-member polls do not extend the member's presence.
    feed.touch(None, now=40.0)
    feed._step(12.0 + diagnostic_feed.PRESENCE_S + 0.5)
    assert room.shown == [True, False]
    await feed.close()
    assert room.shown == [True, False] and other.shown == []


def test_name_holds_count_every_holder():
    holds = NameHolds()
    shown = []
    client = SimpleNamespace(show_names=shown.append)  # unhashable, like the tests' rooms
    video, page = object(), object()
    holds.hold(client, video)
    holds.hold(client, page)
    holds.hold(client, video)
    assert shown == [True]
    holds.release(client, video)
    holds.release(client, video)
    assert shown == [True]
    holds.release(client, page)
    assert shown == [True, False]
    holds.release(client, page)
    holds.hold(client, page)
    assert shown == [True, False, True]
    # A client that cannot read names is left alone.
    holds.hold(SimpleNamespace(), video)
    holds.release(SimpleNamespace(), video)


async def test_the_video_ending_keeps_names_the_page_still_holds(monkeypatch):
    import cassini_live.diagnostics as module

    monkeypatch.setenv("CASSINI_LIVE_DEBUG_VIDEO", "1")
    room = Room("Ana Ferreira")
    service = service_with(room=room)
    service.settings = SimpleNamespace(
        server="wss://example.invalid", model_dump=lambda: {"stunservers": [], "turnservers": []}
    )
    holds = NameHolds()
    feed = without_ticker(make_feed(monkeypatch, service, holds))
    video = service.diagnostics = DiagnosticVideo(service, names=holds)

    class Pipe:
        def write(self, data):
            pass

        async def drain(self):
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

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(module, "process_usage", lambda pid: (0, 1))
    monkeypatch.setattr(module, "snapshot", lambda: dict(SNAPSHOT))
    quiet(monkeypatch, video.sampler)
    feed.touch("room", now=1.0)
    feed._step(1.5)
    assert room.shown == [True]
    await video._run("room")
    assert video.state == "failed"
    # The video held and released the room's names; the page still holds them.
    assert room.shown == [True]
    room.speak()
    feed._step(2.5)
    assert feed.read(0)["sample"]["lanes"][0]["name"] == "Ana Ferreira"
    await feed.close()
    assert room.shown == [True, False]
    # Alone, the video turns names on and off as before.
    await video._run("room")
    assert room.shown == [True, False, True, False]


async def test_an_owner_change_releases_the_old_room_and_forgets_the_member(monkeypatch):
    first, second = Room("Ana"), Room("Bo")
    service = service_with(**{"room-a": first, "room-b": second})
    feed = without_ticker(make_feed(monkeypatch, service))
    feed.touch("room-a", now=1.0)
    feed._step(1.5)
    assert first.shown == [True] and feed.names_token() == "room-a"
    del service.clients["room-a"]
    feed._step(2.5)
    # Membership was checked against the room that closed.
    assert first.shown == [True, False] and second.shown == []
    assert feed.names_token() == "room-b"
    feed.touch("room-b", now=3.0)
    feed._step(3.5)
    assert second.shown == [True]
    # The same room's new client (captions turned off and on again) keeps the member.
    service.clients["room-b"] = again = Room("Bo")
    feed._step(4.5)
    assert second.shown == [True, False] and again.shown == [True]
    await feed.close()
    assert again.shown == [True, False]
    body = feed.read(0)
    assert (body["head"], body["sample"], body["columns"]) == (0, None, [])


async def test_a_member_poll_never_holds_another_rooms_names(monkeypatch):
    first, second = Room("Ana"), Room("Bob Secret")
    service = service_with(**{"room-a": first, "room-b": second})
    feed = without_ticker(make_feed(monkeypatch, service))
    # The poll that wakes the feed is checked against room-a, which closes before the
    # first step.
    feed.touch(feed.owner_token(), now=1.0)
    del service.clients["room-a"]
    feed._step(2.0)
    assert first.shown == [] and second.shown == [] and feed.names_token() == "room-b"
    await feed.close()


async def test_a_step_failing_as_the_owner_changes_holds_no_new_names(monkeypatch):
    first, second = Room("Ana"), Room("Bob Secret")
    service = service_with(**{"room-a": first, "room-b": second})
    feed = without_ticker(make_feed(monkeypatch, service))
    feed.touch("room-a", now=1.0)
    feed._step(1.5)
    assert first.shown == [True]
    del service.clients["room-a"]

    def snapshot():
        raise PermissionError("cgroup")

    monkeypatch.setattr(diagnostic_feed, "snapshot", snapshot)
    with pytest.raises(PermissionError):
        feed._step(2.5)
    # The last good sample is still room-a's: polls are checked against room-a.
    assert first.shown == [True, False] and feed.names_token() == "room-a"
    feed.touch(feed.names_token(), now=3.0)
    monkeypatch.setattr(diagnostic_feed, "snapshot", lambda: dict(SNAPSHOT))
    feed._step(3.5)
    assert second.shown == [] and feed.names_token() == "room-b"
    await feed.close()


async def test_speech_while_nobody_watched_is_not_shown_as_current(monkeypatch):
    room = Room("Ana")
    feed = make_feed(monkeypatch, service_with(room=room))
    feed._step(1.0)
    room.speak()
    feed._step(2.0)
    assert [lane["id"] for lane in feed.latest["lanes"]] == [1]
    await feed.close()
    # Ana speaks once while the page is closed; it opens again ten minutes later.
    room.speak()
    feed.sampler.prime(600.0)
    feed._step(601.0)
    feed._step(602.0)
    assert feed.latest["lanes"] == [] and feed.latest["silentMics"] == 1
    # Her lane keeps its number.
    room.speak()
    feed._step(603.0)
    assert [lane["id"] for lane in feed.latest["lanes"]] == [1]


async def test_ticker_samples_while_polled_then_stops_and_drops_everything(monkeypatch):
    monkeypatch.setattr(diagnostic_feed, "SAMPLE_S", 0.01)
    monkeypatch.setattr(diagnostic_feed, "PRESENCE_S", 0.3)
    room = Room("Ana")
    feed = make_feed(monkeypatch, service_with(room=room))
    primes = []
    prime = feed.sampler.prime
    monkeypatch.setattr(feed.sampler, "prime", lambda now=None: primes.append(now) or prime(now))
    feed.touch("room")
    task = feed.task
    await wait_for(lambda: len(feed.columns) >= 3, lambda: feed.touch("room"))
    assert feed.task is task and room.shown == [True] and len(primes) == 1
    # Nobody polls any more: the ticker stops on its own and forgets what it held.
    async with asyncio.timeout(5):
        await task
    assert room.shown == [True, False]
    assert (feed.latest, list(feed.columns), feed.names_token()) == (None, [], None)
    body = feed.read(0)
    assert (body["head"], body["sample"], body["columns"], body["error"]) == (0, None, [], None)
    # A later poll starts it again, primed afresh.
    feed.touch(None)
    assert feed.task is not task
    await wait_for(lambda: feed.latest is not None, lambda: feed.touch(None))
    assert len(primes) == 2 and room.shown == [True, False]
    await feed.close()
    assert feed.task is None and feed.latest is None


async def test_the_first_tick_only_primes(monkeypatch):
    monkeypatch.setattr(diagnostic_feed, "SAMPLE_S", 0.05)
    feed = make_feed(monkeypatch, service_with(room=Room("Ana")))
    calls = []
    prime, step = feed.sampler.prime, feed._step
    monkeypatch.setattr(
        feed.sampler, "prime", lambda now=None: calls.append(("prime", now)) or prime(now)
    )
    monkeypatch.setattr(feed, "_step", lambda now: calls.append(("step", now)) or step(now))
    feed.touch(None)
    await wait_for(lambda: len(calls) >= 2, lambda: feed.touch(None))
    await feed.close()
    (first, primed), (second, sampled) = calls[:2]
    # CPU figures are deltas: a sample in the same tick as prime() reads 100 % host CPU.
    assert (first, second) == ("prime", "step")
    assert sampled - primed >= diagnostic_feed.SAMPLE_S / 2


async def test_a_failing_tick_keeps_the_last_good_data_and_names_only_the_error(monkeypatch):
    monkeypatch.setattr(diagnostic_feed, "SAMPLE_S", 0.01)
    room = Room("Ana")
    feed = make_feed(monkeypatch, service_with(room=room))
    failing = {"on": False}

    def snapshot():
        if failing["on"]:
            raise PermissionError("/secret/cgroup/path for Ana")
        return dict(SNAPSHOT)

    monkeypatch.setattr(diagnostic_feed, "snapshot", snapshot)
    feed.touch(None)
    await wait_for(lambda: len(feed.columns) >= 2, lambda: feed.touch(None))
    failing["on"] = True
    await wait_for(lambda: feed.error is not None, lambda: feed.touch(None))
    latest, columns = feed.latest, list(feed.columns)
    await asyncio.sleep(0.05)
    feed.touch(None)
    body = feed.read(0)
    assert body["error"] == "PermissionError" and not feed.task.done()
    assert body["sample"] is latest and body["columns"] == columns
    assert "secret" not in json.dumps(body)
    failing["on"] = False
    await wait_for(lambda: feed.error is None, lambda: feed.touch(None))
    await wait_for(lambda: len(feed.columns) > len(columns), lambda: feed.touch(None))
    assert feed.latest is not latest
    await feed.close()


async def test_a_failing_prime_is_retried(monkeypatch):
    monkeypatch.setattr(diagnostic_feed, "SAMPLE_S", 0.01)
    feed = DiagnosticFeed(service_with(), NameHolds())
    quiet(monkeypatch, feed.sampler)
    readings = iter([OSError("no cpu.stat"), (100, 90)])

    def host_cpu():
        reading = next(readings, (100, 90))
        if isinstance(reading, Exception):
            raise reading
        return reading

    monkeypatch.setattr(feed.sampler, "_host_cpu", host_cpu)
    feed.touch(None)
    await wait_for(lambda: feed.latest is not None, lambda: feed.touch(None))
    assert feed.error is None
    await feed.close()


async def test_closing_the_service_stops_the_feed(monkeypatch):
    from cassini_live.config import Config
    from cassini_live.service import CaptionService

    service = CaptionService(Config(None, 2, "secret", ""))
    assert service.diagnostics.names is service.diagnostic_feed.names is service.name_holds
    room = Room("Ana")
    service.clients["room"] = room
    feed = without_ticker(service.diagnostic_feed)
    quiet(monkeypatch, feed.sampler)
    feed.sampler.prime(0.0)
    feed.touch("room", now=1.0)
    feed._step(1.0)
    feed._step(2.0)
    assert room.shown == [True] and feed.columns
    await service.close()
    assert feed.task is None and feed.latest is None and not feed.columns
    assert room.shown == [True, False]

