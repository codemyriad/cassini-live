"""Paced, synthetic ten-track meeting; only bundled public speech, no live rooms.

Run with PYTHONPATH=. and NEEDLE3_LIB_PATH pointing at the bundled engine.
Tests real VAD, worker allocation and native recognition. Network/Opus delivery
is covered separately and must also be verified in a Talk call after deployment.
"""

import argparse
import asyncio
import json
import re
import resource
import time
import wave

import numpy as np

from cassini_live import transcriber
from cassini_live.pool import WorkerPool
from cassini_live.transcriber import WhistleTranscriber


async def main(args):
    with wave.open(args.audio) as wav:
        assert wav.getframerate() == 16000 and wav.getnchannels() == 1
        speech = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype("<f4") / 32768
    # Include leading and trailing silence to exercise activation and final flush.
    samples = np.tile(
        np.concatenate((np.zeros(16000), speech, np.zeros(32000))).astype("<f4"),
        args.rounds,
    )
    pool = WorkerPool(args.workers, args.weights)
    await pool.start()
    transcriber.POOL = pool
    captions = asyncio.Queue()
    tracks = [WhistleTranscriber(str(i), "en", captions) for i in range(args.participants)]
    decoders = []
    start = time.monotonic()
    max_busy = 0
    finals = []
    first_caption = None
    max_drain = 0
    try:
        for track in tracks:
            await track.connect()
            decoders.append(asyncio.create_task(track._decode()))
        for offset in range(0, len(samples), 320):
            frame = samples[offset : offset + 320]
            await asyncio.gather(
                *(
                    t._put_audio(frame if i < args.speakers else np.zeros(len(frame), dtype="<f4"))
                    for i, t in enumerate(tracks)
                )
            )
            max_busy = max(max_busy, args.workers - pool.idle.qsize())
            while not captions.empty():
                event = captions.get_nowait()
                if event.message and first_caption is None:
                    first_caption = time.monotonic() - start
                if event.final:
                    finals.append((event.speaker_session_id, event.message))
            await asyncio.sleep(max(0, start + (offset + len(frame)) / 16000 - time.monotonic()))
        for track in tracks:
            track._flush_gate()
            await track.queue.put(None)
        drain = time.monotonic()
        await asyncio.wait_for(asyncio.gather(*decoders), 30)
        max_drain = time.monotonic() - drain
        while not captions.empty():
            event = captions.get_nowait()
            if event.final:
                finals.append((event.speaker_session_id, event.message))
        status = pool.status()
        elapsed = time.monotonic() - start
    finally:
        for task in decoders:
            task.cancel()
        await asyncio.gather(*decoders, return_exceptions=True)
        for track in tracks:
            await track._release(healthy=False)
        await pool.close()
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    by_speaker = {
        str(i): " ".join(text for sid, text in finals if sid == str(i))
        for i in range(args.speakers)
    }
    reference = (
        re.findall(r"\w+", open(args.audio.replace(".wav", ".txt")).read().lower()) * args.rounds
    )

    def word_error_rate(text):
        row = list(range(len(reference) + 1))
        for i, word in enumerate(re.findall(r"\w+", text.lower()), 1):
            previous, row = row, [i]
            for j, expected in enumerate(reference, 1):
                row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (word != expected)))
        return row[-1] / max(1, len(reference))

    errors = {sid: word_error_rate(text) for sid, text in by_speaker.items()}
    print(
        json.dumps(
            {
                "participants": args.participants,
                "speakers": args.speakers,
                "audioSeconds": round(len(samples) / 16000, 2),
                "elapsedSeconds": round(elapsed, 2),
                "firstCaptionSeconds": first_caption,
                "finalDrainSeconds": round(max_drain, 2),
                "maxBusyWorkers": max_busy,
                "workerCpuSeconds": round(usage.ru_utime + usage.ru_stime, 2),
                "status": status,
                "publicFixtureTranscripts": by_speaker,
                "wordErrorRates": errors,
            },
            indent=2,
        )
    )
    assert status["droppedSpeechChunks"] == status["streamFailures"] == status["capacityWaits"] == 0
    assert status["available"] == args.workers
    assert all(rate <= 0.15 for rate in errors.values()), "Public fixture accuracy regressed"
    assert all(int(sid) < args.speakers for sid, _ in finals)
    assert max_busy <= args.speakers


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio")
    parser.add_argument("--weights", default=None)
    parser.add_argument("--participants", type=int, default=10)
    parser.add_argument("--speakers", type=int, default=1)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--workers", type=int, default=2)
    asyncio.run(main(parser.parse_args()))
