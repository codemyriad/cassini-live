/**
 * What the home page shows, from the app's own answers. Pure: no DOM, no fetch.
 *
 * `status` is GET /status (any logged-in user). `admin` is GET settings/api/state
 * (administrators only) or null. The page never guesses: without `status` it says
 * it cannot reach Cassini Live.
 */

const READY = { kind: 'ready', text: 'Captions ready' }

function talkStep(talk, status) {
  const check = talk?.check
  const done = Boolean(talk?.secretConfigured) && (check ? check.state === 'passed' : Boolean(status?.signallingConfigured))
  let detail = "Enter the internal secret of Talk's high-performance backend. Cassini Live checks it right away."
  if (talk?.unreadable) {
    detail = 'The saved Talk connection could not be read. Open settings to see how to restore it.'
  } else if (talk?.secretConfigured && check && check.state !== 'passed') {
    detail = `The last check did not pass: ${sentence(check.message)}`
  } else if (talk?.secretConfigured && !done) {
    detail = "The secret is saved. Cassini Live is still loading Talk's signalling settings."
  } else if (done) {
    detail = 'The high-performance backend accepted the internal secret.'
  }
  return { id: 'talk', title: 'Connect to Talk', done, detail, action: 'settings' }
}

function limitsStep(host, safety, talkDone) {
  const set = Boolean(host?.cpuQuota) && Boolean(host?.memoryLimitBytes)
  // Process and swap limits are only checked once Talk is connected; then the
  // service names what is missing.
  const refused = talkDone && safety?.state === 'blocked' && safety?.phase === 'configuration'
  const done = set && !refused
  let detail = 'The container needs enforced CPU, memory and process limits, with swap off. This is done on the server, not here.'
  if (refused && safety.reason) detail = sentence(safety.reason)
  else if (done) detail = 'CPU and memory limits are in place.'
  return { id: 'limits', title: 'Set container limits', done, detail, action: 'limits-docs' }
}

function engineStep(safety, earlierDone) {
  const done = safety?.state === 'ready'
  let detail = 'Happens by itself once the first two are done. This page then shows Captions ready.'
  let state = 'Waiting'
  if (done) {
    detail = 'The speech engine is loaded.'
    state = 'Done'
  } else if (earlierDone) {
    state = safety?.phase === 'loading' ? 'Loading' : 'Starting'
    if (safety?.reason) detail = sentence(safety.reason)
  }
  return { id: 'engine', title: 'Speech engine loads', done, detail, state }
}

function sentence(text) {
  const trimmed = String(text || '').trim()
  if (!trimmed) return ''
  return /[.!?…]$/.test(trimmed) ? trimmed : `${trimmed}.`
}

function talkFact(talk) {
  if (!talk?.secretConfigured) return { text: 'Internal secret not set', tone: 'warning' }
  const check = talk.check
  if (!check) return { text: 'Secret saved, not checked since restart', tone: 'neutral' }
  if (check.state === 'passed') return { text: 'Passed', tone: 'good' }
  return { text: check.state === 'failed' ? 'Check failed' : 'Backend unreachable', tone: 'warning' }
}

export function viewModel({ status, admin }) {
  if (!status) {
    return {
      pill: { kind: 'off', text: 'Cannot reach Cassini Live' },
      userNote: 'Cassini Live did not answer. Reload this page; if it stays like this, tell an administrator.',
      setup: null,
      facts: null,
    }
  }
  const safety = status.resourceSafety || {}
  const ready = Boolean(status.enabled) && Boolean(status.signallingConfigured) && safety.state === 'ready'

  let setup = null
  let facts = null
  if (admin) {
    const talk = admin.talk || {}
    const first = talkStep(talk, status)
    const second = limitsStep(admin.panel?.host, safety, first.done)
    const third = engineStep(safety, first.done && second.done)
    if (!ready) setup = [first, second, third]
    const rooms = Number(status.activeRooms) || 0
    facts = {
      engine: status.model ? (safety.state === 'ready' ? status.model : `${status.model}, not loaded`) : 'Unknown',
      talk: talkFact(talk),
      calls: rooms === 0 ? 'None right now' : rooms === 1 ? '1 call' : `${rooms} calls`,
    }
  }

  if (ready) return { pill: READY, userNote: null, setup, facts }
  if (admin) {
    const blocking = setup.slice(0, 2).some((step) => !step.done)
    return { pill: { kind: 'setup', text: blocking ? 'Setup needed' : 'Starting' }, userNote: null, setup, facts }
  }
  const starting = safety.state === 'recovering' && !safety.adminActionRequired
  return {
    pill: starting ? { kind: 'setup', text: 'Captions are starting' } : { kind: 'setup', text: 'Captions are not set up yet' },
    userNote: starting
      ? 'Cassini Live is starting. Captions will work in a moment.'
      : 'Captions are not set up yet. Ask an administrator to finish setting up Cassini Live.',
    setup: null,
    facts: null,
  }
}
