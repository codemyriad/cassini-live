import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const distDir = join(here, '..', 'dist')

async function bookmarkletCode(name) {
  const url = (await readFile(join(distDir, name), 'utf8')).trim()
  expect(url.startsWith('javascript:')).toBe(true)
  return decodeURIComponent(url.slice('javascript:'.length))
}

// page.evaluate goes through CDP, which is CSP-exempt — the same way a real
// javascript: bookmark is. The fixture CSP still applies to everything the
// executed code does afterwards (DOM, styles, script loads, fetch).
async function clickBookmarklet(page, name = 'bookmarklet-inline.txt') {
  await page.evaluate(await bookmarkletCode(name))
}

const sid = (page, who) => page.evaluate((w) => window.__FIXTURE__.sid[w], who)
const full = (page, who) => page.evaluate((w) => window.__FIXTURE__.full[w], who)
const setCall = (page, room, call) => page.evaluate(([r, c]) => window.__FIXTURE__.setCall(r, c), [room, call])
// append finals/joins to the server-side timeline, as cassini-live would
const append = (page, token, entries) =>
  page.evaluate(([t, e]) => window.__FIXTURE__.appendTimeline(t, e), [token, entries])
const partial = (page, id, message) =>
  page.evaluate(([i, m]) => window.__FIXTURE__.transcript({ id: i, message: m, final: false, languageId: 'en' }), [id, message])

const panel = (page) => page.locator('#cassini-live-tabpanel')
const tabButton = (page) => page.locator('#cassini-live-tab')
const lines = (page) => panel(page).locator('.block .line')
const fin = (speakerSessionId, message, t) => ({ type: 'final', speakerSessionId, message, langId: 'en', t })
const joined = (speakerSessionId, displayName, userId, t) => ({ type: 'join', speakerSessionId, displayName, userId, t })

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/call/test')
})

test('mounts as a sidebar tab styled like Talk, labelled "Live transcription"', async ({ page }) => {
  await clickBookmarklet(page)
  await expect(tabButton(page)).toBeVisible()
  await expect(tabButton(page)).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.app-sidebar-tabs__nav #cassini-live-tab')).toHaveCount(1)
  await expect(page.locator('#cassini-live-panel')).toHaveCount(0) // no floating window
  await expect(tabButton(page)).toContainText('Live transcription')
})

test('finals arrive from the polled server timeline, not from live events', async ({ page }) => {
  await clickBookmarklet(page)
  await setCall(page, 't-poll', 't-poll')
  // these were never sent as live caption events — only appended server-side
  await append(page, 't-poll', [fin(await full(page, 'bob'), 'from the server', 1), fin(await full(page, 'bob'), 'via polling', 2)])
  await expect(lines(page)).toHaveText(['from the server', 'via polling'], { timeout: 6000 })
  await expect(panel(page).locator('.block .speaker')).toHaveText(['Bob Babbage'])
})

test('a viewer sees speech from before AND after they would enable captions (the gap)', async ({ page }) => {
  await clickBookmarklet(page)
  await setCall(page, 't-gap', 't-gap')
  // Everything the server buffered for the call, regardless of who subscribed:
  // user A's speech, then user B's speech from before B enabled captions.
  await append(page, 't-gap', [
    fin(await full(page, 'alice'), 'A spoke first', 1),
    fin(await full(page, 'bob'), 'B spoke before enabling captions', 2),
    fin(await full(page, 'bob'), 'and B kept talking', 3),
  ])
  await expect(lines(page)).toHaveText(
    ['A spoke first', 'B spoke before enabling captions', 'and B kept talking'],
    { timeout: 6000 },
  )
})

test('shows a join marker per participant; the viewer’s own reads "You joined here"', async ({ page }) => {
  await clickBookmarklet(page)
  await setCall(page, 't-joins', 't-joins')
  await append(page, 't-joins', [
    joined(await full(page, 'alice'), 'Alice Lovelace', 'alice', 1),
    fin(await full(page, 'alice'), 'hello all', 2),
    joined(await full(page, 'me'), 'Mel Me', 'me', 3),
    fin(await full(page, 'me'), 'hi, just joined', 4),
    joined(await full(page, 'bob'), 'Bob Babbage', 'bob', 5),
  ])
  await expect(panel(page).locator('.marker')).toHaveText(
    ['Alice Lovelace joined', 'You joined here', 'Bob Babbage joined'],
    { timeout: 6000 },
  )
  // the viewer's own marker is accented
  await expect(panel(page).locator('.marker.you')).toHaveText('You joined here')
})

