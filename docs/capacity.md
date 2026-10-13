# Check caption capacity without a call

Cassini Live 0.6.0 measures capacity automatically after startup, using the actual
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
seconds of heavy CPU throttling, event-loop delay or host CPU pressure.
The CPU check distinguishes frequent brief quota enforcement from substantial
stalls: at least 80% of periods throttled and 200 ms throttled time per elapsed
second. Missing severity counters retain frequency-only refusal. Event-loop
lag above 500 ms and host CPU PSI `some avg10` at least 20% are separate triggers. Three worker failures within a minute also stop captions. Excess speaking
demand drops audio locally and reports load; it does not restart the service.
Audio queues, wait times, input tracks and rooms are bounded.

Recovery is automatic. The supervisor waits at least two minutes after failure,
with exponential backoff up to an hour plus 0–20% jitter. A failed capacity
assessment waits at least 15 minutes. Before reloading models, it requires three
continuous minutes with memory/PIDs below 60%, spare host memory for workers,
and readable, low host/container pressure (PSI `some avg60`: CPU below 20%, memory below
1%). Missing PSI is reported as a configuration problem (enable PSI in the
kernel and expose its host and cgroup counters). A pressure spike restarts that healthy window. Recovery attempts use the
same bounded capacity test; failure counts reset only after 30 minutes ready.

A small recovery journal records the retry deadline before model loading. It
survives process/container restarts, preventing an OOM restart from bypassing
the cooldown. The app uses `APP_PERSISTENT_STORAGE`, or
`/home/cassini/.local/state` when unset. Preserve that directory across container
recreation if your deploy daemon does not provide persistent storage. An
unwritable or corrupt journal blocks model loading and is reported as a
configuration problem. The journal contains no call identifiers or audio.

Missing limits remain blocked, with cheap checks every 15 seconds. Corrected
limits are detected automatically, and default worker counts are recalculated.
Explicit worker overrides must still fit the budget. Disabling the app cancels
loading, calibration and recovery; a disabled app starts no workers. If Nextcloud
or Talk is still starting, connection discovery retries with a 15–60 second
backoff without loading models until the connection and enabled state are known.

After recovery, new caption requests work without administrator intervention.
Existing caption rooms are closed on overload and are not automatically rejoined;
participants may need to turn captions off and on. This avoids immediately
recreating the same meeting load. No call subscriptions are saved to disk.

Room teardown must finish before workers reopen. If it cannot finish within
30 seconds, the caption process exits so the container supervisor can reap its
remaining processes. Its persisted cooldown still applies on restart.

This protects resources allocated to this container; it cannot
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
- **Not established / interrupted / error:** no capacity claim is made. Captions remain unavailable. Transient failures retry automatically; configuration problems report the required correction.

The status page separately warns about recently dropped live audio. The measured speaker count limits concurrent recognition leases.
A historical passing result never overrides a current resource refusal. Results are
kept in memory and remeasured on service restart. No call content is stored.

## Inspect recovery

To see the current assessment and recovery status:

```sh
docker exec nc_app_live_transcription python -m cassini_live.capacity --installed
```

This command only reads status. It does not restart workers, recalibrate or
change the retry budget, and it works while calls are active. By default it
reports pending recovery immediately; add `--wait 600` to wait up to ten minutes.
A check already in progress is awaited for up to 150 seconds. The JSON contains
no credentials or call content. Exit status is zero when the service is ready.

The startup check is mandatory, including after an upgrade. The old
`CASSINI_CAPACITY_CHECK` setting no longer disables it.

## Test an image before installing

Run on the intended server with the intended container CPU/memory constraints,
when the additional workload will not compete with live captions:

```sh
docker run --rm --network none --cpus=4 --memory=2g --memory-swap=2g --pids-limit=128 \
  --log-driver=local --log-opt max-size=10m --log-opt max-file=3 \
  --entrypoint python ghcr.io/codemyriad/cassini-live:0.6.0 \
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
