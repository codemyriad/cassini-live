import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  emptyLanesText,
  GAP_S,
  lagGraph,
  lagSummary,
  laneLabel,
  laneRows,
  laneSegments,
  lanesSummary,
  laneTag,
  LEGEND,
  leftOut,
} from '../../src/diagnostics/lanes.js'
import { LANE_CASES, NOW, TEN_LANES, governor, laneColumns, sample } from '../diagnostics-fixtures.mjs'

const html = readFileSync(new URL('../../src/diagnostics/index.html', import.meta.url), 'utf8')
const strip = (markup) => markup.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim()
const TITLE = html.match(/id="lanes-title"[^>]*>([^<]*)</)[1]
const AXIS = [...html.match(/<p class="axis"[^>]*>(.*?)<\/p>/)[1].matchAll(/<span>([^<]*)<\/span>/g)].map((m) => m[1])
const SENT = strip(html.match(/<li class="sent">(.*?)<\/li>/)[1])

// What diagnostic_video.render(sample, columns, "lanes") drew for each LANE_CASES case,
// with its clock at NOW and int lane keys as in the caption process: the row above the
// band (title, legend, who has no lane), the band's text when it has no lanes, each lane's
// name, tag and value and its runs as "state start-end" in seconds before the newest
// column, the lag line as "x:lag" and the shed marks and caption ticks at x seconds, the
// graph's labels and the axis. The video cuts a name that overflows a quarter of the band
// with "…"; the page cuts it with CSS.
const VIDEO = {
  tenLanes: {
    head: ["SPEAKERS, LAST TWO MINUTES", "captioned", "held", "paused", "lost", "+3 silent mics · +2 more speakers"],
    empty: null,
    rows: [
      ["Ana Ferreira", "scribe 1", "0.3 s", "captioned 112-103 captioned 88-79 captioned 64-55 captioned 40-31 captioned 16-7 captioned 3-0"],
      ["Bo", "scribe 2", "0.0 s", "captioned 115-103 captioned 87-75 captioned 59-47 captioned 31-19 captioned 3-0"],
      ["Chidi Okonkwo", "paused", "2.4 s", "captioned 94-88 captioned 62-56 captioned 30-24 paused 12-0"],
      ["Dana Whitfield", "held", "1.6 s", "held 97-95 captioned 95-88 held 61-59 captioned 59-52 held 25-23 captioned 23-16 held 8-0"],
      ["Emeka Adeyemi", "scribe 3", "0.8 s", "captioned 116-104 captioned 96-84 captioned 76-64 captioned 56-44 captioned 36-34 lost 34-30 captioned 30-24 captioned 16-4"],
      ["Guest · Farah", "idle", "", "captioned 120-117 captioned 99-93 captioned 75-69 captioned 51-45 captioned 27-21"],
      ["Maximiliane Theodora von Wolkenst…", "idle", "", "captioned 120-113 captioned 94-85 captioned 66-57 captioned 38-29"],
      ["Jun", "scribe 4", "1.1 s", "captioned 120-117 captioned 97-85 captioned 65-53 captioned 33-21 captioned 3-0"],
      ["Participant", "idle", "", "captioned 96-90 captioned 60-54 captioned 24-18"],
      ["other call", "scribe 5", "0.5 s", "captioned 101-95 captioned 73-67 captioned 45-39 captioned 17-11 captioned 3-0"],
    ],
    lag: "119.5:0.54 118.5:0.55 117.5:0.54 116.5:0.53 115.5:0.5 114.5:0.46 113.5:0.41 112.5:0.37 111.5:0.32 110.5:0.27 109.5:0.23 108.5:0.19 107.5:0.17 106.5:0.15 105.5:0.15 104.5:0.16 103.5:0.18 102.5:0.22 101.5:0.26 100.5:0.6 99.5:0.86 98.5:1.12 97.5:1.38 96.5:1.64 95.5:1.9 94.5:2.16 93.5:2.42 92.5:2.5 91.5:2.5 90.5:0.5 89.5:0.47 88.5:0.42 87.5:0.37 86.5:0.32 85.5:0.27 84.5:0.23 83.5:0.19 82.5:0.17 81.5:0.15 79.5:0.16 78.5:0.18 77.5:0.21 76.5:0.25 75.5:0.3 74.5:0.35 73.5:0.4 72.5:0.45 71.5:0.49 70.5:0.52 69.5:0.54 68.5:0.55 67.5:0.55 66.5:0.53 65.5:0.51 64.5:0.47 63.5:0.43 62.5:0.38 61.5:0.33 60.5:0.28 59.5:0.24 58.5:0.2 57.5:0.17 56.5:0.15 55.5:0.15 54.5:0.16 53.5:0.18 52.5:0.21 51.5:0.25 50.5:0.29 49.5:0.34 48.5:0.39 47.5:0.44 46.5:0.48 45.5:0.51 44.5:0.54 43.5:0.55 42.5:0.55 41.5:0.53 40.5:0.6 39.5:0.86 38.5:1.12 37.5:1.38 36.5:1.64 35.5:1.9 34.5:2.16 33.5:2.42 32.5:2.5 31.5:2.5 30.5:0.15 29.5:0.16 28.5:0.17 27.5:0.2 26.5:0.24 25.5:0.29 24.5:0.34 23.5:0.39 22.5:0.43 21.5:0.48 19.5:0.53 18.5:0.55 17.5:0.55 16.5:0.54 15.5:0.51 14.5:0.48 13.5:0.44 12.5:0.39 11.5:0.34 10.5:0.29 9.5:0.25 8.5:0.21 7.5:0.18 6.5:0.16 5.5:0.15 4.5:0.15 3.5:0.17 2.5:0.2 1.5:0.24 0.5:0.28",
    sheds: "92.5 32.5",
    sent: "119.5 116.5 114.5 113.5 110.5 107.5 104.5 101.5 100.5 98.5 95.5 93.5 92.5 89.5 86.5 83.5 80.5 79.5 77.5 74.5 72.5 71.5 68.5 65.5 62.5 59.5 58.5 56.5 53.5 51.5 50.5 47.5 44.5 41.5 38.5 37.5 35.5 32.5 30.5 29.5 26.5 23.5 20.5 17.5 16.5 14.5 11.5 9.5 8.5 5.5 2.5",
    labels: ["shed 2.0 s", "admit 1.0 s", "▮ caption sent"],
    axis: ["−2 min", "−1 min", "now"],
  },
  nemotron: {
    head: ["SPEAKERS, LAST TWO MINUTES", "captioned", "held", "paused", "lost", "+1 silent mic · +1 more speaker"],
    empty: null,
    rows: [
      ["Ana Ferreira", "slot 1", "0.3 s", "captioned 112-103 captioned 88-79 captioned 64-55 captioned 40-31 captioned 16-7 captioned 3-0"],
      ["Bo", "slot 2", "0.0 s", "captioned 115-103 captioned 87-75 captioned 59-47 captioned 31-19 captioned 3-0"],
      ["Chidi Okonkwo", "paused", "2.4 s", "captioned 94-88 captioned 62-56 captioned 30-24 paused 12-0"],
      ["Dana Whitfield", "held", "1.6 s", "held 97-95 captioned 95-88 held 61-59 captioned 59-52 held 25-23 captioned 23-16 held 8-0"],
    ],
    lag: "119.5:0.54 118.5:0.55 117.5:0.54 116.5:0.53 115.5:0.5 114.5:0.46 113.5:0.41 112.5:0.37 111.5:0.32 110.5:0.27 109.5:0.23 108.5:0.19 107.5:0.17 106.5:0.15 105.5:0.15 104.5:0.16 103.5:0.18 102.5:0.22 101.5:0.26 100.5:0.6 99.5:0.86 98.5:1.12 97.5:1.38 96.5:1.64 95.5:1.9 94.5:2.16 93.5:2.42 92.5:2.5 91.5:2.5 90.5:0.5 89.5:0.47 88.5:0.42 87.5:0.37 86.5:0.32 85.5:0.27 84.5:0.23 83.5:0.19 82.5:0.17 81.5:0.15 79.5:0.16 78.5:0.18 77.5:0.21 76.5:0.25 75.5:0.3 74.5:0.35 73.5:0.4 72.5:0.45 71.5:0.49 70.5:0.52 69.5:0.54 68.5:0.55 67.5:0.55 66.5:0.53 65.5:0.51 64.5:0.47 63.5:0.43 62.5:0.38 61.5:0.33 60.5:0.28 59.5:0.24 58.5:0.2 57.5:0.17 56.5:0.15 55.5:0.15 54.5:0.16 53.5:0.18 52.5:0.21 51.5:0.25 50.5:0.29 49.5:0.34 48.5:0.39 47.5:0.44 46.5:0.48 45.5:0.51 44.5:0.54 43.5:0.55 42.5:0.55 41.5:0.53 40.5:0.6 39.5:0.86 38.5:1.12 37.5:1.38 36.5:1.64 35.5:1.9 34.5:2.16 33.5:2.42 32.5:2.5 31.5:2.5 30.5:0.15 29.5:0.16 28.5:0.17 27.5:0.2 26.5:0.24 25.5:0.29 24.5:0.34 23.5:0.39 22.5:0.43 21.5:0.48 19.5:0.53 18.5:0.55 17.5:0.55 16.5:0.54 15.5:0.51 14.5:0.48 13.5:0.44 12.5:0.39 11.5:0.34 10.5:0.29 9.5:0.25 8.5:0.21 7.5:0.18 6.5:0.16 5.5:0.15 4.5:0.15 3.5:0.17 2.5:0.2 1.5:0.24 0.5:0.28",
    sheds: "92.5 32.5",
    sent: "119.5 116.5 114.5 113.5 110.5 107.5 104.5 101.5 100.5 98.5 95.5 93.5 92.5 89.5 86.5 83.5 80.5 79.5 77.5 74.5 72.5 71.5 68.5 65.5 62.5 59.5 58.5 56.5 53.5 51.5 50.5 47.5 44.5 41.5 38.5 37.5 35.5 32.5 30.5 29.5 26.5 23.5 20.5 17.5 16.5 14.5 11.5 9.5 8.5 5.5 2.5",
    labels: ["shed 2.0 s", "admit 1.0 s", "▮ caption sent"],
    axis: ["−2 min", "−1 min", "now"],
  },
  redacted: {
    head: ["SPEAKERS, LAST TWO MINUTES", "captioned", "held", "paused", "lost"],
    empty: null,
    rows: [
      ["Participant", "scribe 1", "0.3 s", "captioned 40-31 captioned 16-7 captioned 3-0"],
      ["Participant", "scribe 2", "0.0 s", "captioned 31-19 captioned 3-0"],
      ["other call", "scribe 5", "0.5 s", ""],
    ],
    lag: "44.5:0.54 43.5:0.55 42.5:0.55 41.5:0.53 40.5:0.6 39.5:0.86 38.5:1.12 37.5:1.38 36.5:1.64 35.5:1.9 34.5:2.16 33.5:2.42 32.5:2.5 31.5:2.5 30.5:0.15 29.5:0.16 28.5:0.17 27.5:0.2 26.5:0.24 25.5:0.29 24.5:0.34 23.5:0.39 22.5:0.43 21.5:0.48 19.5:0.53 18.5:0.55 17.5:0.55 16.5:0.54 15.5:0.51 14.5:0.48 13.5:0.44 12.5:0.39 11.5:0.34 10.5:0.29 9.5:0.25 8.5:0.21 7.5:0.18 6.5:0.16 5.5:0.15 4.5:0.15 3.5:0.17 2.5:0.2 1.5:0.24 0.5:0.28",
    sheds: "32.5",
    sent: "44.5 41.5 38.5 37.5 35.5 32.5 30.5 29.5 26.5 23.5 20.5 17.5 16.5 14.5 11.5 9.5 8.5 5.5 2.5",
    labels: ["shed 2.0 s", "admit 1.0 s", "▮ caption sent"],
    axis: ["−2 min", "−1 min", "now"],
  },
  quiet: {
    head: ["SPEAKERS, LAST TWO MINUTES", "captioned", "held", "paused", "lost"],
    empty: "Nobody spoke in the last two minutes · 3 mics open",
    rows: [],
    lag: "119.5:0 118.5:0.1 117.5:0.2 116.5:0.3 115.5:0.4 114.5:0.5 113.5:0.6 112.5:0.7 111.5:0.8 110.5:0.9 109.5:0 108.5:0.1 107.5:0.2 106.5:0.3 105.5:0.4 104.5:0.5 103.5:0.6 102.5:0.7 101.5:0.8 100.5:0.9 99.5:0 98.5:0.1 97.5:0.2 96.5:0.3 95.5:0.4 94.5:0.5 93.5:0.6 92.5:0.7 91.5:0.8 90.5:0.9 89.5:0 88.5:0.1 87.5:0.2 86.5:0.3 85.5:0.4 84.5:0.5 83.5:0.6 82.5:0.7 81.5:0.8 80.5:0.9 79.5:0 78.5:0.1 77.5:0.2 76.5:0.3 75.5:0.4 74.5:0.5 73.5:0.6 72.5:0.7 71.5:0.8 70.5:0.9 69.5:0 68.5:0.1 67.5:0.2 66.5:0.3 65.5:0.4 64.5:0.5 63.5:0.6 62.5:0.7 61.5:0.8 60.5:0.9 59.5:0 58.5:0.1 57.5:0.2 56.5:0.3 55.5:0.4 54.5:0.5 53.5:0.6 52.5:0.7 51.5:0.8 50.5:0.9 49.5:0 48.5:0.1 47.5:0.2 46.5:0.3 45.5:0.4 44.5:0.5 43.5:0.6 42.5:0.7 41.5:0.8 40.5:0.9 39.5:0 38.5:0.1 37.5:0.2 36.5:0.3 35.5:0.4 34.5:0.5 33.5:0.6 32.5:0.7 31.5:0.8 30.5:0.9 29.5:0 28.5:0.1 27.5:0.2 26.5:0.3 25.5:0.4 24.5:0.5 23.5:0.6 22.5:0.7 21.5:0.8 20.5:0.9 19.5:0 18.5:0.1 17.5:0.2 16.5:0.3 15.5:0.4 14.5:0.5 13.5:0.6 12.5:0.7 11.5:0.8 10.5:0.9 9.5:0 8.5:0.1 7.5:0.2 6.5:0.3 5.5:0.4 4.5:0.5 3.5:0.6 2.5:0.7 1.5:0.8 0.5:0.9",
    sheds: "",
    sent: "119.5 116.5 113.5 110.5 107.5 104.5 101.5 98.5 95.5 92.5 89.5 86.5 83.5 80.5 77.5 74.5 71.5 68.5 65.5 62.5 59.5 56.5 53.5 50.5 47.5 44.5 41.5 38.5 35.5 32.5 29.5 26.5 23.5 20.5 17.5 14.5 11.5 8.5 5.5 2.5",
    labels: ["shed 2.0 s", "admit 1.0 s", "▮ caption sent"],
    axis: ["−2 min", "−1 min", "now"],
  },
  oneMic: {
    head: ["SPEAKERS, LAST TWO MINUTES", "captioned", "held", "paused", "lost"],
    empty: "Nobody spoke in the last two minutes · 1 mic open",
    rows: [],
    lag: "",
    sheds: "",
    sent: "",
    labels: ["shed 2.0 s", "admit 1.0 s", "▮ caption sent"],
    axis: ["−2 min", "−1 min", "now"],
  },
  noMics: {
    head: ["SPEAKERS, LAST TWO MINUTES", "captioned", "held", "paused", "lost"],
    empty: "No microphones open",
    rows: [],
    lag: "",
    sheds: "",
    sent: "",
    labels: ["shed 2.0 s", "admit 1.0 s", "▮ caption sent"],
    axis: ["−2 min", "−1 min", "now"],
  },
  tuned: {
    head: ["SPEAKERS, LAST TWO MINUTES", "captioned", "held", "paused", "lost"],
    empty: null,
    rows: [
      ["Ana Ferreira", "scribe 1", "0.3 s", "captioned 60-55 captioned 40-31 captioned 16-7 captioned 3-0"],
    ],
    lag: "59.5:0.24 58.5:0.2 57.5:0.17 56.5:0.15 55.5:0.15 54.5:0.16 53.5:0.18 52.5:0.21 51.5:0.25 50.5:0.29 49.5:0.34 48.5:0.39 47.5:0.44 46.5:0.48 45.5:0.51 44.5:0.54 43.5:0.55 42.5:0.55 41.5:0.53 40.5:0.6 39.5:0.86 38.5:1.12 37.5:1.38 36.5:1.64 35.5:1.9 34.5:2.16 33.5:2.42 32.5:2.5 31.5:2.5 30.5:0.15 29.5:0.16 28.5:0.17 27.5:0.2 26.5:0.24 25.5:0.29 24.5:0.34 23.5:0.39 22.5:0.43 21.5:0.48 19.5:0.53 18.5:0.55 17.5:0.55 16.5:0.54 15.5:0.51 14.5:0.48 13.5:0.44 12.5:0.39 11.5:0.34 10.5:0.29 9.5:0.25 8.5:0.21 7.5:0.18 6.5:0.16 5.5:0.15 4.5:0.15 3.5:0.17 2.5:0.2 1.5:0.24 0.5:0.28",
    sheds: "32.5",
    sent: "59.5 58.5 56.5 53.5 51.5 50.5 47.5 44.5 41.5 38.5 37.5 35.5 32.5 30.5 29.5 26.5 23.5 20.5 17.5 16.5 14.5 11.5 9.5 8.5 5.5 2.5",
    labels: ["shed 3.0 s", "admit 0.8 s", "▮ caption sent"],
    axis: ["−2 min", "−1 min", "now"],
  },
}

