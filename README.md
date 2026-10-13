# Cassini Live

Live captions for Nextcloud Talk, running on your own server.

Turn on **Show captions** in Talk's call menu. Cassini Live receives each
speaker's audio and sends captions back to Talk, with their name attached.
Audio stays on your infrastructure. There is no speech API subscription,
saved transcript or recording to clean up afterward.

I want captions to be useful without making the rest of the server miserable.
If the machine cannot keep up, Cassini Live should refuse to caption.

## Where it stands

**0.4.0 is a development release.** The earlier 0.3.0 pipeline has produced
captions in a real Nextcloud Talk browser call. This version adds mandatory
resource checks and overload shutdown; those changes have automated tests
and still need validation in real meetings.

* Whistle runs on the CPU. A GPU does not accelerate this engine.
* Seven languages: English, German, French, Spanish, Italian, Dutch and Polish.
  Broad accuracy testing across these languages is still missing.
* Speech detection filters silent and noisy tracks before recognition.
  Ten unmuted microphones do not reserve ten recognizers, though receiving and
  checking their audio still costs resources.
* Workers share speaking time fairly, with bounded queues and waits.
* Before accepting calls, a bundled recording measures how many simultaneous
  speakers the installation can handle, among ten open audio tracks.

The service requires enforced CPU, memory and process limits, with swap disabled.
It refuses unsafe configurations before loading recognition workers. During use,
resource pressure or repeated failures stop captions until an administrator
reruns the check. The operating system enforces the limits even if the application
misbehaves. See [the safety policy and capacity test](docs/capacity.md).

## Try it

You need Nextcloud with Talk's High-performance backend, AppAPI, a deploy daemon
and Linux with cgroup v2. The image includes the models and runs offline after
installation. An administrator must supply the HPB internal secret.

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
`docker build -t cassini-live:0.4.0 .`.

This project is substantially AI-assisted. Automated checks cover the audio
pipeline, signalling, WebRTC and resource refusal; they do not establish
production readiness or replace human review.

[Verification](docs/verification.md) · [Architecture](docs/architecture.md) · [Privacy](docs/privacy.md) ·
[Third-party notices](NOTICE.md) · [AGPL-3.0-or-later](LICENSE)
