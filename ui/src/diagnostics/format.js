/**
 * View models for the diagnostics page, from the feed's latest sample and its history
 * columns. Pure: no DOM. The wording, thresholds and number formats are the diagnostic
 * video's (cassini_live/diagnostic_video.py), so the page and the video say the same
 * things about the same sample. Times in columns and `now` are the server's monotonic
 * seconds; a sample's numbers may be null where the container exposes no reading.
 */

export const DASH = '–'

export const ENGINE_LABELS = {
  whistle: 'Whistle',
  'nemotron-en': 'Nemotron EN',
  'nemotron-multilingual': 'Nemotron ML',
}

export const REASONS = {
  lag: 'lag',
  cpu: 'CPU stall',
  memory: 'memory',
  ceiling: 'all seats taken',
  shed: 'paused',
}

// safety.py's pressure_problem pauses captions at 80 % of the memory budget.
export const MEMORY_PAUSE = 0.8
// The memory bar turns amber from this fraction of the limit (diagnostic_video._limit_bar).
const MEMORY_WARN = 0.6
// The engine is near saturation from this duty cycle.
const DUTY_WARN = 0.9
// Dropped audio colours the status this long after it happened, in server seconds.
const LOST_S = 60
// The video's seat chips fit eight in their column.
const CHIPS = 8
// The lag bar's scale, seconds: the shed threshold sits two thirds along.
const LAG_SCALE_S = 3
// The page polls once a second; an answer older than this is stale.
export const STALE_MS = 5000

export const OBSERVE_NOTE = 'Cassini Live observes these rules; nothing is held or paused yet.'
export const IDLE_NOTE = 'The page wakes when a call enables them.'

function has(object, key) {
  return object !== null && typeof object === 'object' && Object.prototype.hasOwnProperty.call(object, key)
}

// Python's dict.get: the fallback only when the key is absent.
function get(object, key, fallback) {
  return has(object, key) ? object[key] : fallback
}

// Python's int(x or 0).
function int(value) {
  const number = Math.trunc(Number(value))
  return Number.isFinite(number) ? number : 0
}

function clamp(fraction) {
  return Number.isFinite(fraction) ? Math.max(0, Math.min(1, fraction)) : 0
}

function known(value) {
  return value !== null && value !== undefined
}

export function plural(n, one, many) {
  return n === 1 ? one : many
}

/** Python's f"{x:.Nf}": correctly rounded, an exact tie to the even digit (toFixed takes the larger). */
export function fixed(value, digits) {
  const x = Number(value)
  const text = x.toFixed(digits)
  if (!Number.isFinite(x)) return text
  const exact = Math.abs(x).toFixed(Math.min(100, digits + 25))
  if (!exact.endsWith('5' + '0'.repeat(24)) || Number(text.at(-1)) % 2 === 0) return text
  return (x < 0 ? '-' : '') + (Math.abs(x) - 10 ** -digits / 2).toFixed(digits)
}

/** Python's f"{x:g}" and f"{x:.Ng}". */
export function g(value, precision = 6) {
  const x = Number(value)
  if (!Number.isFinite(x)) return String(x)
  if (x === 0) return '0'
  const trim = (text) => (text.includes('.') ? text.replace(/\.?0+$/, '') : text)
  const [mantissa, power] = x.toExponential(precision - 1).split('e')
  const exponent = Number(power)
  if (exponent < -4 || exponent >= precision) {
    const digits = String(Math.abs(exponent)).padStart(2, '0')
    return `${trim(mantissa)}e${exponent < 0 ? '-' : '+'}${digits}`
  }
  return trim(fixed(x, precision - 1 - exponent))
}

/** diagnostic_video._seconds */
export function seconds(value) {
  return known(value) ? `${fixed(value, 1)} s` : DASH
}

/** diagnostic_video._percent */
export function percent(value) {
  return known(value) ? `${fixed(Number(value) * 100, 0)}%` : DASH
}

function gib(mib, precision = 3) {
  return g(mib / 1024, precision)
}

function admissionOf(sample) {
  const admission = (sample && sample.admission) || {}
  return { admission, observing: Boolean(admission.observeOnly), governed: Object.keys(admission).length > 0 }
}

function closedOf(sample) {
  const { admission, governed } = admissionOf(sample)
  return governed && !get(admission, 'open', true)
}

function reasonOf(admission) {
  const reason = admission.reason
  return has(REASONS, reason) ? REASONS[reason] : reason || ''
}

function heldText(admission, observing) {
  return (observing ? 'would hold: ' : 'holding: ') + reasonOf(admission)
}

