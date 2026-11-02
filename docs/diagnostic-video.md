# Diagnostic video

Set `CASSINI_LIVE_DEBUG_VIDEO=1` in AppAPI deploy options. When somebody enables
captions, a diagnostic video publisher joins the first captioned room. Everyone
in that room can see it. It shows how Cassini Live is working and the names of
that room's speakers, never audio or caption text. Codemyriad deployments enable
this option; other installations default to off.

Administrators can see the same diagnostics on the
[diagnostics page](diagnostics-page.md), without joining a call or setting this
option. The page leaves the video unchanged, and each works without the other.

## What it shows

The feed alternates every 20 seconds between two views.

**Speakers.** The header names the engine and the container's CPU and memory
limits, with a status pill: *Captions live*, *Speakers waiting*, *Audio lost* or
*Holding new voices*. Below it, three numbers: voices heard now, speaking seats
taken out of the seat ceiling (with "holding: lag" and the like when admission
is closed), and how far captions trail speech, on a 0–3 s bar marked at the
admit and shed thresholds.

One lane per recent speaker scrolls two minutes of history: speech that was
captioned (light cyan), speech held waiting for a seat (a teal outline), speech
paused by shedding (dark amber inside a bright amber edge) and audio lost
(red). The four differ in lightness as well as hue. The speaker's display name sits over the lane's
oldest end. A tag beside each lane shows the slot (Nemotron) or scribe
(Whistle) it holds and that speaker's own lag, or *held*, *paused* or *idle*.

A large call has many open microphones and few speakers, so lanes go to the
people heard in the last two minutes, the same window the lanes draw: a speaker
gets a lane while their voice gate is open or has produced speech in that time,
and always while they hold a slot or scribe, wait for one or are paused. At most
ten lanes are shown, out of up to 24 open microphones (the track limit); past
ten, speakers holding or waiting for a seat stay, then this room's speakers,
then the most recent. Lanes keep the order in which their speakers first spoke
within the window, this room's first, so rows do not move from one second to the
next; a newcomer is added at the bottom. A speaker silent for two minutes loses
the lane. The section title is followed by "+N silent mics" for open
microphones without a lane and "+M more speakers" for recent speakers over ten.
When nobody spoke in two minutes the lanes read "Nobody spoke in the last two
minutes · N mics open".

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
participant list from Talk, as the Nextcloud user who most recently enabled
captions there. For the video it does so only for the room that hosts the
running video and only while it runs, at most once every 10 seconds; the
[diagnostics page](diagnostics-page.md#names) also has the list of the call it
samples read while a participant of that conversation has polled the page in
the last 30 seconds. A participant who joins while a read is in flight is
picked up by one more read once the 10 seconds have passed. The first read as
a given user costs two requests (nc_py_api also reads the server's capabilities
when it switches user); later reads cost one. If Talk refuses that user (403
or 404, for example after they left the room), the app stops asking as them
until the next user enables captions there.

Names reach the video for the room that hosts it and are dropped from memory
when the video stops or moves to another room, unless the diagnostics page is
still showing them to a participant of that conversation. Lanes from other
captioned rooms read *other call*, and the decision line calls their speakers
"someone in another call". Guests show as "Guest · name". When the list cannot
be read the lanes read *Participant*; captions are unaffected. Names are never
logged. Each name is cut to 40 characters and to 240 bytes as it travels to the
video process (20 emoji), which keeps every metrics line well inside the pipe's
8 KiB line limit; a longer line would be skipped, not fatal.

## Cost and limits

There is at most one video publisher per installation. It sends 960×540 VP8 at
one frame per second, matching the governor's one-second tick, with a keyframe
every four frames and at each view change, so a late joiner waits at most four
seconds. Each frame is capped at 32 KiB, two seconds of the 128 kbit/s the
publisher asks for. Frames encode at quantizer 40; a frame over the cap is
encoded again at 48 and then 56 (the busiest lanes view measured 54 KB at 40 and
26 KB at 56), and in the unlikely case it is still too big it is skipped: viewers
keep the previous picture and the next frame is a keyframe. The next view goes
back to quantizer 40. A skipped frame is logged once per video. Locally,
rendering and encoding cost 9–13 ms of CPU per frame (about
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