const n = (value) => String(Math.round(value * 1000) / 1000)
const runs = (segments) => segments.map((s) => `${s.state} ${n(s.start)}-${n(s.end)}`).join(' ')
// The page's text where the video cut it short: compared up to the cut.
const asVideo = (text, cut) => (typeof cut === 'string' && cut.endsWith('…') && text.startsWith(cut.slice(0, -1)) ? cut : text)

function pageView({ sample: data, columns }, video) {
  const rows = laneRows(data)
  const missing = rows.length ? leftOut(data) : ''
  const graph = lagGraph(columns, data, 120, NOW)
  return {
    head: [TITLE.toUpperCase(), ...LEGEND, ...(missing ? [asVideo(missing, video.head[5])] : [])],
    empty: rows.length ? null : emptyLanesText(data),
    rows: rows.map((row, i) => [
      asVideo(row.label.text, video.rows[i] && video.rows[i][0]),
      row.tag.name,
      row.tag.value,
      runs(laneSegments(row.id, columns, 120, NOW)),
    ]),
    lag: graph.lines.flat().map((p) => `${n(p.x)}:${n(p.lag)}`).join(' '),
    sheds: graph.sheds.map(n).join(' '),
    sent: graph.sent.map(n).join(' '),
    labels: [graph.labels.shed, graph.labels.admit, SENT],
    axis: AXIS,
  }
}

