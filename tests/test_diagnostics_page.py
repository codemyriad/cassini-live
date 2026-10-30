"""The diagnostics page and its feed: administrators only, names for room members only."""

import base64
import copy
import weakref
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from cassini_live import __version__, diagnostics_page
from cassini_live.diagnostic_history import COLUMNS
from cassini_live.load import LoadGovernor
from tests.test_diagnostic_feed import Room, quiet
from tests.test_diagnostics import widest
from tests.test_settings_page import CACHED, Hygiene, assert_secure, error

FEED = "/diagnostics/api/feed"
PAGE = "/diagnostics/"
# Distinctive identifiers that must never reach the page.
TOKEN, OTHER_TOKEN = "ROOMTOKEN-q9x7", "ROOMTOKEN-other-m4v2"
SESSION, ATTENDEE = "SESSIONID-k3z5", "ATTENDEE-p2w8"
KEYS = {
    "version",
    "window",
    "presence",
    "names",
    "instance",
    "collecting",
    "sampledAt",
    "head",
    "now",
    "sample",
    "columns",
    "error",
}


class WatchedRoom(Room):
    """A room whose names the diagnostic video is reading, so samples carry them whether or
    not the page holds them."""

    def __init__(self, *names, sessions=SESSION):
        super().__init__(*names, sessions=sessions)
        self.nc_sid_map = {f"{ATTENDEE}-{i}": sid for i, sid in enumerate(self.transcribers)}

    def speaker_name(self, session_id):
        return self.names[session_id]

    def history_snapshot(self, since=0):
        return []

    def history_head(self):
        return 0


def participants(token):
    return f"/ocs/v2.php/apps/spreed/api/v4/room/{token}/participants"


def build_app(monkeypatch, *, member, rooms, on_ask=None):
    """`member`: whether the administrator is in every room, or the tokens of those they are in."""
    from cassini_live.app import create_app
    from cassini_live.config import Config
    from cassini_live.pool import WorkerPool
    from cassini_live.service import CaptionService

    asked = []

    class Nextcloud:
        async def set_user(self, user):
            self.user = user

        async def ocs(self, method, path):
            asked.append((self.user, method, path))
            if on_ask:
                on_ask()
            if member is not True and path not in {participants(t) for t in member or ()}:
                raise RuntimeError("403 not a participant")
            return []

    config = Config(None, 2, "secret", "")
    service = CaptionService(config)
    service.enabled = True
    service.settings = object()
    app = create_app(config, WorkerPool(2), service, nc_factory=Nextcloud)
    app.state.safety.state = "ready"
    service.clients.update(rooms)
    feed = service.diagnostic_feed
    quiet(monkeypatch, feed.sampler)
    feed.sampler.prime(0.0)
    return app, service, asked


def sample(service, *rooms, times=(1.0, 2.0)):
    """Samples at `times`, every room speaking between them."""
    for i, now in enumerate(times):
        if i:
            for room in rooms:
                room.speak()
        service.diagnostic_feed._step(now)


def decide(service, subject):
    """The governor's last decision held back `subject`'s voice."""
    governor = service.admission.governor
    governor.last_decision = {
        "at": 1.0,
        "reason": "lag",
        "seats": 1,
        "ceiling": 2,
        "lag": 1.3,
        "cpuStall": None,
        "headroomMiB": None,
    }
    governor.last_decision_ref = weakref.ref(subject)


def spy_touch(monkeypatch, feed):
    touched = []
    touch = feed.touch
    monkeypatch.setattr(
        feed, "touch", lambda member_of: touched.append(member_of) or touch(member_of)
    )
    return touched


def first_mic(room):
    return next(iter(room.transcribers.values()))


def test_a_member_reads_the_owner_rooms_names(monkeypatch, headers):
    room, other = WatchedRoom("Ana Ferreira"), WatchedRoom("Bob Secret", sessions="other")
    app, service, asked = build_app(
        monkeypatch, member=True, rooms={TOKEN: room, OTHER_TOKEN: other}
    )
    decide(service, first_mic(room))
    sample(service, room, other)
    touched = spy_touch(monkeypatch, service.diagnostic_feed)
    response = TestClient(app).get(FEED, headers=headers)
    assert response.status_code == 200
    assert_secure(response)
    body = response.json()
    assert set(body) == KEYS | {"video"}
    assert (body["version"], body["window"], body["presence"]) == (__version__, COLUMNS, 30.0)
    assert body["names"] is True and touched == [TOKEN]
    assert [lane.get("name") for lane in body["sample"]["lanes"]] == ["Ana Ferreira", None]
    assert body["sample"]["decision"]["who"] == "Ana Ferreira"
    assert (body["head"], [c["t"] for c in body["columns"]], body["error"]) == (2.0, [2.0], None)
    assert body["video"] == service.diagnostics.status()
    # Asked of Talk as the requesting administrator, about the room the sample shows.
    assert asked == [("alice", "GET", participants(TOKEN))]


