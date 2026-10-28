import { test } from 'node:test'
import assert from 'node:assert/strict'
import { backoffMs, feedHistory, feedPath, historyStart, mergeColumns, restarted } from '../../src/diagnostics/feed.js'
import {
  decisionLine,
  engineLine,
  fixed,
  footer,
  freshness,
  g,
  hero,
  observeOnly,
  percent,
  pipelineStages,
  protectionPanels,
  sampleError,
  seconds,
  statusPill,
  unreachable,
  videoLine,
} from '../../src/diagnostics/format.js'
import { CASES, NOW, columns, governor, sample } from '../diagnostics-fixtures.mjs'

// What diagnostic_video.render() draws for each case with its clock at NOW, in drawing
// order, without the video-only chrome (logo, legends, the tile's own cost, the view
// alternation note) and the lanes and lag graph (S4). The video cuts a line that overflows
// its card with "…"; the page shows it whole.
const VIDEO = {
  empty: {
    lanes: ['Starting', 'Whistle', '0', 'voices', 'heard now', '0', '/0', 'seats', 'taken', '–', 'captions trail', 'speech by', 'No admission decision yet', 'Memory', '–', 'CPU stall', '–', 'Engine busy', '–', 'Reply p95', '–'],
    pipeline: ['Whistle', '1 · LISTEN', 'Microphones', '0', 'voices of 0 mics', 'voice gate per mic', '32 ms frames', 'silence ends a turn', '2 · CUT', 'Speech pieces', '1 s', 'per piece', 'queue 0 of 6', 'older than 4 s:', 'dropped, not late', '3 · ADMIT', 'Seats', '0/0', 'admitting', 'first voice: always', 'then lag < 1.0 s', 'and stall < 20%', '4 · RECOGNISE', 'Engine', '–', 'busy, 1 thread', 'memory –', '5 · SEND', 'Captions', '0', 'lines so far', 'trail speech –', 'via Talk signaling', 'no audio leaves', '◆', 'Admission decides per speaker, the moment they start talking. A seat, once given, is kept.', 'THE KERNEL PROTECTS THE CALL', 'cpu.max     – hard cap', 'cpu.weight  –, Talk 100: we yield', 'memory.max  –, no swap', 'OOM         engine dies, Talk lives', 'LAG KEEPS CAPTIONS USEFUL', 'lag ≥ 1.0 s   hold new voices', 'lag ≥ 2.0 s   for 5 s: pause the', '              newest speaker 10 s', 'lag < 0.5 s   for 5 s: reopen', 'ONLY A CRASH STOPS CAPTIONS', 'engine died    restart, ~2 s', '3 deaths/60 s  rest 2 min, doubling', 'OOM kill       rest 2 min', 'since start    0 restarts · 0 OOM'],
  },
  busy: {
    lanes: ['Holding new voices', 'Nemotron ML · 4 CPU · 3 GiB', '3', 'voices', 'heard now', '4', '/4', 'seats taken', 'holding: lag', '1.2', ' s', 'captions trail', 'speech by', '18:34:56', 'hold Ana Ferreira', 'lag 1.3 s ≥ admit 1.0 · reopens under 0.5 s for 5 s', 'Memory', '2.54/3 GiB', 'CPU stall', '12%', 'Engine busy', '97%', 'Reply p95', '1.1 s'],
    pipeline: ['Nemotron ML · 4 CPU · 3 GiB', '1 · LISTEN', 'Microphones', '3', 'voices of 4 mics', 'voice gate per mic', '32 ms frames', 'silence ends a turn', '2 · CUT', 'Speech pieces', '1 s', 'per piece, 8 s max', 'queue 7 of 24', 'older than 4 s:', 'dropped, not late', '3 · ADMIT', 'Seats', '4/4', 'holding: lag', 'first voice: always', 'then lag < 1.0 s', 'and stall < 20%', '4 · RECOGNISE', 'Engine', '97%', 'busy, 3 threads', '1 process, 4 slots', 'reply p95 1.1 s', '2.54 of 3 GiB', '5 · SEND', 'Captions', '20', 'lines per minute', 'trail speech 1.2 s', 'via Talk signaling', 'no audio leaves', '◆', 'Admission decides per speaker, the moment they start talking. A seat, once given, is kept.', 'THE KERNEL PROTECTS THE CALL', 'cpu.max     4 CPU hard cap', 'cpu.weight  10, Talk 100: we yield', 'memory.max  3 GiB, no swap', 'OOM         engine dies, Talk lives', 'LAG KEEPS CAPTIONS USEFUL', 'lag ≥ 1.0 s   hold new voices', 'lag ≥ 2.0 s   for 5 s: pause the', '              newest speaker 12.5 s', 'lag < 0.5 s   for 5 s: reopen', 'ONLY A CRASH STOPS CAPTIONS', 'engine died    restart, ~2 s', '3 deaths/60 s  rest 2 min, doubling', 'OOM kill       rest 2 min', 'since start    1 restart · 2 OOM'],
  },
  noGovernor: {
    lanes: ['Captions live', 'Whistle · 2 CPU · 3 GiB', '0', 'voices', 'heard now', '0', '/0', 'seats', 'taken', '–', 'captions trail', 'speech by', 'No admission decision yet', 'Memory', '1.07/3 GiB', 'CPU stall', '–', 'Engine busy', '–', 'Reply p95', '0.8 s'],
    pipeline: ['Whistle · 2 CPU · 3 GiB', '1 · LISTEN', 'Microphones', '0', 'voices of 0 mics', 'voice gate per mic', '32 ms frames', 'silence ends a turn', '2 · CUT', 'Speech pieces', '1 s', 'per piece', 'queue 1 of 6', 'older than 4 s:', 'dropped, not late', '3 · ADMIT', 'Seats', '0/0', 'admitting', 'first voice: always', 'then lag < 1.0 s', 'and stall < 20%', '4 · RECOGNISE', 'Engine', '–', 'busy, 1 thread', '0 workers', 'reply p95 0.8 s', '1.07 of 3 GiB', '5 · SEND', 'Captions', '10', 'lines so far', 'trail speech –', 'via Talk signaling', 'no audio leaves', '◆', 'Admission decides per speaker, the moment they start talking. A seat, once given, is kept.', 'THE KERNEL PROTECTS THE CALL', 'cpu.max     2 CPU hard cap', 'cpu.weight  10, Talk 100: we yield', 'memory.max  3 GiB, no swap', 'OOM         engine dies, Talk lives', 'LAG KEEPS CAPTIONS USEFUL', 'lag ≥ 1.0 s   hold new voices', 'lag ≥ 2.0 s   for 5 s: pause the', '              newest speaker 10 s', 'lag < 0.5 s   for 5 s: reopen', 'ONLY A CRASH STOPS CAPTIONS', 'engine died    restart, ~2 s', '3 deaths/60 s  rest 2 min, doubling', 'OOM kill       rest 2 min', 'since start    0 restarts · 0 OOM'],
  },
  observeOnly: {
    lanes: ['Would hold new voices', 'Whistle · 2 CPU · 3 GiB', 'observe only', '2', 'voices', 'heard now', '4', '/4', 'seats taken', 'would hold: all seats taken', '1.0', ' s', 'captions trail', 'speech by', '18:34:56', 'would hold a speaker', 'all 4 seats taken', 'Memory', '1.07/3 GiB', 'CPU stall', '6%', 'Engine busy', '42%', 'Reply p95', '0.8 s'],
    pipeline: ['Whistle · 2 CPU · 3 GiB', 'observe only', '1 · LISTEN', 'Microphones', '2', 'voices of 4 mics', 'voice gate per mic', '32 ms frames', 'silence ends a turn', '2 · CUT', 'Speech pieces', '1 s', 'per piece, 15 s …', 'queue 1 of 24', 'older than 4 s:', 'dropped, not late', '3 · ADMIT', 'Seats', '4/4', 'would hold: all s…', 'first voice: always', 'then lag < 1.0 s', 'and stall < 20%', '4 · RECOGNISE', 'Engine', '42%', 'busy, 2 threads', '4 workers', 'reply p95 0.8 s', '1.07 of 3 GiB', '5 · SEND', 'Captions', '20', 'lines per minute', 'trail speech 1.0 s', 'via Talk signaling', 'no audio leaves', '◆', 'Admission decides per speaker, the moment they start talking. A seat, once given, is kept.', 'THE KERNEL PROTECTS THE CALL', 'cpu.max     2 CPU hard cap', 'cpu.weight  10, Talk 100: we yield', 'memory.max  3 GiB, no swap', 'OOM         engine dies, Talk lives', 'LAG KEEPS CAPTIONS USEFUL', 'lag ≥ 1.0 s   hold new voices', 'lag ≥ 2.0 s   for 5 s: pause the', '              newest speaker 10 s', 'lag < 0.5 s   for 5 s: reopen', 'ONLY A CRASH STOPS CAPTIONS', 'engine died    restart, ~2 s', '3 deaths/60 s  rest 2 min, doubling', 'OOM kill       rest 2 min', 'since start    0 restarts · 0 OOM'],
  },
  notReady: {
    lanes: ['Captions live', 'Whistle · 2 CPU · 3 GiB', '0', 'voices', 'heard now', '0', '/0', 'seats', 'taken', '–', 'captions trail', 'speech by', 'No admission decision yet', 'Memory', '1.07/3 GiB', 'CPU stall', '–', 'Engine busy', '–', 'Reply p95', '0.8 s'],
    pipeline: ['Whistle · 2 CPU · 3 GiB', '1 · LISTEN', 'Microphones', '0', 'voices of 1 mic', 'voice gate per mic', '32 ms frames', 'silence ends a turn', '2 · CUT', 'Speech pieces', '1 s', 'per piece, 15 s …', 'queue 1 of 6', 'older than 4 s:', 'dropped, not late', '3 · ADMIT', 'Seats', '0/0', 'admitting', 'first voice: always', 'then lag < 1.0 s', 'and stall < 20%', '4 · RECOGNISE', 'Engine', '–', 'busy, 2 threads', '0 workers', 'reply p95 0.8 s', '1.07 of 3 GiB', '5 · SEND', 'Captions', '0', 'lines so far', 'trail speech –', 'via Talk signaling', 'no audio leaves', '◆', 'Admission decides per speaker, the moment they start talking. A seat, once given, is kept.', 'THE KERNEL PROTECTS THE CALL', 'cpu.max     2 CPU hard cap', 'cpu.weight  10, Talk 100: we yield', 'memory.max  3 GiB, no swap', 'OOM         engine dies, Talk lives', 'LAG KEEPS CAPTIONS USEFUL', 'lag ≥ 1.0 s   hold new voices', 'lag ≥ 2.0 s   for 5 s: pause the', '              newest speaker 10 s', 'lag < 0.5 s   for 5 s: reopen', 'ONLY A CRASH STOPS CAPTIONS', 'engine died    restart, ~2 s', '3 deaths/60 s  rest 2 min, doubling', 'OOM kill       rest 2 min', 'since start    0 restarts · 0 OOM'],
  },
  noRooms: {
    lanes: ['Captions live', 'Whistle · 2 CPU · 3 GiB', '0', 'voices', 'heard now', '2', '/4', 'seats', 'taken', '0.4', ' s', 'captions trail', 'speech by', '18:34:56', 'admit Ana Ferreira', 'seat 2 of 4 · lag 0.4 s < 1.0 · stall 6%', 'Memory', '1.07/3 GiB', 'CPU stall', '6%', 'Engine busy', '42%', 'Reply p95', '0.8 s'],
    pipeline: ['Whistle · 2 CPU · 3 GiB', '1 · LISTEN', 'Microphones', '0', 'voices of 0 mics', 'voice gate per mic', '32 ms frames', 'silence ends a turn', '2 · CUT', 'Speech pieces', '1 s', 'per piece', 'queue 0 of 6', 'older than 4 s:', 'dropped, not late', '3 · ADMIT', 'Seats', '2/4', 'admitting', 'first voice: always', 'then lag < 1.0 s', 'and stall < 20%', '4 · RECOGNISE', 'Engine', '42%', 'busy, 2 threads', '4 workers', 'reply p95 0.8 s', '1.07 of 3 GiB', '5 · SEND', 'Captions', '0', 'lines per minute', 'trail speech 0.4 s', 'via Talk signaling', 'no audio leaves', '◆', 'Admission decides per speaker, the moment they start talking. A seat, once given, is kept.', 'THE KERNEL PROTECTS THE CALL', 'cpu.max     2 CPU hard cap', 'cpu.weight  10, Talk 100: we yield', 'memory.max  3 GiB, no swap', 'OOM         engine dies, Talk lives', 'LAG KEEPS CAPTIONS USEFUL', 'lag ≥ 1.0 s   hold new voices', 'lag ≥ 2.0 s   for 5 s: pause the', '              newest speaker 10 s', 'lag < 0.5 s   for 5 s: reopen', 'ONLY A CRASH STOPS CAPTIONS', 'engine died    restart, ~2 s', '3 deaths/60 s  rest 2 min, doubling', 'OOM kill       rest 2 min', 'since start    0 restarts · 0 OOM'],
  },
  nullBudgets: {
    lanes: ['Captions live', 'Whistle', '2', 'voices', 'heard now', '2', '/4', 'seats', 'taken', '–', 'captions trail', 'speech by', '18:34:56', 'admit Ana Ferreira', 'seat 2 of 4 · lag 0.4 s < 1.0 · stall 6%', 'Memory', '–', 'CPU stall', '–', 'Engine busy', '–', 'Reply p95', '–'],
    pipeline: ['Whistle', '1 · LISTEN', 'Microphones', '2', 'voices of 4 mics', 'voice gate per mic', '32 ms frames', 'silence ends a turn', '2 · CUT', 'Speech pieces', '1 s', 'per piece, 15 s …', 'queue 1 of 24', 'older than 4 s:', 'dropped, not late', '3 · ADMIT', 'Seats', '2/4', 'admitting', 'first voice: always', 'then lag < 1.0 s', 'and stall < 20%', '4 · RECOGNISE', 'Engine', '–', 'busy, 2 threads', 'memory –', '5 · SEND', 'Captions', '19', 'lines so far', 'trail speech –', 'via Talk signaling', 'no audio leaves', '◆', 'Admission decides per speaker, the moment they start talking. A seat, once given, is kept.', 'THE KERNEL PROTECTS THE CALL', 'cpu.max     – hard cap', 'cpu.weight  –, Talk 100: we yield', 'memory.max  –, no swap', 'OOM         engine dies, Talk lives', 'LAG KEEPS CAPTIONS USEFUL', 'lag ≥ 1.0 s   hold new voices', 'lag ≥ 2.0 s   for 5 s: pause the', '              newest speaker 10 s', 'lag < 0.5 s   for 5 s: reopen', 'ONLY A CRASH STOPS CAPTIONS', 'engine died    restart, ~2 s', '3 deaths/60 s  rest 2 min, doubling', 'OOM kill       rest 2 min', 'since start    0 restarts · 0 OOM'],
  },
  lost: {
    lanes: ['Audio lost', 'Whistle · 2 CPU · 3 GiB', '2', 'voices', 'heard now', '2', '/4', 'seats', 'taken', '0.4', ' s', 'captions trail', 'speech by', '18:34:56', 'admit Ana Ferreira', 'seat 2 of 4 · lag 0.4 s < 1.0 · stall 6%', 'Memory', '1.07/3 GiB', 'CPU stall', '6%', 'Engine busy', '42%', 'Reply p95', '0.8 s'],
    pipeline: ['Whistle · 2 CPU · 3 GiB', '1 · LISTEN', 'Microphones', '2', 'voices of 4 mics', 'voice gate per mic', '32 ms frames', 'silence ends a turn', '2 · CUT', 'Speech pieces', '1 s', 'per piece, 15 s …', 'queue 1 of 24', 'older than 4 s:', 'dropped, not late', '3 · ADMIT', 'Seats', '2/4', 'admitting', 'first voice: always', 'then lag < 1.0 s', 'and stall < 20%', '4 · RECOGNISE', 'Engine', '42%', 'busy, 2 threads', '4 workers', 'reply p95 0.8 s', '1.07 of 3 GiB', '5 · SEND', 'Captions', '20', 'lines per minute', 'trail speech 0.4 s', 'via Talk signaling', 'no audio leaves', '◆', 'Admission decides per speaker, the moment they start talking. A seat, once given, is kept.', 'THE KERNEL PROTECTS THE CALL', 'cpu.max     2 CPU hard cap', 'cpu.weight  10, Talk 100: we yield', 'memory.max  3 GiB, no swap', 'OOM         engine dies, Talk lives', 'LAG KEEPS CAPTIONS USEFUL', 'lag ≥ 1.0 s   hold new voices', 'lag ≥ 2.0 s   for 5 s: pause the', '              newest speaker 10 s', 'lag < 0.5 s   for 5 s: reopen', 'ONLY A CRASH STOPS CAPTIONS', 'engine died    restart, ~2 s', '3 deaths/60 s  rest 2 min, doubling', 'OOM kill       rest 2 min', 'since start    0 restarts · 0 OOM'],
  },
}