for (const [name, fixture] of Object.entries(LANE_CASES)) {
  test(`the lanes and lag graph say what the video says: ${name}`, () => {
    assert.deepEqual(pageView(fixture, VIDEO[name]), VIDEO[name])
  })
}

test('the fixtures exercise every lane state, a cut name, a shed and a clamped lag', () => {
  const { sample: data, columns } = LANE_CASES.tenLanes
  const states = new Set(laneRows(data).flatMap((row) => laneSegments(row.id, columns, 120, NOW).map((s) => s.state)))
  assert.deepEqual([...states].sort(), ['captioned', 'held', 'lost', 'paused'])
  assert.ok(VIDEO.tenLanes.rows.some((row) => row[0].endsWith('…')))
  assert.ok(VIDEO.tenLanes.sheds.length)
  assert.ok(VIDEO.tenLanes.lag.includes(':2.5 '))
})

/* ---------- runs ---------- */

const column = (t, lanes, extra = {}) => ({ t, lanes, finals: 0, drops: 0, lag: null, shed: false, ...extra })

test('contiguous columns make one run per state, in seconds before now', () => {
  const columns = [
    column(98, { 1: 'speech' }),
    column(99, { 1: 'speech' }),
    column(100, { 1: 'waiting' }),
    column(101, {}),
    column(102, { 1: 'paused' }),
    column(103, { 1: 'lost' }),
    column(104, { 1: 'lost' }),
  ]
  assert.deepEqual(laneSegments(1, columns, 120, 104), [
    { state: 'captioned', start: 7, end: 5 },
    { state: 'held', start: 5, end: 4 },
    { state: 'paused', start: 3, end: 2 },
    { state: 'lost', start: 2, end: 0 },
  ])
  // The answer's now runs ahead of the newest column: everything sits that much further back.
  assert.deepEqual(laneSegments(1, columns.slice(-2), 120, 104.5), [{ state: 'lost', start: 2.5, end: 0.5 }])
  assert.deepEqual(laneSegments(2, columns, 120, 104), [])
  assert.deepEqual(laneSegments(1, [], 120, 104), [])
  // Without a now, the newest column is now.
  assert.deepEqual(laneSegments(1, columns), laneSegments(1, columns, 120, 104))
  assert.deepEqual(lagGraph(columns.map((c) => ({ ...c, finals: 1 })), sample()).sent, [6.5, 5.5, 4.5, 3.5, 2.5, 1.5, 0.5])
})

