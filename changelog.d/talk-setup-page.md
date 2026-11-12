### Added
- The settings page has a **Talk connection** section for the HPB internal secret and an optional HPB URL override, and checks that the HPB accepts the secret. Nextcloud 34 and later deploy App Store ExApps from the Apps page without asking for deploy options, so the secret can now be entered after installation. As in Cassini, a value set in deploy options takes precedence and cannot be changed from the page. The saved values live in `cassini-live-talk.json` on the persistent volume; the secret is write-only and never returned by the API. Saving connects to Talk again without a restart.

### Changed
- When the internal secret is missing, the status points to the settings page instead of deploy options.
