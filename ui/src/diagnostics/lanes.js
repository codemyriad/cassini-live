/**
 * The speaker lanes and the lag graph under them, from the feed's latest sample and its
 * history columns. Pure: no DOM. Rules, wording and scales are the diagnostic video's
 * (diagnostic_video._render_lanes), so the page and the video say the same things about
 * the same data.
 *
 * The video draws its ring of samples one 6 px column each, newest against the "now"
 * line. The page draws the feed's own history (feed.feedHistory: lane ids are only
 * meaningful within one Cassini Live process), which can still have gaps the video's
 * never has (a sample that failed or ran late), so the page places columns by their
 * server time `t`, in seconds before the answer's `now`: 0 is now, the window (two
 * minutes) the oldest edge. Each column covers the second before its `t`. On contiguous
 * columns the two agree exactly.
 */

import { fixed, get, int, known, mics, plural, seconds, thresholds } from './format.js'

// diagnostics.MAX_LANES and diagnostic_video.NAME_MAX.
export const MAX_LANES = 10
export const NAME_MAX = 40
// Two minutes of one-second columns (diagnostic_history.COLUMNS).
export const WINDOW_S = 120
export const COLUMN_S = 1
// Columns are a second apart; a longer silence between two breaks a run or the lag line
// rather than drawing it across time nobody sampled.
export const GAP_S = 2.5
// The lag graph's ceiling (diagnostic_video gy()): higher lag is drawn at 2.5 s.
export const LAG_MAX = 2.5

export const UNNAMED = 'Participant'
export const OTHER_CALL = 'other call'
// The video's legend, in its order; each is also the state's class on the page.
export const LEGEND = ['captioned', 'held', 'paused', 'lost']
// diagnostic_history.history_column's lane states. The video draws any other state as
// captioned speech.
const STATES = { speech: 'captioned', waiting: 'held', paused: 'paused', lost: 'lost' }

const newest = (columns) => (columns.length ? columns[columns.length - 1].t : 0)

/** The name over a lane's oldest end: {text, other} (diagnostic_video._lane_label). */
export function laneLabel(lane) {
  if (lane.other) return { text: OTHER_CALL, other: true }
  // Code points, as Python slices; the feed already cut names to 40 (diagnostics.clip_name).
  const chars = Array.from(String(lane.name || UNNAMED))
  return { text: chars.length > NAME_MAX ? `${chars.slice(0, NAME_MAX).join('')}…` : chars.join(''), other: false }
}

/**
 * The tag beside a lane, {kind, name, value}: paused, else the scribe (Whistle) or slot
 * (other engines) it holds, else held waiting for one, else idle; with that speaker's lag.
 */
export function laneTag(lane, engine = 'whistle') {
  const lag = known(lane.lag) ? seconds(lane.lag) : ''
  if (lane.paused) return { kind: 'paused', name: 'paused', value: lag }
  if (lane.scribe) return { kind: 'slot', name: `${engine === 'whistle' ? 'scribe' : 'slot'} ${lane.scribe}`, value: lag }
  if (lane.waiting) return { kind: 'held', name: 'held', value: lag }
  return { kind: 'idle', name: 'idle', value: '' }
}

/** One row per lane, at most ten, in the sample's order (the sampler keeps it stable). */
export function laneRows(sample) {
  const lanes = sample && Array.isArray(sample.lanes) ? sample.lanes : []
  const engine = get(sample, 'engine', 'whistle')
  return lanes.slice(0, MAX_LANES).map((lane) => ({ id: lane.id, label: laneLabel(lane), tag: laneTag(lane, engine) }))
}

/** '+N silent mics · +M more speakers': open tracks and speakers without a lane (_left_out). */
export function leftOut(sample) {
  const silent = int(get(sample, 'silentMics', 0))
  const hidden = int(get(sample, 'hiddenSpeakers', 0))
  const parts = []
  if (silent) parts.push(`+${silent} silent ${plural(silent, 'mic', 'mics')}`)
  if (hidden) parts.push(`+${hidden} more ${plural(hidden, 'speaker', 'speakers')}`)
  return parts.join(' · ')
}