test('a gap of more than 2.5 s splits a run; a shorter one is drawn through', () => {
  const at = (...times) => times.map((t) => column(t, { 7: 'speech' }))
  assert.equal(GAP_S, 2.5)
  assert.deepEqual(laneSegments(7, at(90, 91, 95, 96), 120, 96), [
    { state: 'captioned', start: 7, end: 5 },
    { state: 'captioned', start: 2, end: 0 },
  ])
  assert.deepEqual(laneSegments(7, at(90, 91, 93.5, 94.5), 120, 94.5), [{ state: 'captioned', start: 5.5, end: 0 }])
  // A gap with no column for the lane at all still splits: the lane's columns are not the clock.
  const other = [column(90, { 7: 'speech' }), column(94, { 8: 'speech' }), column(95, { 7: 'speech' })]
  assert.equal(laneSegments(7, other, 120, 95).length, 2)
})

test('lane ids match the columns by string, as JSON keys arrive', () => {
  const parsed = JSON.parse(JSON.stringify([column(10, { 3: 'speech' }), column(11, { 3: 'speech' })]))
  assert.deepEqual(Object.keys(parsed[0].lanes), ['3'])
  assert.deepEqual(laneSegments(3, parsed, 120, 11), [{ state: 'captioned', start: 2, end: 0 }])
  assert.deepEqual(laneSegments('3', parsed, 120, 11), laneSegments(3, parsed, 120, 11))
  assert.deepEqual(laneSegments(3, [column(11, new Map([[3, 'speech']]))], 120, 11), [])
})

