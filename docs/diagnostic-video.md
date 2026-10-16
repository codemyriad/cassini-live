# Diagnostic video

Set `CASSINI_LIVE_DEBUG_VIDEO=1` in AppAPI deploy options. When somebody enables
captions, a diagnostic video publisher joins the first captioned room. Everyone
in that room can see it. It shows how Cassini Live is working and the names of
that room's speakers, never audio or caption text. Codemyriad deployments enable
this option; other installations default to off.

## What it shows

The feed alternates every 20 seconds between two views.

**Speakers.** The header names the engine and the container's CPU and memory
limits, with a status pill: *Captions live*, *Speakers waiting*, *Audio lost* or
*Holding new voices*. Below it, three numbers: voices heard now, speaking seats
taken out of the seat ceiling (with "holding: lag" and the like when admission
is closed), and how far captions trail speech, on a 0–3 s bar marked at the
admit and shed thresholds.

One lane per open microphone scrolls two minutes of history: speech that was
captioned, speech held waiting for a seat, speech paused by shedding (amber
stripes) and audio lost. The speaker's display name sits over the lane's
oldest end. A tag beside each lane shows the slot (Nemotron) or scribe
(Whistle) it holds and that speaker's own lag, or *held*, *paused* or *idle*.

Under the lanes a graph plots the caption lag over the same two minutes against
dashed admit and shed lines, with a mark where a speaker was (or would have
been) paused and a tick for every caption line sent. The decision line spells
out the governor's last decision and its inputs, for example
"admit Bruno Keller: seat 2 of 4 · lag 0.3 s < 1.0 · stall 2% · 1.8 GiB free".
The footer shows memory against its limit (with the level at which captions
pause), CPU stall, how busy the engine is, the 95th-percentile reply time and
the tile's own cost.

While the governor only observes (0.7.3), the pill reads *Would hold new
voices*, decisions read "would admit" or "would hold", an *observe only* badge
sits in the header, and no lane is ever paused.

**How a caption is made.** The pipeline in five steps, each with live numbers:
microphones and voices, speech pieces and the queue, seats and admission, the
engine (busy share, threads, slots, reply time, memory) and captions per
minute. Three panels below say what protects the call (the kernel's CPU cap,
CPU weight and memory limit), the lag rules that keep captions useful, and what
counts as a crash, with the engine restarts and out-of-memory kills since the
container started.

## Names

Talk's call events carry no display names, so the app reads the room's
participant list from Talk as the Nextcloud user who enabled captions there, at
most once every 10 seconds, and only while the diagnostic video is enabled.
Names reach the video for the room that hosts it. Lanes from other captioned
rooms read *other call*, and the decision line calls their speakers "someone in
another call". Guests show as "Guest · name". When the list cannot be read the
lanes read *Participant*; captions are unaffected. Names are never logged.

## Cost and limits

There is at most one video publisher per installation. It sends 960×540 VP8 at
one frame per second, matching the governor's one-second tick, with a keyframe
every four frames and at each view change, so a late joiner waits at most four
seconds. Locally, rendering and encoding cost 9–13 ms of CPU per frame (about
1–1.5 % of one CPU) and 55–65 kbit/s for an unchanging picture. At 0.5 fps the
whole publisher measured 1.5–4.9 % of one CPU on staging; the 1 fps figure
there is still to be measured. It uses one codec thread and a low-priority
child process. The child receives only metrics and this room's speaker names
through a bounded pipe. It neither requests participant media nor reads
Docker's socket. The host's `/proc` counters must describe the host; nested
containers may expose only their enclosing VM.

The overlay reports CPU and resident memory for the whole diagnostic child
(rendering, encoding, signaling and WebRTC), sampling CPU time in the caption
process, and RTP payload uplink. Network headers, SFU forwarding and viewers'
decoding are outside that figure. Container totals include the diagnostic child.

Before starting, the app reserves its full 128 MiB budget below the 70%
container-memory threshold. It checks that its owning caption room still exists
on every update.

The optional process stops if it exceeds 128 MiB RSS or stays above 15% of one
CPU for six seconds, if the caption memory budget reaches 70%, or if captions
stop being ready. Its address space is capped at 768 MiB. It does not restart
in a loop. A failed feed stays off until its owning caption room closes and a
later room starts. It also stops when its parent closes the pipe or stops
heartbeats. Every stop logs `Diagnostic video ended (…)` with its reason.
The normal container limits still apply to the entire application.

This is diagnostic video, not a recording or a separate monitoring service.
Turning it off requires changing the deploy option and restarting the ExApp.
