### Added
- The two translation routes of Talk's caption provider API, `GET /api/v1/translation/languages` and `POST /api/v1/translation/set-target-language`. Cassini Live does not translate, and they say so the way Talk understands (`550`).
- Routes that stand in for Talk while Talk cannot call this app id (nextcloud/spreed#20296): `POST` and `DELETE /mock_talk_proxy/live-transcription/{token}`. They make Talk's checks, then send the native caption API the request Talk would send. The transcript panel starts captions through them.

### Changed
- The native caption API (`/api/v1/...`) now answers as Nextcloud's `live_transcription` app does: errors as `{"error": ...}`, the same status codes and the same messages. An unsupported language is `400`, no longer `422`. The language `auto` stays, and stays the default.

### Deprecated
- `POST /captions/transcribe`. It still works for panels already open during an upgrade and will be removed in a later release.
