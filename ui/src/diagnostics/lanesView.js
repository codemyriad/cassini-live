/**
 * The speaker lanes and the lag graph, drawn into #lanes from lanes.js's models.
 *
 * Both SVGs map seconds to x through their viewBox, stretched to the column's width
 * (preserveAspectRatio none) at one unit per pixel vertically; strokes do not scale
 * (app.css). Names and tags are HTML over and beside the band, so they stay selectable
 * and undistorted. Every node is reused from one answer to the next: the band holds at
 * most ten lanes of runs, and nothing here listens for events.
 */

import { $, el, ensureChildren, setAttrs, setText, setVar, show, svgEl } from './dom.js'
import {
  clampLag,
  emptyLanesText,
  lagGraph,
  lagSummary,
  laneRows,
  laneSegments,
  lanesSummary,
  LEGEND,
  leftOut,
  WINDOW_S,
} from './lanes.js'

// Up to four lanes keep the video's 30 px lanes and 8 px gaps. More lanes close up, but
// not to the video's 12 px: the page can grow, and names and tags stay legible.
const ROOMY = { lane: 30, gap: 8 }
const DENSE = { lane: 22, gap: 4 }
const ROOMY_LANES = 4
// The band keeps the height of four lanes, so the graph under it does not move.
const MIN_BAND = ROOMY_LANES * ROOMY.lane + (ROOMY_LANES - 1) * ROOMY.gap
// diagnostic_video's graph: 58 px high, 0 s at 50 px from the top, 18 px per second, the
// caption ticks along the bottom from 52 px.
const GRAPH_H = 58
const ZERO_Y = 50
const PER_S = 18
const TICK_Y = 52
// Outlined states (stroke widths in px): inset by half a stroke to stay inside the lane.
const STROKES = { held: 3, paused: 2 }
// A run stops this short of the next column, the video's 1 px of each 6 px column; a
// caption tick is the other 5 px, so ticks narrow with the band rather than merging.
const RUN_GAP_S = 1 / 6
const TICK_S = 5 / 6
const MIN_RUN_S = 0.05

const round = (value) => Math.round(value * 1000) / 1000

/** `windowSeconds` is the feed's window, `now` its server time (lanes.js's coordinates). */
export function renderLanes(sample, columns, windowSeconds, now) {
  const span = Number.isInteger(windowSeconds) && windowSeconds > 0 ? windowSeconds : WINDOW_S
  const rows = laneRows(sample)
  const { lane, gap } = rows.length > ROOMY_LANES ? DENSE : ROOMY
  const root = $('lanes')
  const density = rows.length > ROOMY_LANES ? 'dense' : 'roomy'
  if (root.dataset.density !== density) root.dataset.density = density
  setVar(root, '--lane-h', `${lane}px`)
  setVar(root, '--lane-gap', `${gap}px`)

  renderLegend()
  // The video names who has no lane only beside lanes; with none, the band counts them.
  const missing = rows.length ? leftOut(sample) : ''
  setText($('left-out'), missing)
  show($('left-out'), Boolean(missing))
  // The band's label reads this sentence to screen readers (the visible copy is
  // aria-hidden), and no empty "Speakers now" list follows it.
  const empty = rows.length ? '' : emptyLanesText(sample)
  setText($('lanes-empty'), empty)
  show($('lanes-empty'), Boolean(empty))
  show($('lane-tags'), rows.length > 0)

  const band = $('band')
  const height = Math.max(MIN_BAND, rows.length * (lane + gap) - gap)
  setAttrs(band, { viewBox: `0 0 ${span} ${height}`, height, 'aria-label': lanesSummary(sample) })
  const groups = ensureChildren(band, rows.length, () => svgEl('g'))
  rows.forEach((row, i) => renderLane(groups[i], laneSegments(row.id, columns, span, now), i * (lane + gap), lane, span))
  renderNames(rows)
  renderTags(rows)
  renderGraph(lagGraph(columns, sample, span, now), span, lagSummary(sample))
}