// The video never shows these: it runs only in a live room while captions are ready.
const PAGE_ONLY_PILLS = { notReady: 'Captions not ready · blocked', noRooms: 'No captions running' }
const PANEL_TITLES = ['THE KERNEL PROTECTS THE CALL', 'LAG KEEPS CAPTIONS USEFUL', 'ONLY A CRASH STOPS CAPTIONS']

const squash = (text) => text.replace(/\s+/g, ' ').trim()

// The page's strings where the video cut a line short: compared up to the cut.
function asVideo(page, video) {
  return page.map((text, i) => {
    const cut = video[i]
    return typeof cut === 'string' && cut.endsWith('…') && text.startsWith(cut.slice(0, -1)) && text !== cut ? cut : text
  })
}

// The three panels as {title: every line squashed into one}; the video pads its
// monospace columns and wraps one line, the page lays rows out as key and value.
function videoPanels(lines) {
  const panels = {}
  let title = null
  for (const line of lines) {
    if (PANEL_TITLES.includes(line)) panels[(title = line)] = []
    else panels[title].push(line)
  }
  return Object.fromEntries(Object.entries(panels).map(([k, v]) => [k, squash(v.join(' '))]))
}

function pagePanels(sample) {
  return Object.fromEntries(
    protectionPanels(sample).map((panel) => [panel.title.toUpperCase(), squash(panel.rows.map((row) => row.join(' ')).join(' '))]),
  )
}

