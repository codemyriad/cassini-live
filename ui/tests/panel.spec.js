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

const full = (page, who) => page.evaluate((w) => window.__FIXTURE__.full[w], who)
const setCall = (page, room, call) => page.evaluate(([r, c]) => window.__FIXTURE__.setCall(r, c), [room, call])
// Enter a call and wait until the session has reset + started it — observable
// via auto-enable, which fires in startCall — before emitting any live finals,
// so they are not reset away by a startCall that is still a tick behind.
async function enterCall(page, room) {
  await setCall(page, room, room)
  await expect.poll(() => page.evaluate(() => window.__FIXTURE__.enabledTokens())).toContain(room)
}
// the live HPB feed: a raw signalling 'message' (finals carry seq, like real Talk)
const liveFinal = (page, seq, sidv, message) =>
  page.evaluate(([s, id, m]) => window.__FIXTURE__.message({ type: 'transcript', final: true, seq: s, speakerSessionId: id, message: m, langId: 'en' }), [seq, sidv, message])
const livePartial = (page, sidv, message) =>
  page.evaluate(([id, m]) => window.__FIXTURE__.message({ type: 'transcript', final: false, speakerSessionId: id, message: m, langId: 'en' }), [sidv, message])
// the /history poll source
const append = (page, token, entries) =>
  page.evaluate(([t, e]) => window.__FIXTURE__.appendTimeline(t, e), [token, entries])

const panel = (page) => page.locator('#cassini-live-tabpanel')
const tabButton = (page) => page.locator('#cassini-live-tab')
const lines = (page) => panel(page).locator('.block .line')
const fin = (seq, speakerSessionId, message, t) => ({ seq, type: 'final', speakerSessionId, message, langId: 'en', t })
const joined = (seq, speakerSessionId, displayName, userId, t) => ({ seq, type: 'join', speakerSessionId, displayName, userId, t })

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/call/test')
})

test('mounts as a sidebar tab styled like Talk, labelled "Live transcription"', async ({ page }) => {
  await clickBookmarklet(page)
  await expect(tabButton(page)).toBeVisible()
  await expect(tabButton(page)).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.app-sidebar-tabs__nav #cassini-live-tab')).toHaveCount(1)
  await expect(page.locator('#cassini-live-panel')).toHaveCount(0)
  await expect(tabButton(page)).toContainText('Live transcription')
})

test('activating the panel turns Talk live captions on for the conversation', async ({ page }) => {
  await clickBookmarklet(page)
  await setCall(page, 't-enable', 't-enable')
  await expect
    .poll(() => page.evaluate(() => window.__FIXTURE__.enabledTokens()))
    .toContain('t-enable')
})

test('the live HPB feed renders finals (with seq) near-instantly, names resolved', async ({ page }) => {
  await clickBookmarklet(page)
  await enterCall(page, 't-live')
  await liveFinal(page, 1, await full(page, 'bob'), 'live one')
  await liveFinal(page, 2, await full(page, 'bob'), 'live two')
  await expect(lines(page)).toHaveText(['live one', 'live two'], { timeout: 3000 })
  await expect(panel(page).locator('.block .speaker')).toHaveText(['Bob Babbage'])
})

test('a gap in the live feed is held, then filled in order by the poll', async ({ page }) => {
  await clickBookmarklet(page)
  await enterCall(page, 't-gap')
  await liveFinal(page, 1, await full(page, 'bob'), 'one')
  await liveFinal(page, 3, await full(page, 'bob'), 'three') // seq 2 missed → held
  await expect(lines(page)).toHaveText(['one'], { timeout: 3000 })
  // the server timeline has 2 and 3; the poll fills the gap, then 3 flushes
  await append(page, 't-gap', [fin(2, await full(page, 'bob'), 'two'), fin(3, await full(page, 'bob'), 'three')])
  await expect(lines(page)).toHaveText(['one', 'two', 'three'], { timeout: 8000 })
})