test('runs are clipped to the window; an unknown state draws as captioned, like the video', () => {
  const columns = [column(4880, { 1: 'speech' }), column(4881, { 1: 'speech' }), column(4882, { 1: 'murmur' })]
  assert.deepEqual(laneSegments(1, columns, 120, NOW), [
    { state: 'captioned', start: 120, end: 119 },
    { state: 'captioned', start: 119, end: 118 },
  ])
  assert.deepEqual(laneSegments(1, columns.slice(0, 1), 120, NOW), [])
})

/* ---------- labels and tags ---------- */

test('labels: the name, "Participant" without one, "other call" for another room', () => {
  assert.deepEqual(laneLabel({ id: 1, name: 'Ana' }), { text: 'Ana', other: false })
  assert.deepEqual(laneLabel({ id: 1 }), { text: 'Participant', other: false })
  assert.deepEqual(laneLabel({ id: 1, name: '' }), { text: 'Participant', other: false })
  assert.deepEqual(laneLabel({ id: 1, name: 'Participant' }), { text: 'Participant', other: false })
  assert.deepEqual(laneLabel({ id: 9, other: true }), { text: 'other call', other: true })
  // Hostile markup is data: passed through for textContent.
  assert.equal(laneLabel({ id: 1, name: '<img src=x onerror=alert(1)>' }).text, '<img src=x onerror=alert(1)>')
})

