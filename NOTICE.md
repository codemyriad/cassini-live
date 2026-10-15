# Third-party provenance

Cassini Live is licensed under AGPL-3.0-or-later, like Cassini.

The Talk transport in `cassini_live/talk/client.py`, `types.py`, `audio_stream.py`
and `constants.py` is adapted from [nextcloud/live_transcription](https://github.com/nextcloud/live_transcription),
revision `517ddadc95c7c93974891731e56af8b8ecdd5464` (version 2.1.3).
Copyright 2025 Nextcloud GmbH and Nextcloud contributors. Original SPDX notices
are retained. Changes: package imports, speech-engine adapter, discovered HPB URL,
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
[Cactus-Compute/whistle](https://huggingface.co/Cactus-Compute/whistle), Apache-2.0.

The optional Nemotron engines run on
[`sherpa-onnx==1.13.8`](https://github.com/k2-fsa/sherpa-onnx) and its
`sherpa-onnx-core` wheel, Apache-2.0 (`LICENSES/Apache-2.0.txt`). The core wheel
bundles its own copy of ONNX Runtime (`libonnxruntime.so` 1.28.2, MIT). The
manylinux wheels also vendor alsa-lib 1.2.12 (`sherpa_onnx.libs/libasound-*.so.2.0.0`),
LGPL-2.1-or-later (`LICENSES/LGPL-2.1.txt`); its source is at
<https://github.com/alsa-project/alsa-lib/releases/tag/v1.2.12>. The sherpa-onnx
extension links it dynamically, so it is loaded with the engine; Cassini Live does
not modify it and never opens an audio device.

The Nemotron model weights are **not** in the image. They are downloaded only when
an administrator chooses a Nemotron engine, from the `asr-models` release of
k2-fsa/sherpa-onnx, into the app's persistent storage. Each extracted file is checked
against the SHA-256 below, and the license text (and notice) is stored next to it.

* **Nemotron English**: [nvidia/nemotron-speech-streaming-en-0.6b](https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b),
  sherpa-onnx export `sherpa-onnx-nemotron-speech-streaming-en-0.6b-560ms-int8-2026-04-25.tar.bz2`.
  NVIDIA Open Model License (version of 2025-10-24,
  `LICENSES/NVIDIA-Open-Model-License.txt`), shown with its full text on the
  settings page. Notice: "Licensed by NVIDIA Corporation under the NVIDIA Open Model
  License" (`LICENSES/NVIDIA-Open-Model-License-NOTICE.txt`).
  * `encoder.int8.onnx` `7d932213491ad355c6e5576705dc3494731a52af87d7a1b954559340147909d8`
  * `decoder.int8.onnx` `0be9702c2f427a2b6bb241d298e0d3836a558de1f5b9fd3018f1cce6e2b3fa98`
  * `joiner.int8.onnx` `a35eac38a22ebceb04d230ed7afe0d68f446ba6914a036b97f14fece95967e23`
  * `tokens.txt` `dc0b4584ab2e4ddbf888425c076c61b736e7356a015250db7d307e6f1a8188ff`
* **Nemotron Multilingual**: [nvidia/nemotron-3.5-asr-streaming-0.6b](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b),
  sherpa-onnx export `sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11.tar.bz2`.
  OpenMDW-1.1 (`LICENSES/OpenMDW-1.1.txt`). Origin notice: "nvidia/nemotron-3.5-asr-streaming-0.6b
  by NVIDIA Corporation, exported to ONNX by the k2-fsa/sherpa-onnx project. Licensed
  under OpenMDW-1.1."
  * `encoder.int8.onnx` `012e9321373af99021415e0b0eb3ec827b4be3153be6f30d9b448fe65e896e68`
  * `decoder.int8.onnx` `19f9c98fc6d0a2c33a65a43b36fdb2e914c26c0aa9764be3aebc502a1e982fb0`
  * `joiner.int8.onnx` `4101c7c679a0bc30483794b27a059e34e79232aa2068d78d51231a22c8b0d7ce`
  * `tokens.txt` `729cc103155bafa785f9cd45746cd41cabe97eab7182fc04d594129587958f8a`

These model licenses are separate from Cassini Live's AGPL-3.0-or-later. The pins
live in `cassini_live/catalog.py`.

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
