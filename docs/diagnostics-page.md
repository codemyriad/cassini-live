# Diagnostics page

Administrators can watch how Cassini Live is working on a web page served by
the app:

```
/index.php/apps/app_api/proxy/live_transcription/diagnostics/
```

The Status section of the Cassini Live settings page links to it ("Live
diagnostics"), and the page links back. It shows what the
[diagnostic video](diagnostic-video.md) shows, but you don't have to be in a
call, and no deploy option is needed. Both views are on screen at once, update
once a second, and the text can be selected and copied into a bug report. The
video is unchanged and works independently of the page.

## What it shows

**Status.** The header shows a status pill, the engine with the container's
CPU and memory limits, and whether the numbers are live, stale (the last answer
is 5 seconds old or more) or out of reach. The pill reads, in order of
precedence:

- *Starting*, while there is no sample yet
- *Captions not ready* with the service state (blocked, recovering or disabled)
- *No captions running*, when no call has captions on
- the video's states: *Audio lost*, *Holding new voices* (or *Would hold new
  voices* while the governor only observes), *Speakers waiting*, *Captions live*

**Speakers.** The same three numbers as the video: voices heard now, seats taken
out of the ceiling (with the hold reason when admission is closed), and how far
captions trail speech on a 0–3 s bar marked at the admit and shed thresholds.
Below them is one lane per recent speaker, covering two minutes:

- the lanes show captioned, held, paused and lost speech in the video's
  colours, which also differ in lightness
- each lane carries a name chip over its oldest end, and a tag with the slot
  or scribe it holds and its own lag
- the "+N silent mics · +M more speakers" counts follow the title

Lanes follow the video's rules: at most ten, recent speakers only, in a stable
order (see [Diagnostic video](diagnostic-video.md#what-it-shows)). Under the
lanes are the lag graph, with dashed admit and shed lines, pause marks and a
tick per caption line, then the governor's last decision with its time in UTC
(as the video draws it), and a footer with memory against its limit, CPU
stall, engine busy share, reply p95 and the page's own sampling cost.

**How a caption is made.** The five pipeline steps with live numbers, and the
three panels of rules: what protects the call, the lag rules, and what counts
as a crash.

A line at the bottom reports the diagnostic video: off, or its state and, while
it publishes, its own CPU, memory and bit rate. It never names the video's
room. If a sample fails, the page says so, names only the error's type, and
keeps showing the last good numbers.

## How it works

The page polls the feed, `diagnostics/api/feed`, a second after each answer.
The first poll wakes the feed: a one-second ticker whose sampler reads the same
state as the video's sampler, with the same code. The feed keeps the latest
sample and two minutes of one-second history columns. It answers each poll with
the sample and the columns newer than the newest one the page has. The page
keeps its own copy of the two minutes and draws both panes from it.

```
administrator's browser            caption service process
┌──────────────────────┐           ┌──────────────────────────────────────┐
│ diagnostics page     │           │ feed route (ADMIN)                   │
│  its own copy of the │   GET     │  is the admin a participant of the   │
│  two minutes; both   │ api/feed  │  sampled conversation? (asked of     │
│  panes drawn from it │ ────────► │  Talk as them)                       │
│                      │ ◄──────── │  touch(), then read(since)           │
└──────────────────────┘ JSON, 1/s └──────────────────┬───────────────────┘
                                                      │ sampling goes on
                                                      ▼ while polled < 30 s
                                   ┌──────────────────────────────────────┐
                                   │ DiagnosticFeed                       │
                                   │  1 Hz ticker, the latest sample and  │
                                   │  a ring of 120 one-second columns    │
                                   │   └─► DiagnosticSampler, the video's │
                                   │       code: engine, seats, rooms,    │
                                   │       lanes, memory, CPU             │
                                   └──────────────────────────────────────┘
```

The page and the video run separate samplers over the same state, so their
numbers agree, and running both costs one more sample a second. While the
video runs, the page samples the video's room for names; otherwise neither
affects the other.

## Names

The page samples one room for names: the diagnostic video's room while the
video runs, otherwise the oldest call with captions on. You see that room's
speakers' names only if you are a participant of its Talk conversation; you
need not have joined the call. On each poll the app checks this by asking Talk
for the room's participants as you, the same check the transcript panel's
catch-up uses; a yes is remembered for 30 seconds and a no for 10. Everyone
else sees "Participant" in the lanes and "a speaker" in the decision line.
Lanes from other calls read *other call* for everyone, and the decision line
calls their speakers "someone in another call".

Participant lists are read from Talk only while a participant of the sampled
conversation has polled the page in the last 30 seconds, or while the video
runs in that room. As for the video, the list is read as the Nextcloud user who
most recently enabled captions there, at most once every 10 seconds. The names
are kept in memory only for as long as that lasts, and dropped when it ends or
the page moves to another room. Names are never logged. Each name is cut to 40
characters (20 emoji). The feed carries anonymous lane numbers, never a room
token, session id or participant id.

## Cost

Nothing is sampled until an administrator opens the page. The footer shows the
sampler's own CPU time per sample as "Sampling". The 0.6.0 sampler, before
lanes and names, used 1.1–3.0 ms per update on staging in one short call
([verification](verification.md)); the current sampler has not been measured
there. Thirty seconds after the last poll the ticker stops and drops its two
minutes of history and its hold on names, so a page opened later fills its
lanes from that moment.

A hidden browser tab stops polling, and polls again at once when shown. Each
answer carries only the history columns the page doesn't have yet: up to 120
on the first poll, then about one per second. The largest answer we could
construct (ten lanes in every column, every name 20 emoji, every number at its
widest) measured 46,552 bytes. When the app cannot be reached the page retries
after 2, 4, 8 and 16 seconds, then every 30, and keeps the last numbers on
screen marked stale.

If you lose access while the page is open (you sign out, are no longer an
administrator, or Cassini Live is disabled), AppAPI answers the page's requests
with Nextcloud's "Page not found" page. It cannot tell those causes apart, so
the page names all three ("Nextcloud refused the feed …") and keeps retrying on
the same schedule; it resumes once access is back, for instance after you sign
in again in another tab. Only a redirect, or a 401 or 403 answer (as from the
app's own administrator check), stops polling until you reload.

Lane numbers count from 1 in each Cassini Live process, and a restarted
container keeps the host's monotonic clock. Each answer therefore carries a
random `instance` token. When it changes, or when the clock goes back, the page
drops its history and asks again from the start.

## Access

Every route is a read-only GET at the ADMIN access level in
`appinfo/info.xml`: `diagnostics/` (and its `index.html`, `app.js`, `app.css`)
and `diagnostics/api/feed`. AppAPI refuses anyone else before the request
reaches the app. The page uses the settings page's Content Security Policy
(`script-src 'self'; style-src 'self'; connect-src 'self'`): scripts, styles
and requests only from the origin that serves the page, which is your
Nextcloud server, and no inline code. AppAPI re-reads routes only on a version
change, so upgrade with `occ app_api:app:update live_transcription` (or
reinstall).

The feed, `GET diagnostics/api/feed?since=<seconds>`, returns:

- `sample`: the latest sample
- `columns`: the history columns newer than `since`
- `head` (the newest column's time, 0 when there is none) and `now`, both in
  the app process's monotonic seconds
- `instance`: a random token, new each time the app starts
- `names`: whether names are included
- `video`: the diagnostic video's status
- `error`: the exception type when the last sample failed, otherwise null
- `version`, `window` (120 columns) and `presence` (30 seconds)

The page sends the last `head` back as `since`.