test('names over 40 characters are cut to 40 with an ellipsis, by code point', () => {
  const forty = 'x'.repeat(40)
  assert.equal(laneLabel({ name: forty }).text, forty)
  assert.equal(laneLabel({ name: forty + 'yz' }).text, forty + '…')
  const emoji = '\u{1F600}'.repeat(41)
  assert.equal(laneLabel({ name: emoji }).text, '\u{1F600}'.repeat(40) + '…')
  assert.equal(Array.from(laneLabel({ name: '\u{1F600}'.repeat(40) }).text).length, 40)
  const rtl = 'محمد عبد الرحمن'
  assert.equal(laneLabel({ name: rtl }).text, rtl)
})

test('tags: paused, else the scribe or slot held, else held waiting, else idle', () => {
  const lane = { id: 1, scribe: 2, waiting: true, paused: true, lag: 2.35 }
  assert.deepEqual(laneTag(lane), { kind: 'paused', name: 'paused', value: '2.4 s' })
  assert.deepEqual(laneTag({ ...lane, paused: false }), { kind: 'slot', name: 'scribe 2', value: '2.4 s' })
  assert.deepEqual(laneTag({ ...lane, paused: false }, 'nemotron-en'), { kind: 'slot', name: 'slot 2', value: '2.4 s' })
  assert.deepEqual(laneTag({ ...lane, paused: false }, 'nemotron-multilingual').name, 'slot 2')
  assert.deepEqual(laneTag({ ...lane, paused: false, scribe: 0 }), { kind: 'held', name: 'held', value: '2.4 s' })
  assert.deepEqual(laneTag({ id: 1, scribe: 0, waiting: false, paused: false, lag: 0.5 }), { kind: 'idle', name: 'idle', value: '' })
  // A lag the sampler could not measure shows no value.
  assert.deepEqual(laneTag({ ...lane, paused: false, lag: null }).value, '')
  assert.equal(laneTag({ id: 1, scribe: 1, lag: 1.125 }).value, '1.1 s')
})

