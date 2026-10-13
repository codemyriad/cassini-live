"""Compare forced 2s handoffs with pause-aware handoffs using public audio.

Run with PYTHONPATH=. and NEEDLE3_LIB_PATH set to models/libneedle3.so.
One local worker, no network or calls. A simulated waiter exercises the actual
handoff code; this is an accuracy experiment, not a server capacity assessment.
"""

import asyncio
import json
import resource
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from cassini_live.calibration import FIXTURES, speech_fixture, word_error_rate
from cassini_live.pool import WorkerPool
from cassini_live.speech import SpeechGate
from cassini_live.transcriber import WhistleTranscriber


class ContendedPool(WorkerPool):
    @property
    def waiting(self):
        # Only simulate contention while a stream owns the worker. Acquisition
        # and the idle pool retain their normal semantics.
        return int(bool(self.leased))


async def measure(samples, reference, prefer_pauses):
    pool = ContendedPool(1, str(Path(__file__).resolve().parents[1] / "models/whistle.cact"))
    captions = asyncio.Queue()
    track = WhistleTranscriber("fixture", "en", captions, pool=pool)
    gate = SpeechGate()
    chunks = gate.feed(samples) + gate.flush()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    await pool.start()
    started = time.monotonic()
    first_result = None
    finals = []
    try:
        for chunk in chunks:
            # Offline playback: don't mistake elapsed benchmark time for stale
            # live audio. Both policies receive exactly the same retained PCM.
            chunk = replace(
                chunk,
                created_at=time.monotonic(),
                pause_cut=chunk.pause_cut if prefer_pauses else None,
            )
            await track._decode_audio(chunk)
            if chunk.end:
                await track._finish(chunk.end_reason)
            while not captions.empty():
                event = captions.get_nowait()
                if event.message and first_result is None:
                    first_result = time.monotonic() - started
                if event.final:
                    finals.append(event.message)
        await track._finish()
        elapsed = time.monotonic() - started
        assert pool.dropped_chunks == 0
    finally:
        await pool.close()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    text = " ".join(finals)
    return {
        "policy": "pause-aware" if prefer_pauses else "fixed",
        "wordErrorRate": round(word_error_rate(reference, text), 4),
        "processingSeconds": round(elapsed, 3),
        "firstResultProcessingSeconds": round(first_result, 3) if first_result else None,
        "workerCpuSecondsIncludingStartup": round(
            after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime, 3
        ),
        "streamEnds": dict(pool.stream_ends),
        "text": text,
    }


async def main():
    speech = speech_fixture()
    reference = (FIXTURES / "jfk.txt").read_text()
    results = []
    # Change the speech's alignment with 1s chunks and the 2s lease deadline.
    for shift in (0, 0.25, 0.5):
        samples = np.concatenate((np.zeros(int(16000 * shift)), speech)).astype("<f4")
        for prefer_pauses in (False, True):
            result = await measure(samples, reference, prefer_pauses)
            result["leadingSilenceSeconds"] = shift
            results.append(result)
    print(
        json.dumps(
            {
                "scope": "One English fixture, simulated contention, offline playback",
                "results": results,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