function lanesView(name, { sample: data, columns: history }) {
  const pill = statusPill(data, history, NOW)
  const model = hero(data)
  const decision = decisionLine(data)
  const budgets = footer(data)
  const metric = (id) => budgets.metrics.find((m) => m.id === id).value
  return [
    PAGE_ONLY_PILLS[name] ? VIDEO[name].lanes[0] : pill.label,
    engineLine(data),
    ...(observeOnly(data).on ? [observeOnly(data).badge] : []),
    model.voices.value,
    ...model.voices.label,
    model.seats.value,
    model.seats.of,
    ...model.seats.label,
    model.lag.value,
    ...(model.lag.unit ? [model.lag.unit] : []),
    ...model.lag.label,
    ...(decision ? [decision.time, decision.verb, decision.text] : ['No admission decision yet']),
    'Memory',
    budgets.memory.text,
    'CPU stall',
    metric('stall'),
    'Engine busy',
    metric('duty'),
    'Reply p95',
    metric('p95'),
  ]
}

function pipelineView({ sample: data, columns: history }) {
  const stages = pipelineStages(data, history, NOW)
  return [
    engineLine(data),
    ...(observeOnly(data).on ? [observeOnly(data).badge] : []),
    ...stages.flatMap((stage, i) => [
      stage.step.toUpperCase(),
      stage.name,
      stage.big,
      stage.unit,
      // Without a memory limit the video's engine card keeps only its last line: its
      // conditional takes the whole implicitly concatenated f-string.
      ...(i === 3 && !data.limitMiB ? stage.detail.slice(2) : stage.detail),
    ]),
    '◆',
    'Admission decides per speaker, the moment they start talking. A seat, once given, is kept.',
  ]
}

