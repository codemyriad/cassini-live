# Cassini Live transcript panel

A scrollable, speaker-grouped live transcript for Nextcloud Talk calls,
rendered from the captions Cassini Live already delivers — including the
viewer's own lines, which Talk's built-in overlay drops.

It mounts as an extra **tab in Talk's right sidebar**, next to Chat /
Participants / Shared items, rather than a floating window, and hides Talk's
native caption overlay while it is on (the sidebar replaces it).

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

```
 cassini-live ──HPB signalling──▶ Talk's SimpleWebRTC ──'transcript'──▶ tap
                                        (window.OCA.Talk.SimpleWebRTC)   │
   Talk's own overlay ◀── CallParticipantModel (drops own lines)         │
                                                                         ▼
                                   store (speaker blocks) ─▶ sidebar tab view
```

`simplewebrtc.js` emits `'transcript'` for every caption message the HPB
delivers to this session, before any filtering — that is where the panel
listens. Captions still have to be enabled in the call (Talk's CC menu): that
is what makes cassini-live target this session at all.

Live events only reach a session from the moment it enables captions, so a late
joiner would miss everything said before. The panel therefore also fetches
`GET /history?token=<roomToken>` (`history.js`) when a call starts; the store
seeds those earlier finals as *historical* blocks and the view draws a "You
joined here" divider before the first live block. Everything is scoped per
conversation by `session.js`, which watches the room and call tokens and resets
the store on a new call or a switch to another conversation.

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

- `src/store.js` — caption semantics: blocks of consecutive finals per speaker,
  partials render in place and never push committed text around.
- `src/tap.js` — attaches to `OCA.Talk.SimpleWebRTC`, polls until the global
  exists, follows instance replacement on call rejoin.
- `src/names.js` — prefix-aware name resolution (see above).
- `src/history.js` — fetches `/history` for catch-up, tolerant of errors.
- `src/session.js` — conversation/call scoping: resets + re-seeds the store on a
  new call or a change of conversation; an ended call stays until navigation.
- `src/store.js` also has `seedHistory` / `reset` and a `historical` flag per
  block; the view draws the "You joined here" divider from it.
- `src/panel.js` — `createTranscriptView`: renders the store (replaying current
  state on mount) into a shadow root the caller supplies. CSP discipline
  throughout: no `innerHTML`, styles via `adoptedStyleSheets`, everything
  `createElement`.
- `src/sidebar.js` — integrates the view as a sidebar tab, reactively: injects
  only while Talk renders the sidebar on a call page and removes it when gone.
  The tab is cloned from a live native tab (exact styling despite build-hashed
  classes), re-skinned with a captions glyph and a "Live transcription" label;
  it suppresses the native `.transcript` overlay while present.
- `src/main.js` — mount/toggle entry point; exposes
  `window.__CASSINI_LIVE_PANEL__` (destroy, injectTranscript, attached).
- `src/loader.js` — the loader bookmarklet source.
- `scripts/build.js` — builds `dist/` and stages the served files into
  `../cassini_live/static/panel/` (committed; `--check` fails when stale).

## Develop

```bash
npm ci
npm run build        # dist/ + ../cassini_live/static/panel/
npm run test:unit    # store semantics (node:test)
npm run test:e2e     # Playwright against a CSP-faithful fake Talk page
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
