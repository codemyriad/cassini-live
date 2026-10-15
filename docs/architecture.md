# Architecture and verification

```text
Talk's caption menu → Nextcloud Talk → authenticated AppAPI request
                                           ↓
                                 Cassini Live / room manager
                                           ↓
                                HPB internal signalling client
                                           ↓
                                  WebRTC / per-speaker audio
                                           ↓
                                 16 kHz mono float32 audio
                                           ↓
                             Silero VAD + bounded speech queue
                                           ↓
         leased exclusive Whistle worker, or a Nemotron speaking slot
                                           ↓
                           committed text + replaceable pending text
                                           ↓
                        HPB transcript messages to opted-in participants
                                           ↓
                                  native Talk caption display
```

`cassini_live/talk/client.py` adapts Nextcloud's AGPL transport at the revision
recorded in `NOTICE.md`. It handles session mapping, offers, ICE candidates,
participant events and caption targeting. The surrounding application owns
room creation, authentication, enable/disable, worker readiness and teardown.
Its original translation hooks are inert and no translation capability is
advertised. Native API endpoints are not exposed as browser proxy routes.

The process pool preloads Whistle before the server finishes startup. The
image bakes pinned weights and native libraries at build time. Whistle's
streaming state is global and not thread safe, so every simultaneous speaker
has an exclusive subprocess. Workers stay loaded and are reused after the
stream's `end`/`done` exchange. Failure kills and replaces that process, with a circuit breaker after three
failures within a minute.

The pinned native engine has no C API thread setting. Its homogeneous-CPU
fallback creates up to twelve threads per worker, using the host CPU count
rather than container capacity. `native/cpu_limit.c` bounds only the processor
count queries, leaving other `sysconf` calls unchanged. The container builds
this bridge and preloads it **only into worker subprocesses**, with
`CASSINI_WHISTLE_THREADS=2` by default, further capped by process affinity.
The API, WebRTC and HaRP processes are unaffected. Remove this bridge when a
supported native thread-control API is available. Integration tests verify the
actual loaded worker's thread count. Local development without
`CASSINI_WHISTLE_CPU_SHIM` retains the engine's own threading defaults.

PyAV resamples decoded audio while preserving channels; NumPy averages the
channels afterward. This avoids the equal-power stereo downmix gain that can
clip identical microphone channels. The capture queue holds at most six
chunks of at most one second. Silero VAD v6.2.1 checks 512-sample (32 ms) frames
using one shared ONNX CPU session with one inference thread, and separate recurrent
state/context for every audio track. Two of three frames above 0.5 probability
start a segment; 0.35 keeps an existing segment active. The gate retains 320 ms
of pre-roll and ends after 608 ms of non-speech, or after one second without
received frames. Its pinned model is included at build time; runtime never downloads it.

A full queue discards its oldest chunk. Queued chunks older than four seconds are also
discarded, including a second age check after waiting for a worker. Sequence
numbers force recognition to flush across gaps rather than splice discontinuous
audio. Capture continues while a failed recognizer is replaced. Workers are
leased only for speech, with two-second bounded capacity waits; exhausting
actual resource pressure closes caption rooms through the safety controller.
Excess speaking demand drops audio without restarting the service. FIFO futures directly hand returning workers to
waiting speakers, including safe return of leases if cancellation races handoff.
With waiters, a recognizer prefers a VAD pause already buffered between one and
two seconds of supplied audio, otherwise it cuts at two seconds. The remainder
keeps its original age and waits its turn; no audio is replayed. This bounds
audio per turn under contention, not elapsed inference/finalization time.
Uncontended segments prefer a pause after 14 seconds and end at 15 seconds
(within one VAD frame). `/status` reports `streamEnds` counters for natural
pauses, duration limits, scheduling pauses, forced handoffs and audio gaps.
Stop flushes partial audio and the recogniser's final words;
transport teardown drains captions before sending `bye`, subject to timeouts.

## Speech engines

An administrator chooses one engine for the whole server in **Administration
settings → Artificial intelligence → Cassini Live**. The form is an AppAPI
declarative settings form. AppAPI sends the app no event when an administrator
saves it, so `settings_sync.py` reads the stored value every three seconds and
drives `engine_switch.py`. Nextcloud cannot grey out an option or refresh the
form by itself. The app therefore writes eligibility and progress into the
option labels and the status text, and registers the form again when they
change (at most every three seconds, and in 5% download steps). An invalid choice
is written back to the active engine, with the reason in the status text.
`admin_form.py` builds the form from plain data and has no side effects.

A switch checks eligibility and the license, downloads and verifies the model
while the old engine keeps serving, closes caption rooms as on an overload
pause, loads the new engine and runs the capacity check. Any failure rolls back
to the previous engine. The choice is saved in the app's persistent storage and
restored at startup, after the model files are verified again.

The Whistle path is unchanged: one worker subprocess per simultaneous speaker,
as described above, with two-second handoffs.

