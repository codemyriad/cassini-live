/**
 * A fake Talk call page with Nextcloud's CSP posture, for Playwright tests.
 *
 * It replicates the parts of a Talk call page the panel touches:
 *   - the nonce + strict-dynamic script CSP (the part that breaks naive injection)
 *   - #app-content-vue, a right sidebar with the Chat / Participants / Shared
 *     items tab structure (nav buttons + tab panels), and a native caption
 *     overlay (.transcript)
 *   - window.OCA.Talk.SimpleWebRTC with on/off, connection.getSessionId() and
 *     connection.joinedUsers (the authoritative name roster), webrtc.peers, and
 *     a test-only transcript() to replay caption traffic
 *
 * The transcript event ids are the SHORT form (`sid-bob`) while the roster /
 * cards are keyed by the full form (`sid-bob-FULLSUFFIX`), reproducing the Talk
 * 25 prefix mismatch that hid remote speaker names.
 *
 * It also serves the built panel bundle at the AppAPI proxy path, so the
 * loader bookmarklet flavour is exercised against the same CSP.
 */

import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const distDir = join(here, '..', 'dist')
export const PORT = Number(process.env.FIXTURE_PORT || 4317)

const NONCE = 'fixture-nonce'
const CSP = [
  "default-src 'none'",
  `script-src 'nonce-${NONCE}' 'strict-dynamic'`,
  "style-src 'unsafe-inline'",
  "img-src 'self' data:",
  "connect-src 'self'",
  "base-uri 'none'",
  "form-action 'self'",
].join('; ')

// Realistic session ids from live Talk 25 probing. A caption event carries the
// SHORT form (SID.*); the roster, peers and cards are keyed by SHORT + a
// per-connection suffix (FULL.*). Names must resolve across that gap.
const SID = {
  me: 'RPULGAfItvxdYzItZG5la2NhYgkaA5',
  alice: 'arE_gwsT19mAADItZG5la2NhYgkaA5',
  bob: 'E9BqAi4RAcd0jDItZG5la2NhYgkaA5',
  carol: 'Cw9ZtQrs04kkLLItZG5la2NhYgkaA5',
}
const FULL = {
  me: SID.me + 'fP1KSNgYgQHwg',
  alice: SID.alice + 'fP0Lj8_68QHAg',
  bob: SID.bob + 'fP0L--tqUQHQg',
  carol: SID.carol + 'fP0LxyZ123QAg',
}

const pageScript = `
  const listeners = new Map()
  function bucket(name) {
    if (!listeners.has(name)) listeners.set(name, new Set())
    return listeners.get(name)
  }
  window.OCA = {
    Talk: {
      SimpleWebRTC: {
        on(name, cb) { bucket(name).add(cb) },
        off(name, cb) { bucket(name).delete(cb) },
        connection: {
          getSessionId: () => ${JSON.stringify(FULL.me)},
          // the authoritative roster: full session id -> {userid, user.displayname}
          joinedUsers: {
            ${JSON.stringify(FULL.me)}: { sessionid: ${JSON.stringify(FULL.me)}, userid: 'me', user: { displayname: 'Mel Me' } },
            ${JSON.stringify(FULL.alice)}: { sessionid: ${JSON.stringify(FULL.alice)}, userid: 'alice', user: { displayname: 'Alice Lovelace' } },
            ${JSON.stringify(FULL.bob)}: { sessionid: ${JSON.stringify(FULL.bob)}, userid: 'bob', user: { displayname: 'Bob Babbage' } },
          },
        },
        // peers carry no nick in this Talk build (as measured)
        webrtc: { peers: [ { id: ${JSON.stringify(FULL.alice)}, type: 'video' }, { id: ${JSON.stringify(FULL.bob)}, type: 'video' } ] },
      },
    },
  }
  window.OC = { getCurrentUser: () => ({ uid: 'me', displayName: 'Mel Me' }) }
  window.__FIXTURE__ = {
    // the SHORT (event) ids, exposed so tests speak in the same terms as Talk
    sid: ${JSON.stringify(SID)},
    transcript(data) { for (const cb of bucket('transcript')) cb(data) },
    listenerCount(name) { return bucket(name).size },
    selectTab(id) {
      document.querySelectorAll('.app-sidebar-tabs__nav button[role=tab]').forEach((b) => {
        const on = b.id === 'tab-button-' + id
        b.setAttribute('aria-selected', on ? 'true' : 'false')
        b.className = on ? 'tabBtn tabBtn--selected' : 'tabBtn'
      })
      document.querySelectorAll('.app-sidebar__tab').forEach((s) => {
        s.classList.toggle('app-sidebar__tab--active', s.id === 'tab-' + id)
      })
    },
  }
`