function thresholds(admission) {
  return {
    lagAdmit: Number(admission.lagAdmit || 1.0),
    lagShed: Number(admission.lagShed || 2.0),
    stallAdmit: Number(admission.cpuStallAdmit || 0.2),
  }
}

function seatsOf(sample) {
  const { admission } = admissionOf(sample)
  return {
    seats: int(get(admission, 'seats', get(sample, 'busy', 0))),
    ceiling: int(get(admission, 'ceiling', get(sample, 'capacity', 0))),
  }
}

/** Every open microphone: the lanes plus the tracks they leave out (diagnostic_video._mics). */
export function mics(sample) {
  const lanes = (sample && sample.lanes) || []
  return lanes.length + int(get(sample, 'silentMics', 0)) + int(get(sample, 'hiddenSpeakers', 0))
}

const NOT_READY_TONES = { recovering: 'warn', disabled: 'idle' }

/**
 * The status pill: {label, tone, note}, tone one of live, warn, bad, idle.
 *
 * Page-only states come first: they say captions are not running at all, which the video
 * never shows because it runs only in a live room while captions are ready. Then the
 * video's own rules in its order (_status): audio dropped in the last minute, admission
 * closed, speakers waiting, live.
 */
export function statusPill(sample, columns = [], now = 0) {
  const pill = (label, tone, note = '') => ({ label, tone, note })
  if (!sample || !Object.keys(sample).length) return pill('Starting', 'idle')
  if (typeof sample.state === 'string' && sample.state !== 'ready') {
    return pill(`Captions not ready · ${sample.state}`, NOT_READY_TONES[sample.state] || 'bad')
  }
  if (sample.rooms === 0) return pill('No captions running', 'idle', IDLE_NOTE)
  const recent = now - LOST_S
  if (columns.some((column) => column.drops && column.t >= recent)) return pill('Audio lost', 'bad')
  if (closedOf(sample)) {
    return pill(admissionOf(sample).observing ? 'Would hold new voices' : 'Holding new voices', 'warn')
  }
  if (int(get(sample, 'waiting', 0))) return pill('Speakers waiting', 'warn')
  return pill('Captions live', 'live')
}

/** The engine and, when the container exposes both, its CPU quota and memory limit. */
export function engineLine(sample) {
  if (!sample) return ''
  const engine = get(sample, 'engine', 'whistle') ?? 'whistle'
  let text = has(ENGINE_LABELS, engine) ? ENGINE_LABELS[engine] : String(engine)
  const quota = sample.quota
  const limit = sample.limitMiB
  if (quota && limit) text += ` · ${g(quota)} CPU · ${gib(int(limit))} GiB`
  return text
}

/** Whether the governor only observes its rules, and how the page says so. */
export function observeOnly(sample) {
  const { observing } = admissionOf(sample)
  return { on: observing, badge: observing ? 'observe only' : '', note: observing ? OBSERVE_NOTE : '' }
}

/** The hero row: voices heard now, seats taken of the ceiling, how far captions trail speech. */
export function hero(sample) {
  const data = sample || {}
  const { admission, observing } = admissionOf(data)
  const voices = int(get(data, 'vadActive', 0))
  const { seats, ceiling } = seatsOf(data)
  const closed = closedOf(data)
  const lag = known(admission.lagSeconds) ? Number(admission.lagSeconds) : null
  const { lagAdmit, lagShed } = thresholds(admission)
  const late = lag !== null && lag >= lagAdmit
  return {
    voices: { value: String(voices), label: [plural(voices, 'voice', 'voices'), 'heard now'] },
    seats: {
      value: String(seats),
      of: `/${ceiling}`,
      label: closed ? ['seats taken', heldText(admission, observing)] : ['seats', 'taken'],
      tone: closed ? 'warn' : 'soft',
      chips: Array.from({ length: Math.max(0, Math.min(ceiling, CHIPS)) }, (_, i) => i < seats),
    },
    lag: {
      value: lag === null ? DASH : fixed(lag, 1),
      unit: lag === null ? '' : ' s',
      label: ['captions trail', 'speech by'],
      tone: late ? 'warn' : 'gold',
      fill: lag ? clamp(lag / LAG_SCALE_S) : 0,
      admit: clamp(Math.min(LAG_SCALE_S, lagAdmit) / LAG_SCALE_S),
      shed: clamp(Math.min(LAG_SCALE_S, lagShed) / LAG_SCALE_S),
    },
  }
}

/** The governor's last admission decision, or null (diagnostic_video._render_lanes). */
export function decisionLine(sample) {
  const decision = sample && sample.decision
  if (!decision) return null
  let verb = decision.verb || ''
  if (admissionOf(sample).observing && verb) verb = 'would ' + verb
  const at = new Date(Number(get(decision, 'at', 0)) * 1000)
  return {
    // The governor stamps decisions with wall-clock seconds; the video draws them in UTC.
    time: Number.isNaN(at.getTime()) ? DASH : at.toISOString().slice(11, 19),
    verb: `${verb} ${decision.who ?? ''}`.trim(),
    text: String(decision.text ?? ''),
    tone: decision.verb === 'admit' ? 'live' : 'warn',
  }
}

