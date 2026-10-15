import asyncio
import base64
from types import SimpleNamespace

import numpy as np
import pytest
from av import AudioFrame

from cassini_live import transcriber
from cassini_live.pool import CapacityError, WorkerPool
from cassini_live.speech import SpeechGate
from cassini_live.talk.types import StreamEndedException
from cassini_live.transcriber import WhistleTranscriber


class AlwaysSpeech:
    def is_speech(self, pcm, rate):
        return True


@pytest.fixture
def speech_gate(monkeypatch):
    monkeypatch.setattr(transcriber, "SpeechGate", lambda **kw: SpeechGate(AlwaysSpeech(), **kw))


class FakeWorker:
    def __init__(self, _weights=None):
        self.process = SimpleNamespace(returncode=None)
        self.sent = []
        self.replies = asyncio.Queue()

    async def start(self):
        pass

    async def send(self, message):
        self.sent.append(message)
        if message["type"] == "audio":
            self.replies.put_nowait({"type": "result", "result": {"pending": "hello"}})
        if message["type"] == "end":
            self.replies.put_nowait({"type": "result", "result": {"text": "hello world"}})
            self.replies.put_nowait({"type": "done"})

    async def receive(self, **_args):
        return await self.replies.get()

    async def close(self):
        self.process.returncode = 0


class AudioSource:
    """48 kHz stereo, like an incoming decoded Opus track."""

    def __init__(self, frames=125, endless=False):
        self.index = 0
        self.frames = frames
        self.endless = endless
        self.stopped = False

    async def receive(self):
        await asyncio.sleep(0.001)
        if not self.endless and self.index == self.frames:
            raise StreamEndedException()
        stereo = np.full((1, 960 * 2), 8192, dtype=np.int16)
        frame = AudioFrame.from_ndarray(stereo, format="s16", layout="stereo")
        frame.sample_rate = 48000
        frame.pts = self.index * 960
        self.index += 1
        return frame

    def stop(self):
        self.stopped = True


async def test_resampling_preview_and_tail_flush(monkeypatch, speech_gate):
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    monkeypatch.setattr(transcriber, "POOL", pool)
    queue = asyncio.Queue()
    captioner = WhistleTranscriber("speaker-a", "en", queue)
    await captioner.connect()
    worker = next(iter(pool.workers))
    source = AudioSource()
    await captioner.start(source)
    await captioner.audio_task
    chunks = [
        np.frombuffer(base64.b64decode(m["pcm"]), dtype="<f4")
        for m in worker.sent
        if m["type"] == "audio"
    ]
    assert sum(map(len, chunks)) == 40000  # 2.5 seconds exactly, including resampler tail.
    assert all(len(chunk) <= 16000 for chunk in chunks)
    assert np.isclose(np.concatenate(chunks)[100:-100].mean(), 0.25, atol=0.001)
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert [(e.final, e.message) for e in events] == [(False, "hello"), (True, "hello world")]
    assert all(e.speaker_session_id == "speaker-a" for e in events)
    assert pool.idle.qsize() == 1
    assert source.stopped
    await pool.close()


async def test_stop_mid_sentence_flushes_before_reuse(monkeypatch, speech_gate):
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    monkeypatch.setattr(transcriber, "POOL", pool)
    queue = asyncio.Queue()
    captioner = WhistleTranscriber("speaker", "auto", queue)
    await captioner.connect()
    worker = next(iter(pool.workers))
    await captioner.start(AudioSource(endless=True))
    while queue.empty():
        await asyncio.sleep(0.01)
    await captioner.shutdown()
    assert worker.sent[-1] == {"type": "end"}
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert any(e.final and e.message == "hello world" for e in events)
    assert pool.idle.qsize() == 1
    await pool.close()


async def test_pool_is_exclusive_and_replaces_failed_workers():
    pool = WorkerPool(2, worker_factory=FakeWorker)
    await pool.start()
    first, second = pool.acquire(), pool.acquire()
    assert first is not second
    with pytest.raises(CapacityError):
        pool.acquire()
    await pool.release(first, healthy=False)
    replacement = pool.acquire()
    assert replacement is not first
    assert first.process.returncode == 0
    assert len(pool.workers) == 2
    await pool.release(second)
    await pool.release(replacement)
    await pool.close()
    assert all(w.process.returncode == 0 for w in (second, replacement))