function renderLegend() {
  const items = ensureChildren($('legend'), LEGEND.length, () => {
    const item = el('li')
    item.append(el('span', 'swatch'), el('span'))
    return item
  })
  LEGEND.forEach((state, i) => {
    const [swatch, label] = items[i].children
    setAttrs(swatch, { class: `swatch ${state}`, 'aria-hidden': 'true' })
    setText(label, state)
  })
}

// A lane: its background, then one rect per run of a state.
function renderLane(group, segments, y, height, span) {
  const rects = ensureChildren(group, segments.length + 1, () => svgEl('rect'))
  setAttrs(rects[0], { class: 'lane-bg', x: 0, y, width: span, height })
  segments.forEach((segment, k) => {
    const stroke = STROKES[segment.state] || 0
    setAttrs(rects[k + 1], {
      class: `seg ${segment.state}`,
      x: round(span - segment.start),
      y: y + stroke / 2,
      width: round(Math.max(MIN_RUN_S, segment.start - segment.end - RUN_GAP_S)),
      height: height - stroke,
      'stroke-width': stroke,
    })
  })
}

// Names over each lane's oldest end; the tags carry them for screen readers.
function renderNames(rows) {
  const items = ensureChildren($('lane-names'), rows.length, () => {
    const item = el('li')
    const name = el('span', 'lane-name')
    name.dir = 'auto'
    item.append(name)
    return item
  })
  rows.forEach((row, i) => {
    const name = items[i].firstElementChild
    setText(name, row.label.text)
    setAttrs(name, { title: row.label.text })
    name.classList.toggle('other', row.label.other)
  })
}

function renderTags(rows) {
  const items = ensureChildren($('lane-tags'), rows.length, () => {
    const item = el('li', 'lane-tag')
    const who = el('span', 'visually-hidden')
    who.dir = 'auto'
    item.append(who, el('span', 'tag-name'), el('span', 'tag-value'))
    return item
  })
  rows.forEach((row, i) => {
    const [who, name, value] = items[i].children
    if (items[i].dataset.kind !== row.tag.kind) items[i].dataset.kind = row.tag.kind
    setText(who, `${row.label.text}: `)
    setText(name, row.tag.name)
    setText(value, row.tag.value)
  })
}

const lagY = (lag) => round(ZERO_Y - clampLag(lag) * PER_S)

function renderGraph(graph, span, summary) {
  setAttrs($('graph'), { viewBox: `0 0 ${span} ${GRAPH_H}`, height: GRAPH_H, 'aria-label': summary })
  setAttrs($('graph-bg'), { x: 0, y: 0, width: span, height: GRAPH_H })
  for (const [id, value] of [['graph-admit', graph.admit], ['graph-shed', graph.shed]]) {
    setAttrs($(id), { x1: 0, x2: span, y1: lagY(value), y2: lagY(value) })
  }
  const at = (x) => round(span - x)
  ensureChildren($('graph-sheds'), graph.sheds.length, () => svgEl('line')).forEach((mark, i) => {
    setAttrs(mark, { x1: at(graph.sheds[i]), x2: at(graph.sheds[i]), y1: 0, y2: GRAPH_H })
  })
  ensureChildren($('graph-sent'), graph.sent.length, () => svgEl('rect')).forEach((tick, i) => {
    setAttrs(tick, { x: round(at(graph.sent[i]) - TICK_S / 2), y: TICK_Y, width: round(TICK_S), height: GRAPH_H - TICK_Y })
  })
  ensureChildren($('graph-lines'), graph.lines.length, () => svgEl('polyline')).forEach((line, i) => {
    setAttrs(line, { points: graph.lines[i].map((point) => `${at(point.x)},${lagY(point.lag)}`).join(' ') })
  })
  setText($('shed-label'), graph.labels.shed)
  setText($('admit-label'), graph.labels.admit)
}