class Gone:
    pass


@pytest.mark.parametrize(
    "subject, who",
    [("owner", "a speaker"), ("other", "someone in another call"), ("gone", "a speaker")],
)
def test_others_read_generic_labels(monkeypatch, headers, subject, who):
    room, other = WatchedRoom("Ana Ferreira"), WatchedRoom("Bob Secret", sessions="other")
    app, service, asked = build_app(
        monkeypatch, member=False, rooms={TOKEN: room, OTHER_TOKEN: other}
    )
    gone = Gone()
    decide(service, {"owner": first_mic(room), "other": first_mic(other), "gone": gone}[subject])
    del gone
    sample(service, room, other)
    touched = spy_touch(monkeypatch, service.diagnostic_feed)
    response = TestClient(app).get(FEED, headers=headers)
    assert response.status_code == 200
    assert_secure(response)
    body = response.json()
    assert body["names"] is False and touched == [None] and len(asked) == 1
    assert [lane.get("name") for lane in body["sample"]["lanes"]] == ["Participant", None]
    assert body["sample"]["decision"]["who"] == who
    assert "Ana Ferreira" not in response.text and "Bob Secret" not in response.text
    # Nobody in the call watches: the page never asked the room for its names.
    assert room.shown == [] and other.shown == []


def test_a_member_of_a_room_that_just_closed_reads_no_other_rooms_names(monkeypatch, headers):
    room, after = WatchedRoom("Ana Ferreira"), WatchedRoom("Cy Next", sessions="after")
    rooms = {TOKEN: room, OTHER_TOKEN: after}

    def room_closes():
        # While Talk answers, the owner room closes and the feed samples the next one.
        del service.clients[TOKEN]
        service.diagnostic_feed._step(3.0)

    app, service, asked = build_app(monkeypatch, member=True, rooms=rooms, on_ask=room_closes)
    sample(service, room, after)
    touched = spy_touch(monkeypatch, service.diagnostic_feed)
    body = TestClient(app).get(FEED, headers=headers).json()
    assert asked[0][2] == participants(TOKEN)
    assert body["names"] is False and touched == [None]
    assert [lane.get("name") for lane in body["sample"]["lanes"]] == ["Participant"]


def test_names_from_a_closed_room_need_membership_of_that_room(monkeypatch, headers):
    room, other = WatchedRoom("Ana Ferreira"), WatchedRoom("Bob Secret", sessions="other")
    app, service, asked = build_app(
        monkeypatch, member={OTHER_TOKEN}, rooms={TOKEN: room, OTHER_TOKEN: other}
    )
    sample(service, room, other)
    # The sampled room closes before the next step: the latest sample still names its
    # speakers, though the next owner is a room this administrator is in.
    del service.clients[TOKEN]
    assert service.diagnostic_feed.owner_token() == OTHER_TOKEN
    touched = spy_touch(monkeypatch, service.diagnostic_feed)
    response = TestClient(app).get(FEED, headers=headers)
    body = response.json()
    assert asked == [("alice", "GET", participants(TOKEN))]
    assert body["names"] is False and touched == [None]
    assert [lane.get("name") for lane in body["sample"]["lanes"]] == ["Participant", None]
    assert "Ana Ferreira" not in response.text


def test_a_non_member_costs_one_talk_check_per_window(monkeypatch, headers):
    room = WatchedRoom("Ana Ferreira")
    app, service, asked = build_app(monkeypatch, member=False, rooms={TOKEN: room})
    sample(service, room)
    client = TestClient(app)
    for _ in range(5):
        assert client.get(FEED, headers=headers).json()["names"] is False
    assert asked == [("alice", "GET", participants(TOKEN))]
    # Another room is asked about afresh.
    service.clients[OTHER_TOKEN] = service.clients.pop(TOKEN)
    service.diagnostic_feed._step(3.0)
    assert client.get(FEED, headers=headers).json()["names"] is False
    assert asked[1:] == [("alice", "GET", participants(OTHER_TOKEN))]