async def test_language_switch_keeps_track_and_flushes(monkeypatch, speech_gate):
    pool = WorkerPool(1, worker_factory=FakeWorker)
    await pool.start()
    monkeypatch.setattr(transcriber, "POOL", pool)
    captioner = WhistleTranscriber("speaker", "en", asyncio.Queue())
    await captioner.connect()
    worker = next(iter(pool.workers))
    source = AudioSource(endless=True)
    await captioner.start(source)
    await asyncio.sleep(0.025)
    await captioner.set_language("de")
    assert not source.stopped
    async with asyncio.timeout(2):
        while len([m for m in worker.sent if m["type"] == "start"]) < 2:
            await asyncio.sleep(0.01)
    assert [m.get("language") for m in worker.sent if m["type"] == "start"] == ["en", "de"]
    await captioner.shutdown()
    assert source.stopped
    await pool.close()


async def test_audio_burst_keeps_recent_audio_without_killing_stream(
    caplog, monkeypatch, speech_gate
):
    monkeypatch.setattr(transcriber, "POOL", WorkerPool(1))
    captioner = WhistleTranscriber("speaker", "auto", asyncio.Queue())
    # A burst longer than the queue capacity must not end captioning. The
    # decoder can resume with bounded latency and the newest buffered audio.
    for second in range(9):
        await captioner._put_audio(np.full(16000, second, dtype="<f4"))
    assert captioner.queue.qsize() == 6
    assert captioner.dropped_chunks == 3
    assert [int(captioner.queue.get_nowait().samples[0]) for _ in range(6)] == list(range(3, 9))
    await captioner._put_audio(np.full(16000, 9, dtype="<f4"))
    assert int(captioner.queue.get_nowait().samples[0]) == 9
    assert "discarded 1 speech chunks" in caplog.text


async def test_worker_pool_backend_members():
    pool = WorkerPool(2, worker_factory=FakeWorker)
    assert pool.engine_id == "whistle" and pool.load_check is None
    pool.closed = True
    pool.resize(3)
    assert pool.size == 3 and pool.idle.maxsize == 3
    pool.resize(2)
    await pool.start()
    assert pool.is_idle() and pool.busy == 0
    with pytest.raises(RuntimeError):
        pool.resize(1)
    worker = pool.acquire()
    assert pool.busy == 1 and not pool.is_idle()
    track = pool.transcriber("s", "de", asyncio.Queue(), lambda: False)
    assert type(track) is WhistleTranscriber and track.pool is pool and track.language == "de"
    assert pool.status()["engine"] == "whistle"
    await pool.release(worker)
    await pool.close()


async def test_create_dispatches_on_the_active_backend(monkeypatch):
    class Backend:
        def transcriber(self, *args):
            return ("backend", args)

    queue = asyncio.Queue()
    monkeypatch.setattr(transcriber, "POOL", None)
    assert type(transcriber.create("s", "en", queue)) is WhistleTranscriber
    monkeypatch.setattr(transcriber, "POOL", Backend())
    assert transcriber.create("s", "en", queue, 1, 2) == ("backend", ("s", "en", queue, 1, 2))
    explicit = WorkerPool(1)
    assert transcriber.create("s", "en", queue, pool=explicit).pool is explicit


async def test_calibration_aborts_every_track(monkeypatch):
    from cassini_live import calibration

    aborted = []

    class Track(WhistleTranscriber):
        async def _decode(self):
            await asyncio.Future()

        async def abort(self):
            aborted.append(self.session_id)
            await super().abort()

    pool = WorkerPool(1, worker_factory=FakeWorker)
    pool.transcriber = lambda s, lang, q, *h: Track(s, lang, q, pool=pool)
    await pool.start()
    monkeypatch.setattr(calibration, "speech_fixture", lambda: np.zeros(1600, dtype="<f4"))
    task = asyncio.create_task(calibration.scenario(pool, 1, participants=3, speed=100))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sorted(aborted) == ["0", "1", "2"]
    await pool.close()