/** What the band says when it has no lanes. */
export function emptyLanesText(sample) {
  const open = mics(sample)
  return open ? `Nobody spoke in the last two minutes · ${open} ${plural(open, 'mic', 'mics')} open` : 'No microphones open'
}

/**
 * One lane's runs of a state, oldest first: {state, start, end} in seconds before `now`,
 * `start` the older edge, clipped to the window; `now` defaults to the newest column.
 * `columns` are in time order (as feed.mergeColumns keeps them); their lane keys are
 * strings after JSON.
 */
export function laneSegments(laneId, columns, span = WINDOW_S, now = newest(columns)) {
  const key = String(laneId)
  const runs = []
  let run = null
  let last = null
  for (const column of columns) {
    const t = column.t
    const state = (column.lanes && column.lanes[key]) || null
    const contiguous = last !== null && t - last <= GAP_S
    last = t
    if (run && contiguous && state === run.raw) {
      run.end = now - t
      continue
    }
    if (run) runs.push(run)
    run = state ? { raw: state, start: now - t + COLUMN_S, end: now - t } : null
  }
  if (run) runs.push(run)
  return runs
    .map(({ raw, start, end }) => ({ state: STATES[raw] || 'captioned', start: Math.min(span, start), end: Math.max(0, end) }))
    .filter((segment) => segment.start > segment.end)
}

/** Lag clamped to the graph's 0–2.5 s scale. */
export function clampLag(value) {
  return Math.min(LAG_MAX, Math.max(0, Number(value)))
}

/**
 * The lag graph over the window: the control lag as polylines of {x, lag} (x in seconds
 * before `now` at each column's middle, lag clamped), broken where columns are more than
 * GAP_S apart and joined across columns without a reading, as the video joins them;
 * a mark where a speaker was paused, a tick where a caption line was sent; and the admit
 * and shed thresholds with their labels.
 */
export function lagGraph(columns, sample, span = WINDOW_S, now = newest(columns)) {
  const admission = (sample && sample.admission) || {}
  const { lagAdmit, lagShed } = thresholds(admission)
  const lines = []
  const sheds = []
  const sent = []
  let line = []
  let last = null
  for (const column of columns) {
    const t = column.t
    if (last !== null && t - last > GAP_S && line.length) {
      lines.push(line)
      line = []
    }
    last = t
    const x = now - t + COLUMN_S / 2
    if (x > span || x < 0) continue
    if (column.shed) sheds.push(x)
    if (Number(column.finals) > 0) sent.push(x)
    if (known(column.lag) && Number.isFinite(Number(column.lag))) line.push({ x, lag: clampLag(column.lag) })
  }
  if (line.length) lines.push(line)
  return {
    // A single reading draws no line (the video needs two points too).
    lines: lines.filter((points) => points.length > 1),
    sheds,
    sent,
    admit: lagAdmit,
    shed: lagShed,
    labels: { shed: `shed ${fixed(lagShed, 1)} s`, admit: `admit ${fixed(lagAdmit, 1)} s` },
  }
}

/** The lane band in one sentence, for its accessible name. */
export function lanesSummary(sample) {
  const rows = laneRows(sample)
  if (!rows.length) return emptyLanesText(sample)
  const count = (kind) => rows.filter((row) => row.tag.kind === kind).length
  return `${rows.length} ${plural(rows.length, 'speaker', 'speakers')} in the last two minutes; ` +
    `${count('slot')} captioned now; ${count('held')} held; ${count('paused')} paused`
}

/** The lag graph in one sentence: the lag now against the admit and shed lines. */
export function lagSummary(sample) {
  const admission = (sample && sample.admission) || {}
  const { lagAdmit, lagShed } = thresholds(admission)
  const admit = `admit ${fixed(lagAdmit, 1)} s`
  const shed = `shed ${fixed(lagShed, 1)} s`
  const head = 'Caption lag over the last two minutes'
  if (!known(admission.lagSeconds)) return `${head}; no reading now; ${admit}, ${shed}`
  const lag = Number(admission.lagSeconds)
  const where = lag >= lagShed
    ? `at or over ${shed}`
    : lag >= lagAdmit ? `at or over ${admit}, under ${shed}` : `under ${admit} and ${shed}`
  return `${head}; now ${seconds(lag)}, ${where}`
}