def test_the_instance_is_the_same_on_every_poll_and_new_in_each_app(monkeypatch, headers):
    room = WatchedRoom("Ana")
    app, service, _ = build_app(monkeypatch, member=False, rooms={TOKEN: room})
    sample(service, room)
    client = TestClient(app)
    polls = [client.get(FEED + query, headers=headers) for query in ("", "?since=2.0", "")]
    seen = {poll.json()["instance"] for poll in polls}
    assert seen == {service.diagnostic_feed.instance}
    # A restarted container is a new app: its lane numbers start over on the same clock.
    again, _, _ = build_app(monkeypatch, member=False, rooms={TOKEN: room})
    assert TestClient(again).get(FEED, headers=headers).json()["instance"] not in seen


def test_redaction_copies_the_sample():
    lanes = [{"id": 1, "name": "Ana"}, {"id": 2, "other": True}]
    body = {
        "sample": {"lanes": lanes, "decision": {"who": "Ana", "verb": "hold"}},
        "columns": [{"t": 1.0, "lanes": {1: "speech"}}],
        "head": 1.0,
    }
    before = copy.deepcopy(body)
    redacted = diagnostics_page.redact(body)
    assert body == before
    assert redacted["sample"] == {
        "lanes": [{"id": 1, "name": "Participant"}, {"id": 2, "other": True}],
        "decision": {"who": "a speaker", "verb": "hold"},
    }
    assert redacted["columns"] == body["columns"] and redacted["head"] == 1.0
    assert diagnostics_page.redact({**body, "sample": None})["sample"] is None
    quiet_sample = {"lanes": [], "decision": None}
    assert diagnostics_page.redact({"sample": quiet_sample})["sample"] == quiet_sample


@pytest.mark.parametrize(
    "query, times",
    [
        ("", [2.0, 3.0, 4.0]),
        ("?since=0", [2.0, 3.0, 4.0]),
        ("?since=2.0", [3.0, 4.0]),
        ("?since=3.5", [4.0]),
        ("?since=4", []),
    ],
)
def test_the_first_poll_backfills_and_later_polls_get_new_columns(
    monkeypatch, headers, query, times
):
    room = WatchedRoom("Ana")
    app, service, _ = build_app(monkeypatch, member=True, rooms={TOKEN: room})
    sample(service, room, times=(1.0, 2.0, 3.0, 4.0))
    body = TestClient(app).get(FEED + query, headers=headers).json()
    assert [column["t"] for column in body["columns"]] == times
    assert body["head"] == 4.0 and body["sample"]["lanes"]


@pytest.mark.parametrize("since", ["abc", "", "nan", "NaN", "inf", "-inf", "-1", "1e999"])
def test_a_bad_since_is_refused(monkeypatch, headers, since):
    app, service, asked = build_app(monkeypatch, member=True, rooms={})
    error(TestClient(app).get(f"{FEED}?since={since}", headers=headers), 400, "bad_request")
    assert service.diagnostic_feed.task is None and asked == []


def test_an_idle_feed_answers_with_nothing_yet(monkeypatch, headers):
    app, service, asked = build_app(monkeypatch, member=True, rooms={})
    body = TestClient(app).get(FEED, headers=headers).json()
    assert (body["names"], body["sample"], body["columns"], body["head"]) == (False, None, [], 0)
    # No room to be a member of: Talk is not asked.
    assert asked == []


def test_the_feed_needs_the_appapi_gateway_and_a_user(monkeypatch, headers):
    app, _, _ = build_app(monkeypatch, member=True, rooms={})
    client = TestClient(app)
    assert client.get(FEED).status_code == 401
    headers["AUTHORIZATION-APP-API"] = base64.b64encode(b":test-exapp-secret").decode()
    error(client.get(FEED, headers=headers), 403, "forbidden")


