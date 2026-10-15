# Privacy

Cassini Live receives participants' call audio through the Talk HPB when a
participant requests captions. It processes the call's available audio tracks;
caption opt-in controls receipt of text, not individual speakers' processing.
Tell participants that server-side captioning is active according to your
organisation's meeting practices.

Audio is held in bounded memory buffers and subprocess pipes. Recognised text
is sent through the existing HPB to participants who enabled captions.
Cassini Live writes neither recordings nor transcripts to disk and exposes
neither caption history nor room lists in its status API. Participants' clients
may retain displayed text; other installed Talk features have their own policies.

The app calls your Nextcloud for lifecycle, settings form registration, public
URL and signalling settings, and connects to your HPB and configured STUN/TURN services.
TLS certificate validation remains enabled. Authentication failures never print
the configured AppAPI secret. Default logs contain lifecycle and error events,
not recognised text or audio. Native core dumps are disabled in the container.

Container builds download Python packages, Whistle weights/native engine and
HaRP's FRP client. Runtime images bundle these assets and set HF_HUB_OFFLINE,
NEEDLE_TELEMETRY=0 and DO_NOT_TRACK=1. Model calls use explicit weights and the
included native library; no runtime model-registry request is needed. Local
development may download assets if they have not been prepared yet.

No external STT provider, LLM, analytics endpoint or translation service is used.

The optional Nemotron engines are not in the image. When an administrator
chooses one in the administration settings, the app downloads its model from the
sherpa-onnx release on github.com, which redirects to
objects.githubusercontent.com. The request carries no call data, user data or
credentials. To show whether a download is possible, the app also sends a small
`HEAD` request for each model that is not yet downloaded: once when the service
is enabled and once when an administrator chooses an engine. Nothing else
contacts GitHub at runtime.

The engine settings are stored in the app's persistent storage. They record the
chosen engine and, for the NVIDIA Open Model License, the accepted license
version and the time of acceptance. They store no user id: AppAPI does not tell
the app who saved the form. The app reads its own settings values from
Nextcloud every 3 seconds while it is enabled, to notice a new choice.

The Silero speech detector is bundled and runs locally. ONNX Runtime telemetry is
disabled with `ORT_DISABLE_TELEMETRY=1` before its import and in the image, plus
the runtime API opt-out. Capacity assessment uses only a bundled public-domain
recording and generated noise. It retains numeric measurements in memory, not
call audio or transcripts, and never joins a Talk room.
