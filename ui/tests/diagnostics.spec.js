import { expect, test } from '@playwright/test'
import { HOSTILE_NAME } from './fixture-server.mjs'

const PROXY = '/index.php/apps/app_api/proxy/live_transcription'
const LIVE = 'Live · updated every second'
const REFUSED = 'Open this page from Nextcloud as an administrator'

// Each test reads its own scripted feed, chosen by the /diag/<id> path prefix.
let opened = 0
const newId = (info) => `w${info.workerIndex}-${Date.now()}-${opened++}`
const control = (request, id, body) => request.post(`/fixture-diagnostics/${id}`, { data: body })

/**
 * Open the page as AppAPI serves it, recording its feed polls (with their answers) and
 * anything that went wrong: console errors, uncaught errors, CSP violations, dialogs.
 */
async function open(page, info, { file = '', before } = {}) {
  const id = newId(info)
  const seen = { id, polls: [], answers: [], errors: [], dialogs: [] }
  page.on('request', (r) => {
    if (r.url().includes('/diagnostics/api/feed')) seen.polls.push({ url: new URL(r.url()), at: Date.now() })
  })
  page.on('response', (r) => {
    if (r.url().includes('/diagnostics/api/feed')) seen.answers.push({ status: r.status(), at: Date.now() })
  })
  page.on('console', (m) => {
    if (m.type() === 'error') seen.errors.push(m.text())
  })
  page.on('pageerror', (e) => seen.errors.push(String(e)))
  page.on('dialog', (d) => {
    seen.dialogs.push(d.message())
    d.dismiss()
  })
  await page.addInitScript(() => {
    window.__violations = []
    document.addEventListener('securitypolicyviolation', (e) => {
      window.__violations.push(`${e.violatedDirective} ${e.blockedURI}`)
    })
  })
  if (before) await before(id)
  await page.goto(`/diag/${id}${PROXY}/diagnostics/${file}`)
  return seen
}

async function expectClean(page, seen) {
  expect(await page.evaluate(() => window.__violations)).toEqual([])
  expect(seen.errors).toEqual([])
  expect(seen.dialogs).toEqual([])
}

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
})

test('renders the status, hero, five pipeline cards and three panels from the feed', async ({ page }, info) => {
  const seen = await open(page, info)
  await expect(page.locator('#freshness')).toHaveText(LIVE)
  await expect(page.locator('#pill-label')).toHaveText('Captions live')
  await expect(page.locator('#pill')).toHaveAttribute('data-tone', 'live')
  await expect(page.locator('#engine')).toHaveText('Whistle · 2 CPU · 3 GiB')
  await expect(page.locator('#observe')).toBeHidden()
  await expect(page.locator('#seats')).toHaveText('2')
  await expect(page.locator('#ceiling')).toHaveText('/4')
  await expect(page.locator('#chips .chip')).toHaveCount(4)
  await expect(page.locator('#chips .chip.taken')).toHaveCount(2)
  await expect(page.locator('.stage .name')).toHaveText(['Microphones', 'Speech pieces', 'Seats', 'Engine', 'Captions'])
  await expect(page.locator('.stage').nth(1).locator('.unit')).toHaveText('per piece, 15 s max')
  await expect(page.locator('.panel h3')).toHaveText([
    'The kernel protects the call',
    'Lag keeps captions useful',
    'Only a crash stops captions',
  ])
  await expect(page.locator('#memory')).toHaveText('1.07/3 GiB')
  await expect(page.locator('#video')).toHaveText('Diagnostic video off')
  await expect(page.locator('#lanes')).toContainText('Speaker lanes')
  await expect(page.locator('#version')).toHaveText('fixture')
  // Widths go through CSSOM, which the page CSP allows; a style attribute would not apply.
  expect(await page.locator('#memory-fill').evaluate((el) => el.getBoundingClientRect().width)).toBeGreaterThan(30)
  await expectClean(page, seen)
})

test('polls a second after each answer and advances', async ({ page }, info) => {
  const seen = await open(page, info)
  const queue = page.locator('.stage').nth(1).locator('.detail li').first()
  await expect(queue).toHaveText(/^queue \d+ of 18$/)
  const first = await queue.textContent()
  await expect(queue).not.toHaveText(first, { timeout: 4000 })
  await page.waitForTimeout(3000)
  const since = seen.polls.map((p) => Number(p.url.searchParams.get('since')))
  expect(since[0]).toBe(0)
  // Each later poll asks from the newest column it holds.
  expect(since.at(-1)).toBeGreaterThan(5000)
  for (let i = 1; i < since.length; i++) expect(since[i]).toBeGreaterThanOrEqual(since[i - 1])
  for (let i = 1; i < seen.polls.length; i++) {
    expect(seen.polls[i].at - seen.polls[i - 1].at).toBeGreaterThan(900)
  }
  await expectClean(page, seen)
})

for (const file of ['', 'index.html']) {
  test(`resolves the feed and the settings link under the proxy at …/diagnostics/${file}`, async ({ page, baseURL }, info) => {
    const seen = await open(page, info, { file })
    await expect(page.locator('#freshness')).toHaveText(LIVE)
    expect(seen.polls[0].url.pathname).toBe(`/diag/${seen.id}${PROXY}/diagnostics/api/feed`)
    const settings = page.locator('a', { hasText: 'Settings' })
    expect(await settings.evaluate((a) => a.href)).toBe(`${baseURL}/diag/${seen.id}${PROXY}/settings/index.html`)
    await expectClean(page, seen)
  })
}

