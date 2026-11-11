# Known limitations

What Cassini Live does not do, or does only partly, as of 0.11.0 and the first
App Store release (not yet published). Each entry says who it affects and the
workaround, if there is one. Add an entry when a limitation is found; remove it
in the pull request that lifts it.

Cassini Live installs in one of two ways, and some entries apply to only one:

- **App Store** (`cassini_live`): installed from the Nextcloud App Store.
- **Native** (`live_transcription`): registered by hand with
  `occ app_api:app:register`, in place of Nextcloud's own Live Transcription.

## Starting and stopping captions

- **App Store: Talk's Show captions button does not start Cassini Live.**
  Talk sends that button only to the app id `live_transcription`, which
  Nextcloud's own Live Transcription app holds on the store. People turn
  captions on from Cassini Live's transcript panel instead. If Nextcloud's
  app is also installed, Talk's button keeps driving it.
- **App Store: each person adds the transcript panel once per browser.** It
  is a bookmarklet, picked up at
  `/index.php/apps/app_api/proxy/cassini_live/panel/`.
- **App Store: guests cannot start or stop captions, or open the transcript
  panel.** The panel and its routes need a Nextcloud login, so people who
  joined from a public link are left out. Workaround: a logged-in participant
  turns captions on. In a native install, guests use Talk's button as usual.
- **App Store: with Talk open in two tabs, a tab that is not in the call can
  turn captions on for itself.** Talk reports whether a user is in the call,
  not each of their sessions, so the check cannot tell a user's tabs apart.
  Captions go only to that user's own session; nobody else is affected.
- **The transcript panel starts captions with automatic language detection,**
  not the conversation's caption language set in Talk. Once a call's captions
  are running, later starts keep the language they began with.
- **App Store: at most 10 caption on/off requests a minute per user.** More
  are refused for a short wait. This protects the server, since each start
  connects to Talk's signalling server.

## Installation

- **Native: replaces Nextcloud's Live Transcription.** Both use the app id
  `live_transcription`, so only one can be installed, and the native install
  does not come from the App Store. The App Store install has neither
  restriction.
- **Talk's High-performance backend is required**, with its internal secret
  entered once: on Cassini Live's settings page after installation, or in the
  deploy options where Nextcloud still offers them. Nextcloud 34 and later do
  not show deploy options when installing from the Apps page. A value set in
  deploy options takes precedence, and the settings page cannot change it.
- **Linux with cgroup v2 and enforced CPU, memory and process limits, swap
  off.** Without them Cassini Live stays blocked and does not load an engine.
  See [install](install.md).
- **The Nemotron engines need outbound access to dist.gocassini.com** when an
  administrator chooses one: the models (about 450 MiB) are not in the image.
  Whistle, the default, runs offline.
- **Nextcloud 33 to 35 are declared, but no automated test covers each
  version.** Each release is checked by hand on a disposable Nextcloud (see
  [install](install.md#check-before-a-release)).

## Captions

- **No translation.** Captions are in the spoken language; Talk's translated
  captions are refused.
- **Seven languages:** English, German, French, Spanish, Italian, Dutch and
  Polish. Nemotron English is English only. Accuracy across the non-English
  languages has not been broadly tested.
- **One speech engine for the whole server.** Administrators choose it; there
  is no per-room or per-user choice.
- **At most 24 open microphones and 64 captioned calls per server.** When the
  server cannot keep up, the newest speaker's captions pause for a few seconds
  while the others continue. See [capacity](capacity.md).
- **No saved transcript.** The panel's catch-up for late joiners lives in
  memory for the current call only, and is lost when the call's captions end or
  the app restarts. Cassini, the recording app, keeps transcripts.
- **After an out-of-memory kill or repeated engine crashes, captions stop**
  and come back automatically after a wait. A call that was interrupted may
  need captions turned off and on again.
- **The optional diagnostic video is visible to everyone in the call.** It is
  off by default; administrators can use the [diagnostics page](diagnostics-page.md)
  instead.
