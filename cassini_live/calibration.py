"""Reproducible local audio workload. No calls, microphones or private audio."""

import asyncio
import re
import time
import wave
from pathlib import Path

import numpy as np

from .load import LagMeter, control_lag

FIXTURES = Path(__file__).parent / "fixtures"


def background(kind, count, dbfs, seed):
    rng = np.random.default_rng(seed)
    if kind == "silence":
        return np.zeros(count, dtype="<f4")
    white = rng.standard_normal(count)
    if kind == "hiss":
        samples = white
    elif kind == "fan":
        frequencies = np.fft.rfftfreq(count, 1 / 16000)
        samples = np.fft.irfft(np.fft.rfft(white) / np.sqrt(np.maximum(frequencies, 40)), n=count)
        samples += 0.02 * np.sin(2 * np.pi * 100 * np.arange(count) / 16000)
    elif kind == "keyboard":
        samples = white * 0.02
        for start in range(0, count - 1600, 8000):
            start += int(rng.integers(0, 4000))
            samples[start : start + 640] += rng.standard_normal(640) * np.exp(-np.arange(640) / 90)
    else:
        raise ValueError(kind)
    samples *= 10 ** (dbfs / 20) / max(float(np.sqrt(np.mean(samples**2))), 1e-10)
    return np.clip(samples, -1, 1).astype("<f4")


def word_error_rate(reference, text):
    reference = re.findall(r"\w+", reference.lower())
    row = list(range(len(reference) + 1))
    for i, word in enumerate(re.findall(r"\w+", text.lower()), 1):
        previous, row = row, [i]
        for j, expected in enumerate(reference, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (word != expected)))
    return row[-1] / max(1, len(reference))


def speech_fixture():
    with wave.open(str(FIXTURES / "jfk.wav")) as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
            raise ValueError("Invalid calibration recording")
        samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    return samples.astype("<f4") / 32768


async def scenario(pool, speakers, *, participants=10, speed=1.3):
    """Pace all tracks faster than real time to demonstrate throughput margin.

    Feed/capture and decoding run concurrently; do not discard input simply to
    make the machine look fast. The same gates, queues and workers serve calls.
    """
    # Amortize the native final flush so it is not mistaken for sustained CPU shortage.
    repeated = np.tile(np.concatenate((speech_fixture(), np.zeros(16000))), 2)
    samples = np.concatenate((np.zeros(16000), repeated, np.zeros(32000))).astype("<f4")
    kinds = ("keyboard", "fan", "hiss")
    inputs = [samples] * speakers + [
        background(kinds[i % 3], len(samples), -25 if i % 2 else -40, i)
        for i in range(speakers, participants)
    ]
    captions = asyncio.Queue()
    tracks = [pool.transcriber(str(i), "en", captions) for i in range(participants)]
    decoders = [asyncio.create_task(t._decode()) for t in tracks]
    finals = [[] for _ in range(speakers)]
    first = [None] * speakers
    noise_captions = 0
    started = time.monotonic()
    max_feed_lag = 0.0
    counters = (pool.failures, pool.capacity_waits, pool.dropped_chunks)

    async def collect():
        nonlocal noise_captions
        while True:
            event = await captions.get()
            if not event.message:
                continue
            index = int(event.speaker_session_id)
            if index >= speakers:
                noise_captions += 1
            else:
                if first[index] is None:
                    first[index] = time.monotonic() - started - 1 / speed
                if event.final:
                    finals[index].append(event.message)

    collector = asyncio.create_task(collect())
    timed_out = False
    # Lag as the governor measures it (load.py), sampled every feed step over the tracks of
    # this scenario only; the per-request meter is swapped out so synthetic requests never
    # reach the live status figures, and each scenario reports its own p95.
    lag, max_lag, sustained = LagMeter(), 0.0, 0.0
    live_requests, pool.lag = pool.lag, LagMeter()
    try:
        async with asyncio.timeout(30):
            for offset in range(0, len(samples), 320):
                now = time.monotonic()
                max_feed_lag = max(max_feed_lag, now - started - offset / 16000 / speed)
                current = control_lag(tracks, now)
                lag.observe(current, now)
                max_lag, sustained = max(max_lag, current), max(sustained, lag.sustained(now))
                await asyncio.gather(
                    *(t._put_audio(inputs[i][offset : offset + 320]) for i, t in enumerate(tracks))
                )
                await asyncio.sleep(
                    max(0, started + (offset + 320) / 16000 / speed - time.monotonic())
                )
            for track in tracks:
                track._flush_gate()
                await track.queue.put(None)
            await asyncio.gather(*decoders)
            await asyncio.sleep(0)  # Deliver the last final caption to the collector.
    except TimeoutError:
        timed_out = True
    finally:
        collector.cancel()
        for task in decoders:
            task.cancel()
        await asyncio.gather(collector, *decoders, return_exceptions=True)
        # Cancellation may leave native streams in progress. Drop them before
        # returning the pool to calls, never reuse their pending protocol replies.
        await asyncio.gather(*(t.abort() for t in tracks))
        requests, pool.lag = pool.lag, live_requests
    elapsed = time.monotonic() - started
    reference = ((FIXTURES / "jfk.txt").read_text() + " ") * 2
    errors = [word_error_rate(reference, " ".join(f)) for f in finals]
    differences = [
        a - b
        for a, b in zip(
            (pool.failures, pool.capacity_waits, pool.dropped_chunks), counters, strict=True
        )
    ]
    duration = len(samples) / 16000
    reasons = []
    if timed_out:
        reasons.append("recognition_timeout")
    if elapsed > duration / 1.2 or max_feed_lag > 0.5:
        reasons.append("insufficient_throughput_margin")
    if any(x is None or x > 3 for x in first):
        reasons.append("caption_delay")
    if max(errors) > 0.25:
        reasons.append("sample_recognition")
    if noise_captions:
        reasons.append("noise_captions")
    if any(differences):
        reasons.append("dropped_audio_or_worker_pressure")
    return {
        "speakers": speakers,
        "participants": participants,
        "passed": not reasons,
        "reasons": reasons,
        "inputSeconds": duration,
        "playbackSpeed": speed,
        "elapsedSeconds": round(elapsed, 3),
        "maxFeedLagSeconds": round(max_feed_lag, 3),
        "firstCaptionSeconds": [round(v, 3) if v is not None else None for v in first],
        "wordErrorRates": errors,
        "noiseCaptionCount": noise_captions,
        "workerFailures": differences[0],
        "capacityTimeouts": differences[1],
        "droppedChunks": differences[2],
        # Reported, never a pass criterion: the staging run sizes the thresholds from them.
        "maxLagSeconds": round(max_lag, 3),
        "lagSustainedSeconds": round(sustained, 3),
        "p95RequestSeconds": round(requests.p95(), 3),
    }
