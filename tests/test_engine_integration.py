"""Real Nemotron model through the engine host. Never in the default run.

Loads about 1 GB, so it runs only inside a capped scope, one model per invocation:

    CASSINI_TEST_MODEL_DIR=local/stt-eval/models/<dir> \\
      systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 \\
      uv run --frozen pytest -m integration tests/test_engine_integration.py -q -s
"""

import asyncio
import os
from pathlib import Path

import numpy as np
import pytest

from cassini_live import catalog
from cassini_live.calibration import scenario, speech_fixture, word_error_rate
from cassini_live.engines.shared import SharedEngine

FIXTURES = Path(__file__).parents[1] / "cassini_live" / "fixtures"


def capped():
    try:
        path = Path("/proc/self/cgroup").read_text().strip().split("::", 1)[1]
        group = Path("/sys/fs/cgroup") / path.lstrip("/")
        memory = (group / "memory.max").read_text().strip()
        swap = (group / "memory.swap.max").read_text().strip()
    except (OSError, IndexError):
        return False
    return memory != "max" and int(memory) <= 3 * 1024**3 and swap == "0"


MODEL_DIR = os.environ.get("CASSINI_TEST_MODEL_DIR")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not MODEL_DIR, reason="CASSINI_TEST_MODEL_DIR is not set"),
    pytest.mark.skipif(not capped(), reason="needs a cgroup with MemoryMax<=3G and no swap"),
]


def engine_id():
    return "nemotron-multilingual" if "3.5" in Path(MODEL_DIR or "").name else "nemotron-en"


async def started(**kwargs):
    shared = SharedEngine(catalog.get(engine_id()), Path(MODEL_DIR), 2, **kwargs)
    await shared.start()
    return shared


async def transcribe(stream, samples, end):
    texts = []
    for offset in range(0, len(samples), 16000):
        await stream.audio(samples[offset : offset + 16000])
    texts.append((await getattr(stream, end)())["text"])
    return " ".join(texts)


async def test_two_streams_wer_threads_and_reaping():
    shared = await started(ready_timeout=300)
    speech, silence = await shared.open_stream("en"), await shared.open_stream("en")
    audio = speech_fixture()
    text, quiet = await asyncio.gather(
        transcribe(speech, audio, "finish"),
        transcribe(silence, np.zeros(len(audio), dtype="<f4"), "finish"),
    )
    reference = (FIXTURES / "jfk.txt").read_text()
    print("\nRSS after load (MiB):", shared.rss_mib)
    assert word_error_rate(reference, text) <= 0.25, text
    assert not quiet.strip()
    pid = shared.process.process.pid
    tasks = len(os.listdir(f"/proc/{pid}/task"))
    print("host threads:", tasks)
    assert tasks <= shared.threads + 4
    await shared.close()
    assert (
        not Path(f"/proc/{pid}").exists() or "zombie" not in Path(f"/proc/{pid}/status").read_text()
    )


async def test_commit_versus_finish():
    shared = await started(ready_timeout=300)
    audio = speech_fixture()
    committed = await transcribe(await shared.open_stream("en"), audio, "commit")
    finished = await transcribe(await shared.open_stream("en"), audio, "finish")
    missing = abs(len(finished.split()) - len(committed.split()))
    print(f"\ncommit: {committed!r}\nfinish: {finished!r}\nword difference: {missing}")
    await shared.close()
    assert missing <= 2, "switch engines.shared.SEGMENTS to per_segment"


@pytest.mark.skipif(engine_id() != "nemotron-multilingual", reason="multilingual model only")
async def test_language_option_and_automatic_tag():
    shared = await started(ready_timeout=300)
    forced = await transcribe(await shared.open_stream("en"), speech_fixture(), "finish")
    stream = await shared.open_stream("auto")
    for offset in range(0, len(speech_fixture()), 16000):
        result = await stream.audio(speech_fixture()[offset : offset + 16000])
    final = await stream.finish()
    print(f"\nforced en: {forced!r}\nauto: {final!r} last partial: {result!r}")
    assert "<" not in final["text"]
    assert final.get("language", "en") == "en"
    await shared.close()


async def test_calibration_passes_for_one_speaker():
    shared = await started(ready_timeout=300)
    result = await scenario(shared, 1)
    print("\n", result)
    await shared.close()
    assert result["passed"], result["reasons"]
