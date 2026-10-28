import { expect, test } from '@playwright/test'
import { TEN_LANES } from './diagnostics-fixtures.mjs'
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
  await expect(page.locator('#lanes-title')).toHaveText('Speakers, last two minutes')
  await expect(page.locator('#lane-names .lane-name')).toHaveText([HOSTILE_NAME, 'Bo'])
  await expect(page.locator('#lane-tags .tag-name')).toHaveText(['scribe 1', 'scribe 2'])
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

test("Nextcloud's own 404 page names the likely causes, keeps retrying, and recovers", async ({ page, request }, info) => {
  test.setTimeout(45_000)
  const seen = await open(page, info)
  await expect(page.locator('#freshness')).toHaveText(LIVE)
  // How AppAPI's proxy refuses a signed-out user, a non-administrator or a disabled ExApp.
  await control(request, seen.id, { status: 404, html: true })
  await expect(page.locator('#freshness')).toHaveText(
    /^Nextcloud refused the feed \(signed out, not an administrator, or Cassini Live disabled\) · last update \d+ s ago · retrying$/,
  )
  await expect(page.locator('#freshness')).toHaveAttribute('data-tone', 'bad')
  await expect(page.locator('#pill-label')).not.toHaveText('No access')
  await expect.poll(() => seen.answers.filter((a) => a.status === 404).length, { timeout: 5000 }).toBe(2)
  const refused = seen.answers.filter((a) => a.status === 404)
  expect(refused[1].at - refused[0].at).toBeGreaterThan(1800)
  // Signing in again in another tab brings the feed back without a reload.
  await control(request, seen.id, { status: null })
  await expect(page.locator('#freshness')).toHaveText(LIVE, { timeout: 8000 })
})

