/**
 * The diagnostics page: polls the feed a second after each answer and renders it.
 *
 * Reached through the AppAPI proxy, so every URL is relative to the page. Speaker names
 * come from Talk users: feed strings reach the DOM only as textContent. The page CSP is
 * `style-src 'self'`, so sizes go through CSSOM, never a style attribute.
 */

import { $, el, ensureChildren, setFraction, setText, setTone, show } from './dom.js'
import { backoffMs, feedHistory, feedPath, historyStart, mergeColumns, POLL_MS, restarted, viewTime } from './feed.js'
import {
  decisionLine,
  engineLine,
  footer,
  freshness,
  hero,
  notServed,
  observeOnly,
  pipelineStages,
  protectionPanels,
  sampleError,
  statusPill,
  unreachable,
  videoLine,
} from './format.js'
import { renderLanes } from './lanesView.js'

// The connection line ages once a second while a poll is pending or backing off.
const TICK_MS = 1000
// A poll stuck behind the proxy fails after this long and backs off like any other.
const TIMEOUT_MS = 10000
const REFUSED = 'Open this page from Nextcloud as an administrator'

const state = {
  body: null,
  columns: [],
  head: 0,
  // Server time after which the ring's columns are the feed's own history (feed.historyStart).
  historyStart: null,
  lastNow: null,
  // The Cassini Live process the ring's columns and lane numbers come from (feed.restarted).
  instance: null,
  lastOkAt: null,
  failures: 0,
  // The last failure was Nextcloud's own 404 page (request), not the app's answer.
  notServed: false,
  refused: false,
  inFlight: false,
  nextPollAt: 0,
  // The one pending timer: the next poll, or the next connection-line refresh.
  timer: null,
}

/* ---------- polling ---------- */

function arm(delay) {
  clearTimeout(state.timer)
  state.timer = null
  if (state.refused || document.hidden) return
  state.timer = setTimeout(tick, Math.max(0, delay))
}

function tick() {
  state.timer = null
  if (state.refused || document.hidden) return
  renderConnection()
  const wait = state.nextPollAt - Date.now()
  if (state.inFlight || wait > 0) arm(state.inFlight ? TICK_MS : Math.min(TICK_MS, wait))
  else poll()
}

async function request(since) {
  const options = {
    credentials: 'same-origin',
    cache: 'no-store',
    redirect: 'manual',
    headers: { Accept: 'application/json' },
  }
  if (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) options.signal = AbortSignal.timeout(TIMEOUT_MS)
  const response = await fetch(new URL(feedPath(since), document.baseURI), options)
  const json = (response.headers.get('content-type') || '').includes('application/json')
  // A redirect to a login page, or a 401 or 403 (the app's own administrator check):
  // polling stops.
  if (response.type === 'opaqueredirect' || response.status === 401 || response.status === 403) {
    return { refused: true }
  }
  // AppAPI's proxy answers a signed-out user and a non-administrator (measured) and a
  // disabled ExApp (ExAppProxyController) with Nextcloud's HTML 404 page, never 401 or 403;
  // the app's own 404 is JSON. Access can come back without a reload, so polling goes on.
  if (response.status === 404 && !json) return { failed: true, notServed: true }
  if (!response.ok) return { failed: true }
  // A 2xx answer that is not the feed's JSON is Nextcloud's login or error page.
  if (!json) return { refused: true }
  const body = await response.json()
  return body && typeof body === 'object' && typeof body.now === 'number' ? { body } : { failed: true }
}

function poll() {
  state.inFlight = true
  arm(TICK_MS)
  request(state.head)
    .then(
      (result) => {
        if (result.refused) refuse()
        else if (result.failed) fail(Boolean(result.notServed))
        else accept(result.body)
      },
      () => fail(),
    )
    .finally(() => {
      state.inFlight = false
      arm(Math.min(TICK_MS, state.nextPollAt - Date.now()))
    })
}

function accept(body) {
  if (restarted(state.lastNow, body.now, state.instance, body.instance)) {
    state.columns = []
    state.head = 0
    state.historyStart = null
    state.lastNow = null
    state.instance = null
    state.nextPollAt = 0
    return
  }
  state.columns = mergeColumns(state.columns, body.columns, body.window, viewTime(body))
  state.historyStart = historyStart(state.historyStart, body)
  if (state.columns.length) state.head = state.columns[state.columns.length - 1].t
  state.lastNow = body.now
  state.instance = typeof body.instance === 'string' ? body.instance : null
  state.body = body
  state.lastOkAt = Date.now()
  state.failures = 0
  state.nextPollAt = Date.now() + POLL_MS
  render()
}

function fail(byProxy = false) {
  state.failures += 1
  state.notServed = byProxy
  state.nextPollAt = Date.now() + backoffMs(state.failures)
  renderConnection()
}

function refuse() {
  state.refused = true
  clearTimeout(state.timer)
  state.timer = null
  render()
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    clearTimeout(state.timer)
    state.timer = null
    return
  }
  if (state.refused) return
  state.nextPollAt = 0
  if (state.inFlight) arm(TICK_MS)
  else poll()
})

/* ---------- rendering ---------- */

