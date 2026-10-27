// The replay viewer: the real panel bundle on the replay page, scrubbed through
// a small hand-written timeline. Proves that what the panel shows at a replay
// time is exactly what existed then (finals, joins, the live partial), that a
// seek backwards forgets what came later, and that "view as" moves the "you".
import { expect, test } from '@playwright/test'
import { loadReplay, startReplayServer, historyAt, partialsAt, createClock } from '../replay/server.mjs'

const PORT = 4399

const timeline = {
  format: 'cassini-live-replay/1',
  source: { title: 'Replay spec', startedAt: '2026-03-13T11:29:34Z' },
  window: { fromMs: 0, toMs: 60000 },
  engine: { id: 'fake', speed: 0 },
  participants: [
    { sid: 'alice', name: 'Alice Lovelace', userId: 'alice', joinMs: 0 },
    { sid: 'bob', name: 'Bob Babbage', userId: 'bob', joinMs: 5000 },
  ],
  events: [
    { at: 0, type: 'join', speakerSessionId: 'alice' },
    { at: 2000, type: 'partial', speakerSessionId: 'alice', message: 'good', langId: 'en' },
    { at: 3000, type: 'final', speakerSessionId: 'alice', message: 'good morning', langId: 'en' },
    { at: 3000, type: 'partial', speakerSessionId: 'alice', message: '', langId: 'en' },
    { at: 5000, type: 'join', speakerSessionId: 'bob' },
    { at: 9000, type: 'partial', speakerSessionId: 'bob', message: 'hi al', langId: 'en' },
    { at: 11000, type: 'final', speakerSessionId: 'bob', message: 'hi alice', langId: 'en' },
    { at: 11000, type: 'partial', speakerSessionId: 'bob', message: '', langId: 'en' },
    { at: 30000, type: 'final', speakerSessionId: 'alice', message: 'later on', langId: 'en' },
  ],
}

let replayServer
test.beforeAll(async () => {
  replayServer = await startReplayServer({ replay: loadReplay(timeline), port: PORT })
})
test.afterAll(async () => {
  await replayServer.close()
})
test.beforeEach(async () => {
  replayServer.clock.pause()
  replayServer.clock.seek(0)
})

const url = (q = '') => `http://127.0.0.1:${PORT}/call/replay${q}`
const panelHost = (page) => page.locator('#cassini-live-tabpanel')
const lines = (page) => panelHost(page).locator('.block .line')
const markers = (page) => panelHost(page).locator('.marker')
const partials = (page) => panelHost(page).locator('.partial-line')
const seekTo = (page, ms) => page.evaluate((t) => window.__REPLAY_CONTROL__.seekTo(t), ms)

test('the server-side timeline is cut at the replay time', () => {
  const replay = loadReplay(timeline)
  expect(historyAt(replay, 0).entries.map((e) => e.type)).toEqual(['join'])
  expect(historyAt(replay, 11000).entries.map((e) => [e.type, e.message ?? e.displayName])).toEqual([
    ['join', 'Alice Lovelace'],
    ['final', 'good morning'],
    ['join', 'Bob Babbage'],
    ['final', 'hi alice'],
  ])
  expect(historyAt(replay, 11000, 3).entries.map((e) => e.seq)).toEqual([4])
  expect(historyAt(replay, 11000).entries[1].t).toBe(Date.parse('2026-03-13T11:29:34Z') + 3000)
  expect(partialsAt(replay, 2500)).toEqual([{ sid: 'alice', message: 'good', lang: 'en' }])
  expect(partialsAt(replay, 3000)).toEqual([]) // the final cleared it
  expect(partialsAt(replay, 10000)).toEqual([{ sid: 'bob', message: 'hi al', lang: 'en' }])
})

test('the clock plays at a speed, pauses, seeks and stops at the end', async () => {
  const clock = createClock(0, 1000)
  expect(clock.now()).toBe(0)
  clock.setSpeed(8)
  clock.play()
  await new Promise((r) => setTimeout(r, 60))
  const t = clock.now()
  expect(t).toBeGreaterThan(200)
  clock.pause()
  const paused = clock.now()
  await new Promise((r) => setTimeout(r, 30))
  expect(clock.now()).toBe(paused)
  clock.seek(999)
  clock.play()
  await new Promise((r) => setTimeout(r, 30))
  expect(clock.now()).toBe(1000)
  expect(clock.playing).toBe(false)
})

test('mounts the panel as a Talk sidebar tab and shows the state at time zero', async ({ page }) => {
  await page.goto(url('?as=alice'))
  await expect(page.locator('#cassini-live-tab')).toBeVisible()
  await expect(markers(page)).toHaveText(['You joined here'], { timeout: 6000 })
  await expect(lines(page)).toHaveCount(0)
})

test('seeking shows exactly what existed then, including the live partial', async ({ page }) => {
  await page.goto(url('?as=alice'))
  await expect(markers(page)).toHaveCount(1, { timeout: 6000 })
  await seekTo(page, 10000)
  await expect(lines(page)).toHaveText(['good morning'], { timeout: 6000 })
  await expect(markers(page)).toHaveText(['You joined here', 'Bob Babbage joined'])
  await expect(partials(page)).toHaveText(['Bob Babbage:hi al'])
  await expect(panelHost(page).locator('.block .speaker')).toHaveText(['Alice Lovelace'])
  await expect(panelHost(page).locator('.block .you-chip')).toBeVisible()
  await seekTo(page, 40000)
  await expect(lines(page)).toHaveText(['good morning', 'hi alice', 'later on'], { timeout: 6000 })
  await expect(partials(page)).toHaveCount(0)
})

test('seeking backwards forgets what came later', async ({ page }) => {
  await page.goto(url('?as=alice&t=0:40'))
  await expect(lines(page)).toHaveCount(3, { timeout: 6000 })
  await seekTo(page, 4000)
  await expect(lines(page)).toHaveText(['good morning'], { timeout: 6000 })
  await expect(markers(page)).toHaveText(['You joined here'])
})

test('playing advances the panel without a remount', async ({ page }) => {
  await page.goto(url('?as=bob'))
  await seekTo(page, 2500)
  await expect(partials(page)).toHaveText(['Alice Lovelace:good'], { timeout: 6000 })
  await page.selectOption('#tr-speed', '16')
  await page.click('#tr-play')
  await expect(page.locator('#tr-play')).toHaveAttribute('aria-pressed', 'true')
  await expect(lines(page)).toHaveText(['good morning', 'hi alice'], { timeout: 10000 })
  await expect(markers(page)).toHaveText(['Alice Lovelace joined', 'You joined here'])
  await expect(panelHost(page).locator('.block .you-chip:not([hidden])')).toHaveCount(1)
  await expect(panelHost(page).locator('.block.own .speaker')).toHaveText('Bob Babbage')
})

test('"view as" changes who is you', async ({ page }) => {
  await page.goto(url('?as=alice'))
  await expect(markers(page)).toHaveText(['You joined here'], { timeout: 6000 })
  await page.selectOption('#tr-as', 'bob')
  await page.waitForURL(/as=bob/)
  await seekTo(page, 6000)
  await expect(markers(page)).toHaveText(['Alice Lovelace joined', 'You joined here'], { timeout: 6000 })
})
