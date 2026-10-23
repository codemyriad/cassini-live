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

const transcript = (page, data) => page.evaluate((d) => window.__FIXTURE__.transcript(d), data)
const sid = (page, who) => page.evaluate((w) => window.__FIXTURE__.sid[w], who)
const setCall = (page, room, call) => page.evaluate(([r, c]) => window.__FIXTURE__.setCall(r, c), [room, call])
const panel = (page) => page.locator('#cassini-live-tabpanel')
const tabButton = (page) => page.locator('#cassini-live-tab')
const lines = (page) => panel(page).locator('.line:not(.partial)')

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/call/test')
})

test('mounts as a sidebar tab styled like Talk, labelled "Live transcription"', async ({ page }) => {
  await clickBookmarklet(page)
  await expect(tabButton(page)).toBeVisible()
  await expect(tabButton(page)).toHaveAttribute('aria-selected', 'true')
  // sits in the tab nav, not a floating window, and carries the label
  await expect(page.locator('.app-sidebar-tabs__nav #cassini-live-tab')).toHaveCount(1)
  await expect(page.locator('#cassini-live-panel')).toHaveCount(0)
  await expect(tabButton(page)).toContainText('Live transcription')
})

test('remote speakers resolve to display names, not raw ids (the bug)', async ({ page }) => {
  await clickBookmarklet(page)
  await transcript(page, { id: await sid(page, 'bob'), message: 'from the roster', final: true, languageId: 'en' })
  await transcript(page, { id: await sid(page, 'carol'), message: 'from the dom card', final: true, languageId: 'en' })
  await transcript(page, { id: await sid(page, 'me'), message: 'my own line', final: true, languageId: 'en' })
  const speakers = panel(page).locator('.block .speaker')
  await expect(speakers).toHaveText(['Bob Babbage', 'Carol From The DOM', 'Mel Me'])
  for (const name of await speakers.allTextContents()) expect(name).not.toContain('ItZG5la2NhYg')
  const own = panel(page).locator('.block.own')
  await expect(own.locator('.speaker')).toHaveText('Mel Me')
  await expect(own.locator('.you-chip')).toBeVisible()
})

test('a late joiner is caught up: history above, "You joined here" divider, live below', async ({ page }) => {
  await clickBookmarklet(page)
  // Enter a call whose conversation has earlier finals (the fixture serves them
  // from /history for a token containing "hist").
  await setCall(page, 'call-hist-1', 'call-hist-1')
  // the two earlier lines arrive as history
  await expect(lines(page)).toHaveText(['before you joined one', 'before you joined two'], { timeout: 6000 })
  await expect(panel(page).locator('.marker')).toHaveCount(0) // no divider until a live line
  // a live line now draws the divider before it
  await transcript(page, { id: await sid(page, 'bob'), message: 'live after joining', final: true, languageId: 'en' })
  await expect(panel(page).locator('.marker')).toHaveText('You joined here')
  // divider sits between the history and the live line
  const texts = await panel(page).locator('.marker, .line:not(.partial)').allTextContents()
  expect(texts).toEqual([
    'before you joined one',
    'before you joined two',
    'You joined here',
    'live after joining',
  ])
})

test('no history means no divider (you were there from the start)', async ({ page }) => {
  await clickBookmarklet(page)
  await setCall(page, 'call-empty-1', 'call-empty-1')
  await transcript(page, { id: await sid(page, 'bob'), message: 'first thing said', final: true, languageId: 'en' })
  await expect(lines(page)).toHaveText(['first thing said'])
  await expect(panel(page).locator('.marker')).toHaveCount(0)
})

test('the transcript is scoped per conversation and resets on change', async ({ page }) => {
  await clickBookmarklet(page)
  await setCall(page, 'room-a', 'room-a')
  await transcript(page, { id: await sid(page, 'bob'), message: 'the weather is nice', final: true, languageId: 'en' })
  await expect(lines(page)).toHaveText(['the weather is nice'])
  // switch to a different conversation: the old lines must not carry over
  await setCall(page, 'room-b', 'room-b')
  await expect(lines(page)).toHaveCount(0, { timeout: 6000 })
  await transcript(page, { id: await sid(page, 'bob'), message: 'that was Sunday', final: true, languageId: 'en' })
  await expect(lines(page)).toHaveText(['that was Sunday'])
})

test('a new call in the same conversation starts fresh', async ({ page }) => {
  await clickBookmarklet(page)
  await setCall(page, 'room-x', 'room-x')
  await transcript(page, { id: await sid(page, 'bob'), message: 'first call line', final: true, languageId: 'en' })
  await expect(lines(page)).toHaveText(['first call line'])
  // call ends — the transcript stays on screen
  await page.evaluate(() => window.__FIXTURE__.endCall())
  await page.waitForTimeout(1500)
  await expect(lines(page)).toHaveText(['first call line'])
  // a new call in the same room starts fresh
  await setCall(page, 'room-x', 'room-x')
  await expect(lines(page)).toHaveCount(0, { timeout: 6000 })
})

test('the pane is removed when Talk removes its sidebar, and returns with it', async ({ page }) => {
  await clickBookmarklet(page)
  await expect(tabButton(page)).toBeVisible()
  await page.evaluate(() => window.__FIXTURE__.removeSidebar())
  await expect(tabButton(page)).toHaveCount(0, { timeout: 3000 })
  // native captions are restored while our pane is gone
  await expect(page.locator('#native-captions')).toBeVisible()
  await page.evaluate(() => window.__FIXTURE__.addSidebar())
  await expect(tabButton(page)).toBeVisible({ timeout: 3000 })
})

test('partials render italic in place and finals commit them', async ({ page }) => {
  await clickBookmarklet(page)
  await transcript(page, { id: await sid(page, 'alice'), message: 'typing someth', final: false, languageId: 'en' })
  const partial = panel(page).locator('.partial:visible')
  await expect(partial).toHaveText('typing someth')
  await transcript(page, { id: await sid(page, 'alice'), message: 'typing something longer', final: false, languageId: 'en' })
  await expect(partial).toHaveText('typing something longer')
  await transcript(page, { id: await sid(page, 'alice'), message: 'typing something longer, done.', final: true, languageId: 'en' })
  await expect(panel(page).locator('.partial:visible')).toHaveCount(0)
  await expect(lines(page)).toHaveText(['typing something longer, done.'])
})

test('native caption overlay is hidden while mounted, restored on teardown', async ({ page }) => {
  const native = page.locator('#native-captions')
  await expect(native).toBeVisible()
  await clickBookmarklet(page)
  await expect(native).toBeHidden()
  await clickBookmarklet(page)
  await expect(native).toBeVisible()
})

test('selecting a native tab hides our overlay; our tab shows it again', async ({ page }) => {
  await clickBookmarklet(page)
  await expect(panel(page)).toBeVisible()
  await page.locator('#tab-button-participants').click()
  await expect(panel(page)).toBeHidden()
  await expect(tabButton(page)).toHaveAttribute('aria-selected', 'false')
  await tabButton(page).click()
  await expect(panel(page)).toBeVisible()
  await expect(tabButton(page)).toHaveAttribute('aria-selected', 'true')
})

test('second click tears everything down, including the transcript listener', async ({ page }) => {
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
  await transcript(page, { id: await sid(page, 'alice'), message: 'served by the exapp', final: true, languageId: 'en' })
  await expect(lines(page)).toHaveText(['served by the exapp'])
  expect(errors.filter((t) => t.includes('Content Security Policy'))).toHaveLength(0)
})