test('rows: at most ten, in the sample order, worded for its engine', () => {
  const eleven = [...TEN_LANES, { id: 40, active: true, speech: 1, lost: 0, scribe: 0, waiting: false, paused: false, lag: null, name: 'Late' }]
  const rows = laneRows(sample({ lanes: eleven }))
  assert.equal(rows.length, 10)
  assert.deepEqual(rows.map((row) => row.id), TEN_LANES.map((lane) => lane.id))
  assert.equal(laneRows(sample({ lanes: TEN_LANES, engine: 'nemotron-en' }))[0].tag.name, 'slot 1')
  const { engine, ...unnamed } = sample({ lanes: TEN_LANES })
  assert.equal(engine, 'whistle')
  assert.equal(laneRows(unnamed)[0].tag.name, 'scribe 1')
  assert.deepEqual(laneRows(null), [])
  assert.deepEqual(laneRows({}), [])
})

test('who has no lane: silent mics and speakers past ten, singular and plural', () => {
  assert.equal(leftOut(sample({ silentMics: 1, hiddenSpeakers: 1 })), '+1 silent mic · +1 more speaker')
  assert.equal(leftOut(sample({ silentMics: 13, hiddenSpeakers: 2 })), '+13 silent mics · +2 more speakers')
  assert.equal(leftOut(sample({ silentMics: 0, hiddenSpeakers: 3 })), '+3 more speakers')
  assert.equal(leftOut(sample({ silentMics: 2, hiddenSpeakers: 0 })), '+2 silent mics')
  assert.equal(leftOut(sample({ silentMics: 0, hiddenSpeakers: 0 })), '')
  assert.equal(leftOut(sample({ silentMics: null, hiddenSpeakers: undefined })), '')
  assert.equal(leftOut(null), '')
})

test('an empty band counts the open microphones, or says there are none', () => {
  assert.equal(emptyLanesText(sample({ lanes: [], silentMics: 3, hiddenSpeakers: 1 })), 'Nobody spoke in the last two minutes · 4 mics open')
  assert.equal(emptyLanesText(sample({ lanes: [], silentMics: 1, hiddenSpeakers: 0 })), 'Nobody spoke in the last two minutes · 1 mic open')
  assert.equal(emptyLanesText(sample({ lanes: [], silentMics: 0, hiddenSpeakers: 0 })), 'No microphones open')
  assert.equal(emptyLanesText(null), 'No microphones open')
})

/* ---------- lag graph ---------- */