test('polling is idempotent — re-sent entries do not duplicate', async ({ page }) => {
  await clickBookmarklet(page)
  await setCall(page, 't-idem', 't-idem')
  await append(page, 't-idem', [fin(await full(page, 'bob'), 'only once', 1)])
  await expect(lines(page)).toHaveText(['only once'], { timeout: 6000 })
  // let several more polls happen; the entry must not be re-added
  await page.waitForTimeout(3000)
  await expect(lines(page)).toHaveText(['only once'])
})

test('a live partial shows transient italic and clears when the final lands', async ({ page }) => {
  await clickBookmarklet(page)
  await setCall(page, 't-partial', 't-partial')
  await partial(page, await sid(page, 'bob'), 'typing in progress')
  const p = panel(page).locator('.partial-line')
  await expect(p).toContainText('Bob Babbage')
  await expect(p).toContainText('typing in progress')
  // the committed final arrives via the timeline and clears the partial
  await append(page, 't-partial', [fin(await full(page, 'bob'), 'typing in progress done', 1)])
  await expect(lines(page)).toHaveText(['typing in progress done'], { timeout: 6000 })
  await expect(panel(page).locator('.partial-line')).toHaveCount(0)
})

test('the transcript is scoped per conversation and resets on change', async ({ page }) => {
  await clickBookmarklet(page)
  await setCall(page, 'room-a', 'room-a')
  await append(page, 'room-a', [fin(await full(page, 'bob'), 'the weather is nice', 1)])
  await expect(lines(page)).toHaveText(['the weather is nice'], { timeout: 6000 })
  await setCall(page, 'room-b', 'room-b')
  await expect(lines(page)).toHaveCount(0, { timeout: 6000 })
  await append(page, 'room-b', [fin(await full(page, 'bob'), 'that was Sunday', 1)])
  await expect(lines(page)).toHaveText(['that was Sunday'])
})

test('a new call in the same conversation starts fresh; an ended call stays', async ({ page }) => {
  await clickBookmarklet(page)
  await setCall(page, 'room-x', 'room-x')
  await append(page, 'room-x', [fin(await full(page, 'bob'), 'first call line', 1)])
  await expect(lines(page)).toHaveText(['first call line'], { timeout: 6000 })
  // call ends — transcript stays
  await page.evaluate(() => window.__FIXTURE__.endCall())
  await page.waitForTimeout(1500)
  await expect(lines(page)).toHaveText(['first call line'])
  // a new call (fresh server buffer) starts fresh
  await page.evaluate(() => window.__FIXTURE__.clearTimeline('room-x'))
  await setCall(page, 'room-x', 'room-x')
  await expect(lines(page)).toHaveCount(0, { timeout: 6000 })
})

test('the pane is removed when Talk removes its sidebar, and returns with it', async ({ page }) => {
  await clickBookmarklet(page)
  await expect(tabButton(page)).toBeVisible()
  await page.evaluate(() => window.__FIXTURE__.removeSidebar())
  await expect(tabButton(page)).toHaveCount(0, { timeout: 3000 })
  await expect(page.locator('#native-captions')).toBeVisible()
  await page.evaluate(() => window.__FIXTURE__.addSidebar())
  await expect(tabButton(page)).toBeVisible({ timeout: 3000 })
})

test('native caption overlay is hidden while mounted, restored on teardown', async ({ page }) => {
  const native = page.locator('#native-captions')
  await expect(native).toBeVisible()
  await clickBookmarklet(page)
  await expect(native).toBeHidden()
  await clickBookmarklet(page)
  await expect(native).toBeVisible()
})

test('second click tears everything down, including the partial listener', async ({ page }) => {
  await clickBookmarklet(page)
  await expect(tabButton(page)).toHaveCount(1)
  await expect.poll(() => page.evaluate(() => window.__FIXTURE__.listenerCount('transcript'))).toBe(1)
  await clickBookmarklet(page)
  await expect(tabButton(page)).toHaveCount(0)
  expect(await page.evaluate(() => window.__FIXTURE__.listenerCount('transcript'))).toBe(0)
  expect(await page.evaluate(() => window.__CASSINI_LIVE_PANEL__)).toBeFalsy()
  await clickBookmarklet(page)
  await expect(tabButton(page)).toHaveCount(1)
})

test('the loader flavour injects the ExApp-served bundle through the page CSP', async ({ page }) => {
  const errors = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text())
  })
  await clickBookmarklet(page, 'bookmarklet-loader.txt')
  await expect(tabButton(page)).toBeVisible()
  await setCall(page, 't-loader', 't-loader')
  await append(page, 't-loader', [fin(await full(page, 'alice'), 'served by the exapp', 1)])
  await expect(lines(page)).toHaveText(['served by the exapp'], { timeout: 6000 })
  expect(errors.filter((t) => t.includes('Content Security Policy'))).toHaveLength(0)
})