for (const [name, fixture] of Object.entries(CASES)) {
  test(`the header, hero and budgets say what the video says: ${name}`, () => {
    const video = VIDEO[name].lanes
    assert.deepEqual(asVideo(lanesView(name, fixture), video), video)
  })

  test(`the pipeline cards and panels say what the video says: ${name}`, () => {
    const video = VIDEO[name].pipeline
    const split = video.indexOf(PANEL_TITLES[0])
    assert.deepEqual(asVideo(pipelineView(fixture), video.slice(0, split)), video.slice(0, split))
    assert.deepEqual(pagePanels(fixture.sample), videoPanels(video.slice(split)))
  })
}

test('the engine card keeps its workers and p95 lines without a memory limit', () => {
  const { sample: data, columns: history } = CASES.nullBudgets
  assert.deepEqual(pipelineStages(data, history, NOW)[3].detail, ['4 workers', 'reply p95 –', 'memory –'])
})

test('the video cut these lines; the page shows them whole', () => {
  const stages = pipelineStages(CASES.observeOnly.sample, CASES.observeOnly.columns, NOW)
  assert.equal(stages[1].unit, 'per piece, 15 s max')
  assert.equal(stages[2].unit, 'would hold: all seats taken')
})

/* ---------- status pill ---------- */

const pill = (data, history = [], now = NOW) => {
  const { label, tone } = statusPill(data, history, now)
  return [label, tone]
}
const closed = governor({ open: false, reason: 'memory' })
const dropAt = (t) => [{ t, lanes: {}, finals: 0, drops: 2, lag: null, shed: false }]