test('lag is clamped to 0–2.5 s; a column without a reading is skipped and joined', () => {
  const columns = [
    column(1, {}, { lag: -0.5 }),
    column(2, {}, { lag: 0.75 }),
    column(3, {}, { lag: null }),
    column(4, {}, { lag: 3.9 }),
    column(5, {}, { lag: 'n/a' }),
  ]
  const graph = lagGraph(columns, sample(), 120, 5)
  assert.deepEqual(graph.lines, [[{ x: 4.5, lag: 0 }, { x: 3.5, lag: 0.75 }, { x: 1.5, lag: 2.5 }]])
})

test('the lag line breaks where the columns do, and a lone reading draws nothing', () => {
  const columns = [column(1, {}, { lag: 0.2 }), column(2, {}, { lag: 0.3 }), column(6, {}, { lag: 0.4 }), column(7, {}, { lag: 0.5 }), column(11, {}, { lag: 0.6 })]
  const graph = lagGraph(columns, sample(), 120, 11)
  assert.deepEqual(graph.lines.map((line) => line.map((p) => p.x)), [[10.5, 9.5], [5.5, 4.5]])
  assert.deepEqual(lagGraph(columns.slice(0, 1), sample(), 120, 1).lines, [])
})

test('shed marks and caption ticks sit at their columns; nothing outside the window', () => {
  const columns = [
    column(4879, {}, { shed: true, finals: 3, lag: 1 }),
    column(4990, {}, { shed: true, finals: 0 }),
    column(4991, {}, { finals: 2 }),
    column(5000, {}, { finals: 1 }),
  ]
  const graph = lagGraph(columns, sample(), 120, NOW)
  assert.deepEqual(graph.sheds, [10.5])
  assert.deepEqual(graph.sent, [9.5, 0.5])
  assert.deepEqual(graph.lines, [])
})

test('thresholds default to admit 1.0 s and shed 2.0 s, as the video draws them', () => {
  for (const admission of [{}, null, governor({ lagAdmit: null, lagShed: 0 })]) {
    const graph = lagGraph([], sample({ admission }), 120, NOW)
    assert.deepEqual([graph.admit, graph.shed, graph.labels], [1, 2, { shed: 'shed 2.0 s', admit: 'admit 1.0 s' }])
  }
  const tuned = lagGraph([], sample({ admission: governor({ lagAdmit: 0.75, lagShed: 3 }) }), 120, NOW)
  assert.deepEqual([tuned.admit, tuned.shed, tuned.labels.admit, tuned.labels.shed], [0.75, 3, 'admit 0.8 s', 'shed 3.0 s'])
})

/* ---------- accessible summaries ---------- */

test('the band and the graph each say what they show in one sentence', () => {
  assert.equal(lanesSummary(LANE_CASES.tenLanes.sample), '10 speakers in the last two minutes; 5 captioned now; 1 held; 1 paused')
  assert.equal(lanesSummary(sample({ lanes: TEN_LANES.slice(1, 2) })), '1 speaker in the last two minutes; 1 captioned now; 0 held; 0 paused')
  assert.equal(lanesSummary(LANE_CASES.quiet.sample), 'Nobody spoke in the last two minutes · 3 mics open')
  const lag = (lagSeconds) => lagSummary(sample({ admission: governor({ lagSeconds }) }))
  assert.equal(lag(0.4), 'Caption lag over the last two minutes; now 0.4 s, under admit 1.0 s and shed 2.0 s')
  assert.equal(lag(1.0), 'Caption lag over the last two minutes; now 1.0 s, at or over admit 1.0 s, under shed 2.0 s')
  assert.equal(lag(2.6), 'Caption lag over the last two minutes; now 2.6 s, at or over shed 2.0 s')
  assert.equal(lag(null), 'Caption lag over the last two minutes; no reading now; admit 1.0 s, shed 2.0 s')
  assert.equal(lagSummary(null), 'Caption lag over the last two minutes; no reading now; admit 1.0 s, shed 2.0 s')
})

test('the scripted columns round-trip through JSON with string lane keys', () => {
  const columns = laneColumns(TEN_LANES, 3)
  assert.deepEqual(columns.map((c) => c.t), [NOW - 2, NOW - 1, NOW])
  assert.ok(Object.keys(columns[2].lanes).every((key) => typeof key === 'string'))
})
