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
// executed code does afterwards (DOM, styles, script loads).
async function clickBookmarklet(page, name = 'bookmarklet-inline.txt') {
  await page.evaluate(await bookmarkletCode(name))
}

const transcript = (page, data) => page.evaluate((d) => window.__FIXTURE__.transcript(d), data)
const sid = (page, who) => page.evaluate((w) => window.__FIXTURE__.sid[w], who)
const panel = (page) => page.locator('#cassini-live-tabpanel')
const tabButton = (page) => page.locator('#cassini-live-tab')

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/call/test')
})

test('mounts as a sidebar tab and auto-selects it', async ({ page }) => {
  await clickBookmarklet(page)
  await expect(tabButton(page)).toBeVisible()
  await expect(tabButton(page)).toHaveAttribute('aria-selected', 'true')
  await expect(panel(page)).toBeVisible()
  // sits alongside the native tabs, not floating
  await expect(page.locator('.app-sidebar-tabs__nav #cassini-live-tab')).toHaveCount(1)
  await expect(page.locator('#cassini-live-panel')).toHaveCount(0) // no floating window
  await expect(panel(page).locator('.hint')).toContainText('Turn on captions')
})

test('remote speakers resolve to display names, not raw ids (the bug)', async ({ page }) => {
  await clickBookmarklet(page)
  // caption events carry the SHORT id; roster/cards are keyed by the full id
  await transcript(page, { id: await sid(page, 'bob'), message: 'from the roster', final: true, languageId: 'en' })
  await transcript(page, { id: await sid(page, 'alice'), message: 'also roster', final: true, languageId: 'en' })
  await transcript(page, { id: await sid(page, 'carol'), message: 'from the dom card', final: true, languageId: 'en' })
  await transcript(page, { id: await sid(page, 'me'), message: 'my own line', final: true, languageId: 'en' })

  const speakers = panel(page).locator('.block .speaker')
  await expect(speakers).toHaveText(['Bob Babbage', 'Alice Lovelace', 'Carol From The DOM', 'Mel Me'])
  // none of them is a raw session id or a shortened-id fallback
  for (const name of await speakers.allTextContents()) {
    expect(name).not.toContain('ItZG5la2NhYg') // the shared id segment
    expect(name).not.toContain('…')
  }
  // own line marked
  const own = panel(page).locator('.block.own')
  await expect(own.locator('.speaker')).toHaveText('Mel Me')
  await expect(own.locator('.you-chip')).toBeVisible()
})

test('an unknown speaker falls back to a shortened id without crashing', async ({ page }) => {
  await clickBookmarklet(page)
  await transcript(page, { id: 'sid-of-a-late-guest-9999', message: 'who am i', final: true, languageId: 'en' })
  await expect(panel(page).locator('.block .speaker')).toContainText('…')
})

test('partials render italic in place and finals commit them', async ({ page }) => {
  await clickBookmarklet(page)
  await transcript(page, { id: await sid(page, 'alice'), message: 'typing someth', final: false, languageId: 'en' })
  const partial = panel(page).locator('.partial:visible')
  await expect(partial).toHaveText('typing someth')
  await transcript(page, { id: await sid(page, 'alice'), message: 'typing something longer', final: false, languageId: 'en' })
  await expect(partial).toHaveText('typing something longer')
  await expect(panel(page).locator('.block')).toHaveCount(1)
  await transcript(page, { id: await sid(page, 'alice'), message: 'typing something longer, done.', final: true, languageId: 'en' })
  await expect(panel(page).locator('.partial:visible')).toHaveCount(0)
  await expect(panel(page).locator('.line:not(.partial)')).toHaveText(['typing something longer, done.'])
})

test('native caption overlay is hidden while mounted, restored on teardown', async ({ page }) => {
  const native = page.locator('#native-captions')
  await expect(native).toBeVisible()
  await clickBookmarklet(page)
  await expect(native).toBeHidden()
  await clickBookmarklet(page) // toggle off
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
  await expect
    .poll(() => page.evaluate(() => window.__FIXTURE__.listenerCount('transcript')))
    .toBe(1)
  await clickBookmarklet(page)
  await expect(tabButton(page)).toHaveCount(0)
  await expect(panel(page)).toHaveCount(0)
  expect(await page.evaluate(() => window.__FIXTURE__.listenerCount('transcript'))).toBe(0)
  expect(await page.evaluate(() => window.__CASSINI_LIVE_PANEL__)).toBeFalsy()
  // a third click mounts fresh again
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
  await expect(panel(page).locator('.line:not(.partial)')).toHaveText(['served by the exapp'])
  expect(errors.filter((t) => t.includes('Content Security Policy'))).toHaveLength(0)
})