test('the pill starts with no sample, including an empty one', () => {
  assert.deepEqual(pill(null), ['Starting', 'idle'])
  assert.deepEqual(pill(undefined), ['Starting', 'idle'])
  assert.deepEqual(pill({}), ['Starting', 'idle'])
})

test('the pill names a caption service that is not ready, with its state', () => {
  assert.deepEqual(pill(sample({ state: 'blocked' })), ['Captions not ready · blocked', 'bad'])
  assert.deepEqual(pill(sample({ state: 'recovering' })), ['Captions not ready · recovering', 'warn'])
  assert.deepEqual(pill(sample({ state: 'disabled' })), ['Captions not ready · disabled', 'idle'])
})

test('the pill says when no call has captions, and how the page wakes', () => {
  assert.deepEqual(statusPill(sample({ rooms: 0 }), [], NOW), {
    label: 'No captions running',
    tone: 'idle',
    note: 'The page wakes when a call enables them.',
  })
  assert.equal(statusPill(sample(), [], NOW).note, '')
})

test('audio dropped in the last minute of server time turns the pill red', () => {
  assert.deepEqual(pill(sample(), dropAt(NOW - 60)), ['Audio lost', 'bad'])
  assert.deepEqual(pill(sample(), dropAt(NOW - 60.5)), ['Captions live', 'live'])
  assert.deepEqual(pill(sample(), columns(120)), ['Captions live', 'live'])
})

test('closed admission holds new voices, or would hold them while observing', () => {
  assert.deepEqual(pill(sample({ admission: closed })), ['Holding new voices', 'warn'])
  assert.deepEqual(pill(sample({ admission: { ...closed, observeOnly: true } })), ['Would hold new voices', 'warn'])
  // A governor that has not reported `open` admits; no governor never holds.
  const { open, ...silent } = governor()
  assert.equal(open, true)
  assert.deepEqual(pill(sample({ admission: silent })), ['Captions live', 'live'])
  assert.deepEqual(pill(sample({ admission: {}, waiting: 0 })), ['Captions live', 'live'])
})

test('speakers waiting for a seat, else captions live', () => {
  assert.deepEqual(pill(sample({ waiting: 3 })), ['Speakers waiting', 'warn'])
  assert.deepEqual(pill(sample({ waiting: 0 })), ['Captions live', 'live'])
})

test('pill precedence: not ready, no rooms, audio lost, holding, waiting, live', () => {
  const lost = dropAt(NOW - 1)
  const everything = { state: 'blocked', rooms: 0, admission: closed, waiting: 2 }
  assert.equal(pill(sample(everything), lost)[0], 'Captions not ready · blocked')
  assert.equal(pill(sample({ ...everything, state: 'ready' }), lost)[0], 'No captions running')
  assert.equal(pill(sample({ ...everything, state: 'ready', rooms: 1 }), lost)[0], 'Audio lost')
  assert.equal(pill(sample({ ...everything, state: 'ready', rooms: 1 }), [])[0], 'Holding new voices')
  assert.equal(pill(sample({ waiting: 2 }), [])[0], 'Speakers waiting')
})

/* ---------- observe-only wording ---------- */

test('observe-only mode shows a badge and says nothing is held yet', () => {
  assert.deepEqual(observeOnly(CASES.observeOnly.sample), {
    on: true,
    badge: 'observe only',
    note: 'Cassini Live observes these rules; nothing is held or paused yet.',
  })
  assert.deepEqual(observeOnly(CASES.busy.sample), { on: false, badge: '', note: '' })
  assert.deepEqual(observeOnly(null), { on: false, badge: '', note: '' })
})

test('observe-only decisions and holds read "would"', () => {
  const observing = sample({ admission: governor({ open: false, reason: 'cpu', observeOnly: true }) })
  assert.deepEqual(hero(observing).seats.label, ['seats taken', 'would hold: CPU stall'])
  assert.equal(pipelineStages(observing)[2].unit, 'would hold: CPU stall')
  assert.equal(decisionLine(observing).verb, 'would admit Ana Ferreira')
  const enforcing = sample({ admission: governor({ open: false, reason: 'cpu' }) })
  assert.deepEqual(hero(enforcing).seats.label, ['seats taken', 'holding: CPU stall'])
  assert.equal(decisionLine(enforcing).verb, 'admit Ana Ferreira')
  // An unknown reason is shown as it is.
  assert.equal(hero(sample({ admission: governor({ open: false, reason: 'drain' }) })).seats.label[1], 'holding: drain')
})

/* ---------- hero ---------- */

