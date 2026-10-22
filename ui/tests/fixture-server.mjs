/**
 * A fake Talk call page with Nextcloud's CSP posture, for Playwright tests.
 *
 * It replicates the parts of a Talk call page the panel touches:
 *   - the nonce + strict-dynamic script CSP (the part that breaks naive injection)
 *   - #app-content-vue and participant cards with display names
 *   - window.OCA.Talk.SimpleWebRTC with on/off, connection.getSessionId() and
 *     webrtc.peers, plus a test-only emit() to replay caption traffic
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
        connection: { getSessionId: () => 'sid-me' },
        webrtc: {
          peers: [
            { id: 'sid-alice', nick: 'Alice Lovelace' },
            { id: 'sid-bob', nick: 'Bob Babbage' },
          ],
        },
      },
    },
  }
  window.OC = { getCurrentUser: () => ({ uid: 'me', displayName: 'Mel Me' }) }
  window.__FIXTURE__ = {
    transcript(data) { for (const cb of bucket('transcript')) cb(data) },
    listenerCount(name) { return bucket(name).size },
  }
`

const page = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Talk fixture call</title>
  <script nonce="${NONCE}">${pageScript}</script>
</head>
<body>
  <main id="app-content-vue">
    <div id="container_sid-alice_video_incoming" class="video-container">
      <span class="video-container__user-name">Alice Lovelace</span>
    </div>
    <div id="container_sid-carol_video_incoming" class="video-container">
      <span class="video-container__user-name">Carol From The DOM</span>
    </div>
    <div class="localVideoContainer"></div>
  </main>
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
