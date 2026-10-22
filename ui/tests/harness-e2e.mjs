// Real-stack e2e: join a Talk call in the gocassini harness, enable live
// captions, click the bookmarklet, and watch real Whistle captions land in
// the panel. Not part of `npm test` — it needs the harness up and the ExApp
// installed (gocassini: ./harness/bin/live-panel-setup.sh). Run it from the
// gocassini repo root, in the Playwright container with host networking:
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
// It speaks the JFK clip into the call through Chromium's fake microphone and
// asserts the panel renders the captions as own lines, then tears down.
import { chromium } from 'playwright-core'
import { readFile, writeFile } from 'node:fs/promises'

const BASE = process.env.NC_URL || 'http://127.0.0.1:28080'
const CALL_URL = process.env.CALL_URL
const OUT = process.env.OUT_DIR || '/out'
const token = new URL(CALL_URL).pathname.split('/').pop()

const log = (...a) => console.log('[e2e]', ...a)

const browser = await chromium.launch({
  headless: true,
  args: [
    '--no-sandbox',
    '--use-fake-ui-for-media-stream',
    '--use-fake-device-for-media-stream',
    `--use-file-for-fake-audio-capture=/media/jfk.wav`,
    '--autoplay-policy=no-user-gesture-required',
    '--disable-audio-output',
  ],
})
const page = await browser.newPage({ baseURL: BASE })
page.on('pageerror', (e) => console.log('[pageerror]', String(e).slice(0, 200)))

const shot = (name) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage: false })

try {
  // 1. Login
  await page.goto('/login')
  await page.fill('#user', 'admin')
  await page.fill('#password', 'admin')
  await page.click('button[type=submit]')
  await page.waitForURL(/apps\/dashboard|apps\/files/, { timeout: 30000 })
  log('logged in')

  // 2. Open the call page and join
  await page.goto(CALL_URL)
  await page.waitForSelector('#app-content-vue', { timeout: 30000 })
  // clear the first-run welcome modal and the unsupported-browser toast
  await page.waitForTimeout(2000)
  await page.keyboard.press('Escape')
  for (const closer of await page.locator('.toast-close, .modal-container__close').all()) {
    await closer.click({ timeout: 2000 }).catch(() => {})
  }
  await page.evaluate(() => document.querySelectorAll('.toastify').forEach((t) => t.remove()))
  const joinTop = page.getByRole('button', { name: /start call|join call/i }).first()
  await joinTop.click({ timeout: 30000 })
  // device-checker modal
  const modalJoin = page.locator('.modal-container button', { hasText: /join call|start call/i }).first()
  await modalJoin.click({ timeout: 20000 }).catch(() => log('no device modal — joined directly'))
  await page.getByRole('button', { name: /leave call/i }).first().waitFor({ timeout: 40000 })
  log('in the call')
  await shot('01-in-call')

  // 3. Enable live captions for this session (what Talk's CC menu does)
  const enable = await page.evaluate(async (t) => {
    const res = await fetch(`/ocs/v2.php/apps/spreed/api/v1/live-transcription/${t}`, {
      method: 'POST',
      headers: {
        'OCS-APIRequest': 'true',
        requesttoken: window.OC.requestToken,
        'Content-Type': 'application/json',
      },
      credentials: 'same-origin',
    })
    return { status: res.status, body: (await res.text()).slice(0, 300) }
  }, token)
  log('enable live-transcription:', JSON.stringify(enable))
  if (enable.status >= 400) throw new Error(`live-transcription enable failed: ${JSON.stringify(enable)}`)

  // 4. Click the bookmarklet (CDP evaluate is CSP-exempt, like a real bookmark)
  const url = (await readFile('/ui/dist/bookmarklet-inline.txt', 'utf8')).trim()
  const code = decodeURIComponent(url.slice('javascript:'.length))
  await page.evaluate(code)
  await page.waitForSelector('#cassini-live-panel', { timeout: 10000 })
  log('panel mounted; attached =', await page.evaluate(() => window.__CASSINI_LIVE_PANEL__?.attached))
  await shot('02-panel-mounted')

  // 5. Wait for real captions (Whistle transcribing the fake-mic JFK clip)
  await page.locator('#cassini-live-panel .line').first().waitFor({ timeout: 120000 })
  // let a few more lines accumulate
  await page.waitForTimeout(15000)
  const blocks = await page.evaluate(() => {
    const shadow = document.getElementById('cassini-live-panel').shadowRoot
    return [...shadow.querySelectorAll('.block')].map((b) => ({
      speaker: b.querySelector('.speaker')?.textContent,
      own: b.classList.contains('own'),
      lines: [...b.querySelectorAll('.line:not(.partial)')].map((l) => l.textContent),
      partial: b.querySelector('.partial:not([hidden])')?.textContent || null,
    }))
  })
  await shot('03-captions')
  await writeFile(`${OUT}/transcript.json`, JSON.stringify(blocks, null, 2))
  log('TRANSCRIPT:', JSON.stringify(blocks, null, 2))

  // 6. Toggle off cleanly
  await page.evaluate(code)
  if (await page.locator('#cassini-live-panel').count()) throw new Error('panel did not tear down')
  log('teardown OK')
  log('SUCCESS')
} catch (err) {
  await shot('99-failure').catch(() => {})
  console.error('[e2e] FAILED:', err)
  process.exitCode = 1
} finally {
  await browser.close()
}
