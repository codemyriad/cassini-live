// Real-stack e2e for the diagnostics page against the gocassini harness. Proves, end to end
// through Nextcloud's AppAPI proxy:
//   - an administrator who is NOT in the call opens …/diagnostics/ and watches a live
//     caption session: the pill reads "Captions live", the speaker's lane fills with
//     captioned speech and the feed reports caption lines;
//   - that administrator is a participant of the conversation, so the lane carries the
//     speaker's display name (`names: true` in the feed);
//   - a second administrator who is not a participant sees the same lanes as
//     "Participant" (`names: false`), and the speaker's name appears nowhere on the page;
//   - the page loads under the app's CSP without violations;
//   - signed out while the page is open, it names AppAPI's refusal (Nextcloud's HTML 404)
//     instead of reporting Cassini Live as unreachable.
//
// Not part of `npm test`: needs the harness up and the ExApp installed
// (gocassini: ./harness/bin/live-panel-setup.sh) and a second admin account. Run from the
// gocassini repo root:
//
//   docker exec -u www-data -e OC_PASS='Aud1t-Pass!42' spreedtest-nextcloud-1 \
//     php occ user:add --password-from-env --group admin auditor   # once
//   URL=$(./bin/cassini dev room create --name 'Diagnostics e2e' | tail -1 | sed 's/127.0.0.1/localhost/')
//   docker run --rm --network host \
//     -v "$PWD/cassini-live/ui:/ui" \
//     -v "$PWD/cassini-live/tests/assets/jfk.wav:/media/jfk.wav:ro" \
//     -v "$PWD/.e2e-out:/out" \
//     -e NC_URL=http://localhost:28080 -e CALL_URL="$URL" \
//     -w /ui mcr.microsoft.com/playwright:v1.64.1-noble node tests/harness-diagnostics-e2e.mjs
//
// localhost, not 127.0.0.1: the HPB keys rooms by backend URL, and the ExApp joins via
// overwrite.cli.url (http://localhost:28080).
import { chromium } from 'playwright-core'
import { writeFile } from 'node:fs/promises'

const BASE = process.env.NC_URL || 'http://localhost:28080'
const CALL_URL = process.env.CALL_URL
const OUT = process.env.OUT_DIR || '/out'
const BOT_PASSWORD = process.env.BOT_PASSWORD || 'zN4vQ9mT2Kp7R1x!'
const AUDITOR = process.env.AUDITOR || 'auditor'
const AUDITOR_PASSWORD = process.env.AUDITOR_PASSWORD || 'Aud1t-Pass!42'
const PAGE = '/index.php/apps/app_api/proxy/live_transcription/diagnostics/'
const token = new URL(CALL_URL).pathname.split('/').pop()
const log = (...a) => console.log('[diag-e2e]', ...a)

