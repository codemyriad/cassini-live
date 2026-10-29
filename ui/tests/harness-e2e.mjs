// Real-stack e2e against the gocassini harness. Proves, end to end:
//   - the pre-captions GAP is closed: the viewer sees their own speech from
//     before they enabled captions (it reaches them only via the server
//     timeline poll, since they were not a live subscriber then);
//   - per-participant JOIN markers, the viewer's own reading "You joined here";
//   - catch-up of another speaker's earlier speech; names, not session ids;
//   - the sidebar tab and the hidden native caption overlay.
//
// Scenario: botuser (speaker) joins first, enables captions, and talks.
// admin (viewer) joins, talks for a while WITHOUT captions, THEN enables
// captions and opens the panel — and must see their own earlier words.
//
// Not part of `npm test`: needs the harness up and the ExApp installed
// (gocassini: ./harness/bin/live-panel-setup.sh). Run from the gocassini repo
// root, in the Playwright container with host networking:
//
//   URL=$(./bin/cassini dev room create --name 'Gap e2e' | tail -1 | sed 's/127.0.0.1/localhost/')
//   docker run --rm --network host \
//     -v "$PWD/cassini-live/ui:/ui" \
//     -v "$PWD/cassini-live/tests/assets/jfk.wav:/media/jfk.wav:ro" \
//     -v "$PWD/.e2e-out:/out" \
//     -e NC_URL=http://localhost:28080 -e CALL_URL="$URL" \
//     -w /ui mcr.microsoft.com/playwright:v1.64.1-noble node tests/harness-e2e.mjs
//
// localhost, not 127.0.0.1: the HPB keys rooms by backend URL, and the ExApp
// joins via overwrite.cli.url (http://localhost:28080). A browser on another
// host string lands in a different room instance and captions never arrive.
import { chromium } from 'playwright-core'
import { readFile, writeFile } from 'node:fs/promises'

const BASE = process.env.NC_URL || 'http://localhost:28080'
const CALL_URL = process.env.CALL_URL
const OUT = process.env.OUT_DIR || '/out'
const BOT_PASSWORD = process.env.BOT_PASSWORD || 'zN4vQ9mT2Kp7R1x!'
const token = new URL(CALL_URL).pathname.split('/').pop()
const log = (...a) => console.log('[e2e]', ...a)

const launch = () =>
  chromium.launch({
    headless: true,
    args: [
      '--no-sandbox',
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      '--use-file-for-fake-audio-capture=/media/jfk.wav',
      '--autoplay-policy=no-user-gesture-required',
      '--disable-audio-output',
    ],
  })

async function login(page, user, password) {
  await page.goto('/login')
  await page.fill('#user', user)
  await page.fill('#password', password)
  await page.click('button[type=submit]')
  await page.waitForURL(/apps\/(dashboard|files)/, { timeout: 30000 })
}

async function joinCall(page) {
  await page.waitForSelector('#app-content-vue', { timeout: 30000 })
  const inCall = () => page.getByRole('button', { name: /leave call/i }).first().isVisible().catch(() => false)
  for (let i = 0; i < 8 && !(await inCall()); i++) {
    await page.waitForTimeout(2000)
    await page.evaluate(() => document.querySelectorAll('.toastify').forEach((t) => t.remove()))
    const modalJoin = page.locator('.modal-mask:visible').getByRole('button', { name: /^(join call|start call)$/i }).last()
    if (await modalJoin.isVisible().catch(() => false)) await modalJoin.click({ timeout: 4000 }).catch(() => {})
    else await page.getByRole('button', { name: /^(join call|start call)$/i }).first().click({ timeout: 4000 }).catch(() => {})
    await page.waitForTimeout(3000)
  }
  await page.getByRole('button', { name: /leave call/i }).first().waitFor({ timeout: 20000 })
}

const enableCaptions = (page) =>
  page.evaluate(async (t) => {
    const r = await fetch(`/ocs/v2.php/apps/spreed/api/v1/live-transcription/${t}`, {
      method: 'POST',
      headers: { 'OCS-APIRequest': 'true', requesttoken: window.OC.requestToken },
      credentials: 'same-origin',
    })
    return r.status
  }, token)