test('the hero counts voices and seats, at most eight seat chips', () => {
  const model = hero(sample({ vadActive: 1, admission: governor({ seats: 3, ceiling: 12 }) }))
  assert.deepEqual(model.voices, { value: '1', label: ['voice', 'heard now'] })
  assert.equal(`${model.seats.value}${model.seats.of}`, '3/12')
  assert.deepEqual(model.seats.chips, [true, true, true, false, false, false, false, false])
  // Without a governor: the pool's busy workers of its capacity.
  assert.equal(`${hero(sample({ admission: {}, busy: 1, capacity: 2 })).seats.value}/2`, '1/2')
})

test('the lag figure turns amber at the admit threshold, on a 0-3 s bar', () => {
  const at = (lagSeconds, extra = {}) => hero(sample({ admission: governor({ lagSeconds, ...extra }) })).lag
  assert.deepEqual([at(0.99).value, at(0.99).unit, at(0.99).tone], ['1.0', ' s', 'gold'])
  assert.equal(at(1.0).tone, 'warn')
  assert.equal(at(1.5).fill, 0.5)
  assert.equal(at(9).fill, 1)
  assert.equal(at(0).fill, 0)
  assert.deepEqual([at(null).value, at(null).unit, at(null).fill], ['–', '', 0])
  assert.deepEqual([at(1).admit, at(1).shed], [1 / 3, 2 / 3])
  assert.equal(at(1, { lagShed: 5 }).shed, 1)
})

/* ---------- pipeline ---------- */

test('captions sent count the last minute, per minute once the history spans one', () => {
  const sent = (history) => pipelineStages(sample(), history, NOW)[4]
  const every = (count) => columns(count, { finals: () => 1 })
  assert.deepEqual([sent(every(59)).big, sent(every(59)).unit], ['59', 'lines per minute'])
  assert.deepEqual([sent(every(58)).big, sent(every(58)).unit], ['58', 'lines so far'])
  assert.deepEqual([sent(every(120)).big, sent(every(120)).unit], ['61', 'lines per minute'])
  assert.deepEqual([sent([]).big, sent([]).unit], ['0', 'lines so far'])
})

test('the microphone count includes silent and lane-less tracks, the cut its maximum', () => {
  const stages = (extra) => pipelineStages(sample(extra), [], NOW)
  assert.equal(stages({ silentMics: 13, hiddenSpeakers: 2 })[0].unit, 'voices of 18 mics')
  assert.equal(stages({ lanes: [], silentMics: 1 })[0].unit, 'voices of 1 mic')
  assert.equal(stages({ silentMics: 13, hiddenSpeakers: 2 })[1].detail[0], 'queue 1 of 108')
  assert.equal(stages({ segmentSeconds: 0 })[1].unit, 'per piece')
  assert.equal(stages({ segmentSeconds: 8 })[1].unit, 'per piece, 8 s max')
})

test('the engine card names the engine shape and warns at 90 % duty', () => {
  const engine = (extra, admission = {}) => pipelineStages(sample({ ...extra, admission: governor(admission) }), [], NOW)[3]
  assert.equal(engine({ engine: 'whistle', capacity: 1 }).detail[0], '1 worker')
  assert.equal(engine({ engine: 'nemotron-en', capacity: 1 }).detail[0], '1 process, 1 slot')
  assert.equal(engine({ threads: 1 }).unit, 'busy, 1 thread')
  assert.equal(engine({}, { engineDuty: 0.9 }).tone, 'warn')
  assert.equal(engine({}, { engineDuty: 0.89 }).tone, 'live')
})

test('the panels fall back where the governor or the cgroup gives nothing', () => {
  const panels = protectionPanels(sample({ admission: {}, cpuWeight: null, quota: null, limitMiB: null }))
  assert.deepEqual(panels[0].rows.slice(0, 3), [
    ['cpu.max', '– hard cap'],
    ['cpu.weight', '–, Talk 100: we yield'],
    ['memory.max', '–, no swap'],
  ])
  assert.deepEqual(panels[1].rows[1], ['lag ≥ 2.0 s', 'for 5 s: pause the newest speaker 10 s'])
  assert.equal(protectionPanels(sample({ admission: governor({ holdOff: null }) }))[1].rows[1][1], 'for 5 s: pause the newest speaker – s')
})

/* ---------- budget footer ---------- */

test('the memory bar turns amber from 60 % and marks the 80 % pause', () => {
  const memory = (captionMiB, limitMiB = 1000) => footer(sample({ captionMiB, limitMiB })).memory
  assert.deepEqual([memory(600).tone, memory(599).tone], ['warn', 'grey'])
  assert.equal(memory(600).mark, 0.8)
  assert.equal(memory(1500).fraction, 1)
  assert.deepEqual([memory(null, null).text, memory(null, null).fraction], ['–', 0])
  assert.equal(memory(1100, 3072).text, '1.07/3 GiB')
})

test('engine busy warns from 90 %; the other budgets read as numbers or dashes', () => {
  const metrics = (admission, extra = {}) =>
    Object.fromEntries(footer(sample({ ...extra, admission: governor(admission) })).metrics.map((m) => [m.id, [m.value, m.tone]]))
  assert.deepEqual(metrics({ engineDuty: 0.9 }).duty, ['90%', 'warn'])
  assert.deepEqual(metrics({ engineDuty: 0.895 }).duty, ['90%', 'soft'])
  assert.deepEqual(metrics({ cpuStall: 0.125 }).stall, ['12%', 'soft'])
  assert.deepEqual(metrics({}, { p95: 1.25 }).p95, ['1.2 s', 'soft'])
  assert.deepEqual(metrics({}, { samplerMs: 2.25 }).sampler, ['2.2 ms', 'soft'])
  const empty = Object.fromEntries(footer(null).metrics.map((m) => [m.id, m.value]))
  assert.deepEqual(empty, { stall: '–', duty: '–', p95: '–', sampler: '–' })
})

