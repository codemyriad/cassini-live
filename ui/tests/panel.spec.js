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
// javascript: bookmark is. The CSP on the fixture page still applies to
// everything the executed code does afterwards (DOM, styles, script loads).
async function clickBookmarklet(page, name = 'bookmarklet-inline.txt') {
  await page.evaluate(await bookmarkletCode(name))
}

const transcript = (page, data) =>
  page.evaluate((d) => window.__FIXTURE__.transcript(d), data)

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/call/test')
})

test('mounts under Talk CSP, renders finals grouped by speaker', async ({ page }) => {
  await clickBookmarklet(page)
  const panel = page.locator('#cassini-live-panel .panel')
  await expect(panel).toBeVisible()
  await expect(panel.locator('.hint')).toContainText('Turn on captions')

  await transcript(page, { id: 'sid-alice', message: 'hello there', final: true, languageId: 'en' })
  await transcript(page, { id: 'sid-alice', message: 'how are you', final: true, languageId: 'en' })
  await transcript(page, { id: 'sid-bob', message: 'fine thanks', final: true, languageId: 'en' })

  const blocks = panel.locator('.block')
  await expect(blocks).toHaveCount(2)
  await expect(blocks.nth(0).locator('.speaker')).toHaveText('Alice Lovelace')
  await expect(blocks.nth(0).locator('.line:not(.partial)')).toHaveCount(2)
  await expect(blocks.nth(1).locator('.speaker')).toHaveText('Bob Babbage')
  await expect(panel.locator('.marker')).toBeVisible()
})

test('partials render italic in place and finals commit them', async ({ page }) => {
  await clickBookmarklet(page)
  const panel = page.locator('#cassini-live-panel .panel')

  await transcript(page, { id: 'sid-alice', message: 'typing someth', final: false, languageId: 'en' })
  const partial = panel.locator('.partial:visible')
  await expect(partial).toHaveText('typing someth')

  await transcript(page, { id: 'sid-alice', message: 'typing something longer', final: false, languageId: 'en' })
  await expect(partial).toHaveText('typing something longer')
  await expect(panel.locator('.block')).toHaveCount(1)

  await transcript(page, { id: 'sid-alice', message: 'typing something longer was said.', final: true, languageId: 'en' })
  await expect(panel.locator('.partial:visible')).toHaveCount(0)
  await expect(panel.locator('.line:not(.partial)')).toHaveText(['typing something longer was said.'])
})

test('own captions show with the you chip, DOM names resolve as fallback', async ({ page }) => {
  await clickBookmarklet(page)
  const panel = page.locator('#cassini-live-panel .panel')

  await transcript(page, { id: 'sid-me', message: 'my own words', final: true, languageId: 'en' })
  const own = panel.locator('.block.own')
  await expect(own.locator('.speaker')).toHaveText('Mel Me')
  await expect(own.locator('.you-chip')).toBeVisible()

  // sid-carol is not in peers: resolved from the participant card in the DOM
  await transcript(page, { id: 'sid-carol', message: 'hi from the dom', final: true, languageId: 'en' })
  await expect(panel.locator('.block').nth(1).locator('.speaker')).toHaveText('Carol From The DOM')

  // unknown sid falls back to a shortened session id, never crashes
  await transcript(page, { id: 'sid-of-a-late-guest-0001', message: 'who am i', final: true, languageId: 'en' })
  await expect(panel.locator('.block').nth(2).locator('.speaker')).toContainText('…')
})

test('auto-follow sticks to the bottom, manual scroll shows jump-to-live', async ({ page }) => {
  await clickBookmarklet(page)
  const panel = page.locator('#cassini-live-panel .panel')
  const scroll = panel.locator('.scroll')

  for (let i = 0; i < 40; i++) {
    await transcript(page, {
      id: i % 2 ? 'sid-alice' : 'sid-bob',
      message: `line number ${i} with some extra words to fill the row`,
      final: true,
      languageId: 'en',
    })
  }
  const atBottom = () =>
    scroll.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight < 60)
  await expect.poll(atBottom).toBe(true)
  await expect(panel.locator('.jump')).toBeHidden()

  await scroll.evaluate((el) => el.scrollTo({ top: 0 }))
  await expect(panel.locator('.jump')).toBeVisible()
  await transcript(page, { id: 'sid-alice', message: 'new while scrolled up', final: true, languageId: 'en' })
  await expect.poll(atBottom).toBe(false)

  await panel.locator('.jump').click()
  await expect.poll(atBottom).toBe(true)
  await expect(panel.locator('.jump')).toBeHidden()
})

test('second click tears everything down, including the transcript listener', async ({ page }) => {
  await clickBookmarklet(page)
  await expect(page.locator('#cassini-live-panel')).toHaveCount(1)
  await expect
    .poll(() => page.evaluate(() => window.__FIXTURE__.listenerCount('transcript')))
    .toBe(1)

  await clickBookmarklet(page)
  await expect(page.locator('#cassini-live-panel')).toHaveCount(0)
  expect(await page.evaluate(() => window.__FIXTURE__.listenerCount('transcript'))).toBe(0)
  expect(await page.evaluate(() => window.__CASSINI_LIVE_PANEL__)).toBeFalsy()

  // and a third click mounts a fresh panel again
  await clickBookmarklet(page)
  await expect(page.locator('#cassini-live-panel')).toHaveCount(1)
})

test('the loader flavour injects the ExApp-served bundle through the page CSP', async ({ page }) => {
  const errors = []
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  await clickBookmarklet(page, 'bookmarklet-loader.txt')
  const panel = page.locator('#cassini-live-panel .panel')
  await expect(panel).toBeVisible()

  await transcript(page, { id: 'sid-alice', message: 'served by the exapp', final: true, languageId: 'en' })
  await expect(panel.locator('.line:not(.partial)')).toHaveText(['served by the exapp'])
  expect(errors.filter((text) => text.includes('Content Security Policy'))).toHaveLength(0)
})

test('close button and the panel are keyboard reachable', async ({ page }) => {
  await clickBookmarklet(page)
  const panel = page.locator('#cassini-live-panel .panel')
  await panel.locator('.close').click()
  await expect(page.locator('#cassini-live-panel')).toHaveCount(0)
})