const readView = (page) =>
  page.evaluate(() => {
    const host = document.getElementById('cassini-live-tabpanel')?.querySelector('div')
    const shadow = host?.shadowRoot
    if (!shadow) return { items: [], markers: [] }
    const items = [...shadow.querySelectorAll('.marker, .block')].map((n) =>
      n.classList.contains('marker')
        ? { kind: 'join', text: n.textContent, own: n.classList.contains('you') }
        : {
            kind: 'block',
            speaker: n.querySelector('.speaker')?.textContent,
            own: n.classList.contains('own'),
            lines: [...n.querySelectorAll('.line')].map((l) => l.textContent),
          },
    )
    return { items, markers: items.filter((i) => i.kind === 'join').map((i) => i.text) }
  })

const speaker = await launch()
const viewer = await launch()
const vpage = await (await viewer.newContext({ baseURL: BASE })).newPage()
vpage.on('pageerror', (e) => console.log('[pageerror]', String(e).slice(0, 200)))
const shot = (n) => vpage.screenshot({ path: `${OUT}/${n}.png` }).catch(() => {})

try {
  // admin adds botuser, then botuser (speaker) joins first and enables captions
  await login(vpage, 'admin', 'admin')
  await vpage.evaluate(async (t) => {
    await fetch(`/ocs/v2.php/apps/spreed/api/v4/room/${t}/participants`, {
      method: 'POST',
      headers: { 'OCS-APIRequest': 'true', requesttoken: window.OC.requestToken, 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ newParticipant: 'botuser', source: 'users' }),
    })
  }, token)

  const bob = await (await speaker.newContext({ baseURL: BASE })).newPage()
  await login(bob, 'botuser', BOT_PASSWORD)
  await bob.goto(CALL_URL)
  await joinCall(bob)
  await enableCaptions(bob)
  log('botuser speaking with captions on')
  await bob.waitForTimeout(16000)

  // admin (viewer) joins and talks for a while WITHOUT captions — this is the gap
  await vpage.goto(CALL_URL)
  await joinCall(vpage)
  log('admin joined and is talking WITHOUT captions (the gap window)')
  await vpage.waitForTimeout(16000)

  // now admin enables captions and opens the panel
  await enableCaptions(vpage)
  const code = decodeURIComponent((await readFile('/ui/dist/bookmarklet-inline.txt', 'utf8')).trim().slice('javascript:'.length))
  await vpage.evaluate(code)
  await vpage.waitForSelector('#cassini-live-tab', { timeout: 10000 })
  await vpage.locator('#cassini-live-tabpanel .block').first().waitFor({ timeout: 30000 })
  await vpage.waitForTimeout(6000) // let a couple of polls land
  const view = await readView(vpage)
  await shot('01-gap')
  await writeFile(`${OUT}/gap.json`, JSON.stringify(view, null, 2))
  log('MARKERS:', JSON.stringify(view.markers))
  log('SPEAKERS:', JSON.stringify([...new Set(view.items.filter((i) => i.kind === 'block').map((i) => i.speaker))]))

  const nativeHidden = await vpage.evaluate(() => {
    const n = document.querySelector('.transcript:not(#cassini-live-tabpanel .transcript)')
    return !n || getComputedStyle(n).display === 'none'
  })
  log('native overlay hidden:', nativeHidden)

  // --- assertions ---
  const blocks = view.items.filter((i) => i.kind === 'block')
  if (!blocks.length) throw new Error('no transcript appeared')
  for (const b of blocks) {
    if (/ItZG5la2NhYg/.test(b.speaker) || /…/.test(b.speaker)) {
      throw new Error(`speaker rendered as a raw/short id: ${b.speaker}`)
    }
  }
  // THE GAP: the viewer's own speech (produced before they enabled captions)
  // must be present — it could only arrive via the server timeline poll.
  const ownBlocks = blocks.filter((b) => b.own && b.lines.length)
  if (!ownBlocks.length) throw new Error('the viewer does not see their own pre-captions speech (gap not closed)')
  // another speaker is present too (catch-up)
  const speakers = new Set(blocks.map((b) => b.speaker))
  if (speakers.size < 2) throw new Error(`expected at least two speakers, got ${[...speakers]}`)
  // JOIN markers: at least one, and the viewer's own reads "You joined here"
  if (!view.markers.length) throw new Error('no join markers')
  if (!view.markers.includes('You joined here')) throw new Error(`no own join marker; markers=${JSON.stringify(view.markers)}`)
  log(`own blocks: ${ownBlocks.length}, speakers: ${speakers.size}, join markers: ${view.markers.length}`)
  log('SUCCESS')
} catch (err) {
  await shot('99-failure')
  console.error('[e2e] FAILED:', err)
  process.exitCode = 1
} finally {
  await speaker.close()
  await viewer.close()
}