const page = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Talk fixture call</title>
  <style nonce="${NONCE}">
    .app-sidebar__tab { display: none; }
    .app-sidebar__tab--active { display: block; }
    #app-sidebar-vue { position: absolute; right: 0; top: 0; width: 360px; height: 100%; }
    .app-sidebar-tabs__content { position: relative; height: 400px; }
  </style>
  <script nonce="${NONCE}">${pageScript}</script>
</head>
<body>
  <main id="app-content-vue">
    <div class="video-container" id="container_${FULL.alice}_video_incoming">
      <span class="video-container__user-name">Alice Lovelace</span>
    </div>
    <div class="video-container" id="container_${FULL.carol}_video_incoming">
      <span class="video-container__user-name">Carol From The DOM</span>
    </div>
    <div class="localVideoContainer"></div>
    <!-- native caption overlay the sidebar should suppress -->
    <div class="transcript" id="native-captions">native caption line</div>
  </main>
  <aside id="app-sidebar-vue">
    <div class="app-sidebar-tabs">
      <div class="app-sidebar-tabs__nav" role="tablist">
        <button id="tab-button-chat" role="tab" aria-controls="tab-chat" aria-selected="true" class="tabBtn tabBtn--selected">Chat</button>
        <button id="tab-button-participants" role="tab" aria-controls="tab-participants" aria-selected="false" class="tabBtn">Participants</button>
        <button id="tab-button-shared-items" role="tab" aria-controls="tab-shared-items" aria-selected="false" class="tabBtn">Shared items</button>
      </div>
      <div class="app-sidebar-tabs__content app-sidebar-tabs__content--multiple">
        <section id="tab-chat" role="tabpanel" class="app-sidebar__tab app-sidebar__tab--active">chat</section>
        <section id="tab-participants" role="tabpanel" class="app-sidebar__tab">participants</section>
        <section id="tab-shared-items" role="tabpanel" class="app-sidebar__tab">shared items</section>
      </div>
    </div>
  </aside>
  <script nonce="${NONCE}">
    // wire native tab clicks like Talk: clicking a native tab selects it
    document.querySelectorAll('.app-sidebar-tabs__nav button[role=tab]').forEach((b) => {
      b.addEventListener('click', () => {
        if (b.id.startsWith('tab-button-') && document.getElementById('tab-' + b.id.slice('tab-button-'.length))) {
          window.__FIXTURE__.selectTab(b.id.slice('tab-button-'.length))
        }
      })
    })
  </script>
</body>
</html>`

export function startFixtureServer(port = PORT) {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, `http://127.0.0.1:${port}`)
    if (url.pathname.startsWith('/call/')) {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'content-security-policy': CSP })
      res.end(page)
      return
    }
    if (url.pathname.endsWith('/panel/panel.js')) {
      try {
        const body = await readFile(join(distDir, 'panel.js'))
        res.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8' })
        res.end(body)
      } catch {
        res.writeHead(404)
        res.end('build dist/panel.js first')
      }
      return
    }
    res.writeHead(404)
    res.end('not found')
  })
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve(server)))
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  await startFixtureServer()
  console.log(`fixture Talk page: http://127.0.0.1:${PORT}/call/test`)
}
