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
  const connection = {
    getSessionId: () => ${JSON.stringify(FULL.me)},
    currentRoomToken: 'room-1',
    currentCallToken: 'room-1',
    // the authoritative roster: full session id -> {userid, user.displayname}
    joinedUsers: {
      ${JSON.stringify(FULL.me)}: { sessionid: ${JSON.stringify(FULL.me)}, userid: 'me', user: { displayname: 'Mel Me' } },
      ${JSON.stringify(FULL.alice)}: { sessionid: ${JSON.stringify(FULL.alice)}, userid: 'alice', user: { displayname: 'Alice Lovelace' } },
      ${JSON.stringify(FULL.bob)}: { sessionid: ${JSON.stringify(FULL.bob)}, userid: 'bob', user: { displayname: 'Bob Babbage' } },
    },
  }
  window.OCA = {
    Talk: {
      SimpleWebRTC: {
        on(name, cb) { bucket(name).add(cb) },
        off(name, cb) { bucket(name).delete(cb) },
        connection,
        // peers carry no nick in this Talk build (as measured)
        webrtc: { peers: [ { id: ${JSON.stringify(FULL.alice)}, type: 'video' }, { id: ${JSON.stringify(FULL.bob)}, type: 'video' } ] },
      },
    },
  }
  window.OC = {
    getCurrentUser: () => ({ uid: 'me', displayName: 'Mel Me' }),
    generateUrl: (p) => '/index.php' + p,
  }
  window.__FIXTURE__ = {
    // the SHORT (event) ids and the FULL ids (as the roster/timeline use)
    sid: ${JSON.stringify(SID)},
    full: ${JSON.stringify(FULL)},
    transcript(data) { for (const cb of bucket('transcript')) cb(data) },
    listenerCount(name) { return bucket(name).size },
    // call/room controls, driving the session scoping
    setCall(room, call) { connection.currentRoomToken = room; connection.currentCallToken = call || null },
    endCall() { connection.currentCallToken = null },
    // append finals/joins to a conversation's server-side timeline; the panel
    // picks them up on its next /history poll (this is how a final reaches the
    // viewer without ever being a live caption event)
    appendTimeline(token, entries) {
      return fetch('/index.php/apps/app_api/proxy/live_transcription/fixture-append', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ token, entries }),
      }).then((r) => r.json())
    },
    // simulate a new call's empty buffer for a conversation
    clearTimeline(token) {
      return fetch('/index.php/apps/app_api/proxy/live_transcription/fixture-append', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ token, clear: true }),
      }).then((r) => r.json())
    },
    // sidebar presence, for the reactive show/hide
    removeSidebar() { document.getElementById('app-sidebar-vue')?.remove() },
    addSidebar() { if (!document.getElementById('app-sidebar-vue')) document.body.appendChild(window.__SIDEBAR__.cloneNode(true)) },
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
    // keep a pristine copy of the sidebar so addSidebar() can restore it
    window.__SIDEBAR__ = document.getElementById('app-sidebar-vue').cloneNode(true)
    // wire native tab clicks like Talk: clicking a native tab selects it
    document.addEventListener('click', (e) => {
      const b = e.target.closest && e.target.closest('.app-sidebar-tabs__nav button[role=tab]')
      if (b && b.id.startsWith('tab-button-') && document.getElementById('tab-' + b.id.slice('tab-button-'.length))) {
        window.__FIXTURE__.selectTab(b.id.slice('tab-button-'.length))
      }
    })
  </script>
</body>
</html>`

// Per-conversation server-side timelines, mirroring cassini-live's buffer:
// entries gain a monotonic seq, and /history returns those newer than `since`.
const timelines = new Map() // token -> { entries: [], seq: 0 }

function timeline(token) {
  if (!timelines.has(token)) timelines.set(token, { entries: [], seq: 0 })
  return timelines.get(token)
}

export function startFixtureServer(port = PORT) {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, `http://127.0.0.1:${port}`)
    if (url.pathname.startsWith('/call/')) {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'content-security-policy': CSP })
      res.end(page)
      return
    }
    if (url.pathname.endsWith('/live_transcription/fixture-append')) {
      let raw = ''
      req.on('data', (c) => (raw += c))
      req.on('end', () => {
        const { token, entries, clear } = JSON.parse(raw || '{}')
        const tl = timeline(token)
        if (clear) {
          tl.entries = []
          tl.seq = 0
        }
        for (const e of entries || []) tl.entries.push({ ...e, seq: ++tl.seq })
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end(JSON.stringify({ head: tl.seq }))
      })
      return
    }
    if (url.pathname.endsWith('/live_transcription/history')) {
      const token = url.searchParams.get('token') || ''
      const since = Number(url.searchParams.get('since') || 0)
      const tl = timeline(token)
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ entries: tl.entries.filter((e) => e.seq > since), head: tl.seq }))
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
