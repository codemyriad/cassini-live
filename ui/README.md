# Cassini Live transcript panel

A scrollable, speaker-grouped live transcript for Nextcloud Talk calls,
rendered from the captions Cassini Live already delivers — including the
viewer's own lines, which Talk's built-in overlay drops.

It mounts as an extra **tab in Talk's right sidebar**, next to Chat /
Participants / Shared items, rather than a floating window, and hides Talk's
native caption overlay while it is on (the sidebar replaces it). Activating it
also turns Talk's live captions on for you, so you are on the feed immediately
without clicking the CC button.

## How it reaches the page

Talk pages ship a strict CSP, so the panel arrives one of two ways:

- **Loader bookmarklet (default).** A ~0.6 KB `javascript:` URL that reads the
  page's CSP nonce and injects `<script src>` for the bundle this app serves at
  `…/apps/app_api/proxy/live_transcription/panel/panel.js`. Same-origin, so the
  script load passes `script-src 'nonce-…' 'strict-dynamic'` and future panel
  fetches stay inside `connect-src 'self'`. Users drag it once; the panel
  updates with the app.
- **Standalone bookmarklet.** The whole bundle in one `javascript:` URL
  (~12 KB). No loading step and no dependency on this app being reachable, at
  the cost of being frozen at the copied version.

People pick both up on the install page the app serves at
`/index.php/apps/app_api/proxy/live_transcription/panel/`.

## How it gets the words

Two sources share **one seq space**, so they merge exactly:

```
 cassini-live buffers every final (all speakers) + joins, each with a seq, and
 sends each final over the HPB tagged with that same seq.

   HPB ─ connection.on('message') {type:transcript, seq, final, message, sid} ─▶ live feed (instant)
   HTTP ─ GET /history?token=<room>&since=<contiguousSeq>  (every ~3s) ─────────▶ gap-fill + joins + catch-up
                         │
            store: reorder buffer over the shared seq
              seq == applied+1 → render, advance, flush held
              seq >  applied+1 → hold (out of order / a gap the poll will fill)
              seq <= applied   → already have it, skip     ─▶ sidebar tab view
   live partials (no seq) ─────────────────────────────────▶ transient italic line
```

The live feed gives near-instant finals; the poll is a backstop that fills any
gap the live feed missed and delivers joins and the initial catch-up. Because
both carry the server's `seq`, the merge is exact — no duplicates, no drops, no
content-matching.

