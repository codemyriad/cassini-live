### Added
- A start/stop captions route for the transcript panel, `POST /captions/transcribe`, for installs whose app id Talk's caption button does not reach. It takes the body Talk sends to `/api/v1/call/transcribe` and re-checks, as the caller, what Talk would have: the caller's own session in the room (and in its call, to start), CSRF, and a rate limit.