/* ---------- decision, video, freshness ---------- */

test('the decision line reads the time in UTC, the verb and who; null without one', () => {
  assert.deepEqual(decisionLine(CASES.busy.sample), {
    time: '18:34:56',
    verb: 'hold Ana Ferreira',
    text: 'lag 1.3 s ≥ admit 1.0 · reopens under 0.5 s for 5 s',
    tone: 'warn',
  })
  assert.equal(decisionLine(sample()).tone, 'live')
  assert.equal(decisionLine(sample({ decision: null })), null)
  assert.equal(decisionLine(null), null)
  assert.equal(decisionLine(sample({ decision: { at: 1e300, verb: 'admit', who: 'x', text: '' } })).time, '–')
  // Names are data: passed through untouched for textContent.
  const who = '<img src=x onerror=alert(1)>'
  assert.equal(decisionLine(sample({ decision: { at: 0, verb: 'pause', who, text: '' } })).verb, `pause ${who}`)
})

test('the video line: off, waiting, starting, and its cost while it publishes', () => {
  assert.deepEqual(videoLine({ enabled: false, state: 'disabled' }), { text: 'Diagnostic video off', tone: 'idle' })
  assert.deepEqual(videoLine(null), { text: 'Diagnostic video off', tone: 'idle' })
  assert.deepEqual(videoLine({ enabled: true, state: 'idle' }), {
    text: 'Diagnostic video on, waiting for a captioned call',
    tone: 'idle',
  })
  assert.equal(videoLine({ enabled: true, state: 'starting' }).text, 'Diagnostic video starting')
  const publishing = { enabled: true, state: 'connected', diagnosticCpu: 3.5, diagnosticMiB: 41.6, samplerMs: 2.1, frames: 40, kbps: 70.24, quantizer: 40 }
  assert.deepEqual(videoLine(publishing), { text: 'Diagnostic video publishing · 4% CPU · 42 MiB · 70 kbit/s', tone: 'live' })
  assert.equal(videoLine({ enabled: true, state: 'connected' }).text, 'Diagnostic video publishing')
  // A stopped child's last figures are not its current cost.
  assert.deepEqual(videoLine({ ...publishing, state: 'paused: diagnostic budget exceeded' }), {
    text: 'Diagnostic video paused: diagnostic budget exceeded',
    tone: 'warn',
  })
})

test('the video line reads no field but its state and cost', () => {
  const status = { enabled: true, state: 'connected', diagnosticCpu: 1, diagnosticMiB: 2, kbps: 3, room: 'ROOMTOKEN-q9x7', owner: 'ROOMTOKEN-q9x7' }
  assert.ok(!videoLine(status).text.includes('ROOMTOKEN'))
})

test('the page is stale five seconds after its last answer', () => {
  assert.deepEqual(freshness(null, 1000), { stale: true, text: 'Waiting for the first update', tone: 'idle' })
  assert.deepEqual(freshness(1000, 5999), { stale: false, text: 'Live · updated every second', tone: 'live' })
  assert.deepEqual(freshness(1000, 6000), { stale: true, text: 'Stale · last update 5 s ago', tone: 'warn' })
  assert.equal(freshness(1000, 15999).text, 'Stale · last update 10 s ago')
  assert.equal(freshness(1000, 181000).text, 'Stale · last update 3 min ago')
  assert.deepEqual(unreachable(1000, 13000), { stale: true, text: 'Cassini Live unreachable · last update 10 s ago · retrying', tone: 'bad' })
  assert.equal(unreachable(null, 13000).text, 'Cassini Live unreachable · retrying')
})

test('a failed sample names its exception type only', () => {
  assert.equal(sampleError('KeyError'), 'The last sample failed (KeyError); showing the one before.')
  assert.equal(sampleError(null), '')
})

/* ---------- numbers ---------- */

test('numbers round like Python: exact ties to even, everything else to nearest', () => {
  assert.deepEqual(
    [fixed(1.25, 1), fixed(0.125, 2), fixed(2.5, 0), fixed(0.375, 2), fixed(-1.25, 1), fixed(12.5, 0)],
    ['1.2', '0.12', '2', '0.38', '-1.2', '12'],
  )
  assert.deepEqual([fixed(0.15, 1), fixed(1.35, 1), fixed(2.675, 2), fixed(1.0, 1)], ['0.1', '1.4', '2.67', '1.0'])
  assert.deepEqual([seconds(1.125), seconds(null), seconds(undefined), seconds(0)], ['1.1 s', '–', '–', '0.0 s'])
  assert.deepEqual([percent(0.125), percent(0.97), percent(null)], ['12%', '97%', '–'])
})

test('general formatting matches Python g', () => {
  assert.deepEqual([g(4.0), g(0.5), g(2), g(10), g(12.5)], ['4', '0.5', '2', '10', '12.5'])
  assert.deepEqual([g(3, 3), g(2.25, 3), g(1.5, 3), g(1000, 3), g(1.125, 3)], ['3', '2.25', '1.5', '1e+03', '1.12'])
  assert.deepEqual([g(0.0001), g(0.00001), g(0)], ['0.0001', '1e-05', '0'])
})