const launch = (mic) =>
  chromium.launch({
    headless: true,
    args: [
      '--no-sandbox',
      ...(mic
        ? [
            '--use-fake-ui-for-media-stream',
            '--use-fake-device-for-media-stream',
            '--use-file-for-fake-audio-capture=/media/jfk.wav',
            '--autoplay-policy=no-user-gesture-required',
            '--disable-audio-output',
          ]
        : []),
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

// Opens the diagnostics page as `user` and records every feed answer and CSP violation.
async function watch(browser, user, password) {
  const page = await (await browser.newContext({ baseURL: BASE, viewport: { width: 1280, height: 1400 } })).newPage()
  const answers = []
  const problems = []
  // Nextcloud's own pages (login, dashboard) log errors of their own; only this page counts.
  const ours = () => page.url().includes(PAGE)
  page.on('pageerror', (e) => ours() && problems.push(`pageerror: ${String(e).slice(0, 200)}`))
  page.on('console', (m) => ours() && m.type() === 'error' && problems.push(`console: ${m.text().slice(0, 200)}`))
  page.on('response', async (r) => {
    if (r.url().includes('/diagnostics/api/feed')) answers.push(await r.json().catch(() => null))
  })
  await page.addInitScript(() => {
    window.__violations = []
    document.addEventListener('securitypolicyviolation', (e) => window.__violations.push(e.violatedDirective))
  })
  await login(page, user, password)
  await page.goto(PAGE)
  return { page, answers, problems }
}

const view = (page) =>
  page.evaluate(() => ({
    pill: document.getElementById('pill-label')?.textContent,
    voices: document.getElementById('voices')?.textContent,
    names: [...document.querySelectorAll('#lane-names .lane-name')].map((n) => n.textContent),
    tags: [...document.querySelectorAll('#lane-tags .lane-tag')].map((n) => n.textContent),
    captioned: document.querySelectorAll('#band rect.seg.captioned').length,
    sent: document.querySelectorAll('#graph-sent rect').length,
    decision: document.getElementById('decision')?.textContent,
    video: document.getElementById('video')?.textContent,
    text: document.body.innerText,
    violations: window.__violations || [],
  }))

async function until(page, check, what, timeout = 60000) {
  const end = Date.now() + timeout
  let last
  while (Date.now() < end) {
    last = await view(page)
    if (check(last)) return last
    await page.waitForTimeout(1000)
  }
  throw new Error(`timed out waiting for ${what}; last view: ${JSON.stringify({ ...last, text: undefined })}`)
}

const speaker = await launch(true)
const viewers = await launch(false)
let admin, auditor

try {
  // admin adds botuser to the conversation; botuser joins, turns captions on and talks
  admin = await watch(viewers, 'admin', 'admin')
  // OC.requestToken lives on Nextcloud's pages, not on the app's.
  await admin.page.goto('/apps/dashboard/')
  await admin.page.evaluate(async (t) => {
    await fetch(`/ocs/v2.php/apps/spreed/api/v4/room/${t}/participants`, {
      method: 'POST',
      headers: { 'OCS-APIRequest': 'true', requesttoken: window.OC.requestToken, 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ newParticipant: 'botuser', source: 'users' }),
    })
  }, token)
  await admin.page.goto(PAGE)

  const bob = await (await speaker.newContext({ baseURL: BASE })).newPage()
  await login(bob, 'botuser', BOT_PASSWORD)
  await bob.goto(CALL_URL)
  await joinCall(bob)
  log('captions:', await enableCaptions(bob))
  log('botuser is talking; admin watches the diagnostics page without joining the call')

  // admin: a participant of the conversation, so the lane carries botuser's name
  const seen = await until(
    admin.page,
    (v) => v.pill === 'Captions live' && v.names.includes('botuser') && v.captioned > 0 && v.sent > 0,
    'a named, captioned lane with caption lines sent',
    90000,
  )
  await admin.page.screenshot({ path: `${OUT}/diag-01-member.png`, fullPage: true })
  const memberAnswer = admin.answers.filter(Boolean).at(-1)
  log('member view:', JSON.stringify({ pill: seen.pill, names: seen.names, tags: seen.tags, captioned: seen.captioned, sent: seen.sent }))
  if (memberAnswer?.names !== true) throw new Error(`member feed answer has names=${memberAnswer?.names}`)
  if (!/Diagnostic video off|Diagnostic video/.test(seen.video || '')) throw new Error(`video line: ${seen.video}`)

  // auditor: an administrator who is not a participant reads the same lanes, unnamed
  auditor = await watch(viewers, AUDITOR, AUDITOR_PASSWORD)
  const redacted = await until(
    auditor.page,
    (v) => v.names.length > 0 && v.names.every((n) => n === 'Participant' || n === 'other call') && v.captioned > 0,
    'unnamed lanes for a non-participant administrator',
    30000,
  )
  await auditor.page.waitForTimeout(3000)
  const final = await view(auditor.page)
  await auditor.page.screenshot({ path: `${OUT}/diag-02-non-member.png`, fullPage: true })
  const auditorAnswers = auditor.answers.filter(Boolean)
  log('non-member view:', JSON.stringify({ pill: redacted.pill, names: redacted.names, tags: redacted.tags }))
  if (auditorAnswers.some((a) => a.names !== false)) throw new Error('a non-member feed answer carried names')
  if (auditorAnswers.some((a) => JSON.stringify(a).includes('botuser'))) throw new Error('a non-member feed answer mentions botuser')
  if (final.text.includes('botuser')) throw new Error('the non-member page shows botuser')

  for (const [who, w] of [['admin', admin], ['auditor', auditor]]) {
    const v = await view(w.page)
    if (v.violations.length) throw new Error(`${who}: CSP violations ${v.violations}`)
    if (w.problems.length) throw new Error(`${who}: ${w.problems.join('; ')}`)
  }

  // Signed out while the page is open: AppAPI answers with Nextcloud's HTML 404, which the
  // page must name as a refusal, not as Cassini Live being down.
  await auditor.page.context().clearCookies()
  const out = await auditor.page.waitForFunction(
    () => /Nextcloud refused the feed/.test(document.getElementById('freshness')?.textContent || ''),
    null,
    { timeout: 15000 },
  ).then(() => auditor.page.evaluate(() => document.getElementById('freshness').textContent))
  log('signed out:', out)
  await writeFile(`${OUT}/diag-feed-member.json`, JSON.stringify(memberAnswer, null, 2))
  log('SUCCESS')
} catch (err) {
  for (const [n, w] of [['admin', admin], ['auditor', auditor]]) {
    await w?.page.screenshot({ path: `${OUT}/diag-99-${n}-failure.png`, fullPage: true }).catch(() => {})
  }
  console.error('[diag-e2e] FAILED:', err)
  process.exitCode = 1
} finally {
  await speaker.close()
  await viewers.close()
}