test("the app's own JSON 404 reads as Cassini Live unreachable", async ({ page, request }, info) => {
  const seen = await open(page, info)
  await expect(page.locator('#freshness')).toHaveText(LIVE)
  await control(request, seen.id, { status: 404 })
  await expect(page.locator('#freshness')).toHaveText(/^Cassini Live unreachable · last update \d+ s ago · retrying$/)
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
  await expect(page.locator('#lane-names .lane-name').first()).toHaveText(HOSTILE_NAME)
  await expect(page.locator('#lane-tags li').first()).toContainText(HOSTILE_NAME)
  await expect(page.locator('#decision-time')).toHaveText('18:34:56 UTC')
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

/* ---------- speaker lanes and the lag graph ---------- */

const TEN = { history: 120, sample: { lanes: TEN_LANES, silentMics: 3, hiddenSpeakers: 2 } }
const NAMES = ['Ana Ferreira', 'Bo', 'Chidi Okonkwo', 'Dana Whitfield', 'Emeka Adeyemi', 'Guest · Farah', 'Maximiliane Theodora von Wolkenstein-Rhe', 'Jun', 'Participant', 'other call']

// Each lane's runs, oldest first, as drawn: their edges in seconds before the band's right
// ("now") edge, from where they land on screen.
const drawnRuns = (page) => page.locator('#band').evaluate((band) => {
  const box = band.getBoundingClientRect()
  const perS = box.width / band.viewBox.baseVal.width
  const age = (x) => (box.right - x) / perS
  return Array.from(band.children, (lane) => Array.from(lane.querySelectorAll('rect.seg'), (rect) => {
    const edges = rect.getBoundingClientRect()
    return { state: rect.classList[1], oldest: age(edges.left), newest: age(edges.right) }
  }))
})

// The lag graph on screen: its threshold lines' y, each polyline's points, its box.
const drawnGraph = (page) => page.locator('#graph').evaluate((svg) => {
  const toScreen = svg.getScreenCTM()
  const at = (x, y) => {
    const point = new DOMPoint(x, y).matrixTransform(toScreen)
    return { x: point.x, y: point.y }
  }
  const lineY = (id) => at(0, document.getElementById(id).y1.baseVal.value).y
  const box = svg.getBoundingClientRect()
  return {
    shed: lineY('graph-shed'),
    admit: lineY('graph-admit'),
    lines: Array.from(svg.querySelectorAll('#graph-lines polyline'), (line) => Array.from(line.points, (p) => at(p.x, p.y))),
    top: box.top,
    bottom: box.bottom,
    right: box.right,
    perS: box.width / svg.viewBox.baseVal.width,
  }
})

test('draws ten lanes: names, tags, every state, the lag graph', async ({ page }, info) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  const openedAt = Date.now()
  const seen = await open(page, info, { before: (id) => control(page.request, id, TEN) })
  await expect(page.locator('#lane-names .lane-name')).toHaveText(NAMES)
  await expect(page.locator('#lane-names .lane-name.other')).toHaveText(['other call'])
  await expect(page.locator('#lane-tags .tag-name')).toHaveText(['scribe 1', 'scribe 2', 'paused', 'held', 'scribe 3', 'idle', 'idle', 'scribe 4', 'idle', 'scribe 5'])
  await expect(page.locator('#lane-tags .tag-value')).toHaveText(['0.3 s', '0.0 s', '2.4 s', '1.6 s', '0.8 s', '', '', '1.1 s', '', '0.5 s'])
  await expect(page.locator('#lane-tags li').nth(2)).toHaveAttribute('data-kind', 'paused')
  await expect(page.locator('#left-out')).toHaveText('+3 silent mics · +2 more speakers')
  await expect(page.locator('#lanes-empty')).toBeHidden()
  await expect(page.locator('#lanes')).toHaveAttribute('data-density', 'dense')
  await expect(page.locator('#band')).toHaveAttribute('aria-label', '10 speakers in the last two minutes; 5 captioned now; 1 held; 1 paused')
  await expect(page.locator('#band')).toHaveAttribute('role', 'img')
  await expect(page.locator('#band > g')).toHaveCount(10)
  for (const state of ['captioned', 'held', 'paused', 'lost']) {
    await expect(page.locator(`#band rect.seg.${state}`).first()).toBeAttached()
  }
  // The theme colours the runs through their classes (the dark tokens: cyan, the red of lost audio).
  expect(await page.locator('#band rect.seg.captioned').first().evaluate((r) => getComputedStyle(r).fill)).toBe('rgb(131, 205, 223)')
  expect(await page.locator('#band rect.seg.lost').first().evaluate((r) => getComputedStyle(r).fill)).toBe('rgb(255, 111, 76)')
  expect(await page.locator('#band rect.seg.held').first().evaluate((r) => getComputedStyle(r).fill)).toBe('none')
  // Held is the teal outline, paused the dark amber fill inside an amber edge.
  const paint = (selector) => page.locator(selector).first().evaluate((r) => {
    const style = getComputedStyle(r)
    return [style.fill, style.stroke, style.strokeWidth]
  })
  expect(await paint('#band rect.seg.held')).toEqual(['none', 'rgb(79, 138, 153)', '3px'])
  expect(await paint('#band rect.seg.paused')).toEqual(['rgb(107, 79, 5)', 'rgb(252, 183, 0)', '2px'])
  await expect(page.locator('#lane-tags')).toBeVisible()

  // Runs sit where the video draws them: newest against "now" at the right, oldest left.
  const runs = await drawnRuns(page)
  const watched = (Date.now() - openedAt) / 1000
  // Chidi was paused for the first answer's last twelve columns, and has been since.
  const paused = runs[2].at(-1)
  expect(paused.state).toBe('paused')
  expect(paused.newest).toBeGreaterThanOrEqual(0)
  expect(paused.newest).toBeLessThan(1.25)
  expect(paused.oldest).toBeGreaterThan(11.9)
  expect(paused.oldest).toBeLessThan(13 + watched)
  expect(runs[0].length).toBeGreaterThan(2)
  expect(runs[0][0].oldest).toBeGreaterThan(runs[0].at(-1).oldest)
  for (const lane of runs) {
    for (let i = 1; i < lane.length; i++) expect(lane[i].oldest).toBeLessThanOrEqual(lane[i - 1].newest + 0.01)
  }

  // The lag graph: the shed line 18 px (1 s) over the admit line; time runs left to right
  // up to "now"; a lag over the 2.5 s ceiling is drawn at it, 9 px over the shed line;
  // calm lag under the admit line.
  const graph = await drawnGraph(page)
  expect(graph.shed).toBeLessThan(graph.admit)
  expect(Math.abs(graph.admit - graph.shed - 18)).toBeLessThanOrEqual(1)
  expect(graph.lines.length).toBeGreaterThan(0)
  for (const line of graph.lines) expect(line.at(-1).x).toBeGreaterThan(line[0].x)
  // The newest column's middle, or the one before it when the newest has no reading.
  expect((graph.right - graph.lines.at(-1).at(-1).x) / graph.perS).toBeLessThan(2.6)
  const ys = graph.lines.flat().map((point) => point.y)
  expect(Math.abs(graph.shed - Math.min(...ys) - 9)).toBeLessThanOrEqual(1)
  expect(Math.max(...ys)).toBeGreaterThan(graph.admit)
  for (const y of ys) {
    expect(y).toBeGreaterThanOrEqual(graph.top)
    expect(y).toBeLessThanOrEqual(graph.bottom)
  }
  await expect(page.locator('#graph polyline').first()).toBeAttached()
  expect(await page.locator('#graph polyline').first().evaluate((l) => l.points.length)).toBeGreaterThan(50)
  await expect(page.locator('#graph-sheds line').first()).toBeAttached()
  await expect(page.locator('#graph-sent rect').first()).toBeAttached()
  await expect(page.locator('#graph')).toHaveAttribute('aria-label', /^Caption lag over the last two minutes; /)
  await expect(page.locator('.graph-legend li')).toHaveText(['shed 2.0 s', 'admit 1.0 s', '▮ caption sent'])
  await expect(page.locator('.axis span')).toHaveText(['−2 min', '−1 min', 'now'])
  await expect(page.locator('.legend li')).toHaveText(['captioned', 'held', 'paused', 'lost'])

  // Each name and tag sits on its own lane.
  const middles = (selector) => page.locator(selector).evaluateAll((nodes) => nodes.map((n) => {
    const box = n.getBoundingClientRect()
    return Math.round(box.top + box.height / 2)
  }))
  const lanes = await middles('#band rect.lane-bg')
  for (const selector of ['#lane-names li', '#lane-tags li']) {
    const rows = await middles(selector)
    expect(rows.length).toBe(10)
    rows.forEach((middle, i) => expect(Math.abs(middle - lanes[i])).toBeLessThanOrEqual(1))
  }

  // The DOM stays the same size from one answer to the next: nodes are reused, not added.
  const nodes = () => page.locator('#lanes *').count()
  const before = await nodes()
  const lines = await page.locator('#graph-lines > *').count()
  const head = seen.polls.length
  await expect.poll(() => seen.polls.length, { timeout: 4000 }).toBeGreaterThan(head + 2)
  expect(Math.abs((await nodes()) - before)).toBeLessThan(20)
  expect(await page.locator('#band rect').count()).toBeLessThan(10 * 20)
  // The fixed parts exactly: one leaked node per answer would show here.
  await expect(page.locator('#legend li')).toHaveCount(4)
  await expect(page.locator('#lane-names li')).toHaveCount(10)
  await expect(page.locator('#lane-tags li')).toHaveCount(10)
  await expect(page.locator('#band > g')).toHaveCount(10)
  expect(await page.locator('#graph-lines > *').count()).toBe(lines)
  await expectClean(page, seen)
})

// How far back the runs and the lag line reach, in seconds.
const reach = async (page) => {
  const runs = (await drawnRuns(page)).flat()
  const graph = await drawnGraph(page)
  return {
    runs: runs.length,
    lanes: Math.max(0, ...runs.map((run) => run.oldest)),
    graph: Math.max(0, ...graph.lines.flat().map((point) => (graph.right - point.x) / graph.perS)),
  }
}

const STARTS_OVER = [
  // A restarted container keeps the host's monotonic clock, and the new process numbers
  // its speakers from 1 again: the old lane 1's runs were someone else's.
  ['relaunch', 'a Cassini Live restart that keeps the clock draws the lanes afresh'],
  // A feed nobody polled for 30 s stopped, and the seconds until it started again were
  // never sampled.
  ['idle', 'a feed that stopped and started again draws the lanes from its new start'],
]

for (const [how, title] of STARTS_OVER) {
  test(title, async ({ page, request }, info) => {
    const seen = await open(page, info, { before: (id) => control(page.request, id, TEN) })
    await expect.poll(async () => (await reach(page)).lanes, { timeout: 5000 }).toBeGreaterThan(60)
    expect((await reach(page)).graph).toBeGreaterThan(60)
    const polls = seen.polls.length
    const relaunched = Date.now()
    await control(request, seen.id, { [how]: true })
    await expect.poll(() => seen.polls.length, { timeout: 10000 }).toBeGreaterThan(polls + 5)
    const drawn = await reach(page)
    const since = (Date.now() - relaunched) / 1000
    expect(drawn.runs).toBeGreaterThan(0)
    expect(drawn.lanes).toBeLessThan(since)
    expect(drawn.graph).toBeLessThan(since)
    await expect(page.locator('#lane-names li')).toHaveCount(10)
    await expectClean(page, seen)
  })
}

test('a restart while the tab was hidden draws the lanes afresh, though the new feed has columns', async ({ page, request }, info) => {
  // Another administrator kept the restarted container's feed running, so the page's first
  // answer when it comes back already holds new columns, on the same clock: only the
  // instance says that lane 1 is someone else now.
  const seen = await open(page, info, { before: (id) => control(page.request, id, TEN) })
  const heads = new Map()
  page.on('response', (r) => {
    if (!r.url().includes('/diagnostics/api/feed')) return
    const since = new URL(r.url()).searchParams.get('since')
    r.json().then((body) => heads.set(since, body.head), () => {})
  })
  await expect.poll(async () => (await reach(page)).lanes, { timeout: 5000 }).toBeGreaterThan(60)
  const setHidden = (hidden) => page.evaluate((hidden) => {
    if (hidden) Object.defineProperty(document, 'hidden', { configurable: true, get: () => true })
    else delete document.hidden
    document.dispatchEvent(new Event('visibilitychange'))
  }, hidden)
  await setHidden(true)
  const relaunched = Date.now()
  await control(request, seen.id, { relaunch: true })
  // The new feed's first column comes three seconds after its start.
  await page.waitForTimeout(4500)
  const polls = seen.polls.length
  await setHidden(false)
  await expect.poll(() => seen.polls.length, { timeout: 10000 }).toBeGreaterThan(polls + 3)
  const [back, again] = seen.polls.slice(polls).map((p) => p.url.searchParams.get('since'))
  // It asked from the newest column it held and got columns, then asked again from the start.
  expect(Number(back)).toBeGreaterThan(5000)
  expect(heads.get(back)).toBeGreaterThan(Number(back))
  expect(again).toBe('0')
  const drawn = await reach(page)
  const since = (Date.now() - relaunched) / 1000
  expect(drawn.runs).toBeGreaterThan(0)
  expect(drawn.lanes).toBeLessThan(since)
  expect(drawn.graph).toBeLessThan(since)
  await expect(page.locator('#lane-names li')).toHaveCount(10)
  await expectClean(page, seen)
})

test('redacted names read Participant; another call reads other call', async ({ page }, info) => {
  const lanes = [{ ...TEN_LANES[0], name: 'Participant' }, { ...TEN_LANES[1], name: undefined }, TEN_LANES[9]]
  const seen = await open(page, info, { before: (id) => control(page.request, id, { history: 30, sample: { lanes } }) })
  await expect(page.locator('#lane-names .lane-name')).toHaveText(['Participant', 'Participant', 'other call'])
  await expect(page.locator('#lanes')).toHaveAttribute('data-density', 'roomy')
  await expectClean(page, seen)
})

test('an empty band counts open microphones, or says there are none', async ({ page, request }, info) => {
  const seen = await open(page, info, { before: (id) => control(page.request, id, { sample: { lanes: [], silentMics: 3, hiddenSpeakers: 0 } }) })
  await expect(page.locator('#lanes-empty')).toHaveText('Nobody spoke in the last two minutes · 3 mics open')
  await expect(page.locator('#band')).toHaveAttribute('aria-label', 'Nobody spoke in the last two minutes · 3 mics open')
  // Said once, by the band; no empty "Speakers now" list after it.
  await expect(page.locator('#lanes .lanes-grid')).toMatchAriaSnapshot(`
    - /children: equal
    - img "Nobody spoke in the last two minutes · 3 mics open"
    - img /^Caption lag over the last two minutes; /
    - list:
      - listitem: shed 2.0 s
      - listitem: admit 1.0 s
      - listitem: caption sent
  `)
  await expect(page.locator('#lanes-empty')).toHaveAttribute('aria-hidden', 'true')
  await expect(page.locator('#lane-tags')).toBeHidden()
  await expect(page.locator('#left-out')).toBeHidden()
  await expect(page.locator('#lane-names li')).toHaveCount(0)
  await expect(page.locator('#band > g')).toHaveCount(0)
  await control(request, seen.id, { sample: { lanes: [], silentMics: 0, hiddenSpeakers: 0 } })
  await expect(page.locator('#lanes-empty')).toHaveText('No microphones open')
  await expectClean(page, seen)
})

test('long, emoji and right-to-left names fit a 375 px phone', async ({ page }, info) => {
  await page.setViewportSize({ width: 375, height: 812 })
  const lanes = TEN_LANES.map((lane, i) => (i === 0 ? { ...lane, name: '\u{1F600}'.repeat(40) } : i === 1 ? { ...lane, name: 'عبد الرحمن بن محمد الشريف الحسيني' } : lane))
  const seen = await open(page, info, { before: (id) => control(page.request, id, { ...TEN, sample: { ...TEN.sample, lanes } }) })
  await expect(page.locator('#lane-names li')).toHaveCount(10)
  await expect(page.locator('#lane-names .lane-name').first()).toHaveText('\u{1F600}'.repeat(40))
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(overflow).toBeLessThanOrEqual(0)
  const band = await page.locator('#band').boundingBox()
  const chips = await page.locator('#lane-names .lane-name').evaluateAll((nodes) => nodes.map((n) => {
    const style = getComputedStyle(n)
    return { ...n.getBoundingClientRect().toJSON(), padding: parseFloat(style.paddingLeft) + parseFloat(style.paddingRight), text: n.textContent, cut: n.scrollWidth > n.clientWidth }
  }))
  for (const box of chips) {
    // The name gets a quarter of the band (the chip its padding besides, as the video
    // fits it), so a long name never hides the recent history.
    expect(box.width - box.padding).toBeLessThanOrEqual(band.width / 4 + 1)
    expect(box.left).toBeGreaterThanOrEqual(band.x)
  }
  // The page's own label for another call's speaker is shown whole.
  expect(chips.filter((box) => box.text === 'other call').map((box) => box.cut)).toEqual([false])
  expect(chips[0].cut).toBe(true)
  const spill = await page.locator('#lane-tags li, .graph-legend, .lanes-head').evaluateAll((nodes) => nodes.filter((n) => n.scrollWidth > n.clientWidth + 1).map((n) => n.textContent))
  expect(spill).toEqual([])
  await expectClean(page, seen)
})