test('the engine line adds the quota and limit only when both are known', () => {
  assert.equal(engineLine(sample({ engine: 'nemotron-en', quota: 1.5, limitMiB: 2304 })), 'Nemotron EN · 1.5 CPU · 2.25 GiB')
  assert.equal(engineLine(sample({ quota: 2, limitMiB: null })), 'Whistle')
  assert.equal(engineLine(sample({ quota: null, limitMiB: 3072 })), 'Whistle')
  assert.equal(engineLine(sample({ engine: 'future-engine', quota: null })), 'future-engine')
  assert.equal(engineLine(null), '')
})

/* ---------- feed protocol ---------- */

test('polls back off 2, 4, 8, 16 then 30 s', () => {
  assert.deepEqual([1, 2, 3, 4, 5, 6, 20].map(backoffMs), [2000, 4000, 8000, 16000, 30000, 30000, 30000])
})

test('the feed path is relative to the page', () => {
  assert.equal(feedPath(0), 'api/feed?since=0')
  assert.equal(feedPath(1234.567), 'api/feed?since=1234.567')
  assert.equal(new URL(feedPath(5), 'https://nc.example/index.php/apps/app_api/proxy/live_transcription/diagnostics/').pathname,
    '/index.php/apps/app_api/proxy/live_transcription/diagnostics/api/feed')
  assert.equal(new URL(feedPath(5), 'https://nc.example/index.php/apps/app_api/proxy/live_transcription/diagnostics/index.html').pathname,
    '/index.php/apps/app_api/proxy/live_transcription/diagnostics/api/feed')
})

test('a server clock that went back means a restart', () => {
  assert.equal(restarted(null, 5), false)
  assert.equal(restarted(100, 100.5), false)
  assert.equal(restarted(100, 99.9), true)
  assert.equal(restarted(100, 99.9, 'a1b2', 'a1b2'), true)
})

test('another instance means a restart, though the clock went on', () => {
  // A restarted container keeps the host's monotonic clock.
  assert.equal(restarted(100, 100.5, 'a1b2', 'c3d4'), true)
  assert.equal(restarted(100, 100.5, 'a1b2', 'a1b2'), false)
  // The first answer, or after a restart the page already started over from.
  assert.equal(restarted(null, 5, null, 'c3d4'), false)
  // An answer without an instance leaves the clock to decide.
  assert.equal(restarted(100, 100.5, 'a1b2', undefined), false)
  assert.equal(restarted(100, 100.5, null, 'a1b2'), false)
})

test('new columns are appended once, in order, and the window keeps its count and age', () => {
  const ring = columns(3, { now: 100 })
  const merged = mergeColumns(ring, [...columns(2, { now: 102 }), ...columns(1, { now: 99 })], 120, 102)
  assert.deepEqual(merged.map((c) => c.t), [98, 99, 100, 101, 102])
  assert.equal(merged[0], ring[0])
  assert.deepEqual(mergeColumns(ring, columns(130, { now: 230 }), 120, 230).map((c) => c.t).slice(0, 2), [111, 112])
  assert.equal(mergeColumns(ring, columns(130, { now: 230 }), 120, 230).length, 120)
  // A gap (a paused tab, an idle feed) ages old columns out by server time.
  assert.deepEqual(mergeColumns(columns(5, { now: 100 }), columns(2, { now: 250 }), 120, 250).map((c) => c.t), [249, 250])
  assert.deepEqual(mergeColumns(ring, null, 120, 100).map((c) => c.t), [98, 99, 100])
  assert.deepEqual(mergeColumns([], [{ t: 'x' }, null, { t: 5 }], 120, 6).map((c) => c.t), [5])
})

test('an answer without columns starts the feed history over', () => {
  assert.equal(historyStart(null, { head: 4990, now: 5000 }), null)
  assert.equal(historyStart(null, { head: 0, now: 5000 }), 5000)
  assert.equal(historyStart(5000, { head: 5010, now: 5011 }), 5000)
  assert.equal(historyStart(5000, { head: 0, now: 5100 }), 5100)
  const ring = columns(3, { now: 100 })
  assert.equal(feedHistory(ring, null), ring)
  assert.deepEqual(feedHistory(ring, 98.5).map((c) => c.t), [99, 100])
})

test('after the feed idled, captions sent count its new history, not the minute before', () => {
  // Two minutes watched, the tab hidden 45 s (the feed drops its columns), then five fresh columns.
  const watched = columns(120, { now: NOW - 50, finals: () => 1 })
  let start = historyStart(null, { head: NOW - 50, now: NOW - 50 })
  let ring = mergeColumns([], watched, 120, NOW - 50)
  start = historyStart(start, { head: 0, now: NOW - 5 })
  ring = mergeColumns(ring, [], 120, NOW - 5)
  ring = mergeColumns(ring, columns(5, { now: NOW, finals: () => 1 }), 120, NOW)
  start = historyStart(start, { head: NOW, now: NOW })
  // The ring keeps the columns before the gap for the lanes.
  assert.deepEqual([ring[0].t, ring.length], [NOW - 119, 75])
  const sent = pipelineStages(sample(), feedHistory(ring, start), NOW)[4]
  assert.deepEqual([sent.big, sent.unit], ['5', 'lines so far'])
})