/** The five caption pipeline stages (diagnostic_video._render_pipeline). */
export function pipelineStages(sample, columns = [], now = 0) {
  const data = sample || {}
  const { admission, observing } = admissionOf(data)
  const finals = columns.filter((column) => column.t >= now - 60).reduce((sum, column) => sum + int(column.finals), 0)
  const span = columns.length ? now - columns[0].t : 0
  const open = mics(data)
  const { seats, ceiling } = seatsOf(data)
  const closed = closedOf(data)
  const { lagAdmit, stallAdmit } = thresholds(admission)
  const duty = known(admission.engineDuty) ? Number(admission.engineDuty) : null
  const engine = get(data, 'engine', 'whistle')
  const used = int(data.captionMiB)
  const limit = int(data.limitMiB)
  const threads = int(get(data, 'threads', 1)) || 1
  const size = int(get(data, 'capacity', 0))
  const shape = engine === 'whistle'
    ? `${size} ${plural(size, 'worker', 'workers')}`
    : `1 process, ${size} ${plural(size, 'slot', 'slots')}`
  const segment = data.segmentSeconds
  return [
    {
      step: '1 · Listen',
      name: 'Microphones',
      big: String(int(get(data, 'vadActive', 0))),
      unit: `voices of ${open} ${plural(open, 'mic', 'mics')}`,
      detail: ['voice gate per mic', '32 ms frames', 'silence ends a turn'],
      tone: 'live',
    },
    {
      step: '2 · Cut',
      name: 'Speech pieces',
      big: '1 s',
      // Each engine cuts at its own length; unknown until a microphone opens.
      unit: segment ? `per piece, ${segment} s max` : 'per piece',
      detail: [`queue ${int(get(data, 'queuedChunks', 0))} of ${6 * Math.max(1, open)}`, 'older than 4 s:', 'dropped, not late'],
      tone: 'live',
    },
    {
      step: '3 · Admit',
      name: 'Seats',
      big: `${seats}/${ceiling}`,
      unit: closed ? heldText(admission, observing) : 'admitting',
      detail: ['first voice: always', `then lag < ${fixed(lagAdmit, 1)} s`, `and stall < ${fixed(stallAdmit * 100, 0)}%`],
      tone: closed ? 'warn' : 'live',
    },
    {
      step: '4 · Recognise',
      name: 'Engine',
      big: percent(duty),
      unit: `busy, ${threads} ${plural(threads, 'thread', 'threads')}`,
      // Without a memory limit the video drops the first two lines too; the page keeps them.
      detail: [
        shape,
        `reply p95 ${seconds(data.p95)}`,
        limit ? `${fixed(used / 1024, 2)} of ${gib(limit)} GiB` : `memory ${DASH}`,
      ],
      tone: duty !== null && duty >= DUTY_WARN ? 'warn' : 'live',
    },
    {
      step: '5 · Send',
      name: 'Captions',
      big: String(finals),
      unit: span >= 58 ? 'lines per minute' : 'lines so far',
      detail: [`trail speech ${seconds(admission.lagSeconds)}`, 'via Talk signaling', 'no audio leaves'],
      tone: 'gold',
    },
  ]
}

/** The three protection panels under the pipeline, as {title, rows: [[key, value]]}. */
export function protectionPanels(sample) {
  const data = sample || {}
  const { admission } = admissionOf(data)
  const { lagAdmit, lagShed } = thresholds(admission)
  const weight = data.cpuWeight
  const quota = data.quota ? `${g(data.quota)} CPU` : DASH
  const limit = int(data.limitMiB)
  const memory = limit ? `${gib(limit)} GiB` : DASH
  const holdOff = get(admission, 'holdOff', 10)
  const restarts = int(get(data, 'engineRestarts', 0))
  return [
    {
      title: 'The kernel protects the call',
      rows: [
        ['cpu.max', `${quota} hard cap`],
        ['cpu.weight', `${weight || DASH}, Talk 100: we yield`],
        ['memory.max', `${memory}, no swap`],
        ['OOM', 'engine dies, Talk lives'],
      ],
    },
    {
      title: 'Lag keeps captions useful',
      rows: [
        [`lag ≥ ${fixed(lagAdmit, 1)} s`, 'hold new voices'],
        [`lag ≥ ${fixed(lagShed, 1)} s`, `for 5 s: pause the newest speaker ${known(holdOff) ? g(holdOff) : DASH} s`],
        [`lag < ${fixed(lagAdmit / 2, 1)} s`, 'for 5 s: reopen'],
      ],
    },
    {
      title: 'Only a crash stops captions',
      rows: [
        ['engine died', 'restart, ~2 s'],
        ['3 deaths/60 s', 'rest 2 min, doubling'],
        ['OOM kill', 'rest 2 min'],
        ['since start', `${restarts} ${plural(restarts, 'restart', 'restarts')} · ${int(get(data, 'oomKills', 0))} OOM`],
      ],
    },
  ]
}

