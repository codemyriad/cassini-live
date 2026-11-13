# Privacy

Cassini Live receives participants' call audio through the Talk HPB when a
participant requests captions. It processes the call's available audio tracks;
caption opt-in controls receipt of text, not individual speakers' processing.
Tell participants that server-side captioning is active according to your
organisation's meeting practices.

Audio is held in bounded memory buffers and subprocess pipes. Recognised text
is sent through the existing HPB to participants who enabled captions.
Cassini Live writes neither recordings nor transcripts to disk. It keeps a
bounded in-memory transcript of the **current call** so that someone who joins
late can catch up on what was said; this is served only to participants of that
conversation (membership is checked against Talk as that user, and a yes is
remembered for 30 seconds) and is dropped when the call's caption session
ends. The status API still exposes neither caption history nor room lists.
Participants' clients may retain displayed text; other installed Talk features
have their own policies.

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
chosen engine, a change in progress and the last error only. The app reads its
own settings values from Nextcloud every 3 seconds while it is enabled, to
notice a new choice.

The admin-only diagnostics page shows how captions are made without joining a
call. It samples one room for names: the oldest call with captions on. It shows
that room's speakers' names only to an administrator who is a participant of
its Talk conversation, whether or not they have joined the call. The app checks
this on each poll by asking Talk for the room's participants as that
administrator, and remembers a yes for 30 seconds and a no for 10; everyone
else reads "Participant". Lanes from other rooms read "other call", and their
participant lists are not read. The participant list itself is read as the
Nextcloud user who most recently enabled captions there and at most once every
10 seconds, but only while such a participant has polled the page in the last
30 seconds. Thirty seconds after the last authorized poll the app drops its
hold on the names. Nothing the page shows is logged or written to disk, and its
answers carry anonymous lane numbers, never a room token, session id or
participant id. With no participant of the sampled conversation watching the
page, no list is read.

The admin-only settings page is served by the app through AppAPI. With each of
its requests AppAPI sends the administrator's user id, and forwards the
browser's Nextcloud cookies. The app uses the id only to reject requests without
a user, and neither stores nor logs the id or the cookies. While open, the page
polls the app every 2 to 10 seconds.

The diagnostics page is served the same way. The app uses the administrator's
user id to reject requests without a user and to ask Talk whether they are a
participant of the sampled conversation. It keeps the id only in memory, as
the key of those cached answers, drops it with the answer at the first check
after the answer expires, and neither writes nor logs the id or the cookies.
While open in a visible browser tab, the page polls the app once a second, and
the app samples only while the page was polled in the last 30 seconds.

The Silero speech detector is bundled and runs locally. ONNX Runtime telemetry is
disabled with `ORT_DISABLE_TELEMETRY=1` before its import and in the image, plus
the runtime API opt-out. Capacity assessment uses only a bundled public-domain
recording and generated noise. It retains numeric measurements in memory, not
call audio or transcripts, and never joins a Talk room.
