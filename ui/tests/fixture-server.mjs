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
 *
 * And the diagnostics page (dist/diagnostics/) at /diagnostics/, optionally under
 * /diag/<id> and the AppAPI proxy path, with the ExApp's page CSP and a scripted
 * feed that advances once a second. Each <id> is its own feed, so tests do not
 * share one; POST /fixture-diagnostics/<id> steers it (see diagnosticsControl). Its
 * columns follow the sample's lanes (diagnostics-fixtures.scriptedColumn): speakers
 * talk in bursts, a paused, held or lost lane ends or passes a stretch in that state,
 * and once a minute the lag rises past the shed line and a speaker is paused.
 */

import { randomBytes } from 'node:crypto'
import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { governor, lagAt, sample, scriptedColumn } from './diagnostics-fixtures.mjs'

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
  // Talk's signalling connection is an emitter; the live caption feed arrives on
  // its raw 'message' event (which carries cassini-live's seq), as in real Talk.
  const connection = {
    getSessionId: () => ${JSON.stringify(FULL.me)},
    currentRoomToken: 'room-1',
    currentCallToken: 'room-1',
    on(name, cb) { bucket('conn:' + name).add(cb) },
    off(name, cb) { bucket('conn:' + name).delete(cb) },
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
        connection,
        // peers carry no nick in this Talk build (as measured)
        webrtc: { peers: [ { id: ${JSON.stringify(FULL.alice)}, type: 'video' }, { id: ${JSON.stringify(FULL.bob)}, type: 'video' } ] },
      },
    },
  }
  window.OC = {
    getCurrentUser: () => ({ uid: 'me', displayName: 'Mel Me' }),
    generateUrl: (p) => '/index.php' + p,
    requestToken: 'fixture-request-token',
  }
  window.__FIXTURE__ = {
    // the SHORT (event) ids and the FULL ids (as the roster/timeline use)
    sid: ${JSON.stringify(SID)},
    full: ${JSON.stringify(FULL)},
    // emit a raw signalling 'message' — the live HPB feed (finals carry seq)
    message(data) { for (const cb of bucket('conn:message')) cb(data) },
    listenerCount(name) { return bucket('conn:' + name).size },
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
    // which conversations the panel turned captions on for (auto-enable)
    enabledTokens() {
      return fetch('/fixture-enabled').then((r) => r.json())
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

// cassini_live/settings_page.py CSP, which the diagnostics page is served with.
const PAGE_CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "connect-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
].join('; ')
const DIAGNOSTICS = /^(?:\/diag\/([\w-]+))?(?:\/index\.php\/apps\/app_api\/proxy\/live_transcription)?\/diagnostics\/(.*)$/
const DIAGNOSTICS_FILES = {
  '': ['index.html', 'text/html; charset=utf-8'],
  'index.html': ['index.html', 'text/html; charset=utf-8'],
  'app.js': ['app.js', 'text/javascript; charset=utf-8'],
  'app.css': ['app.css', 'text/css; charset=utf-8'],
}
// A speaker name that would run script if the page ever parsed feed strings as HTML.
export const HOSTILE_NAME = '<img src=x onerror=alert(1)>'

// Scripted diagnostics feeds by id: server monotonic time starts at `base` seconds
// and one column is added per second, as DiagnosticFeed does.
const diagnosticsFeeds = new Map()
// DiagnosticFeed.instance: a random token per Cassini Live process.
const newInstance = () => randomBytes(8).toString('hex')

function diagnosticsFeed(id) {
  if (!diagnosticsFeeds.has(id)) {
    diagnosticsFeeds.set(id, { start: Date.now(), base: 5000, from: 1, instance: newInstance(), status: null, html: false, overrides: {} })
  }
  return diagnosticsFeeds.get(id)
}

/**
 * Steer a feed: {status: 403|503|null} answers every poll with that status (null:
 * normally again) and the app's JSON error, {status: 404, html: true} with Nextcloud's
 * HTML 404 page instead, as AppAPI's proxy refuses a request, {restart: true} restarts its clock lower and keeps its instance, so
 * only the page's clock check can see it (a rebooted host, as an answer without an
 * instance would show it), {relaunch: true} drops its columns, keeps its clock and gets a
 * new instance (a restarted ExApp container: lane ids start over, and the first column
 * comes over two seconds later, as DiagnosticFeed's does after priming), {idle: true}
 * drops its columns and keeps its clock and instance (the feed stopped after 30 s without
 * a poll and started again), {sample: {...}} overrides fields of every sample from now on,
 * {history: s} moves its start s seconds back, so its first answer already holds that
 * much history.
 */
function diagnosticsControl(feed, { status, html, restart, relaunch, idle, sample: overrides, history }) {
  if (status !== undefined) Object.assign(feed, { status, html: Boolean(html) })
  if (restart) Object.assign(feed, { start: Date.now(), base: 100, from: 1 })
  if (relaunch || idle) feed.from = Math.floor((Date.now() - feed.start) / 1000) + 3
  if (relaunch) feed.instance = newInstance()
  if (overrides) feed.overrides = overrides
  if (history) feed.start -= history * 1000
}

function diagnosticsBody(feed, since) {
  const elapsed = (Date.now() - feed.start) / 1000
  const ticks = Math.floor(elapsed)
  const data = sample({
    vadActive: 1 + (ticks % 3),
    queuedChunks: ticks,
    finals: 40 + Math.floor(ticks / 3),
    admission: governor({ lagSeconds: lagAt(feed.base + ticks) }),
    lanes: [
      { id: 1, active: true, speech: 120 + ticks, lost: 0, scribe: 1, waiting: false, paused: false, lag: 0.3, name: HOSTILE_NAME },
      { id: 2, active: false, speech: 80, lost: 0, scribe: 2, waiting: false, paused: false, lag: 0.0, name: 'Bo' },
    ],
    decision: { at: 1793212496.5, verb: 'admit', who: HOSTILE_NAME, text: 'seat 2 of 4 · lag 0.4 s < 1.0 · stall 6%' },
    ...feed.overrides,
  })
  const columns = []
  for (let k = Math.max(feed.from, ticks - 119); k <= ticks; k++) {
    columns.push(scriptedColumn(Array.isArray(data.lanes) ? data.lanes : [], feed.base + k, feed.base + ticks))
  }
  return {
    version: 'fixture',
    window: 120,
    presence: 30,
    names: true,
    instance: feed.instance,
    head: columns.length ? columns[columns.length - 1].t : 0,
    now: Math.round((feed.base + elapsed) * 1000) / 1000,
    sample: data,
    columns: columns.filter((column) => column.t > since),
    error: null,
    video: { enabled: false, state: 'disabled' },
  }
}

// Per-conversation server-side timelines, mirroring cassini-live's buffer:
// entries gain a monotonic seq, and /history returns those newer than `since`.
const timelines = new Map() // token -> { entries: [], seq: 0 }

function timeline(token) {
  if (!timelines.has(token)) timelines.set(token, { entries: [], seq: 0 })
  return timelines.get(token)
}

// Conversations the panel enabled captions for (Talk's live-transcription POST).
const enabled = []

export function startFixtureServer(port = PORT) {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, `http://127.0.0.1:${port}`)
    if (url.pathname.startsWith('/call/')) {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'content-security-policy': CSP })
      res.end(page)
      return
    }
    // Talk's "turn captions on" endpoint — record which conversation was enabled.
    const enableMatch = url.pathname.match(/\/apps\/spreed\/api\/v1\/live-transcription\/([^/]+)$/)
    if (enableMatch && req.method === 'POST') {
      if (!enabled.includes(enableMatch[1])) enabled.push(enableMatch[1])
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ ocs: { meta: { status: 'ok' } } }))
      return
    }
    if (url.pathname === '/fixture-enabled') {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify(enabled))
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
        // Honor an explicit seq so tests keep the live feed and the poll on one
        // shared seq space (as cassini-live does); otherwise auto-assign.
        for (const e of entries || []) {
          const seq = typeof e.seq === 'number' ? e.seq : tl.seq + 1
          tl.seq = Math.max(tl.seq, seq)
          tl.entries.push({ ...e, seq })
        }
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
    const control = url.pathname.match(/^\/fixture-diagnostics\/([\w-]+)$/)
    if (control && req.method === 'POST') {
      let raw = ''
      req.on('data', (c) => (raw += c))
      req.on('end', () => {
        diagnosticsControl(diagnosticsFeed(control[1]), JSON.parse(raw || '{}'))
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end('{}')
      })
      return
    }
    const diagnostics = url.pathname.match(DIAGNOSTICS)
    if (diagnostics && diagnostics[2] === 'api/feed') {
      const feed = diagnosticsFeed(diagnostics[1] || 'default')
      const headers = { 'content-type': 'application/json', 'content-security-policy': PAGE_CSP, 'cache-control': 'no-store' }
      if (feed.status && feed.html) {
        res.writeHead(feed.status, { 'content-type': 'text/html; charset=UTF-8', 'cache-control': 'no-store' })
        res.end('<!DOCTYPE html><html><body><h2>Page not found</h2></body></html>')
        return
      }
      if (feed.status) {
        res.writeHead(feed.status, headers)
        res.end(JSON.stringify({ error: { code: feed.status === 403 ? 'forbidden' : 'unavailable', message: 'refused by the fixture', reasons: [] } }))
        return
      }
      res.writeHead(200, headers)
      res.end(JSON.stringify(diagnosticsBody(feed, Number(url.searchParams.get('since') || 0))))
      return
    }
    if (diagnostics && Object.hasOwn(DIAGNOSTICS_FILES, diagnostics[2])) {
      const [name, type] = DIAGNOSTICS_FILES[diagnostics[2]]
      try {
        const body = (await readFile(join(distDir, 'diagnostics', name), 'utf8')).replaceAll('{{VERSION}}', 'fixture')
        res.writeHead(200, { 'content-type': type, 'content-security-policy': PAGE_CSP, 'cache-control': 'no-store' })
        res.end(body)
      } catch {
        res.writeHead(404)
        res.end('build dist/diagnostics/ first')
      }
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
  console.log(`fixture diagnostics page: http://127.0.0.1:${PORT}/diagnostics/`)
}
