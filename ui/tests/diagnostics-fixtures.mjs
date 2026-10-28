/**
 * Diagnostics feed samples and history columns for the page's unit and Playwright tests,
 * shaped like DiagnosticSampler.sample() and diagnostic_history.history_column() output
 * after a JSON round trip (column lane keys are strings).
 */

// Server monotonic seconds of the newest column.
export const NOW = 5000

export function governor(overrides = {}) {
  return {
    open: true,
    reason: null,
    seats: 2,
    ceiling: 4,
    speaking: 2,
    paused: 0,
    lagSeconds: 0.4,
    lagSustainedSeconds: 0.3,
    captionAgeSeconds: 0.2,
    cpuStall: 0.06,
    cpuPressureAvg10: 1.5,
    memoryHeadroomMiB: 900,
    memoryUsedMiB: 1100,
    memoryLimitMiB: 3072,
    memoryStatAvailable: true,
    pressureAvailable: true,
    engineDuty: 0.42,
    lastRefusal: null,
    lastShed: null,
    shedEvents: 0,
    measuring: false,
    tickMs: 0.4,
    observeOnly: false,
    lagAdmit: 1.0,
    lagShed: 2.0,
    cpuStallAdmit: 0.2,
    holdOff: 10.0,
    ...overrides,
  }
}

export function sample(overrides = {}) {
  return {
    engine: 'whistle',
    state: 'ready',
    rooms: 1,
    tracks: 3,
    maxTracks: 24,
    busy: 2,
    capacity: 4,
    spare: 2,
    vadActive: 2,
    vadPeak: 0.91,
    vadChunks: 340,
    queuedChunks: 1,
    sttChunks: 330,
    sttAudioSec: 310.2,
    leasedSec: 305.5,
    yields: 0,
    endSilence: 12,
    endDuration: 1,
    endSched: 0,
    endFlush: 0,
    targets: 3,
    previewing: 1,
    finals: 40,
    partials: 400,
    hostCpu: 31.5,
    hostAvailableGiB: 9.8,
    hostGiB: 15.5,
    captionCpu: 120.4,
    quota: 2.0,
    captionMiB: 1100,
    limitMiB: 3072,
    drops: 0,
    waiting: 0,
    lanes: [
      { id: 1, active: true, speech: 120, lost: 0, scribe: 1, waiting: false, paused: false, lag: 0.3, name: 'Ana Ferreira' },
      { id: 2, active: false, speech: 80, lost: 0, scribe: 2, waiting: false, paused: false, lag: 0.0, name: 'Bo' },
      { id: 3, active: false, speech: 10, lost: 0, scribe: 0, waiting: false, paused: false, lag: null, other: true },
    ],
    silentMics: 1,
    hiddenSpeakers: 0,
    admission: governor(),
    decision: { at: 1793212496.5, verb: 'admit', who: 'Ana Ferreira', text: 'seat 2 of 4 · lag 0.4 s < 1.0 · stall 6%' },
    p95: 0.84,
    threads: 2,
    segmentSeconds: 15,
    cpuWeight: 10,
    oomKills: 0,
    engineRestarts: 0,
    samplerMs: 2.31,
    ...overrides,
  }
}

/** `count` one-second columns ending at `now`; `drops`/`finals` map a column index to a count. */
export function columns(count, { now = NOW, drops = () => 0, finals = (i) => (i % 3 === 0 ? 1 : 0), shed = () => false } = {}) {
  return Array.from({ length: count }, (_, i) => {
    const t = Math.round((now - (count - 1 - i)) * 1000) / 1000
    return {
      t,
      lanes: { 1: 'speech', 2: i % 5 === 0 ? 'waiting' : 'speech' },
      finals: finals(i),
      drops: drops(i),
      lag: 0.1 * (i % 10),
      shed: shed(i),
    }
  })
}

/**
 * Ten lanes of a busy call, as the sampler orders them: the owning room's speakers first
 * (one paused, one held for a seat, one that lost audio half a minute ago, a 40-character
 * name, a guest, an unknown name), then a speaker in another captioned call, unnamed.
 */
