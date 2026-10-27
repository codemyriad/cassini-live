#!/usr/bin/env node
/**
 * Replay viewer: the real transcript panel on a fake Talk call page, driven by a
 * replay timeline that `python -m cassini_live.replay` made from an archived
 * meeting recording. Scrub to any moment of the meeting and see what Cassini
 * Live would have shown then.
 *
 *   npm run replay -- path/to/meeting.replay.json [--port 4318] [--as <participant>]
 *
 * The page keeps Nextcloud's CSP posture (nonce + strict-dynamic) and the same
 * sidebar structure as the e2e fixture, so the bundle under test is the one the
 * ExApp serves, mounted the way the loader bookmarklet mounts it. The server
 * owns the meeting clock: the panel's own `/history` poll gets exactly the
 * entries that existed at the current replay time, and the page feeds the
 * panel the in-progress partial each speaker had on screen at that moment.
 */

import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { renderPage, NONCE } from './page.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const distDir = join(here, '..', 'dist')

const PROXY = '/index.php/apps/app_api/proxy/live_transcription'
const TOKEN = 'replay'
// A partial older than this is stale: no engine keeps a preview up that long.
const PARTIAL_TTL_MS = 15000

export function loadReplay(json) {
  const body = typeof json === 'string' ? JSON.parse(json) : json
  if (body.format !== 'cassini-live-replay/1') {
    throw new Error(`not a cassini-live replay timeline: format ${body.format}`)
  }
  const participants = body.participants || []
  const byId = new Map(participants.map((p) => [p.sid, p]))
  const events = [...(body.events || [])].sort((a, b) => a.at - b.at)
  const fromMs = body.window?.fromMs ?? 0
  const lastAt = events.length ? events[events.length - 1].at : fromMs
  const toMs = body.window?.toMs ?? lastAt + 2000
  // Real wall-clock times when the recorder stamped the meeting; else a fake
  // epoch that at least reads as "today" in the panel's time column.
  const epoch = body.source?.startedAt ? Date.parse(body.source.startedAt) : Date.now() - toMs
  // The durable timeline the panel polls: joins and finals, in order, with seq.
  const timeline = []
  for (const e of events) {
    if (e.type === 'join') {
      const p = byId.get(e.speakerSessionId) || {}
      timeline.push({
        seq: timeline.length + 1,
        at: e.at,
        type: 'join',
        speakerSessionId: e.speakerSessionId,
        displayName: p.name || null,
        userId: p.userId || null,
        t: epoch + e.at,
      })
    } else if (e.type === 'final' && e.message) {
      timeline.push({
        seq: timeline.length + 1,
        at: e.at,
        type: 'final',
        speakerSessionId: e.speakerSessionId,
        message: e.message,
        langId: e.langId ?? null,
        t: epoch + e.at,
      })
    }
  }
  return { body, participants, events, timeline, fromMs, toMs, epoch }
}

export function createClock(fromMs, toMs) {
  let anchorT = fromMs
  let anchorWall = Date.now()
  let playing = false
  let speed = 1
  const clamp = (t) => Math.max(fromMs, Math.min(toMs, t))
  function now() {
    if (!playing) return anchorT
    const t = anchorT + (Date.now() - anchorWall) * speed
    if (t >= toMs) {
      anchorT = toMs
      playing = false
      return toMs
    }
    return t
  }
  return {
    now,
    get playing() {
      return playing
    },
    get speed() {
      return speed
    },
    play() {
      if (playing) return
      if (anchorT >= toMs) anchorT = fromMs
      anchorWall = Date.now()
      playing = true
    },
    pause() {
      anchorT = now()
      playing = false
    },
    seek(t) {
      anchorT = clamp(Number(t) || 0)
      anchorWall = Date.now()
    },
    setSpeed(s) {
      anchorT = now()
      anchorWall = Date.now()
      speed = Math.max(0.1, Math.min(32, Number(s) || 1))
    },
  }
}

/** Entries of the durable timeline that existed at meeting time `t`, after `since`. */
export function historyAt(replay, t, since = 0) {
  const entries = []
  let head = 0
  for (const e of replay.timeline) {
    if (e.at > t) break
    head = e.seq
    if (e.seq > since) entries.push(e)
  }
  return { entries, head }
}

/** The in-progress partial each speaker had on screen at meeting time `t`. */
export function partialsAt(replay, t) {
  const latest = new Map() // sid -> last partial/final event at or before t
  for (const e of replay.events) {
    if (e.at > t) break
    if (e.type === 'partial' || e.type === 'final') latest.set(e.speakerSessionId, e)
  }
  const out = []
  for (const [sid, e] of latest) {
    if (e.type === 'partial' && e.message && t - e.at <= PARTIAL_TTL_MS) {
      out.push({ sid, message: e.message, lang: e.langId ?? null })
    }
  }
  return out
}

