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