@pytest.mark.parametrize(
    ("path", "name", "content_type", "cache"),
    [
        ("", "index.html", "text/html; charset=utf-8", "no-store"),
        ("index.html", "index.html", "text/html; charset=utf-8", "no-store"),
        ("app.js", "app.js", "text/javascript; charset=utf-8", CACHED),
        ("app.css", "app.css", "text/css; charset=utf-8", CACHED),
    ],
)
def test_the_page_is_served_with_security_headers(
    monkeypatch, headers, path, name, content_type, cache
):
    app, service, asked = build_app(monkeypatch, member=True, rooms={})
    response = TestClient(app).get(PAGE + path, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == content_type
    assert_secure(response, cache)
    built = (diagnostics_page.STATIC / name).read_bytes()
    assert response.content == built.replace(b"{{VERSION}}", __version__.encode())
    # Only the feed's polls keep it sampling or ask Talk.
    assert service.diagnostic_feed.task is None and asked == []


def test_the_page_is_versioned(monkeypatch, headers):
    app, _, _ = build_app(monkeypatch, member=True, rooms={})
    html = TestClient(app).get(PAGE, headers=headers).text
    assert f"app.js?v={__version__}" in html and f"app.css?v={__version__}" in html
    assert f'<span id="version">{__version__}</span>' in html
    assert "{{VERSION}}" not in html


class PageMarkup(Hygiene):
    def __init__(self):
        super().__init__()
        self.live, self.roles, self.links, self.noscript = {}, [], [], False

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(tag, attrs)
        a = dict(attrs)
        if "aria-live" in a:
            self.live[a.get("id")] = a["aria-live"]
        if "role" in a:
            self.roles.append(a["role"])
        if tag in ("a", "link", "script") and (a.get("href") or a.get("src")):
            self.links.append(a.get("href") or a.get("src"))
        self.noscript = self.noscript or tag == "noscript"


def test_the_page_markup_suits_its_csp_and_the_proxy(monkeypatch, headers):
    app, _, _ = build_app(monkeypatch, member=True, rooms={})
    parser = PageMarkup()
    parser.feed(TestClient(app).get(PAGE, headers=headers).text)
    assert parser.scripts and all(parser.scripts) and parser.content == []
    assert parser.styles == 0 and parser.inline == [] and parser.handlers == []
    assert parser.lang and parser.viewport and parser.noscript
    # Two polite announcements: the status pill and the connection line, nothing louder.
    # The only roles are the lane band's and the lag graph's: images with a summary.
    assert parser.live == {"pill": "polite", "freshness": "polite"}
    assert parser.roles == ["img", "img"]
    for required in ("pill", "engine", "observe", "freshness", "lanes", "decision", "stages"):
        assert required in parser.ids, required
    # Relative to the page, so they resolve under the AppAPI proxy prefix.
    assert "../settings/index.html" in parser.links
    for link in parser.links:
        assert not link.startswith("/"), link
        assert link.startswith(("https://", "data:")) or "//" not in link, link


@pytest.mark.parametrize("path", ["", "index.html", "app.js", "app.css"])
def test_the_page_needs_the_appapi_gateway_and_a_user(monkeypatch, headers, path):
    app, _, _ = build_app(monkeypatch, member=True, rooms={})
    client = TestClient(app)
    assert client.get(PAGE + path).status_code == 401
    headers["AUTHORIZATION-APP-API"] = base64.b64encode(b":test-exapp-secret").decode()
    error(client.get(PAGE + path, headers=headers), 403, "forbidden")


@pytest.mark.parametrize(
    "path",
    [
        "other.js",
        "index.htm",
        "api",
        "app.js/",
        "static/app.js",
        "%2e%2e%2fapp.py",
        "..%2f..%2fapp.py",
    ],
)
def test_unknown_page_files_are_not_found(monkeypatch, headers, path):
    app, _, _ = build_app(monkeypatch, member=True, rooms={})
    error(TestClient(app).get(PAGE + path, headers=headers), 404, "not_found")


def test_missing_page_files_are_not_found(monkeypatch, headers, tmp_path):
    monkeypatch.setattr(diagnostics_page, "STATIC", tmp_path)
    app, _, _ = build_app(monkeypatch, member=True, rooms={})
    client = TestClient(app)
    for path in ("", "index.html", "app.js", "app.css"):
        error(client.get(PAGE + path, headers=headers), 404, "not_found")


@pytest.mark.parametrize("path", ["", "index.html", "app.js", "app.css"])
def test_the_page_answers_get_only(monkeypatch, headers, path):
    app, _, _ = build_app(monkeypatch, member=True, rooms={})
    client = TestClient(app)
    for method in ("POST", "PUT", "DELETE"):
        error(client.request(method, PAGE + path, headers=headers), 405, "method_not_allowed")
    head = client.head(PAGE + path, headers=headers)
    assert head.status_code == 405 and head.content == b""
    assert_secure(head)
    unknown = client.head(PAGE + "other.js/x", headers=headers)
    assert unknown.status_code == 404
    assert_secure(unknown)


def test_other_methods_and_paths_are_refused_with_security_headers(monkeypatch, headers):
    app, _, _ = build_app(monkeypatch, member=True, rooms={})
    client = TestClient(app)
    error(client.post(FEED, headers=headers, json={}), 405, "method_not_allowed")
    error(client.delete(FEED, headers=headers), 405, "method_not_allowed")
    error(client.get("/diagnostics/api/other", headers=headers), 404, "not_found")


@pytest.mark.parametrize("member", [True, False])
def test_no_room_token_session_or_participant_id_reaches_the_page(monkeypatch, headers, member):
    room, other = WatchedRoom("Ana"), WatchedRoom("Bo", sessions=f"{SESSION}-other")
    app, service, _ = build_app(
        monkeypatch, member=member, rooms={OTHER_TOKEN: other, TOKEN: room}
    )
    # The video runs in the newer room, so the feed samples that one.
    video = service.diagnostics
    video.task, video.owner, video.state = SimpleNamespace(done=lambda: False), TOKEN, "connected"
    video.metrics = {"frames": 3, "kbps": 9.5}
    decide(service, first_mic(room))
    sample(service, room, other)
    response = TestClient(app).get(FEED, headers=headers)
    assert response.status_code == 200
    assert response.json()["video"]["state"] == "connected"
    for identifier in (TOKEN, OTHER_TOKEN, SESSION, ATTENDEE, "ROOMTOKEN"):
        assert identifier not in response.text


def test_the_page_and_history_share_the_membership_check(monkeypatch, headers):
    room = WatchedRoom("Ana")
    app, service, asked = build_app(monkeypatch, member=True, rooms={TOKEN: room})
    sample(service, room)
    client = TestClient(app)
    assert client.get(f"/history?token={TOKEN}", headers=headers).status_code == 200
    assert client.get(FEED, headers=headers).json()["names"] is True
    assert len(asked) == 1


def test_worst_case_response_stays_under_64_kib(monkeypatch, headers):
    big = 10**12

    class Waiting:
        """Waits for a slot every second: the widest lane state in every column."""

        SEGMENT_SECONDS = 15

        def __init__(self):
            self.gate = SimpleNamespace(active=True, chunks_emitted=big, last_probability=0.123)
            self.queue = SimpleNamespace(qsize=lambda: big)
            self.lost_chunks = self.lease_samples = big
            self.worker = self.paused_until = None
            self.pending_since, self.pending = -1e9, "x"
            self.has_lease = lambda: False

    owner = WatchedRoom(*["\U0001f600" * 40] * 12)
    owner.transcribers = {sid: Waiting() for sid in owner.transcribers}
    others = {f"{OTHER_TOKEN}-{i}": WatchedRoom("x", sessions=f"o{i}") for i in range(9)}
    for room in others.values():
        room.transcribers = {sid: Waiting() for sid in room.transcribers}
    app, service, _ = build_app(monkeypatch, member=True, rooms={TOKEN: owner, **others})
    governor = LoadGovernor(ceiling=8)
    status = {key: widest(value) for key, value in governor.status().items()}
    status.update(
        lastRefusal={"at": -1.2345678901234567e-300, "reason": "memory"},
        lastShed={"at": -1.2345678901234567e-300, "lag": -1.2345678901234567e-300},
    )
    governor.status = lambda: dict(status)
    governor.last_decision = {
        "at": 1.0,
        "reason": None,
        "seats": -big,
        "ceiling": -big,
        "lag": -1.2345678901234567e30,
        "cpuStall": -1.2345678901234567e30,
        "headroomMiB": -big,
    }
    governor.last_decision_ref = weakref.ref(first_mic(owner))
    pool = SimpleNamespace(
        busy=big,
        admission_limit=big,
        waiting=big,
        workers=set(),
        dropped_chunks=0,
        chunks_processed=big,
        audio_seconds=1.2345678901234567e30,
        scheduling_yields=big,
        stream_ends={},
        finals_emitted=0,
        partials_emitted=big,
        engine_id="nemotron-multilingual",
        threads=big,
        restarts=big,
        lag=SimpleNamespace(p95=lambda: -1.2345678901234567e-300),
    )
    service.admission = SimpleNamespace(
        state="ready", pool=pool, governor=governor, tracks=[None] * 99, max_tracks=big
    )
    feed = service.diagnostic_feed
    feed.sampler._next_lane = 10**9  # a long-running process has numbered many lanes
    for i in range(COLUMNS + 1):
        pool.finals_emitted += big
        pool.dropped_chunks += big
        feed._step(10**9 + i + 0.123)
    response = TestClient(app).get(FEED, headers=headers)
    body = response.json()
    assert len(body["columns"]) == COLUMNS
    assert all(set(column["lanes"].values()) == {"waiting"} for column in body["columns"])
    assert all(len(column["lanes"]) == 10 for column in body["columns"])
    lanes = body["sample"]["lanes"]
    assert len(lanes) == 10 and all(lane.get("name") == "\U0001f600" * 20 for lane in lanes)
    # Measured: 46552 bytes, 42001 of them the 120 columns of ten lanes.
    assert len(response.content) <= 64 * 1024
