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
    detail = 'Connected.'
  }
  return { id: 'talk', title: 'Connect to Talk', done, detail, action: 'settings' }
}

function limitsStep(host, safety, talkDone) {
  const set = Boolean(host?.cpuQuota) && Boolean(host?.memoryLimitBytes)
  // A configuration refusal (for example Whistle under its CPU minimum) names itself.
  const refused = talkDone && safety?.state === 'blocked' && safety?.phase === 'configuration'
  const done = set && !refused
  // Advised since D-930, not required: without limits captions run on a default budget.
  let detail = 'Advised: give the container CPU, memory and process limits, and turn swap off, on the server that runs the container. Until then captions run on a default budget.'
  if (refused && safety.reason) detail = sentence(safety.reason)
  else if (done) detail = 'Limits are set.'
  return { id: 'limits', title: 'Set container limits', done, detail, action: 'limits-docs' }
}

function engineStep(safety, earlierDone) {
  const done = safety?.state === 'ready'
  let detail = 'The speech engine starts on its own once both are done.'
  let state = 'Waiting'
  if (done) {
    detail = 'The speech engine is loaded.'
    state = 'Done'
  } else if (earlierDone) {
    state = safety?.phase === 'loading' ? 'Loading' : 'Starting'
    if (safety?.reason) detail = sentence(safety.reason)
  }
  return { id: 'engine', title: 'Speech engine', done, detail, state }
}

function sentence(text) {
  const trimmed = String(text || '').trim()
  if (!trimmed) return ''
  return /[.!?…]$/.test(trimmed) ? trimmed : `${trimmed}.`
}

function talkFact(talk) {
  if (!talk?.secretConfigured) return { text: 'Not connected', tone: 'warning' }
  const check = talk.check
  if (!check) return { text: 'Not checked since restart', tone: 'neutral' }
  if (check.state === 'passed') return { text: 'Connected', tone: 'good' }
  return { text: check.state === 'failed' ? 'Check failed' : 'Backend unreachable', tone: 'warning' }
}

export function viewModel({ status, admin }) {
  if (!status) {
    return {
      pill: { kind: 'off', text: 'Not responding' },
      userNote: "Cassini Live isn't responding. Reload the page. If that doesn't help, tell an administrator.",
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
    // One administrator block at a time: what is left to do, or, once captions
    // are ready, the facts and links.
    if (!ready) {
      setup = [first, second, third]
    } else {
      const rooms = Number(status.activeRooms) || 0
      facts = {
        engine: status.model || 'Unknown',
        talk: talkFact(talk),
        calls: rooms === 0 ? 'None' : rooms === 1 ? '1 call' : `${rooms} calls`,
      }
    }
  }

  if (ready) return { pill: READY, userNote: null, setup, facts }
  if (admin) {
    const blocking = setup.slice(0, 2).some((step) => !step.done)
    return { pill: { kind: 'setup', text: blocking ? 'Setup needed' : 'Starting' }, userNote: null, setup, facts }
  }
  const starting = safety.state === 'recovering' && !safety.adminActionRequired
  return {
    pill: starting ? { kind: 'setup', text: 'Starting' } : { kind: 'setup', text: 'Not set up yet' },
    userNote: starting
      ? 'Cassini Live is starting. Give it a moment.'
      : "Captions aren't working yet. An administrator needs to finish setting up Cassini Live.",
    setup: null,
    facts: null,
  }
}
