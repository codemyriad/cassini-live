# Verification for 0.6.0

## Diagnostic video

Release checks: 114 tests passed; Ruff, manifest validation, source packaging
and the amd64 container build passed.

Local checks include a real WebRTC sender/receiver, bounded VP8 packets at
realistic timestamps, one-publisher admission, cancellation during process
reaping, memory refusal, and last-human departure with an extra internal
publisher. Staging browser tests decoded the 960×540 dashboard in Talk.
The isolated publisher measured roughly 2–5% of one CPU, 98–99 MiB RSS and
92 kbit/s payload uplink. Those numbers exclude SFU fan-out and viewer decoding.

Live speech exposed a false positive in the old CPU guard: 90–100% of quota
periods were throttled, but for only 70–104 ms per second. The revised guard
uses throttled duration and separately checks host CPU pressure and event-loop
lag. Hard CPU, memory, no-swap and PID limits are unchanged. Claude reviewed
the design and the correction; the diagnostic lifecycle findings were addressed.
The final review also caught a backend URL mismatch for installations using an
internal Nextcloud address, and a missing host-pressure check before worker
loading. Both were fixed with regression coverage.

The final staging run recovered through the saved cooldown and passed capacity
calibration without intervention. With diagnostics enabled, a Talk browser
received 63 caption updates and decoded 30 dashboard frames during 60 seconds
of recorded speech. The service stayed ready, then removed both internal
participants when the last human left. On that four-CPU host with a two-CPU
caption quota, host CPU PSI `some avg10` peaked at 6.62%. Sampled event-loop lag
stayed below 1 ms. Steady publisher cost was 1.5–4.9% of one CPU, about 98 MiB
RSS and 91–93 kbit/s; its first startup sample briefly reached 31% CPU.
The parent sampler used 1.1–3.0 ms per update. This is one short fixture call,
not a long meeting or a multi-room load test.

## Pause-aware handoffs

Release checks: 94 tests passed; Ruff and manifest validation passed.

The boundary tests check sample preservation, FIFO speaker handoffs, rejection
of brief dips and the two-second audio budget after partial-chunk handoffs.
These checks establish scheduling behavior, not recognition accuracy.

`scripts/benchmark_boundaries.py` compares fixed and pause-aware handoffs using
one real Whistle worker and the bundled English recording at three timing
offsets. Run locally with:

```sh
PYTHONPATH=. NEEDLE3_LIB_PATH="$PWD/models/libneedle3.so" HF_HUB_OFFLINE=1 \
  uv run python scripts/benchmark_boundaries.py
```

The initial run produced identical transcripts for both policies: word error
rates were 18.18%, 18.18% and 22.73% across the offsets. Only the last offset
used a scheduling pause. No accuracy improvement is established by this sample.
Contention is simulated; this is not a live latency or capacity test. Broader
recordings, languages and speech around the 15-second limit still need evaluation.

## Automatic recovery

Local checks on Linux amd64, 13 October 2026:

* 88 tests passed. New checks cover cooldown and continuous headroom, failed
  calibration, process-restart persistence, stale worker returns, cancellation
  during preload, configuration correction, missing PSI and delayed Nextcloud/Talk
  startup. Excess demand no longer restarts captions.
* A constrained container ran the real Whistle capacity check, stopped and reaped
  its worker under simulated memory pressure, then automatically loaded a new
  worker and passed the real capacity check again. Only the test's retry timings
  were shortened; the production defaults remain two-minute initial backoff and
  three-minute sustained headroom. CPU/RAM/PID limits stayed enforced throughout.
* Desktop/mobile status-page checks covered automatic recovery and disabled states,
  as well as refusing to show an old passing measurement as current readiness.
* Claude reviewed the design before implementation and reviewed the code again.
  The implementation addresses its teardown, stale-worker, retry, configuration
  and startup-discovery findings.

These changes have not been deployed to the live Nextcloud installation. Recovery
makes new requests available automatically; interrupted calls may need captions
toggled back on. End-to-end recovery behavior in real Talk meetings still needs
validation.

## Earlier 0.4.0 checks


Local checks on Linux amd64, 13 October 2026:

* 71 tests passed, including real Whistle workers, local WebRTC/Opus,
  signalling, noise gating, scheduling and resource refusal.
* An unrestricted Docker container refused to load recognition workers.
* With a four-CPU quota, 2 GiB RAM, no swap and 128 PIDs, the offline capacity
  check admitted two simultaneous speakers among ten continuously feeding
  tracks. Both 27-second scenarios ran at 1.3× playback speed, completing in
  20.77 and 21.18 seconds. First captions arrived in about 0.9 seconds.
  There were no noise captions, dropped chunks, worker failures or capacity
  timeouts. Sample word error rate was 13.64%.
* With a quarter-CPU quota, the controller stopped the check for sustained
  resource pressure and exited unsuccessfully, with no admitted speakers.
* The status page was checked in a browser at desktop and mobile widths,
  including a blocked service with a previous passing measurement.
* Manifest validation, lint and source packaging passed.

The 0.3.0 pipeline was previously exercised in a Nextcloud 34 browser call with
one recorded speaker and nine unmuted clients sending synthetic keyboard noise.
Only the speaking participant produced captions. That is evidence for the
pipeline, not validation of 0.4.0 in production. The safety changes have not
been deployed to that live installation.

These are short fixture tests. Microphone quality, echo, other languages,
long meetings, ARM64 and other Nextcloud versions still need validation.
