# Cassini Live transcript panel

A scrollable, speaker-grouped live transcript for Nextcloud Talk calls,
rendered from the captions Cassini Live already delivers — including the
viewer's own lines, which Talk's built-in overlay drops.

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
                                              store (speaker blocks) ─▶ panel
```

`simplewebrtc.js` emits `'transcript'` for every caption message the HPB
delivers to this session, before any filtering — that is where the panel
listens. Captions still have to be enabled in the call (Talk's CC menu): that
is what makes cassini-live target this session at all.

Speaker names resolve in the browser (the payload only carries the speaker's
signalling session id): SimpleWebRTC peers → `nick` events → the participant
card in the DOM → a shortened session id. The viewer's own session id comes
from `connection.getSessionId()`.

## Code layout

- `src/store.js` — caption semantics: blocks of consecutive finals per speaker,
  partials render in place and never push committed text around.
- `src/tap.js` — attaches to `OCA.Talk.SimpleWebRTC`, polls until the global
  exists, follows instance replacement on call rejoin.
- `src/names.js` — the name resolution chain.
- `src/panel.js` — the shadow-root panel. CSP discipline throughout: no
  `innerHTML`, styles via `adoptedStyleSheets`, everything `createElement`.
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
