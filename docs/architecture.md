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
                              leased exclusive Whistle worker
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
After two seconds of supplied audio, a recognizer with waiters finishes and
releases its lease. This bounds audio per turn under contention, not elapsed
inference/finalization time. Uncontended segments last at most 15 seconds.
Stop flushes partial audio and the recogniser's final words;
transport teardown drains captions before sending `bye`, subject to timeouts.

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
- Status page: desktop and mobile browser render, readiness response, navigation
  link and JavaScript errors. Preview status uses a development fixture.

Staging validation on Nextcloud 34 and its Talk HPB verified HaRP certificate
installation, lifecycle, the embedded page, and a browser call with recorded
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
the background while `/status` and the page show `checking`. Caption requests receive HTTP 503 until the workload passes and the resource
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
