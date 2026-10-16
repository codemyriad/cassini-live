import base64
import io
import json
import time

import numpy as np
import pytest

from cassini_live import engine_host
from tests.fakes_engine import CHUNK, FakeRecognizer, HostHarness, pcm

SPEECH = np.full(CHUNK, 0.1, dtype="<f4")
SECOND = np.full(16000, 0.1, dtype="<f4")


@pytest.fixture
def host():
    harness = HostHarness()
    yield harness
    harness.close()


def test_stream_lifecycle_and_one_reply_per_request(host):
    assert host.call({"type": "open", "id": 1, "language": "en"}) == {"type": "opened", "id": 1}
    reply = host.call({"type": "audio", "id": 1, "pcm": pcm(SPEECH)})
    assert reply["type"] == "result" and reply["id"] == 1
    assert reply["result"] == {"text": "", "pending": "w1"}
    assert set(reply) == {"type", "id", "result", "waitMs", "decodeMs"}
    # The commit pad is zeros: no new words, the stream stays open.
    assert host.call({"type": "commit", "id": 1})["result"] == {"text": "w1", "pending": ""}
    assert host.call({"type": "audio", "id": 1, "pcm": pcm(SPEECH)})["result"]["pending"] == "w2"
    assert host.call({"type": "commit", "id": 1})["result"]["text"] == "w2"
    assert len(host.recognizer.streams) == 1
    assert host.call({"type": "finish", "id": 1})["result"] == {"text": "", "pending": ""}
    assert host.recognizer.streams[0].finished
    assert host.call({"type": "audio", "id": 1, "pcm": pcm(SPEECH)})["code"] == "protocol"
    with pytest.raises(Exception):
        host.receive(timeout=0.2)


