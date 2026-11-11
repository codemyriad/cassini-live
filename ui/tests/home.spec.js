import { expect, test } from '@playwright/test'

// One server-side state for the page's two requests: keep these tests in one file
// (one worker, in order).
const ready = {
  enabled: true, signallingConfigured: true, model: 'Whistle', activeRooms: 0, version: '0.11.0',
  resourceSafety: { state: 'ready', phase: 'ready', reason: '' },
}
const fresh = {
  enabled: false, signallingConfigured: false, model: 'Whistle', activeRooms: 0, version: '0.11.0',
  resourceSafety: { state: 'blocked', phase: 'configuration', reason: 'Enter the Talk HPB internal secret', adminActionRequired: true },
}
const host = { cpuQuota: 4, memoryLimitBytes: 2147483648 }

async function open(page, state, { admin = false } = {}) {
  await page.request.post('/fixture-home', { data: state })
  await page.goto(admin ? '/home?admin=1' : '/home')
  return page.locator('#cassini-live-home')
}

test('a user sees the three steps, the Live transcript bookmark and a green status', async ({ page }) => {
  const home = await open(page, { status: ready, admin: null })
  await expect(home.locator('#pill')).toHaveText('Captions ready')
  await expect(home.locator('h1')).toHaveText('Cassini Live')
  await expect(home.locator('.step strong')).toHaveText([
    'Add the Live transcript bookmark', 'Join a Talk call', 'Click the Live transcript bookmark',
  ])
  const bookmark = home.locator('#bookmark')
  await expect(bookmark).toHaveText('Live transcript')
  await expect(bookmark).toHaveAttribute('draggable', 'true')
  const href = await bookmark.getAttribute('href')
  expect(href.startsWith('javascript:')).toBe(true)
  expect(href).toContain('/apps/app_api/proxy/live_transcription/panel/panel.js')
  await expect(home.locator('figure svg')).toHaveAttribute('aria-label', /bookmarks bar/)
  await expect(home.locator('#admin')).toBeHidden()
  await expect(home.locator('#setup')).toBeHidden()
  await expect(home.locator('#version')).toHaveText('Cassini Live 0.11.0')
  await expect(home.locator('#open-talk')).toHaveAttribute('href', '/index.php/apps/spreed/')
  // The thing to drag is never called a button.
  const text = await home.locator('.page').innerText()
  expect(text.toLowerCase()).not.toContain('button')
})

test('clicking the bookmark here explains dragging, and copy offers the manual way', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  const home = await open(page, { status: ready, admin: null })
  await home.locator('#bookmark').click()
  await expect(home.locator('#bookmark-note')).toContainText('Drag it up to your bookmarks bar')
  await home.locator('#copy').click()
  await expect(home.locator('#bookmark-note')).toContainText('name it Live transcript')
  expect(await page.evaluate(() => navigator.clipboard.readText())).toMatch(/^javascript:/)
})

test('dragging shows where to drop it', async ({ page }) => {
  const home = await open(page, { status: ready, admin: null })
  await home.locator('#bookmark').dispatchEvent('dragstart')
  await expect(home.locator('#drop-hint')).toBeVisible()
  await expect(home.locator('#drop-hint')).toContainText('bookmarks bar')
  await home.locator('#bookmark').dispatchEvent('dragend')
  await expect(home.locator('#drop-hint')).toBeHidden()
})

test('an administrator right after install gets the setup list and links', async ({ page }) => {
  const home = await open(
    page,
    { status: fresh, admin: { talk: { secretConfigured: false, check: null }, panel: { host: {} } } },
    { admin: true },
  )
  await expect(home.locator('#pill')).toHaveText('Setup needed')
  await expect(home.locator('#setup')).toBeVisible()
  await expect(home.locator('.setup-step strong')).toHaveText([
    'Connect to Talk', 'Set container limits', 'Speech engine loads',
  ])
  await expect(home.locator('[data-step="talk"] a')).toHaveAttribute(
    'href', '/index.php/apps/app_api/proxy/live_transcription/settings/index.html')
  await expect(home.locator('[data-step="engine"] .setup-state')).toHaveText('Waiting')
  await expect(home.locator('#admin')).toBeVisible()
  await expect(home.locator('#fact-talk')).toHaveText('Internal secret not set')
  await expect(home.locator('#diagnostics-link')).toHaveAttribute(
    'href', '/index.php/apps/app_api/proxy/live_transcription/diagnostics/')
})

test('the page follows setup as it completes, without a reload', async ({ page }) => {
  const home = await open(
    page,
    { status: fresh, admin: { talk: { secretConfigured: false, check: null }, panel: { host } } },
    { admin: true },
  )
  await expect(home.locator('#pill')).toHaveText('Setup needed')
  await page.request.post('/fixture-home', {
    data: { status: ready, admin: { talk: { secretConfigured: true, check: { state: 'passed', message: 'ok' } }, panel: { host } } },
  })
  await expect(home.locator('#pill')).toHaveText('Captions ready', { timeout: 10000 })
  await expect(home.locator('#setup')).toBeHidden()
  await expect(home.locator('#fact-talk')).toHaveText('Passed')
})

test('a non-administrator is told to ask one, and never sees the admin block', async ({ page }) => {
  const home = await open(page, { status: fresh, admin: null })
  await expect(home.locator('#pill')).toHaveText('Captions are not set up yet')
  await expect(home.locator('#user-note')).toContainText('Ask an administrator')
  await expect(home.locator('#admin')).toBeHidden()
  await expect(home.locator('#setup')).toBeHidden()
})

test('an unreachable app is said plainly', async ({ page }) => {
  const home = await open(page, { status: null, admin: null })
  await expect(home.locator('#pill')).toHaveText('Cannot reach Cassini Live')
})
