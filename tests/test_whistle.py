"""Recorded-audio integration; never use the microphone or an external STT API."""

import asyncio
import base64
import os
import re
import wave
from pathlib import Path

import numpy as np
import pytest

from cassini_live.pool import WorkerPool

ASSETS = Path(__file__).parent / "assets"
MODELS = Path(__file__).parents[1] / "models"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (MODELS / "whistle.cact").is_file(), reason="Run uv run python scripts/prepare_model.py"
    ),
]


def words(text):
    return re.findall(r"\w+", text.lower())


async def transcribe(worker, samples):
    await worker.send({"type": "start", "language": "auto"})
    final = []
    for i in range(0, len(samples), 16000):
        await worker.send(
            {"type": "audio", "pcm": base64.b64encode(samples[i : i + 16000].tobytes()).decode()}
        )
        message = await worker.receive()
        assert message["type"] == "result"
        final.append(message["result"]["text"])
    await worker.send({"type": "end"})
    while (message := await worker.receive())["type"] != "done":
        final.append(message["result"]["text"])
    return " ".join(final)


async def test_real_whistle_concurrent_speakers_and_repeated_sessions(monkeypatch):
    monkeypatch.setenv("NEEDLE3_LIB_PATH", str(MODELS / "libneedle3.so"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    with wave.open(str(ASSETS / "jfk.wav")) as source:
        assert source.getframerate() == 16000 and source.getnchannels() == 1
        speech = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    speech = speech.astype("<f4") / 32768
    silence = np.zeros(len(speech), dtype="<f4")
    pool = WorkerPool(2, str(MODELS / "whistle.cact"))
    await pool.start()
    try:
        a, b = pool.acquire(), pool.acquire()
        assert a.process.pid != b.process.pid
        heard, quiet = await asyncio.gather(transcribe(a, speech), transcribe(b, silence))
        assert words(heard) == words((ASSETS / "jfk.txt").read_text())
        assert not quiet.strip()
        # Reuse a former speech worker for silence and vice versa.
        quiet, heard = await asyncio.gather(transcribe(a, silence), transcribe(b, speech))
        assert not quiet.strip()
        assert words(heard) == words((ASSETS / "jfk.txt").read_text())
        await pool.release(a)
        await pool.release(b)
    finally:
        pids = [w.process.pid for w in pool.workers]
        await pool.close()
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
