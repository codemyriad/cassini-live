# Diagnostic video

Set `CASSINI_LIVE_DEBUG_VIDEO=1` in AppAPI deploy options. When somebody enables
captions, a diagnostic video publisher joins the first captioned room. Everyone
in that room can see aggregate server metrics. It never shows audio, transcripts,
participant names or room names. Codemyriad deployments enable this option;
other installations default to off.

The dashboard leads with three numbers: voices heard now, how many more voices
fit now, and captions per minute. Below them, one anonymous lane per open
microphone scrolls two minutes of history: speech that was captioned, speech
waiting for a recognizer ("scribe"), and speech discarded. A tag beside each
lane shows which scribe it holds now, and a strip under the lanes marks every
caption line sent. The footer shows the tile's own cost and the caption
container's memory and CPU against their limits, with the memory level at which
captions pause. Lane numbers are assigned inside the caption process and never
derived from sessions or participants. Spare voices come from the installation's actual calibration,
minus current worker leases. They are instantaneous headroom, not a promise of
more meetings: participants can start speaking at any time, and all rooms share
the ten-track limit. We don't extrapolate additional capacity from idle CPUs.

There is at most one video publisher per installation. It uses 960×540 VP8 at
one frame every two seconds, one codec thread and a low-priority child process.
The child receives only aggregate metrics through a bounded pipe. It neither
requests participant media nor reads Docker's socket. The host's `/proc` counters
must describe the host; nested containers may expose only their enclosing VM.

The overlay reports CPU and resident memory for the whole diagnostic child
(rendering, encoding, signaling and WebRTC), sampling CPU time in the caption
process, and RTP payload uplink. Network headers, SFU forwarding and viewers'
decoding are outside that figure. Container totals include the diagnostic child.

Before starting, the app reserves its full 128 MiB budget below the 70%
container-memory threshold. It checks that its owning caption room still exists
on every update.

The optional process stops if it exceeds 128 MiB RSS or sustains more than
15% of one CPU, if the caption memory budget reaches 70%, or if captions stop
being ready. Its address space is capped at 768 MiB. It does not restart in a
loop. A failed feed stays off until its owning caption room closes and a later
room starts. It also stops when its parent closes the pipe or stops heartbeats.
The normal container limits still apply to the entire application.

This is diagnostic video, not a recording or a separate monitoring service.
Turning it off requires changing the deploy option and restarting the ExApp.
