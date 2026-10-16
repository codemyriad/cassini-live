# Load, admission and capacity

Cassini Live 0.8.0 decides how many people it captions from the load it sees now,
per speaker, at the moment they start talking. It no longer runs a capacity test
before captions start: an engine is ready as soon as it has loaded (a Nemotron
load takes 1.2-1.9 s from a warm disk). The design and the provenance of every
number below are in [the 0.8.0 proposal](proposals/0.8.0-instant-admission.md).

## The kernel protects the call

The container's cgroup v2 limits are what keep captions from hurting the call,
and Linux enforces them whatever the application does:

* `cpu.max` caps the CPU the container can use.
* A low `cpu.weight` (we run 10 against Talk's and Nextcloud's 100) makes the
  kernel take CPU from the caption engine first when the host is contended.
* `memory.max` with swap off bounds memory. The engine host runs with
  `oom_score_adj=1000`, so if the container runs out, the kernel kills the
  engine, not Talk and not the service.
* `pids.max` bounds processes and threads.

So the service only requires that these limits exist. Before loading an engine
it checks, every 15 seconds while something is missing:

* finite CPU, memory and PID limits, and swap disabled;
* the engine's memory floor and the worker or speaker ceiling (table below);
* that the engine fits: the memory the container cannot reclaim, plus the
  engine's measured size and its allowance for streams and batches, must stay
  under `memory.max`. Page cache does not count, because the kernel reclaims it
  before killing anything; tmpfs pages do count, since with swap off nothing can
  take them.

Unlimited or unreadable configurations leave the API running with captions
blocked and the reason shown, with "Administrator action required". A start that
does not fit is reported the same way and is not counted as a failure.

These are **advice** only and never block captions: a CPU quota above half the
host's CPUs, a memory limit above half the host's RAM, and a PID limit above
256. `/status` lists them under `resourceSafety.advice`, and the settings page
shows them as warnings. Pressure statistics (PSI) are used when present and are
no longer required.

## Admission per speaker

When a speaker starts a segment of speech, before waiting for a Whistle worker
or a Nemotron speaking slot, the load governor decides from figures it already
holds (it reads no file on this path):

1. A speaker held off after a shed or a refusal waits out the hold-off.
2. A speaker who already has a seat is always admitted. Closing admission never
   takes captions away from someone who has them.
3. The first speaker is always admitted.
4. Otherwise the speaker is refused when every seat is taken (the ceiling),
   when memory headroom is below two speakers' allowance, when the container's
   CPU stall over the last second is at or above the threshold, or when the
   speakers already captioned trail their speech by the lag threshold or more.

A refused speaker waits for a seat and then a slot within one bounded wait
(2 s with Whistle, 4 s with Nemotron, both together). Waiting speakers are
served first come, first served: a seat freed by a closing track, a reopen or
a tick goes to the earliest of them, and a newcomer queues behind them. If no
seat frees in time, that piece of speech is dropped and counted
(`capacityWaits`), and the speaker's caption
preview shows **"Captions paused for this speaker while the server catches up"**
(at most once a minute). A refusal for lag or CPU also holds that speaker off
for 10 seconds; pieces dropped during a hold-off are counted separately
(`pausedDrops`), since nothing was waited for. After a refusal for lag or CPU, admission stays closed until
the lag has been under half the threshold, and the CPU stall under half its
threshold, for five seconds.

Rooms are not counted: a room opens whenever captions are ready and the
audio-track budget has room, and its speakers go through admission like any
other. The old Whistle two-room cap is gone.

## Open microphones

Every open microphone in a captioned call is one audio track, and the service
receives at most **24** at once across all rooms (it was 10, which left half of
a 20-person call unheard). A participant over the budget goes without captions
until a track frees; the room and everyone else keep theirs.

A track costs a little CPU and memory whether or not its participant speaks:
WebRTC receive, Opus decoding and the voice gate. Speech detection was measured
at 0.0045 CPU-s per second of audio; with Opus and aiortc estimated at
0.01-0.02 cores, that is 0.0145-0.0245 cores per track, 0.35-0.6 cores for 24.
A local loopback run (no SFU, one laptop CPU) measured 0.025-0.03 cores and
0.6-0.9 MiB per added track: 0.6 cores and 23 MiB for 24 tracks. The Nemotron
turn-taking harness passed at least 16 participants on 2 CPUs. The limit is
**provisional** until a staging call measures per-track CPU and memory.

