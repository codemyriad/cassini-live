# Check caption capacity without a call

Cassini Live 0.4.0 measures capacity automatically after startup, using the actual
installed recognition workers. Open **Cassini Live** in Nextcloud to see the
result. No microphone, Talk room, private recording, model download or network
speech service is used. The small public-domain fixture is included in the image.

The result is a tested number of **simultaneous speakers across the entire
server**, with ten open audio tracks in the workload. Admission is capped at ten audio tracks across all rooms, matching the workload.
At most two rooms may run, and never more rooms than measured speakers. A two-worker configuration does not by itself
prove that the CPU can keep up with two speakers.

## Protect the host first

Before loading any recognition worker, the service requires readable cgroup v2
CPU, memory, swap and PID limits, plus pressure counters and host memory data.
Unlimited, unreadable and unsupported configurations leave the API running with
captions blocked. There is no environment switch to bypass the check.

* CPU quota must be at most half the CPUs visible through process affinity.
* Container memory must be at most half the host RAM, and at least
  384 MiB plus 192 MiB per configured worker.
* Swap must be disabled. The PID limit must be finite and at most 256.
* There must be at least 10% host memory available (and at least 256 MiB).
* At most four workers are allowed, with one per two quota CPUs. A quota below
  two CPUs may try one worker, but must still pass the recording test.

The limits are enforced by Linux, outside the application. The service checks
pressure every second and stops workers and caption rooms at 80% of the memory
or PID budget, low host memory, an OOM kill, changed limits, or three consecutive
seconds of heavy CPU throttling/event-loop delay. Three dropped speech chunks
within a 15-second monitoring window or three worker failures within a minute
also stop captions. Audio queues, wait times, input tracks and rooms are bounded.

Refusal stays in place until an administrator reruns the check. A restart always
checks again. This protects resources allocated to this container; it cannot
reserve the rest of the host against unrelated applications. Operators must also
bound Docker logs, as described in [installation](install.md).

## What the check measures

One through up to four simultaneous speakers play an 11-second English sample
twice, with one-second pauses, one second of leading silence and two of trailing
silence. Repetition reduces the influence of fixed finalization overhead. The other tracks
continuously supply deterministic fan-like noise, hiss and keyboard-like bursts
at −40/−25 dBFS RMS. The workload exercises the same resampling output format,
VAD, bounded queues, fair scheduling and Whistle subprocesses as calls, at **1.3×
playback speed**. It does not simulate ICE, network jitter, Opus decoding or echo
cancellation. The first unsuccessful speaker count stops the check.

A count passes only when:

- Processing including final drain takes at most input duration / 1.2, leaving
  measured throughput margin beyond ordinary real-time playback.
- The audio feeder falls no more than 500 ms behind its accelerated schedule.
- Every speaker receives a first caption within three seconds of speech onset
  in the accelerated workload, and final sample word error rate is at most 25%.
- There are no worker failures, capacity timeouts, dropped chunks or captions
  attributed to background-noise tracks.

This is an installation screen, not a capacity guarantee or general accuracy
benchmark. One short English sample cannot predict all languages, accents,
quiet voices, echo, music, background conversations or sustained thermal/CPU
contention. Digital dBFS is not room loudness. The check runs with current host
load and resource limits; results can become outdated when these change. Test
real calls for end-to-end quality and leave resources for other applications.

## Results

- **Measured for N simultaneous speakers:** counts through N passed. If the
  next count failed, N is still the measured limit; adding workers alone does
  not increase it. More than four simultaneous speakers are never claimed by
  this short check, even if more workers are configured.
- **Check did not pass:** even one speaker failed a criterion. Inspect `/status`
  → `capacityAssessment.scenarios[].reasons`; this can be timing, noise or sample
  recognition failure, rather than simply insufficient CPU.
- **Not established / interrupted / error:** no capacity claim is made. Captions remain unavailable. Rerun after resolving the reported problem.

The status page separately warns about recently dropped live audio. The measured speaker count limits concurrent recognition leases.
A historical passing result never overrides a current resource refusal. Results are
kept in memory and remeasured on service restart. No call content is stored.

## Rerun on an installed service

When no caption rooms are active:

```sh
docker exec nc_app_live_transcription python -m cassini_live.capacity --installed
```

This authenticated internal command refuses to start while rooms are active.
It rebuilds the bounded worker pool, prints a JSON report without credentials or call
content, and updates the page. Only container-level access can initiate it;
ordinary browser users cannot start repeated resource-intensive checks.

The startup check is mandatory, including after an upgrade. The old
`CASSINI_CAPACITY_CHECK` setting no longer disables it.

## Test an image before installing

Run on the intended server with the intended container CPU/memory constraints,
when the additional workload will not compete with live captions:

```sh
docker run --rm --network none --cpus=4 --memory=2g --memory-swap=2g --pids-limit=128 \
  --log-driver=local --log-opt max-size=10m --log-opt max-file=3 \
  --entrypoint python ghcr.io/codemyriad/cassini-live:0.4.0 \
  -m cassini_live.capacity --workers 2
```

The resource values above are example constraints, not minimum requirements.
No Nextcloud credentials are needed for this standalone mode. The JSON includes
visible CPU quota/affinity and cgroup v2 memory limits. Lack of a reported memory
limit means unknown/unlimited, not a memory-capacity guarantee. Model preload
must succeed; a process killed by the memory limit cannot report a passing check.
Exit status is zero when at least one speaker count passes and the safety
controller is ready. A `limited` result admits only the lower passing count.
A blocked installation exits with status one and a reason in `resourceSafety`.

## Reproduce the original noisy-track regression

For local development, first prepare both models and compile the worker thread
limit shim as described in the Dockerfile. Set `NEEDLE3_LIB_PATH`,
`CASSINI_WHISTLE_CPU_SHIM` and optionally `CASSINI_VAD_MODEL` to those local files:

```sh
PYTHONPATH=. uv run python scripts/benchmark_noise.py tests/assets/jfk.wav \
  --weights models/whistle.cact --background keyboard --dbfs -40
```

This script prints fixture captions for debugging; automatic installation checks
only report numerical recognition/error metrics. Neither accesses call audio.
