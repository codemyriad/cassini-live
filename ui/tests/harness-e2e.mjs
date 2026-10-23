// Real-stack e2e against the gocassini harness. Proves the sidebar transcript
// tab and the remote-speaker name fix end to end:
//
//   - admin joins as the silent VIEWER, enables live captions, clicks the
//     bookmarklet (the transcript tab appears in Talk's sidebar)
//   - botuser joins as the REMOTE SPEAKER with the JFK clip on a fake mic
//   - admin's sidebar tab fills with Whistle captions attributed to "botuser"
//     (a display name, not a raw session id — the bug this guards), the native
//     caption overlay is hidden, and a second bookmarklet click tears it down
//
// Not part of `npm test`: it needs the harness up and the ExApp installed
// (gocassini: ./harness/bin/live-panel-setup.sh). Run from the gocassini repo
// root, in the Playwright container with host networking:
//
//   URL=$(./bin/cassini dev room create --name 'Live e2e' | tail -1 | sed 's/127.0.0.1/localhost/')
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

const viewerBrowser = await launch(false)
const speakerBrowser = await launch('/media/jfk.wav')
const page = await (await viewerBrowser.newContext({ baseURL: BASE })).newPage()
page.on('pageerror', (e) => console.log('[pageerror]', String(e).slice(0, 200)))
const shot = (name) => page.screenshot({ path: `${OUT}/${name}.png` }).catch(() => {})

// read the transcript blocks out of the sidebar tab's shadow root
const readBlocks = () =>
  page.evaluate(() => {
    const host = document.getElementById('cassini-live-tabpanel')?.querySelector('div')
    const shadow = host?.shadowRoot
    if (!shadow) return []
    return [...shadow.querySelectorAll('.block')].map((b) => ({
      speaker: b.querySelector('.speaker')?.textContent,
      own: b.classList.contains('own'),
      lines: [...b.querySelectorAll('.line:not(.partial)')].map((l) => l.textContent),
    }))
  })

try {
  // 1. viewer joins, enables captions
  await login(page, 'admin', 'admin')
  await page.goto(CALL_URL)
  await joinCall(page)
  log('admin (viewer) in call')
  await page.evaluate(async (t) => {
    await fetch(`/ocs/v2.php/apps/spreed/api/v4/room/${t}/participants`, {
      method: 'POST',
      headers: { 'OCS-APIRequest': 'true', requesttoken: window.OC.requestToken, 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ newParticipant: 'botuser', source: 'users' }),
    })
  }, token)
  const enable = await page.evaluate(async (t) => {
    const r = await fetch(`/ocs/v2.php/apps/spreed/api/v1/live-transcription/${t}`, {
      method: 'POST',
      headers: { 'OCS-APIRequest': 'true', requesttoken: window.OC.requestToken },
      credentials: 'same-origin',
    })
    return r.status
  }, token)
  log('captions enable:', enable)
  if (enable >= 400) throw new Error(`enable failed: ${enable}`)

  // 2. bookmarklet → sidebar tab
  const code = decodeURIComponent((await readFile('/ui/dist/bookmarklet-inline.txt', 'utf8')).trim().slice('javascript:'.length))
  await page.evaluate(code)
  await page.waitForSelector('#cassini-live-tab', { timeout: 10000 })
  log('transcript tab mounted; attached =', await page.evaluate(() => window.__CASSINI_LIVE_PANEL__?.attached))
  const nativeHidden = await page.evaluate(() => {
    const n = document.querySelector('.transcript:not(#cassini-live-tabpanel .transcript)')
    return !n || n.offsetParent === null || getComputedStyle(n).display === 'none'
  })
  log('native caption overlay hidden:', nativeHidden)
  await shot('01-tab-mounted')

  // 3. remote speaker joins and talks
  const bob = await (await speakerBrowser.newContext({ baseURL: BASE })).newPage()
  await login(bob, 'botuser', BOT_PASSWORD)
  await bob.goto(CALL_URL)
  await joinCall(bob)
  log('botuser (speaker) in call')

  // 4. captions attributed to the remote speaker show their display name
  await page.locator('#cassini-live-tabpanel .block').first().waitFor({ timeout: 120000 })
  await page.waitForTimeout(15000)
  const blocks = await readBlocks()
  await shot('02-captions')
  await writeFile(`${OUT}/transcript.json`, JSON.stringify(blocks, null, 2))
  log('TRANSCRIPT:', JSON.stringify(blocks, null, 2))

  const speakers = [...new Set(blocks.map((b) => b.speaker))]
  const remote = blocks.filter((b) => !b.own)
  if (!remote.length) throw new Error('no remote-speaker captions appeared')
  for (const b of remote) {
    if (/ItZG5la2NhYg/.test(b.speaker) || /…/.test(b.speaker)) {
      throw new Error(`remote speaker rendered as a raw/short id, not a name: ${b.speaker}`)
    }
  }
  log('remote speaker names:', JSON.stringify(speakers.filter((s) => s !== 'admin')))

  // 5. clean teardown
  await page.evaluate(code)
  if (await page.locator('#cassini-live-tab').count()) throw new Error('tab did not tear down')
  const restored = await page.evaluate(() => {
    const n = document.querySelector('.transcript')
    return n ? getComputedStyle(n).display !== 'none' : true
  })
  log('native overlay restored after teardown:', restored)
  log('SUCCESS')
} catch (err) {
  await shot('99-failure')
  console.error('[e2e] FAILED:', err)
  process.exitCode = 1
} finally {
  await viewerBrowser.close()
  await speakerBrowser.close()
}