function render() {
  const body = state.body
  const sample = body ? body.sample : null
  const now = body ? viewTime(body) : 0
  // Only the history since the feed last started: the seconds before it were not sampled.
  // Columns from another Cassini Live process never get here (accept drops them).
  const history = feedHistory(state.columns, state.historyStart)
  // Before the first sample the figures are the video's empty ones, shown dimmed.
  document.body.classList.toggle('waiting', !sample)
  renderHeader(sample, state.columns, now)
  renderHero(sample)
  renderLanes(sample, history, body ? body.window : null, now)
  renderDecision(sample)
  renderFooter(sample)
  renderStages(pipelineStages(sample, history, now))
  renderPanels(protectionPanels(sample))
  const video = videoLine(body ? body.video : null)
  setText($('video'), video.text)
  setTone($('video'), video.tone)
  const error = sampleError(body ? body.error : null)
  setText($('sample-error'), error)
  show($('sample-error'), Boolean(error))
  renderConnection()
}

function renderConnection() {
  const now = Date.now()
  let line
  if (state.refused) line = { stale: true, text: REFUSED, tone: 'bad' }
  else if (state.failures) line = (state.notServed ? notServed : unreachable)(state.lastOkAt, now)
  else if (state.body?.collecting === false) line = {
    stale: false, tone: 'idle',
    text: state.body.sample ? 'Captions stopped · showing final diagnostics' : 'Waiting for live captions',
  }
  else line = freshness(state.lastOkAt, now)
  setText($('freshness'), line.text)
  setTone($('freshness'), line.tone)
  document.body.classList.toggle('stale', line.stale && state.lastOkAt !== null)
}

function renderHeader(sample, columns, now) {
  let pill = statusPill(sample, columns, now)
  if (state.refused) pill = { label: 'No access', tone: 'bad', note: '' }
  else if (state.body?.collecting === false) pill = { label: 'No captions running', tone: 'idle', note: '' }
  setText($('pill-label'), pill.label)
  setTone($('pill'), pill.tone)
  setText($('pill-note'), pill.note)
  show($('pill-note'), Boolean(pill.note))
  setText($('engine'), engineLine(sample))
  const observe = observeOnly(sample)
  show($('observe'), observe.on)
  setText($('observe-note'), observe.note)
  show($('observe-note'), observe.on)
}

function renderHero(sample) {
  const model = hero(sample)
  setText($('voices'), model.voices.value)
  setText($('voices-label'), model.voices.label[0])
  setText($('seats'), model.seats.value)
  setText($('ceiling'), model.seats.of)
  setText($('seats-label'), model.seats.label[0])
  setText($('seats-detail'), model.seats.label[1])
  setTone($('seats-caption'), model.seats.tone)
  const chips = ensureChildren($('chips'), model.seats.chips.length, () => el('span', 'chip'))
  chips.forEach((chip, i) => chip.classList.toggle('taken', model.seats.chips[i]))
  setText($('lag'), model.lag.value)
  setText($('lag-unit'), model.lag.unit)
  setTone($('lag-figure'), model.lag.tone)
  setFraction($('lag-fill'), 'width', model.lag.fill)
  setFraction($('lag-admit'), 'left', model.lag.admit)
  setFraction($('lag-shed'), 'left', model.lag.shed)
}

function renderDecision(sample) {
  const line = decisionLine(sample)
  setText($('decision-time'), line ? line.time : '')
  setText($('decision-verb'), line ? line.verb : '')
  setText($('decision-text'), line ? line.text : 'No admission decision yet')
  setTone($('decision'), line ? line.tone : 'idle')
}

function renderFooter(sample) {
  const model = footer(sample)
  setText($('memory'), model.memory.text)
  setFraction($('memory-fill'), 'width', model.memory.fraction)
  setFraction($('memory-mark'), 'left', model.memory.mark)
  setTone($('memory-fill'), model.memory.tone)
  for (const metric of model.metrics) {
    setText($(metric.id), metric.value)
    setTone($(metric.id), metric.tone)
  }
}

function stageCard() {
  const card = el('li', 'stage')
  card.append(el('p', 'step'), el('h3', 'name'), el('p', 'big'), el('p', 'unit'), el('ul', 'detail'))
  return card
}

function renderStages(stages) {
  const cards = ensureChildren($('stages'), stages.length, stageCard)
  stages.forEach((stage, i) => {
    const [step, name, big, unit, detail] = cards[i].children
    setTone(cards[i], stage.tone)
    setText(step, stage.step)
    setText(name, stage.name)
    setText(big, stage.big)
    setText(unit, stage.unit)
    ensureChildren(detail, stage.detail.length, () => el('li')).forEach((item, k) => setText(item, stage.detail[k]))
  })
}

function panelBox() {
  const panel = el('div', 'panel')
  panel.append(el('h3'), el('dl'))
  return panel
}

function panelRow() {
  const row = el('div')
  row.append(el('dt'), el('dd'))
  return row
}

function renderPanels(panels) {
  const boxes = ensureChildren($('panels'), panels.length, panelBox)
  panels.forEach((panel, i) => {
    const [title, list] = boxes[i].children
    setText(title, panel.title)
    ensureChildren(list, panel.rows.length, panelRow).forEach((row, k) => {
      setText(row.children[0], panel.rows[k][0])
      setText(row.children[1], panel.rows[k][1])
    })
  })
}

render()
if (!document.hidden) poll()
