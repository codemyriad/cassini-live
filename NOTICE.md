# Third-party provenance

Cassini Live is licensed under AGPL-3.0-or-later, like Cassini.

The Talk transport in `cassini_live/talk/client.py`, `types.py`, `audio_stream.py`
and `constants.py` is adapted from [nextcloud/live_transcription](https://github.com/nextcloud/live_transcription),
revision `517ddadc95c7c93974891731e56af8b8ecdd5464` (version 2.1.3).
Copyright 2025 Nextcloud GmbH and Nextcloud contributors. Original SPDX notices
are retained. Changes: package imports, Whistle adapter, discovered HPB URL,
Cassini secret/name, bounded queues, transcript timeout logging, flush ordering,
reconnect teardown/session preservation, inactive video negotiation, and ICE
configuration without implicit STUN, and bounded audio-track admission.
Translation hooks remain inert for compatibility with the transport; translation
is neither advertised nor connected to any provider.

Whistle's Python wrapper and native engine are supplied by
[`cactus-needle==3.2.0`](https://github.com/cactus-compute/needle), Apache-2.0.
The native engine version is 3.3.0, as selected by that wrapper. Model and engine
repositories are pinned in `scripts/prepare_model.py`; the image includes a
manifest with their revisions and SHA-256 checksums. Model weights come from
[Cactus-Compute/whistle](https://huggingface.co/Cactus-Compute/whistle).

HaRP's FRP client comes from Nextcloud/HaRP revision
`e28dcb5872267a20af101f882778527523aa6849`, version 0.61.1, Apache-2.0.

The Cassini orbit icon comes from the sibling `gocassini` repository (AGPL-3.0).

Speech gating uses [Silero VAD v6.2.1](https://github.com/snakers4/silero-vad),
MIT, revision `7e30209a3e901f9842f81b225f3e93d8199902b1`. The model is pinned
and checksum-verified in `scripts/prepare_vad.py`; its notice is in
`LICENSES/silero-vad.txt`. The NumPy state/context adapter follows upstream's
ONNX input contract. ONNX Runtime supplies CPU inference (MIT).

The bundled capacity fixture is an excerpt of John F. Kennedy's 1961 inaugural
address (public-domain US federal government material); see
`cassini_live/fixtures/README.md`. Synthetic backgrounds are generated locally.