export const TEN_LANES = [
  { id: 1, active: true, speech: 410, lost: 0, scribe: 1, waiting: false, paused: false, lag: 0.3, name: 'Ana Ferreira' },
  { id: 2, active: false, speech: 220, lost: 0, scribe: 2, waiting: false, paused: false, lag: 0.0, name: 'Bo' },
  { id: 3, active: true, speech: 180, lost: 0, scribe: 0, waiting: false, paused: true, lag: 2.35, name: 'Chidi Okonkwo' },
  { id: 4, active: true, speech: 90, lost: 0, scribe: 0, waiting: true, paused: false, lag: 1.6, name: 'Dana Whitfield' },
  { id: 5, active: false, speech: 75, lost: 6, scribe: 3, waiting: false, paused: false, lag: 0.84, name: 'Emeka Adeyemi' },
  { id: 6, active: false, speech: 40, lost: 0, scribe: 0, waiting: false, paused: false, lag: null, name: 'Guest · Farah' },
  { id: 7, active: false, speech: 33, lost: 0, scribe: 0, waiting: false, paused: false, lag: null, name: 'Maximiliane Theodora von Wolkenstein-Rhe' },
  { id: 8, active: true, speech: 61, lost: 0, scribe: 4, waiting: false, paused: false, lag: 1.125, name: 'Jun' },
  { id: 9, active: false, speech: 12, lost: 0, scribe: 0, waiting: false, paused: false, lag: null, name: 'Participant' },
  { id: 12, active: true, speech: 140, lost: 0, scribe: 5, waiting: false, paused: false, lag: 0.5, other: true },
]

/** The control lag the scripted feed reports at server second k: calm, then once a minute a rise past the shed line. */
export function lagAt(k) {
  const cycle = k % 60
  if (cycle === 0) return null
  if (cycle >= 40 && cycle < 50) return Math.round((0.6 + (cycle - 40) * 0.26) * 100) / 100
  return Math.round((0.35 + 0.2 * Math.sin(k / 4)) * 100) / 100
}

/** A speaker was paused after the lag stayed over the shed line. */
export const shedAt = (k) => k % 60 === 48

/**
 * A lane's state at server second k, `age` seconds before the newest column: each speaker
 * talks in bursts of their own rhythm; the paused, held and lost lanes end (or, for lost
 * audio, passed) a stretch in that state; idle lanes have been quiet for a while.
 */
export function laneState(lane, k, age) {
  if (lane.paused && age < 12) return 'paused'
  if (lane.waiting && age < 8) return 'waiting'
  if (lane.lost && age >= 30 && age < 34) return 'lost'
  if (lane.active && age < 3) return 'speech'
  const engaged = lane.active || lane.scribe || lane.waiting || lane.paused
  if (!engaged && age < 10) return undefined
  const period = 20 + (lane.id % 5) * 4
  const phase = (k + lane.id * 7) % period
  if (phase >= 6 + (lane.id % 3) * 3) return undefined
  return lane.waiting && phase < 2 ? 'waiting' : 'speech'
}

/** One history column at server second `t` for `lanes`, `newest` being the latest column's t. */
export function scriptedColumn(lanes, t, newest) {
  const k = Math.round(t)
  const states = {}
  for (const lane of lanes) {
    const state = laneState(lane, k, newest - t)
    if (state) states[lane.id] = state
  }
  return { t, lanes: states, finals: (k % 3 === 0 ? 1 : 0) + (k % 7 === 0 ? 1 : 0), drops: 0, lag: lagAt(k), shed: shedAt(k) }
}

/** `count` scripted one-second columns ending at `now`, after a JSON round trip (string lane keys). */
export function laneColumns(lanes, count, { now = NOW } = {}) {
  const made = Array.from({ length: count }, (_, i) => scriptedColumn(lanes, now - (count - 1 - i), now))
  return JSON.parse(JSON.stringify(made))
}