test('a 403 shows the administrator message and stops polling', async ({ page, request }, info) => {
  const seen = await open(page, info)
  await expect(page.locator('#freshness')).toHaveText(LIVE)
  await control(request, seen.id, { status: 403 })
  await expect(page.locator('#freshness')).toHaveText(REFUSED, { timeout: 4000 })
  await expect(page.locator('#freshness')).toHaveAttribute('data-tone', 'bad')
  await expect(page.locator('#pill-label')).toHaveText('No access')
  const polls = seen.polls.length
  await page.waitForTimeout(3000)
  expect(seen.polls.length).toBe(polls)
})

test('a 403 on the first poll stops at once, and so does a return to the tab', async ({ page }, info) => {
  const seen = await open(page, info, { before: (id) => control(page.request, id, { status: 403 }) })
  await expect(page.locator('#freshness')).toHaveText(REFUSED)
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
  await page.waitForTimeout(2500)
  expect(seen.polls.length).toBe(1)
})

test('backs off on server errors, 2 s then 4 s, and recovers', async ({ page, request }, info) => {
  test.setTimeout(45_000)
  const seen = await open(page, info)
  await expect(page.locator('#freshness')).toHaveText(LIVE)
  await control(request, seen.id, { status: 503 })
  await expect(page.locator('#freshness')).toHaveText(/^Cassini Live unreachable · last update \d+ s ago · retrying$/)
  await expect(page.locator('#freshness')).toHaveAttribute('data-tone', 'bad')
  // The last numbers stay at full contrast and say they are stale.
  const staleTag = () => page.locator('#speakers-title').evaluate((h) => getComputedStyle(h, '::after').content)
  expect(await staleTag()).toBe('" · stale"')
  expect(await page.locator('#speakers').evaluate((pane) => getComputedStyle(pane).opacity)).toBe('1')
  await expect.poll(() => seen.answers.filter((a) => a.status === 503).length, { timeout: 5000 }).toBe(2)
  await control(request, seen.id, { status: null })
  await expect(page.locator('#freshness')).toHaveText(LIVE, { timeout: 8000 })
  expect(await staleTag()).toBe('none')
  const failed = seen.answers.filter((a) => a.status === 503)
  const recovered = seen.answers.find((a) => a.status === 200 && a.at > failed[1].at)
  expect(failed[1].at - failed[0].at).toBeGreaterThan(1800)
  expect(recovered.at - failed[1].at).toBeGreaterThan(3800)
})

test('pauses while the tab is hidden and resumes at once', async ({ page }, info) => {
  const seen = await open(page, info)
  await expect(page.locator('#freshness')).toHaveText(LIVE)
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  const hidden = seen.polls.length
  await page.waitForTimeout(2500)
  expect(seen.polls.length).toBe(hidden)
  await page.evaluate(() => {
    delete document.hidden
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await expect.poll(() => seen.polls.length, { timeout: 500 }).toBeGreaterThan(hidden)
})

test('a restarted server is read again from the start', async ({ page, request }, info) => {
  const seen = await open(page, info)
  const fromZero = () => seen.polls.filter((p) => p.url.searchParams.get('since') === '0').length
  // Until the feed's first column exists the page keeps asking from 0.
  await expect.poll(() => Number(seen.polls.at(-1)?.url.searchParams.get('since')), { timeout: 5000 }).toBeGreaterThan(5000)
  const before = fromZero()
  await control(request, seen.id, { restart: true })
  await expect.poll(fromZero, { timeout: 4000 }).toBeGreaterThan(before)
  await expect(page.locator('#freshness')).toHaveText(LIVE)
  await expectClean(page, seen)
})

test('speaker names render as text, never as markup', async ({ page }, info) => {
  const seen = await open(page, info)
  await expect(page.locator('#decision-verb')).toHaveText(`admit ${HOSTILE_NAME}`)
  await expect(page.locator('#decision-time')).toHaveText('18:34:56')
  await expect(page.locator('img')).toHaveCount(0)
  await page.waitForTimeout(500)
  await expectClean(page, seen)
})

test('shows the idle and observe-only states', async ({ page, request }, info) => {
  const seen = await open(page, info)
  await control(request, seen.id, { sample: { rooms: 0 } })
  await expect(page.locator('#pill-label')).toHaveText('No captions running')
  await expect(page.locator('#pill-note')).toHaveText('The page wakes when a call enables them.')
  await control(request, seen.id, { sample: { admission: { open: false, reason: 'lag', observeOnly: true, seats: 4, ceiling: 4 } } })
  await expect(page.locator('#pill-label')).toHaveText('Would hold new voices')
  await expect(page.locator('#pill-note')).toBeHidden()
  await expect(page.locator('#observe')).toBeVisible()
  await expect(page.locator('#seats-detail')).toHaveText('would hold: lag')
  await expect(page.locator('#observe-note')).toHaveText('Cassini Live observes these rules; nothing is held or paused yet.')
  await expectClean(page, seen)
})

test('fits a 375 px phone without horizontal scrolling', async ({ page, request }, info) => {
  await page.setViewportSize({ width: 375, height: 812 })
  const seen = await open(page, info, {
    before: (id) =>
      control(page.request, id, {
        sample: {
          engine: 'an-engine-id-nobody-has-heard-of-yet-and-far-too-long-for-a-phone',
          decision: { at: 1793212496.5, verb: 'hold', who: '\u{1F600}'.repeat(40), text: 'lag 1.3 s ≥ admit 1.0 · reopens under 0.5 s for 5 s' },
        },
      }),
  })
  await expect(page.locator('#freshness')).toHaveText(LIVE)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
  await expectClean(page, seen)
})

test('is dark unless the system asks for light', async ({ page }, info) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await open(page, info)
  const background = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  expect(await background()).toBe('rgb(9, 9, 9)')
  await page.emulateMedia({ colorScheme: 'light' })
  expect(await background()).toBe('rgb(244, 244, 239)')
})
