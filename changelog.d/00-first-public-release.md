### Added
- First public release. Earlier versions, up to 0.11.0, were internal builds; this entry sums up what they built.
- Live captions for Nextcloud Talk calls, recognised on your own server. Each speaker's words appear under their Talk name. Audio and captions stay on your infrastructure, and nothing is saved once a call's captions end.
- A transcript panel beside the call, opened from a bookmark. It starts captions, lets you scroll back, and shows people who join late what was said before they arrived. It needs a Nextcloud login; guests can't use it.
- A choice of speech engine for the whole server. Whistle is bundled, runs on a CPU and recognises English, German, French, Spanish, Italian, Dutch and Polish. Nemotron English and Nemotron Multilingual are more accurate (8.6% and 12.8% word errors on our English meetings, against 19% for Whistle) and are downloaded when an administrator chooses one (425 and 435 MiB).
- An administrator settings page: every engine's details, download and switch progress, model removal, licenses, the live speaker count and an on-demand capacity measurement.
- An administrator diagnostics page with live server and caption metrics.
- Protection for the call under load. Cassini Live needs enforced CPU, memory and process limits on its container and refuses to load an engine that would not fit. After an out-of-memory kill or repeated engine crashes it waits and recovers by itself. Per-speaker admission from live caption lag and memory headroom is measured and reported, but not yet enforced.