function json(res, status, body) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' })
  res.end(JSON.stringify(body))
}

function readBody(req) {
  return new Promise((resolve) => {
    let raw = ''
    req.on('data', (c) => (raw += c))
    req.on('end', () => resolve(raw))
  })
}

export async function startReplayServer({ replay, port = 4318, host = '127.0.0.1', viewer = null }) {
  const clock = createClock(replay.fromMs, replay.toMs)
  const defaultViewer = viewer || replay.participants[0]?.sid || ''
  const transportJs = await readFile(join(here, 'transport.js'), 'utf8')
  const transportCss = await readFile(join(here, 'transport.css'), 'utf8')

  const server = createServer(async (req, res) => {
    const url = new URL(req.url, `http://${host}:${port}`)
    const path = url.pathname
    try {
      if (path === '/' || path === `/call/${TOKEN}`) {
        if (path === '/') {
          res.writeHead(302, { location: `/call/${TOKEN}${url.search}` })
          return res.end()
        }
        const as = url.searchParams.get('as') || defaultViewer
        res.writeHead(200, {
          'content-type': 'text/html; charset=utf-8',
          'content-security-policy': `default-src 'self'; script-src 'nonce-${NONCE}' 'strict-dynamic'; style-src 'self' 'nonce-${NONCE}' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'`,
        })
        return res.end(renderPage({ replay, viewer: as, token: TOKEN }))
      }
      if (path === '/replay/transport.js') {
        res.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8' })
        return res.end(transportJs)
      }
      if (path === '/replay/transport.css') {
        res.writeHead(200, { 'content-type': 'text/css; charset=utf-8' })
        return res.end(transportCss)
      }
      if (path === '/replay/timeline') return json(res, 200, replay.body)
      if (path === '/replay/state') {
        const t = clock.now()
        return json(res, 200, {
          t: Math.round(t),
          playing: clock.playing,
          speed: clock.speed,
          fromMs: replay.fromMs,
          toMs: replay.toMs,
          epoch: replay.epoch,
          partials: partialsAt(replay, t),
          head: historyAt(replay, t).head,
        })
      }
      if (path === '/replay/control' && req.method === 'POST') {
        const body = JSON.parse((await readBody(req)) || '{}')
        switch (body.action) {
          case 'play':
            clock.play()
            break
          case 'pause':
            clock.pause()
            break
          case 'seek':
            clock.seek(body.t)
            break
          case 'speed':
            clock.setSpeed(body.speed)
            break
          default:
            return json(res, 400, { error: `unknown action ${body.action}` })
        }
        return json(res, 200, { t: Math.round(clock.now()), playing: clock.playing, speed: clock.speed })
      }
      if (path === `${PROXY}/history`) {
        const token = url.searchParams.get('token') || ''
        if (token !== TOKEN) return json(res, 200, { entries: [], head: 0 })
        const since = Number(url.searchParams.get('since') || 0)
        return json(res, 200, historyAt(replay, clock.now(), since))
      }
      if (path === `${PROXY}/panel/panel.js`) {
        try {
          const body = await readFile(join(distDir, 'panel.js'))
          res.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8', 'cache-control': 'no-store' })
          return res.end(body)
        } catch {
          res.writeHead(404)
          return res.end('build the panel first: npm run build')
        }
      }
      res.writeHead(404)
      res.end('not found')
    } catch (error) {
      json(res, 500, { error: String(error && error.message) })
    }
  })
  await new Promise((resolve) => server.listen(port, host, resolve))
  return {
    server,
    clock,
    url: `http://${host}:${port}/call/${TOKEN}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  }
}

async function main(argv) {
  const args = [...argv]
  let port = Number(process.env.REPLAY_PORT || 4318)
  let viewer = null
  const files = []
  while (args.length) {
    const a = args.shift()
    if (a === '--port') port = Number(args.shift())
    else if (a === '--as') viewer = args.shift()
    else files.push(a)
  }
  if (files.length !== 1) {
    console.error('usage: node replay/server.mjs <meeting.replay.json> [--port 4318] [--as <participant>]')
    process.exit(2)
  }
  const replay = loadReplay(await readFile(resolve(files[0]), 'utf8'))
  const { url } = await startReplayServer({ replay, port, viewer })
  const title = replay.body.source?.title || files[0]
  const minutes = (ms) => `${Math.floor(ms / 60000)}:${String(Math.floor((ms % 60000) / 1000)).padStart(2, '0')}`
  console.log(`${title} — ${replay.participants.length} participants, ${minutes(replay.fromMs)}–${minutes(replay.toMs)}, engine ${replay.body.engine?.id}`)
  console.log(`open ${url}`)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2))
}