def test_interleaved_streams_are_demultiplexed(host):
    for sid in range(10):
        host.call({"type": "open", "id": sid, "language": "auto" if sid % 2 else "de"})
    for sid in range(10):
        host.send({"type": "audio", "id": sid, "pcm": pcm(SPEECH[: CHUNK // (1 + sid % 2)])})
    replies = {r["id"]: r for r in (host.receive() for _ in range(10))}
    assert sorted(replies) == list(range(10))
    for sid, reply in replies.items():
        assert reply["result"]["pending"] == ("" if sid % 2 else "w1")
    assert max(host.recognizer.batches) <= 4


def test_cancel_supersedes_an_outstanding_request(host):
    host.call({"type": "open", "id": 3})
    host.send({"type": "audio", "id": 3, "pcm": pcm(SECOND)})
    host.send({"type": "cancel", "id": 3})
    replies = []
    while True:
        try:
            replies.append(host.receive(timeout=0.3))
        except Exception:
            break
    # Either the audio finished before the cancel arrived, or the cancel replaced it.
    assert replies[-1] == {"type": "closed", "id": 3}
    assert len(replies) <= 2
    assert host.call({"type": "commit", "id": 3})["code"] == "protocol"


@pytest.mark.parametrize(
    "message",
    [
        {"type": "audio", "id": 1, "pcm": pcm(np.zeros(16001))},
        {"type": "audio", "id": 1, "pcm": base64.b64encode(b"abc").decode()},
        {"type": "audio", "id": 1, "pcm": ""},
        {"type": "audio", "id": 9, "pcm": pcm(SPEECH)},
        {"type": "bogus", "id": 1},
        {"type": "open", "id": 1},
    ],
)
def test_invalid_requests_are_stream_errors(host, message):
    host.call({"type": "open", "id": 1})
    reply = host.call(message)
    assert reply["type"] == "error" and reply["id"] == message["id"]
    # The process lives on.
    assert host.call({"type": "open", "id": 2}) == {"type": "opened", "id": 2}


def test_limits_and_languages():
    harness = HostHarness(max_streams=2)
    assert harness.call({"type": "open", "id": 1, "language": "xx"})["code"] == "language"
    harness.call({"type": "open", "id": 1, "language": "fr"})
    harness.call({"type": "open", "id": 2, "language": "auto"})
    assert harness.call({"type": "open", "id": 3})["code"] == "limit"
    assert [s.options["language"] for s in harness.recognizer.streams] == ["fr", "auto"]
    harness.close()


def test_automatic_language_tag_is_stripped_and_reported(host):
    host.call({"type": "open", "id": 1, "language": "auto"})
    result = host.call({"type": "audio", "id": 1, "pcm": pcm(SPEECH)})["result"]
    assert result == {"text": "", "pending": "w1", "language": "de"}
    host.call({"type": "open", "id": 2, "language": "it"})
    assert "language" not in host.call({"type": "audio", "id": 2, "pcm": pcm(SPEECH)})["result"]


def test_english_ignores_the_language_option():
    harness = HostHarness(engine="nemotron-en")
    assert harness.call({"type": "open", "id": 1, "language": "de"})["type"] == "opened"
    assert harness.recognizer.streams[0].options == {}
    harness.close()


def test_a_second_outstanding_request_is_refused():
    replies = []
    host = engine_host.Host(FakeRecognizer(), engine_host.Options("x", True), replies.append)
    host.handle({"type": "open", "id": 1})
    host.handle({"type": "audio", "id": 1, "pcm": pcm(SECOND)})
    host.handle({"type": "commit", "id": 1})
    assert replies[-1]["type"] == "error" and replies[-1]["code"] == "protocol"
    # The stream is freed, so its outstanding request ends with that error.
    assert not host.busy and host.streams == {}


def test_fatal_error_exits_1():
    harness = HostHarness(recognizer=FakeRecognizer(fail_decode=True))
    harness.call({"type": "open", "id": 1})
    harness.send({"type": "audio", "id": 1, "pcm": pcm(SPEECH)})
    assert harness.receive() == {"type": "error", "message": "RuntimeError"}
    assert harness.receive() is None
    assert harness.exit_code == 1


def test_malformed_line_is_fatal():
    out = io.StringIO()
    code = engine_host.serve(["not json\n"], out, FakeRecognizer(), engine_host.Options("x", True))
    assert code == 1 and json.loads(out.getvalue())["type"] == "error"


def test_quit_and_eof_exit_0():
    out = io.StringIO()
    options = engine_host.Options("nemotron-en", False)
    assert engine_host.serve(['{"type":"quit"}\n'], out, FakeRecognizer(), options) == 0
    lines = ['{"type":"open","id":1}\n', json.dumps({"type": "audio", "id": 1, "pcm": pcm(SPEECH)})]
    assert engine_host.serve(lines, out, FakeRecognizer(), options) == 0
    # Outstanding work completes before an EOF exit.
    assert json.loads(out.getvalue().splitlines()[-1])["result"]["pending"] == "w1"


def test_rotation_after_thirty_minutes(host, monkeypatch):
    monkeypatch.setattr(engine_host, "ROTATE_SAMPLES", CHUNK)
    host.call({"type": "open", "id": 1, "language": "es"})
    host.call({"type": "audio", "id": 1, "pcm": pcm(SPEECH)})
    assert host.call({"type": "commit", "id": 1})["result"]["text"] == "w1"
    assert len(host.recognizer.streams) == 2
    assert host.recognizer.streams[1].options == {"language": "es"}
    assert host.call({"type": "audio", "id": 1, "pcm": pcm(SPEECH)})["result"]["pending"] == "w1"


def test_per_segment_mode():
    harness = HostHarness(segments="per_segment")
    harness.call({"type": "open", "id": 1, "language": "nl"})
    harness.call({"type": "audio", "id": 1, "pcm": pcm(SPEECH)})
    assert harness.call({"type": "commit", "id": 1})["result"]["text"] == "w1"
    first, second = harness.recognizer.streams
    assert first.finished and not second.finished
    assert second.options == {"language": "nl"}
    harness.close()


def test_clean():
    assert engine_host.clean("hallo <de-DE> welt <fr>") == ("hallo welt", "fr")
    assert engine_host.clean("plain text") == ("plain text", None)


def test_results_report_queueing_and_decoding_time_separately():
    class SlowRecognizer(FakeRecognizer):
        def decode_streams(self, streams):
            time.sleep(0.03)
            super().decode_streams(streams)

    replies = []
    host = engine_host.Host(SlowRecognizer(), engine_host.Options("x", True), replies.append)
    host.handle({"type": "open", "id": 1})
    host.handle({"type": "audio", "id": 1, "pcm": pcm(SPEECH)})
    time.sleep(0.02)
    host.step()
    opened, result = replies
    assert "waitMs" not in opened and "decodeMs" not in opened
    assert result["type"] == "result" and result["waitMs"] >= 20 and result["decodeMs"] >= 30
    assert isinstance(result["waitMs"], int) and isinstance(result["decodeMs"], int)
    # A request spanning several batches counts from its first one.
    host.handle({"type": "finish", "id": 1})
    while host.busy:
        host.step()
    assert replies[-1]["type"] == "result"
    assert replies[-1]["waitMs"] >= 0 and replies[-1]["decodeMs"] >= 90
    # Errors carry no timing.
    host.handle({"type": "commit", "id": 1})
    assert replies[-1]["type"] == "error" and "waitMs" not in replies[-1]


def test_batch_never_exceeds_max_batch_and_serves_oldest_first():
    replies = []
    recognizer = FakeRecognizer()
    host = engine_host.Host(recognizer, engine_host.Options("x", True), replies.append)
    for sid in range(10):
        host.handle({"type": "open", "id": sid})
    for sid in reversed(range(10)):
        host.handle({"type": "audio", "id": sid, "pcm": pcm(SPEECH)})
    host.step()
    assert recognizer.batches == [4]
    assert {r["id"] for r in replies[10:]} == {9, 8, 7, 6}
    while host.busy:
        host.step()
    assert recognizer.batches == [4, 4, 2]