Why read the live feed from `connection.on('message')` and not
`SimpleWebRTC.on('transcript')`: the filtered `transcript` event keeps only
`{id, message, languageId, final}` and **drops** the `seq`, but the raw
`message` event (what Talk's own SimpleWebRTC listens to) carries the full
payload. `window.signalingSocket` is a raw fallback. It also still sees the
viewer's own lines (no `CallParticipantModel` filtering).

`session.js` scopes everything per conversation: it watches the room and call
tokens, resets on a new call or a switch to another conversation (and enables
captions for the session so it is on the feed), and polls `/history` for gaps.
An ended call's transcript stays until the viewer navigates away. Join events
render as a divider per participant — the viewer's own reads "You joined here".

Speaker names resolve in the browser (the payload only carries the speaker's
signalling session id). The authoritative source is `connection.joinedUsers`,
the signalling roster that maps session id → `{userid, user.displayname}` for
everyone in the call. One wrinkle makes this non-trivial: the `transcript`
event carries a **shorter** session id than the roster/peers/cards are keyed
by (measured on Talk 25 + standalone HPB: a ~30-char prefix of the full
~43-char id). Exact matching resolved only the viewer's own lines and left
every remote speaker as a raw id — so resolution is prefix-aware, falling back
roster → peer `nick` → `nick` events → participant card DOM → shortened id.

## Code layout

- `src/store.js` — the timeline: a reorder buffer over the shared `seq` (live +
  poll merge), ordered items (`block` | `join`), a transient partials channel,
  `reset()`.
- `src/tap.js` — the live feed: subscribes to `connection.on('message')` for the
  raw payload (incl. `seq`), emits finals and partials, exposes the connection
  (room / call tokens, own session id), re-attaches on rejoin.
- `src/names.js` — prefix-aware name resolution (see above).
- `src/history.js` — `fetchHistory(token, since)` for the gap-fill poll.
- `src/session.js` — conversation/call scoping and the ~3s gap-fill poll;
  resets + enables captions on a new call or a change of conversation; an ended
  call stays until navigation.
- `src/captions.js` — turns Talk's live captions on for the session (best-effort).
- `src/panel.js` — `createTranscriptView`: renders the timeline (speaker blocks,
  "<name> joined" dividers, transient partials), replaying current state on
  mount, into a shadow root the caller supplies. CSP discipline throughout: no
  `innerHTML`, styles via `adoptedStyleSheets`, everything `createElement`.
- `src/sidebar.js` — integrates the view as a sidebar tab, reactively: injects
  only while Talk renders the sidebar on a call page and removes it when gone.
  The tab is cloned from a live native tab (exact styling despite build-hashed
  classes), re-skinned with a captions glyph and a "Live transcription" label;
  it suppresses the native `.transcript` overlay while present.
- `src/main.js` — mount/toggle entry point; exposes
  `window.__CASSINI_LIVE_PANEL__` (destroy, injectTranscript, attached).
- `src/loader.js` — the loader bookmarklet source.
- `scripts/build.js` — builds `dist/` and stages the served files into
  `../cassini_live/static/panel/` and `../cassini_live/static/diagnostics/`
  (committed; `--check` fails when stale).

## Diagnostics page

The package builds three bundles: the panel (`src/main.js`), its loader
bookmarklet (`src/loader.js`) and the administrators' live diagnostics page
(`src/diagnostics/main.js`). The page is a plain static page the app serves at
`…/apps/app_api/proxy/live_transcription/diagnostics/` (ADMIN), under the
settings page's CSP (`script-src 'self'; style-src 'self'`): no inline styles,
sizes through CSSOM. It polls `api/feed` a second after each answer and draws
the diagnostic video's two views from the samples and history columns. See
[the diagnostics page](../docs/diagnostics-page.md).

- `src/diagnostics/format.js`, `src/diagnostics/lanes.js` — pure view models
  for the status, hero numbers, pipeline, panels, lanes and lag graph, ported
  from the video's Pillow renderer (`cassini_live/diagnostic_video.py`).
- `src/diagnostics/feed.js` — the poll protocol: the relative feed path,
  backoff, the page's copy of the columns, and restart detection (a new
  `instance`, or the server clock going back).
- `src/diagnostics/main.js`, `dom.js`, `lanesView.js` — the polling loop and
  the DOM: feed strings reach it as `textContent` only.
- `src/diagnostics/index.html`, `app.css` — copied as they are.

The build writes them to `../cassini_live/static/diagnostics/{index.html,app.js,app.css}`
(committed) and `dist/diagnostics/` (for the fixture server). Unit tests are
`tests/unit/diagnostics-*.test.mjs`; `tests/diagnostics.spec.js` runs the page
in Playwright against the fixture server's scripted feed.

## Develop

```bash
npm ci
npm run build        # dist/ + ../cassini_live/static/{panel,diagnostics}/
npm run test:unit    # store semantics and the diagnostics view models (node:test)
npm run test:e2e     # Playwright against a CSP-faithful fake Talk page and diagnostics feed
npm run replay -- meeting.replay.json   # scrub a replayed meeting (see ../docs/replay.md)
```

On hosts where Playwright's Chromium can't run (e.g. NixOS), use the official
image — the fixture server runs inside the container:

```bash
docker run --rm -v "$PWD:/work" -w /work mcr.microsoft.com/playwright:v1.64.1-noble \
  npx playwright test
```

Manual poking without a Talk call: `npm run serve:fixture`, open
`http://127.0.0.1:4317/call/test`, paste `dist/bookmarklet-inline.txt` into the
address bar (re-type the `javascript:` prefix — browsers strip it on paste), and
feed events from the devtools console:

```js
__FIXTURE__.transcript({ id: 'sid-alice', message: 'hello', final: true, languageId: 'en' })
```

The same server serves the diagnostics page with a scripted feed at
`http://127.0.0.1:4317/diagnostics/`; `POST /fixture-diagnostics/<id>` steers
the feed of `/diag/<id>/diagnostics/` (see `diagnosticsControl` in
`tests/fixture-server.mjs`).
