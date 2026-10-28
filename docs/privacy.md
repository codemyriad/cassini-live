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

When the optional diagnostic video is enabled and running, the app also reads
the Talk participant list of the one room that hosts the video, as the Nextcloud
user who most recently enabled captions there, at most once every 10 seconds.
For the video it keeps the display names in memory only while that video runs,
drops them when it stops or moves to another room (unless the diagnostics page
below still shows them), and shows them only on the diagnostic video in that
same room, over that speaker's lane. Lanes from other rooms read "other call",
and their participant lists are not read. Names are never logged or written to
disk.

The admin-only diagnostics page shows the same picture without joining a call.
It samples one room for names: the video's room while the video runs, otherwise
the oldest call with captions on. It shows that room's speakers' names only to
an administrator who is a participant of its Talk conversation, whether or not
they have joined the call. The app checks this on each poll by asking Talk for
the room's participants as that administrator, and remembers a yes for 30
seconds and a no for 10; everyone else reads "Participant". The participant
list itself is read as for the video, as the user who most recently enabled
captions there and at most once every 10 seconds, but only while such a
participant has polled the page in the last 30 seconds, or while the video runs
in that room. The app samples for the page
only while it is polled: 30 seconds after the last poll it stops, and drops the
two minutes of history and its hold on the names. Nothing the page shows is
logged or written to disk, and its answers carry anonymous lane numbers, never
a room token, session id or participant id. With the video not running and no
participant of the sampled conversation watching the page, no list is read.

The admin-only settings page is served by the app through AppAPI. With each of
its requests AppAPI sends the administrator's user id, and forwards the
browser's Nextcloud cookies. The app uses the id only to reject requests without
a user, and neither stores nor logs the id or the cookies. While open, the page
polls the app every 2 to 10 seconds.

The diagnostics page is served the same way. The app uses the administrator's
user id to reject requests without a user and to ask Talk whether they are a
participant of the sampled conversation. It keeps the id only in memory, as
the key of those cached answers, and neither writes nor logs the id or the
cookies. While open in a visible browser tab, the page polls the app once a
second, and the app samples only while the page was polled in the last 30
seconds.

The Silero speech detector is bundled and runs locally. ONNX Runtime telemetry is
disabled with `ORT_DISABLE_TELEMETRY=1` before its import and in the image, plus
the runtime API opt-out. Capacity assessment uses only a bundled public-domain
recording and generated noise. It retains numeric measurements in memory, not
call audio or transcripts, and never joins a Talk room.