Tracks are not seats: only speakers are admitted to a Whistle worker or a
Nemotron slot. With Nemotron, each participant who has spoken keeps a native
engine stream until their track closes, and the engine holds at most 12 (the
memory floor below allows 12 × 16 MiB). In a call where more than 12 different
people speak, the 13th one's speech is refused ("Too many open caption
streams") until a speaker who has spoken leaves.

## Shedding

Once a second the governor checks the sustained lag: the lowest of the
per-second worst lags over the last five seconds, so a brief decode spike
averages out and a growing backlog does not. When it reaches the shed
threshold and at least two seat holders are speaking, the newest of them is
paused: its segment ends normally and keeps its text, its worker or slot is
freed, it is held off for 10 seconds and its preview shows the paused notice.
A shed also closes admission for lag, with the same reopen rule as a refusal.
At most one speaker is paused every five seconds; a single speaker is never
paused; nothing is paused while a measurement runs. The other speakers and
rooms are not touched. After the hold-off the paused speaker asks for a seat
again like a new speaker. A speaker shed again within 30 seconds of being
re-admitted waits twice as long as the time before (20 s, 40 s, up to 60 s),
so two speakers who do not fit together do not alternate every 20 seconds; 30
seconds with its seat resets this. A shed mark that finds the speaker's
segment already over is dropped: with no lease there is no backlog to relieve.

## Thresholds

| Threshold | Value | Status |
|---|---|---|
| Admit while lag below | 1.0 s | provisional, derived from the 1.3× test harness |
| Shed at sustained lag | 2.0 s | provisional; kept under the 4 s stale-audio cut |
| CPU stall that closes admission | 20% of the last second | unjustified until the staging stress run |
| Hold-off after a shed or refusal | 10 s | derived: two 5 s windows |
| Repeat shed doubles the hold-off within | 30 s of re-admission | the design's staging criterion "shed twice within 30 s" |
| Longest hold-off | 60 s | unjustified; one paused-notice interval |
| Seat kept while silent | 15 s | unjustified; longer than the 8 s segment cap |
| Paused notice at most every | 60 s | unjustified |

While the thresholds are provisional, admission and shedding **observe only**:
every speaker is admitted, nothing is paused, and `/status` reports under
`resourceSafety.admission` (`observeOnly: true`) what would have been refused or
shed. The staging lag measurement replaces the provisional values and records
the date (`load.Thresholds.measured_on`); only then do refusals and pauses
happen. Measuring is described in [verification](verification.md).

## Rules per speech engine

Every number below comes from one table in `cassini_live/catalog.py`. *W* is the
number of workers (Whistle) or simultaneous speakers (Nemotron).

| Rule | Whistle | Nemotron English / Multilingual |
|---|---|---|
| Process layout | One worker process per simultaneous speaker | One shared engine process; one stream per speaker |
| Container memory floor | 384 MiB + 192 MiB × *W* | 2.25 GiB (2304 MiB), whatever *W* is |
| Recommended container memory | 2 GiB | 3 GiB |
| Host RAM (advice) | Twice the container limit | Twice the container limit (4.5 GiB or more) |
| Minimum CPU quota | 2 CPUs (1 CPU stalls) | 1 CPU |
| Recognizer threads for *C* quota CPUs | 2 per worker | max(1, min(4, ⌊*C*⌋ − 1)); one CPU stays free for the service |
| Speaker ceiling (seats) | min(4, max(1, ⌊*C* / 2⌋)) | min(4, 2 × threads): two per thread, from the harness |
| Example: 2 / 4 CPUs | 1 / 2 speakers | 2 / 4 speakers |
| Memory needed free to load | 192 MiB per worker | measured engine size (model 1024 / 1056 MiB before the first load) + 12 streams × 16 MiB + 4 × 45 MiB batch + 128 MiB |
| CPU instructions (x86_64) | any | AVX2 |
| Free disk to download | none (bundled) | about 1.3 GiB, once |
| Load timeout | 30 s | 120 s |