test('the live feed and the poll never double-count a shared seq', async ({ page }) => {
  await clickBookmarklet(page)
  await enterCall(page, 't-dedup')
  await liveFinal(page, 1, await full(page, 'bob'), 'one')
  await liveFinal(page, 2, await full(page, 'bob'), 'two')
  // the server also holds 1..3; the poll should add only 3
  await append(page, 't-dedup', [fin(1, await full(page, 'bob'), 'one'), fin(2, await full(page, 'bob'), 'two'), fin(3, await full(page, 'bob'), 'three')])
  await expect(lines(page)).toHaveText(['one', 'two', 'three'], { timeout: 8000 })
  await page.waitForTimeout(3500) // another poll must not duplicate
  await expect(lines(page)).toHaveText(['one', 'two', 'three'])
})

test('a viewer sees speech from before AND after they enabled captions (the gap)', async ({ page }) => {
  await clickBookmarklet(page)
  await setCall(page, 't-catchup', 't-catchup')
  // what the server buffered before this viewer was on the feed (poll delivers it)
  await append(page, 't-catchup', [
    fin(1, await full(page, 'alice'), 'A spoke first'),
    fin(2, await full(page, 'bob'), 'B before enabling captions'),
  ])
  await expect(lines(page)).toHaveText(['A spoke first', 'B before enabling captions'], { timeout: 8000 })
  // then the live feed continues
  await liveFinal(page, 3, await full(page, 'bob'), 'B after enabling captions')
  await expect(lines(page)).toHaveText(
    ['A spoke first', 'B before enabling captions', 'B after enabling captions'],
    { timeout: 3000 },
  )
})

test('shows a join marker per participant; the viewer’s own reads "You joined here"', async ({ page }) => {
  await clickBookmarklet(page)
  await setCall(page, 't-joins', 't-joins')
  await append(page, 't-joins', [
    joined(1, await full(page, 'alice'), 'Alice Lovelace', 'alice'),
    fin(2, await full(page, 'alice'), 'hello all'),
    joined(3, await full(page, 'me'), 'Mel Me', 'me'),
    joined(4, await full(page, 'bob'), 'Bob Babbage', 'bob'),
  ])
  await expect(panel(page).locator('.marker')).toHaveText(
    ['Alice Lovelace joined', 'You joined here', 'Bob Babbage joined'],
    { timeout: 8000 },
  )
  await expect(panel(page).locator('.marker.you')).toHaveText('You joined here')
})

test('a live partial shows transient italic and clears when the final lands', async ({ page }) => {
  await clickBookmarklet(page)
  await enterCall(page, 't-partial')
  await livePartial(page, await full(page, 'bob'), 'typing in progress')
  const p = panel(page).locator('.partial-line')
  await expect(p).toContainText('Bob Babbage')
  await expect(p).toContainText('typing in progress')
  await liveFinal(page, 1, await full(page, 'bob'), 'typing in progress done')
  await expect(lines(page)).toHaveText(['typing in progress done'], { timeout: 3000 })
  await expect(panel(page).locator('.partial-line')).toHaveCount(0)
})

test('the transcript is scoped per conversation and resets on change', async ({ page }) => {
  await clickBookmarklet(page)
  await enterCall(page, 'room-a')
  await liveFinal(page, 1, await full(page, 'bob'), 'the weather is nice')
  await expect(lines(page)).toHaveText(['the weather is nice'], { timeout: 3000 })
  await enterCall(page, 'room-b')
  await expect(lines(page)).toHaveCount(0, { timeout: 6000 })
  await liveFinal(page, 1, await full(page, 'bob'), 'that was Sunday')
  await expect(lines(page)).toHaveText(['that was Sunday'], { timeout: 3000 })
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

test('second click tears everything down, including the live feed listener', async ({ page }) => {
  await clickBookmarklet(page)
  await expect(tabButton(page)).toHaveCount(1)
  await expect.poll(() => page.evaluate(() => window.__FIXTURE__.listenerCount('message'))).toBe(1)
  await clickBookmarklet(page)
  await expect(tabButton(page)).toHaveCount(0)
  expect(await page.evaluate(() => window.__FIXTURE__.listenerCount('message'))).toBe(0)
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
  await enterCall(page, 't-loader')
  await liveFinal(page, 1, await full(page, 'alice'), 'served by the exapp')
  await expect(lines(page)).toHaveText(['served by the exapp'], { timeout: 3000 })
  expect(errors.filter((t) => t.includes('Content Security Policy'))).toHaveLength(0)
})
