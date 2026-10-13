"""Ten continuously sending tracks: public speech plus synthetic backgrounds.

Synthetic hiss, fan-like noise and keyboard-like bursts are reproducible stress
inputs, not recordings of offices or an acoustic-quality certification. dBFS is
input RMS level, not sound pressure. No production rooms or microphone access.
"""

import argparse
import asyncio
import json
import resource
import time
import wave
from pathlib import Path

import numpy as np

from cassini_live import transcriber
from cassini_live.calibration import background, word_error_rate
from cassini_live.pool import WorkerPool
from cassini_live.speech import SpeechGate
from cassini_live.transcriber import WhistleTranscriber


def gate_seconds(samples):
    gate = SpeechGate()
    chunks = gate.feed(samples) + gate.flush()
    return sum(len(c.samples) for c in chunks) / 16000


async def main(args):
    with wave.open(args.audio) as wav:
        assert wav.getframerate() == 16000 and wav.getnchannels() == 1
        speech = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype("<f4") / 32768
    reference = Path(args.audio).with_suffix(".txt").read_text()
    samples = np.concatenate((np.zeros(16000), speech, np.zeros(32000))).astype("<f4")
    inputs = [samples] + [
        background(args.background, len(samples), args.dbfs, i) for i in range(1, 10)
    ]
    offered = [round(gate_seconds(s), 3) for s in inputs]
    if args.gate_only:
        print(
            json.dumps(
                {"background": args.background, "dbfs": args.dbfs, "gatedSecondsByTrack": offered}
            )
        )
        return
    pool = WorkerPool(2, args.weights)
    await pool.start()
    transcriber.POOL = pool
    captions = asyncio.Queue()
    tracks = [WhistleTranscriber(str(i), "en", captions) for i in range(10)]
    tasks = []
    events = []
    max_busy = 0
    first_caption = None
    started = time.monotonic()
    failure = None
    try:
        for track in tracks:
            await track.connect()
            tasks.append(asyncio.create_task(track._decode()))
        for offset in range(0, len(samples), 320):
            await asyncio.gather(
                *(
                    track._put_audio(inputs[i][offset : offset + 320])
                    for i, track in enumerate(tracks)
                )
            )
            max_busy = max(max_busy, pool.size - pool.idle.qsize())
            while not captions.empty():
                event = captions.get_nowait()
                events.append(event)
                if event.speaker_session_id == "0" and event.message and first_caption is None:
                    first_caption = time.monotonic() - started
            await asyncio.sleep(max(0, started + (offset + 320) / 16000 - time.monotonic()))
        for track in tracks:
            track._flush_gate()
            await track.queue.put(None)
        await asyncio.wait_for(asyncio.gather(*tasks), 35)
    except TimeoutError:
        failure = "Recognition did not drain within 35 seconds"
    finally:
        elapsed = time.monotonic() - started
        while not captions.empty():
            events.append(captions.get_nowait())
        status = pool.status()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for track in tracks:
            await track._release(healthy=False)
        await pool.close()
    text = " ".join(e.message for e in events if e.final and e.speaker_session_id == "0")
    noise_words = [e.message for e in events if e.final and e.speaker_session_id != "0"]
    cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
    print(
        json.dumps(
            {
                "background": args.background,
                "dbfs": args.dbfs,
                "inputSeconds": len(samples) / 16000,
                "elapsedSeconds": round(elapsed, 2),
                "firstSpeechCaptionSeconds": first_caption,
                "maxBusyWorkers": max_busy,
                "gatedSecondsByTrack": offered,
                "workerCpuSeconds": round(cpu.ru_utime + cpu.ru_stime, 2),
                "status": status,
                "publicFixtureTranscript": text,
                "wordErrorRate": word_error_rate(reference, text),
                "noiseFinalCaptions": noise_words,
                "failure": failure,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio")
    parser.add_argument("--weights", default=None)
    parser.add_argument(
        "--background", choices=["silence", "hiss", "fan", "keyboard"], default="silence"
    )
    parser.add_argument("--dbfs", type=float, default=-40)
    parser.add_argument("--gate-only", action="store_true")
    asyncio.run(main(parser.parse_args()))
