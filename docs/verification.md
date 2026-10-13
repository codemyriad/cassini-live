# Verification for 0.4.0

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