// The lanes and lag graph of each case are drawn by the video (diagnostic_video.render)
// and the page; diagnostics-lanes.test.mjs holds what the video drew for each.
export const LANE_CASES = {
  tenLanes: {
    sample: sample({ lanes: TEN_LANES, silentMics: 3, hiddenSpeakers: 2, admission: governor({ lagSeconds: lagAt(NOW), shedEvents: 2 }) }),
    columns: laneColumns(TEN_LANES, 120),
  },
  // The same call on Nemotron: slots, not scribes.
  nemotron: {
    sample: sample({ engine: 'nemotron-en', lanes: TEN_LANES.slice(0, 4), silentMics: 1, hiddenSpeakers: 1 }),
    columns: laneColumns(TEN_LANES.slice(0, 4), 120),
  },
  // A viewer outside the owning room: names redacted, or never read.
  redacted: {
    sample: sample({
      lanes: [
        { ...TEN_LANES[0], name: 'Participant' },
        { ...TEN_LANES[1], name: undefined },
        TEN_LANES[9],
      ],
      silentMics: 0,
      admission: {},
    }),
    columns: laneColumns(TEN_LANES.slice(0, 2), 45),
  },
  quiet: { sample: sample({ lanes: [], silentMics: 3, hiddenSpeakers: 0 }), columns: columns(120) },
  oneMic: { sample: sample({ lanes: [], silentMics: 1, hiddenSpeakers: 0 }), columns: [] },
  noMics: { sample: sample({ lanes: [], silentMics: 0, hiddenSpeakers: 0 }), columns: [] },
  // Thresholds of a tuned governor; the shed line sits over the graph's 2.5 s ceiling.
  tuned: {
    sample: sample({ lanes: TEN_LANES.slice(0, 1), silentMics: 0, admission: governor({ lagAdmit: 0.8, lagShed: 3.0, lagSeconds: 2.75 }) }),
    columns: laneColumns(TEN_LANES.slice(0, 1), 60),
  },
}

// Every case renders the same sample in the video (diagnostic_video.render) and the page;
// diagnostics-format.test.mjs holds the video's strings for each.
export const CASES = {
  // A sample with no fields at all: what the video draws before its first sample.
  empty: { sample: {}, columns: [] },
  busy: {
    sample: sample({
      engine: 'nemotron-multilingual',
      quota: 4.0,
      limitMiB: 3072,
      captionMiB: 2600,
      vadActive: 3,
      capacity: 4,
      busy: 4,
      threads: 3,
      waiting: 1,
      queuedChunks: 7,
      segmentSeconds: 8,
      p95: 1.125,
      engineRestarts: 1,
      oomKills: 2,
      admission: governor({ open: false, reason: 'lag', seats: 4, lagSeconds: 1.25, cpuStall: 0.125, engineDuty: 0.97, shedEvents: 3, holdOff: 12.5 }),
      decision: { at: 1793212496.5, verb: 'hold', who: 'Ana Ferreira', text: 'lag 1.3 s ≥ admit 1.0 · reopens under 0.5 s for 5 s' },
    }),
    columns: columns(120),
  },
  noGovernor: {
    sample: sample({ admission: {}, decision: null, lanes: [], silentMics: 0, segmentSeconds: 0, vadActive: 0, busy: 0, capacity: 0, threads: 1 }),
    columns: columns(30),
  },
  observeOnly: {
    sample: sample({
      admission: governor({ open: false, reason: 'ceiling', observeOnly: true, seats: 4, lagSeconds: 1.0 }),
      decision: { at: 1793212496.5, verb: 'hold', who: 'a speaker', text: 'all 4 seats taken' },
    }),
    columns: columns(120),
  },
  notReady: {
    sample: sample({ state: 'blocked', admission: {}, decision: null, lanes: [], vadActive: 0, busy: 0, capacity: 0 }),
    columns: [],
  },
  noRooms: {
    sample: sample({ rooms: 0, lanes: [], silentMics: 0, vadActive: 0, busy: 0, segmentSeconds: 0, queuedChunks: 0 }),
    columns: columns(120, { finals: () => 0 }),
  },
  // No memory limit, quota or cgroup weight exposed, and a governor without readings yet.
  nullBudgets: {
    sample: sample({
      quota: null,
      limitMiB: null,
      captionMiB: null,
      hostGiB: null,
      hostAvailableGiB: null,
      cpuWeight: null,
      p95: null,
      admission: governor({ lagSeconds: null, cpuStall: null, engineDuty: null, holdOff: 10 }),
    }),
    columns: columns(57),
  },
  lost: {
    sample: sample({ waiting: 2 }),
    columns: columns(120, { drops: (i) => (i === 100 ? 3 : 0) }),
  },
}
