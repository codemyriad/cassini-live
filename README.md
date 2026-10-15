# Cassini Live

Live captions for Nextcloud Talk, running on your own server.

Turn on **Show captions** in Talk's call menu. Cassini Live receives each
speaker's audio and sends captions back to Talk, with their name attached.
Audio stays on your infrastructure. There is no speech API subscription,
saved transcript or recording to clean up afterward.

I want captions to be useful without making the rest of the server miserable.
If the machine cannot keep up, Cassini Live should refuse to caption.

## Where it stands

**0.7.0 is a development release.** It adds a choice of speech engine, including
two optional, more accurate NVIDIA Nemotron engines downloaded on demand, on top of
automatic recovery after resource pressure, worker handoffs that prefer pauses in
speech, and an optional dashboard inside the call. Captions and the dashboard have run in a real Talk browser call
with recorded speech. Validation in everyday meetings is still needed.

* One speech engine serves the whole server. Administrators choose it under
  **Administration settings → Assistant → Cassini Live**. There is
  no separate Cassini Live page in Nextcloud any more.
* Whistle is the bundled default. It runs on the CPU and needs about 2 GiB of
  container memory. It supports seven languages: English, German, French,
  Spanish, Italian, Dutch and Polish. Broad accuracy testing across these
  languages is still missing.
* Two optional NVIDIA Nemotron engines are more accurate on our English
  meetings (8.9% and 12.8% word errors, against 19% for Whistle). Nemotron
  English is English only; Nemotron Multilingual covers the same seven
  languages. Each needs 2.25-3 GiB of container memory and twice that on the
  host. They are not in the image: Cassini Live downloads one (about 450 MiB)
  when an administrator chooses it, and rolls back if it does not pass the
  capacity check. See [choose a speech engine](docs/install.md#choose-a-speech-engine).
* The Nemotron model weights have their own licenses, separate from Cassini
  Live's AGPL: the NVIDIA Open Model License for Nemotron English, which an
  administrator must accept, and OpenMDW-1.1 for Nemotron Multilingual.
* Speech detection filters silent and noisy tracks before recognition.
  Ten unmuted microphones do not reserve ten recognizers, though receiving and
  checking their audio still costs resources.
* Speakers share recognition time fairly, with bounded queues and waits.
* Before accepting calls, a bundled recording measures how many simultaneous
  speakers the installation can handle, among ten open audio tracks.

The service requires enforced CPU, memory and process limits, with swap disabled.
It refuses unsafe configurations before loading recognition workers. During use,
resource pressure or repeated failures stop captions. After resources recover,
it waits, retests and becomes available automatically. Repeated failures make
retries less frequent; container restarts preserve that delay. The operating
system enforces the limits even if the application misbehaves. An interrupted
call may need captions toggled back on once the service is ready.
See [the safety policy and capacity test](docs/capacity.md).

An optional [diagnostic video](docs/diagnostic-video.md) shows resource usage and
measured caption headroom inside a call, including its own overhead. We enable
it on our deployments; other installations default to off.

## Try it

You need Nextcloud with Talk's High-performance backend, AppAPI, a deploy daemon
and Linux with cgroup v2 and PSI pressure statistics. The image includes
Whistle and runs offline after installation. Only an optional Nemotron engine
needs a download from GitHub, when an administrator chooses it. An administrator must supply the
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
`docker build -t cassini-live:0.7.0 .`.

This project is substantially AI-assisted. Automated checks cover the audio
pipeline, signalling, WebRTC and resource refusal; they do not establish
production readiness or replace human review.

[Verification](docs/verification.md) · [Architecture](docs/architecture.md) · [Privacy](docs/privacy.md) ·
[Third-party notices](NOTICE.md) · [AGPL-3.0-or-later](LICENSE)