/**
 * The budget footer: memory against its limit (with the pause mark), CPU stall, engine
 * duty, reply p95, and the feed's own sampling cost.
 */
export function footer(sample) {
  const data = sample || {}
  const { admission } = admissionOf(data)
  const limit = int(data.limitMiB)
  const used = int(data.captionMiB)
  const fraction = clamp(used / Math.max(1, limit))
  const duty = known(admission.engineDuty) ? Number(admission.engineDuty) : null
  return {
    memory: {
      label: 'Memory',
      fraction,
      mark: MEMORY_PAUSE,
      text: limit ? `${fixed(used / 1024, 2)}/${gib(limit)} GiB` : DASH,
      tone: fraction >= MEMORY_WARN ? 'warn' : 'grey',
    },
    metrics: [
      { id: 'stall', label: 'CPU stall', value: percent(admission.cpuStall), tone: 'soft' },
      { id: 'duty', label: 'Engine busy', value: percent(duty), tone: duty !== null && duty >= DUTY_WARN ? 'warn' : 'soft' },
      { id: 'p95', label: 'Reply p95', value: seconds(data.p95), tone: 'soft' },
      { id: 'sampler', label: 'Sampling', value: known(data.samplerMs) ? `${fixed(data.samplerMs, 1)} ms` : DASH, tone: 'soft' },
    ],
  }
}

const VIDEO_STATES = {
  idle: 'Diagnostic video on, waiting for a captioned call',
  starting: 'Diagnostic video starting',
  new: 'Diagnostic video connecting',
  connecting: 'Diagnostic video connecting',
  connected: 'Diagnostic video publishing',
}
const VIDEO_TONES = { connected: 'live', idle: 'idle', starting: 'idle', new: 'idle', connecting: 'idle' }
// The child reports its cost while it runs; after it stops the last figures may linger.
const VIDEO_RUNNING = new Set(['new', 'connecting', 'connected'])

/**
 * The diagnostic video publisher, in one line: off, its state, and while it runs its own
 * CPU, memory and bit rate. Only these fields are read: the status carries no room.
 */
export function videoLine(video) {
  if (!video || !video.enabled) return { text: 'Diagnostic video off', tone: 'idle' }
  const state = String(video.state || 'idle')
  let text = has(VIDEO_STATES, state) ? VIDEO_STATES[state] : `Diagnostic video ${state}`
  if (VIDEO_RUNNING.has(state) && known(video.diagnosticCpu)) {
    text += ` · ${fixed(video.diagnosticCpu, 0)}% CPU · ${fixed(video.diagnosticMiB ?? 0, 0)} MiB · ${fixed(video.kbps ?? 0, 0)} kbit/s`
  }
  return { text, tone: has(VIDEO_TONES, state) ? VIDEO_TONES[state] : 'warn' }
}

function ago(ms) {
  const s = Math.floor(ms / 1000)
  // Five-second steps, then minutes: the line is announced politely on every change.
  return s < 60 ? `${Math.floor(s / 5) * 5} s ago` : `${Math.floor(s / 60)} min ago`
}

/** How fresh the page's numbers are: {stale, text, tone}. */
export function freshness(lastOkAtMs, nowMs) {
  if (!known(lastOkAtMs)) return { stale: true, text: 'Waiting for the first update', tone: 'idle' }
  const age = Math.max(0, nowMs - lastOkAtMs)
  if (age < STALE_MS) return { stale: false, text: 'Live · updated every second', tone: 'live' }
  return { stale: true, text: `Stale · last update ${ago(age)}`, tone: 'warn' }
}

/** The connection line while the feed cannot be reached. */
export function unreachable(lastOkAtMs, nowMs) {
  const last = known(lastOkAtMs) ? ` · last update ${ago(Math.max(0, nowMs - lastOkAtMs))}` : ''
  return { stale: true, text: `Cassini Live unreachable${last} · retrying`, tone: 'bad' }
}

/** The feed's sampling error: the exception type only, the last good sample stays. */
export function sampleError(error) {
  return error ? `The last sample failed (${error}); showing the one before.` : ''
}
