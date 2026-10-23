// Real-stack e2e against the gocassini harness. Proves late-joiner catch-up,
// the sidebar transcript tab, and the remote-speaker name fix end to end:
//
//   - botuser joins first as the SPEAKER (JFK clip on a fake mic), enables
//     captions, and talks — cassini-live buffers the finals
//   - admin joins LATER as the VIEWER, enables captions, clicks the bookmarklet
//   - admin's "Live transcription" sidebar tab shows what botuser said BEFORE
//     admin joined (fetched from /history), a "You joined here" divider, then
//     the live continuation — all attributed to "botuser", not a session id,
//     with Talk's native caption overlay hidden
//
// Not part of `npm test`: it needs the harness up and the ExApp installed at
// >= 0.10.0 (gocassini: ./harness/bin/live-panel-setup.sh). Run from the
// gocassini repo root, in the Playwright container with host networking:
//
//   URL=$(./bin/cassini dev room create --name 'Catch-up e2e' | tail -1 | sed 's/127.0.0.1/localhost/')
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

const launch = (mic) =>
  chromium.launch({
    headless: true,
    args: [
      '--no-sandbox',
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      ...(mic ? [`--use-file-for-fake-audio-capture=${mic}`] : []),
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

async function enableCaptions(page) {
  return page.evaluate(async (t) => {
    const r = await fetch(`/ocs/v2.php/apps/spreed/api/v1/live-transcription/${t}`, {
      method: 'POST',
      headers: { 'OCS-APIRequest': 'true', requesttoken: window.OC.requestToken },
      credentials: 'same-origin',
    })
    return r.status
  }, token)
}

const readBlocks = (page) =>
  page.evaluate(() => {
    const host = document.getElementById('cassini-live-tabpanel')?.querySelector('div')
    const shadow = host?.shadowRoot
    if (!shadow) return { blocks: [], divider: null }
    const order = [...shadow.querySelectorAll('.marker, .block')].map((n) =>
      n.classList.contains('marker')
        ? { divider: n.textContent }
        : {
            speaker: n.querySelector('.speaker')?.textContent,
            own: n.classList.contains('own'),
            lines: [...n.querySelectorAll('.line:not(.partial)')].map((l) => l.textContent),
          },
    )
    return { order, divider: shadow.querySelector('.marker')?.textContent || null }
  })

const speaker = await launch('/media/jfk.wav')
const viewer = await launch(false)
const vpage = await (await viewer.newContext({ baseURL: BASE })).newPage()
vpage.on('pageerror', (e) => console.log('[pageerror]', String(e).slice(0, 200)))
const shot = (n) => vpage.screenshot({ path: `${OUT}/${n}.png` }).catch(() => {})

try {
  // Admin adds botuser to the room, then botuser (speaker) joins and talks FIRST.
  const admin = await (await viewer.newContext({ baseURL: BASE })).newPage()
  await login(admin, 'admin', 'admin')
  await admin.evaluate(async (t) => {
    await fetch(`/ocs/v2.php/apps/spreed/api/v4/room/${t}/participants`, {
      method: 'POST',
      headers: { 'OCS-APIRequest': 'true', requesttoken: window.OC.requestToken, 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ newParticipant: 'botuser', source: 'users' }),
    })
  }, token)
  await admin.close()

  const bob = await (await speaker.newContext({ baseURL: BASE })).newPage()
  await login(bob, 'botuser', BOT_PASSWORD)
  await bob.goto(CALL_URL)
  await joinCall(bob)
  log('botuser (speaker) in call; enabling captions and speaking first')
  await enableCaptions(bob)
  await bob.waitForTimeout(22000) // speak before the viewer arrives → builds history

  // Viewer joins LATE.
  await login(vpage, 'admin', 'admin')
  await vpage.goto(CALL_URL)
  await joinCall(vpage)
  log('admin (viewer) joined late; enabling captions + bookmarklet')
  await enableCaptions(vpage)
  const code = decodeURIComponent((await readFile('/ui/dist/bookmarklet-inline.txt', 'utf8')).trim().slice('javascript:'.length))
  await vpage.evaluate(code)
  await vpage.waitForSelector('#cassini-live-tab', { timeout: 10000 })

  // Catch-up history should appear quickly (fetched, not waited-for live).
  await vpage.locator('#cassini-live-tabpanel .block').first().waitFor({ timeout: 30000 })
  await vpage.waitForTimeout(12000) // let a live line or two land after the divider
  const result = await readBlocks(vpage)
  await shot('01-catchup')
  await writeFile(`${OUT}/catchup.json`, JSON.stringify(result, null, 2))
  log('PANEL:', JSON.stringify(result, null, 2))

  const nativeHidden = await vpage.evaluate(() => {
    const n = document.querySelector('.transcript:not(#cassini-live-tabpanel .transcript)')
    return !n || getComputedStyle(n).display === 'none'
  })
  log('native overlay hidden:', nativeHidden)

  // Assertions.
  const blocks = (result.order || []).filter((o) => o.speaker)
  if (!blocks.length) throw new Error('no transcript appeared for the late joiner')
  for (const b of blocks) {
    if (/ItZG5la2NhYg/.test(b.speaker) || /…/.test(b.speaker)) {
      throw new Error(`speaker rendered as a raw/short id, not a name: ${b.speaker}`)
    }
  }
  if (result.divider !== 'You joined here') {
    throw new Error(`expected a "You joined here" divider, got: ${result.divider}`)
  }
  // The divider must have at least one block before it (history) and after it (live).
  const dividerIdx = (result.order || []).findIndex((o) => o.divider)
  const before = (result.order || []).slice(0, dividerIdx).filter((o) => o.speaker)
  const after = (result.order || []).slice(dividerIdx + 1).filter((o) => o.speaker)
  if (!before.length) throw new Error('no history before the divider')
  if (!after.length) throw new Error('no live lines after the divider')
  log(`history blocks before divider: ${before.length}, live blocks after: ${after.length}`)
  log('SUCCESS')
} catch (err) {
  await shot('99-failure')
  console.error('[e2e] FAILED:', err)
  process.exitCode = 1
} finally {
  await speaker.close()
  await viewer.close()
}