`CASSINI_LIVE_WORKERS` lowers the ceiling (with Nemotron, the number of
seats). A value above the CPU ceiling is clamped to it: the service logs a
warning, the settings page shows it as advice on the engine, and captions run
with the ceiling's count. Whistle hands a busy worker to a waiting speaker
after two seconds of audio, preferring a pause in speech; Nemotron keeps a slot
for one segment (at most 8 s) and never preempts.

## Crashes and recovery

Only two things stop captions everywhere:

* **An OOM kill** in the container (`memory.events` `oom_kill` grows).
* **The failure circuit**: three engine or worker deaths within a minute.

A single engine death restarts the engine in place in about two seconds;
streams reconnect at the next speech and no room closes. A late answer from an
engine that is still alive (for example starved under `cpu.weight` 10) loses
only that request: the speaker's stream is reopened at the next speech. A host
that has said nothing for two request timeouts (10 s) counts as dead.

When captions stop, every caption room closes, the warning `Captions paused,
closing all rooms:` is logged with the reason, and the service waits before
loading again: two minutes, doubling per failure up to an hour, plus 0–20%
jitter. It loads at the deadline itself, with no further wait. Ten minutes of
running resets the failure count. Before any engine process starts, restarts
included, the service refuses to load into a container the kernel has
OOM-killed since the engine was last ready.

A small recovery journal records the retry deadline before every load and that
an engine is running while it serves, so a process killed while loading or
serving counts as a failure and waits out the back-off after a restart. It
lives in `APP_PERSISTENT_STORAGE` (or `/home/cassini/.local/state`). An
unwritable or corrupt journal blocks model loading as a configuration problem.
It contains no call identifiers or audio. A journal left by 0.7.x whose reason is
one of the trips 0.8.0 removed (the capacity check, CPU quota stalls, host CPU
or memory pressure, a lagging event loop, changed limits, an exhausted memory or
PID budget) starts 0.8.0 without that cooldown; the first start rewrites the
journal so a later restart cannot match it again. A cooldown after an OOM kill,
the failure circuit or a crash while running or loading is honoured, and a load
that an upgrade interrupted is not counted as a crash.

Disabling the app cancels a load or a measurement. Existing caption rooms closed
by a crash are not rejoined automatically; participants may need to turn
captions off and on.

## Measure capacity

An administrator can still measure how many simultaneous speakers the server
handles: **Measure capacity** on the Cassini Live settings page, or from the
container:

```sh
docker exec nc_app_live_transcription python -m cassini_live.capacity --installed --measure
```

It needs ready captions and no captioned calls; calls asking for captions while
it runs (up to two minutes) get 503 with Retry-After. One through four
simultaneous speakers play an 11-second English sample twice among ten open
tracks (the others carry fan, hiss and keyboard-like noise) at **1.3× playback
speed**, through the same speech detection, queues and engine as calls. A count
passes when processing keeps a 1.2× real-time margin, every speaker gets a
first caption within three seconds, word errors stay at most 25%, and nothing
is dropped. The result (speakers passed and the lag measured at each count) is
shown on the settings page and in `/status` → `capacityAssessment`. It is
information only: admission never reads it, and nothing runs it automatically.

## Watch the live signals

```sh
docker exec nc_app_live_transcription python -m cassini_live.capacity --installed --watch 600
```

prints one JSON line per second: control lag, its 5-second sustained minimum,
caption age, p95 request latency, container CPU stall, engine duty, memory use
and headroom, the drop, wait and host-restart counters, the seats, the last
refusal and shed, and the cost of the governor's tick (`tickMs`) and of the
whole monitor tick (`monitorTickMs`, budget 5 ms). A failed read prints a line
with an `error` field and the run goes on.

`--installed` alone reports the current state; add `--wait 600` to wait up to
ten minutes for a pending recovery. Exit status is zero when the service is
ready.

## Test an image before installing

Run on the intended server with the intended container constraints, when the
extra workload will not compete with live captions:

```sh
docker run --rm --network none --cpus=4 --memory=2g --memory-swap=2g --pids-limit=128 \
  --log-driver=local --log-opt max-size=10m --log-opt max-file=3 \
  --entrypoint python ghcr.io/codemyriad/cassini-live:0.8.0 \
  -m cassini_live.capacity --workers 2
```

This standalone mode loads the engine under the same rules, then runs the
measurement. No Nextcloud credentials are needed. Exit status is zero when the
service became ready; a blocked installation exits with status one and the
reason in `resourceSafety`.

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