The Nemotron engines run in **one shared engine-host subprocess**. The supervisor
never imports `sherpa_onnx`; only `cassini_live/engine_host.py` does.

```text
supervisor (FastAPI, aiortc, Silero VAD, safety)        engine host subprocess (oom_score_adj=1000)
  Transcriber(track) --open/audio/commit/finish-->       one sherpa_onnx.OnlineRecognizer (model loaded once)
                     <--opened/result/closed (id)--     one OnlineStream per speaking track
                                                         decode loop: ready streams -> decode_streams(batch)
```

The host is a separate process so that the kernel kills it, and not the
supervisor, when memory runs out. It also keeps inference off the event loop and
keeps sherpa-onnx's ONNX Runtime apart from the one Silero uses.

The stream protocol is JSON lines on stdin and stdout, with a stream id in
every message and exactly one reply per request:

| Request | Reply | Effect |
|---|---|---|
| `open` (id, language) | `opened` | New stream. The multilingual model gets the room language or `auto`. |
| `audio` (id, at most 1 s of float32 PCM) | `result` (pending text) | Decodes every ready stream in one batch, at most four. |
| `commit` (id) | `result` (final text) | Ends a caption line after a pause in speech. The stream stays open. |
| `finish` (id) | `result` (final text) | Adds 1.2 s of silence, ends the input and frees the stream. |
| `cancel` (id) | `closed` | Frees the stream without decoding. |

A stream stays open across pauses in speech and ends only when its track stops
or changes language. A per-stream error closes that stream only; the death of
the host counts towards the same three-failures-a-minute circuit breaker as
Whistle workers. Admission uses speaking slots: their number is the measured
`testedSpeakers`, and there is no two-second preemption. Nemotron English
advertises only English (and `auto`, which means English) to Talk.

The Nemotron models are downloaded on demand into
`APP_PERSISTENT_STORAGE/models/<engine>/`, pinned by the SHA-256 of every
extracted file, and stored with their license and notice files. The image
contains only Whistle.

## Verification boundaries

- Unit/protocol tests: authentication without credential logging, API shapes,
  automatic languages, enable/disable, concurrent same-room requests, failed
  joins, stereo downmix, partial/final text, final flush, language restart,
  exclusive worker leases and replacement.
- Local WebSocket fixture: actual HPB hello authentication, participant session
  mapping, caption recipients, full reconnect and final-caption-before-bye order.
- Local WebRTC fixture: real Opus audio exchange between peer connections,
  received through the Talk transport and fed to the caption adapter.
- Recorded-audio integration: real Whistle speech and silence in separate
  processes, automatic language detection, no cross-speaker state, reuse and
  process reaping. No microphone or hosted inference is used.
- Container build and offline model smoke test: bundled model and engine load
  without registry access. The image is tested on the local amd64 host.
- Settings form: unit tests build the form from fixed engine data and check
  labels, defaults, status text and value parsing. No browser test covers it.

Staging validation on Nextcloud 34 and its Talk HPB verified HaRP certificate
installation, lifecycle, the former embedded status page, and a browser call with recorded
speech producing native Talk captions. A user also confirmed live captions.
Other supported server versions, ARM64 runtime and broad multilingual accuracy
remain separate checks.

`PYTHONPATH=. uv run python scripts/benchmark_meeting.py tests/assets/jfk.wav --weights models/whistle.cact`
runs a paced ten-track meeting through real VAD and Whistle (set
`NEEDLE3_LIB_PATH` to the native engine). Add `--speakers 2` for overlap.
This fixture bypasses WebRTC; it is not a substitute for a real Talk call.

## Capacity assessment

`cassini_live.capacity` exercises the actual gate, queues and preloaded worker
pool using the bundled public-domain fixture. Startup runs the assessment in
the background while `/status` and the settings form show `checking`. Caption requests receive HTTP 503 until the workload passes and the resource
controller admits calls. Admission then uses the measured speaking budget. The compatibility wake endpoint rejects browser users
and active/in-flight caption rooms; it cannot force a reload or bypass cooldowns. Simulated counters do not contaminate live
call health. Results remain in memory and are remeasured after restart.

The standalone CLI uses its own pool for testing an image before installation.
The installed CLI reads status without creating an additional competing pool. See [assessment criteria](capacity.md).

## Automatic recovery

One supervisor owns model loading, calibration, ready-state monitoring and
recovery. Cheap headroom checks precede any expensive retry; exponential backoff
is persisted before loading models. Disable cancels this supervisor and awaits
cleanup. Worker generations reject stale returns after a restart. Room close
tasks remain tracked and shielded from caller cancellation, and pending joins
recheck their room generation before accepting subscriptions.

The design was reviewed with Claude before implementation. The review identified
teardown leaks, stale worker returns, restart backoff loss and excess-demand
flapping as prerequisites to automatic recovery. Tests cover those paths. See
[the policy and operational limits](capacity.md).
