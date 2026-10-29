# Cassini Live

Live captions for Nextcloud Talk, running on your own server.

Turn on **Show captions** in Talk's call menu. Cassini Live receives each
speaker's audio and sends captions back to Talk, with their name attached.
Audio stays on your infrastructure. There is no speech API subscription,
saved transcript or recording to clean up afterward.

I want captions to be useful without making the rest of the server miserable.
The kernel's limits keep the call safe; if the machine cannot keep up, Cassini
Live captions fewer speakers rather than everyone late.

## Where it stands

**0.8.0 is a development release.** It offers a choice of speech engine, including
two optional, more accurate NVIDIA Nemotron engines downloaded on demand,
per-speaker admission on live load, worker handoffs that prefer pauses in
speech, and an optional dashboard inside the call. Captions and the dashboard have run in a real Talk browser call
with recorded speech. Validation in everyday meetings is still needed.

0.8.0 implements the [instant-admission design](docs/proposals/0.8.0-instant-admission.md).
Captions start as soon as the engine has loaded: there is no capacity test on
every start, switch or restart any more. Each speaker is admitted when they
start talking, from live caption lag, memory headroom and CPU stall; when
captions fall behind, the newest speaker is paused instead of every room
closing; only an OOM kill or repeated engine crashes stop captions. Until the
staging lag measurement fixes its thresholds, admission observes only and
reports what it would do under `resourceSafety.admission` in `/status`.

* One speech engine serves the whole server. Administrators choose it under
  **Administration settings → Assistant → Cassini Live**. The **?** next to the
  section title opens an admin-only settings page with every engine's details,
  live download and switch progress, cancel, model removal, licenses, the live
  speaker count and an on-demand capacity measurement.
* Whistle is the bundled default. It runs on the CPU and needs about 2 GiB of
  container memory. It supports seven languages: English, German, French,
  Spanish, Italian, Dutch and Polish. Broad accuracy testing across these
  languages is still missing.
* Two optional NVIDIA Nemotron engines are more accurate on our English
  meetings (8.9% and 12.8% word errors, against 19% for Whistle). Nemotron
  English is English only; Nemotron Multilingual covers the same seven
  languages. Each needs 2.25-3 GiB of container memory and twice that on the
  host. They are not in the image: Cassini Live downloads one (about 450 MiB)
  when an administrator chooses it, and rolls back if it does not load. See
  [choose a speech engine](docs/install.md#choose-a-speech-engine).
* The Nemotron model weights have their own licenses, separate from Cassini
  Live's AGPL: the NVIDIA Open Model License for Nemotron English and
  OpenMDW-1.1 for Nemotron Multilingual. The settings page shows each license
  and its full text; there is no acceptance step.
* Upgrading from 0.7.0: 0.7.1 adds admin-only proxy routes. Update with
  `occ app_api:app:update live_transcription` (or reinstall); AppAPI re-reads
  routes only on a version change.
* Speech detection filters silent and noisy tracks before recognition.
  Ten unmuted microphones do not reserve ten recognizers, though receiving and
  checking their audio still costs resources.
* Speakers share recognition time fairly, with bounded queues and waits.
* An administrator can measure how many simultaneous speakers the installation
  handles, with a bundled recording among ten open audio tracks. The result is
  shown, never used as a gate.

The service requires enforced CPU, memory and process limits, with swap disabled,
and refuses to load an engine that would not fit. The operating system enforces
the limits even if the application misbehaves. An OOM kill or repeated engine
crashes stop captions; the service waits, with longer waits after repeated
failures (container restarts preserve them), and becomes available again
automatically. An interrupted call may need captions toggled back on.
See [load, admission and capacity](docs/capacity.md).

Changes in 0.10.0:

* The transcript panel catches up late joiners: the app keeps a bounded
  in-memory transcript of the current call and serves it, to participants only,
  from `GET /history?token=<roomToken>` (any logged-in member of the call). The
  panel shows the earlier lines, a "You joined here" divider, then the live
  continuation. The transcript is per-call: a new call in the same conversation
  starts fresh, and the buffer is dropped when the call's caption session ends.
  The panel mounts as a Talk sidebar tab and is scoped to the current call.

Changes in 0.9.0:

* New transcript panel for Talk calls, delivered as a bookmarklet and served by
  the app at `/panel/` (any logged-in user): a scrollable, speaker-grouped live
  transcript that also shows your own lines. Pick up the bookmarklet on
  `/index.php/apps/app_api/proxy/live_transcription/panel/`. See [ui/](ui/).

Changes in 0.8.2:

* `CASSINI_LIVE_WORKERS` is gone: Cassini Live sizes itself from the CPU limit;
  a leftover value is ignored. See [upgrading to 0.8.2](docs/install.md#upgrading-to-082).

Changes in 0.8.1:

* Calls with many participants: up to 24 open microphones per server (was 10),
  and a Nemotron speaker's engine stream closes after a minute of silence, so
  any 12 people can speak within a minute instead of only the first 12.
* The diagnostic video lists the people who spoke in the last two minutes,
  with a count of silent microphones.

Changes in 0.8.0:

* No automatic capacity check: a start, engine switch, rollback or restart is
  ready in load time instead of up to two minutes.
* Per-speaker admission and lag-driven shedding (observe-only until measured).
* Removed trips: CPU quota stalls, host CPU pressure, a lagging event loop,
  memory or PID use above 80%, low host memory, changed limits and the capacity
  check no longer close every room; the governor holds or pauses speakers
  instead. Only an OOM kill or repeated engine crashes still do.
* Half-host CPU and memory and the PID count are advice, not requirements; PSI
  is optional. Whistle is no longer capped at two rooms.
* A late answer from a live engine loses one request instead of counting as a
  crash.
* A 0.7.x recovery journal and settings are read as they are. A 0.7.x cooldown
  from one of the removed trips, and a capacity-check verdict, are dropped on
  the first start; a cooldown after an OOM kill, repeated crashes or a crash
  while running or loading is still waited out.

An optional [diagnostic video](docs/diagnostic-video.md) shows, inside a call,
how captions are made: each speaker's lane under their name, the caption lag,
admission decisions and resource use, including its own overhead. We enable it
on our deployments; other installations default to off.

## Try it

You need Nextcloud with Talk's High-performance backend, AppAPI, a deploy daemon
and Linux with cgroup v2 (PSI pressure statistics are optional: without them
admission does not use the CPU-stall input). The image includes
Whistle and runs offline after installation. Only an optional Nemotron engine
needs a download (from dist.gocassini.com), when an administrator chooses it. An administrator must supply the
HPB internal secret.

Follow the [installation instructions](docs/install.md). There is no published
App Store release. Talk currently fixes the caption provider ID to
`live_transcription`, so this replaces the official caption ExApp. They cannot
run together. [Cassini](https://github.com/codemyriad/gocassini) can stay installed
for recordings.

## Development

With Python 3.12 and [uv](https://docs.astral.sh/uv/):

```sh
uv sync --frozen
uv run python scripts/prepare_vad.py
uv run pytest -m 'not integration'
```

For real recognition tests, run `uv run python scripts/prepare_model.py`, then
`uv run pytest`. Models go in ignored `models/`. Build the container with
`docker build -t cassini-live:0.9.0 .`.

This project is substantially AI-assisted. Automated checks cover the audio
pipeline, signalling, WebRTC and resource refusal; they do not establish
production readiness or replace human review.

[Verification](docs/verification.md) · [Architecture](docs/architecture.md) · [Privacy](docs/privacy.md) ·
[Third-party notices](NOTICE.md) · [AGPL-3.0-or-later](LICENSE)
