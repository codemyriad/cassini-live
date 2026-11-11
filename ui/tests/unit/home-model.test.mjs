import assert from 'node:assert/strict'
import { test } from 'node:test'
import { viewModel } from '../../src/home/model.js'

const ready = {
  enabled: true, signallingConfigured: true, model: 'Whistle', activeRooms: 0, version: '0.11.0',
  resourceSafety: { state: 'ready', phase: 'ready', reason: '' },
}
const fresh = {
  enabled: false, signallingConfigured: false, model: 'Whistle', activeRooms: 0,
  resourceSafety: { state: 'blocked', phase: 'configuration', reason: 'Enter the Talk HPB internal secret', adminActionRequired: true },
}
const host = { cpuQuota: 4, memoryLimitBytes: 2 * 1024 ** 3 }
const talkOk = { secretConfigured: true, check: { state: 'passed', message: 'ok' } }

test('ready: green pill, no setup list, admin facts only for administrators', () => {
  const user = viewModel({ status: ready, admin: null })
  assert.deepEqual(user.pill, { kind: 'ready', text: 'Captions ready' })
  assert.equal(user.setup, null)
  assert.equal(user.facts, null)
  const admin = viewModel({ status: { ...ready, activeRooms: 2 }, admin: { talk: talkOk, panel: { host } } })
  assert.equal(admin.setup, null)
  assert.deepEqual(admin.facts, { engine: 'Whistle', talk: { text: 'Passed', tone: 'good' }, calls: '2 calls' })
})

test('right after install an administrator sees all three steps open', () => {
  const model = viewModel({ status: fresh, admin: { talk: { secretConfigured: false, check: null }, panel: { host: {} } } })
  assert.equal(model.pill.text, 'Setup needed')
  assert.deepEqual(model.setup.map((s) => [s.id, s.done]), [['talk', false], ['limits', false], ['engine', false]])
  assert.equal(model.setup[2].state, 'Waiting')
  assert.equal(model.facts.talk.text, 'Internal secret not set')
  assert.equal(model.facts.engine, 'Whistle, not loaded')
})

test('a failed check and a refused container are named in their steps', () => {
  const failed = viewModel({
    status: fresh,
    admin: { talk: { secretConfigured: true, check: { state: 'failed', message: 'The HPB refused the internal secret' } }, panel: { host } },
  })
  assert.equal(failed.setup[0].done, false)
  assert.match(failed.setup[0].detail, /did not pass: The HPB refused the internal secret\./)
  const refused = viewModel({
    status: { ...fresh, enabled: true, signallingConfigured: true,
      resourceSafety: { state: 'blocked', phase: 'configuration', reason: 'Set finite cgroup v2 CPU, memory and PID limits' } },
    admin: { talk: talkOk, panel: { host } },
  })
  assert.deepEqual(refused.setup.map((s) => s.done), [true, false, false])
  assert.equal(refused.setup[1].detail, 'Set finite cgroup v2 CPU, memory and PID limits.')
})

test('with both steps done the engine step says it is loading', () => {
  const model = viewModel({
    status: { ...fresh, enabled: true, signallingConfigured: true, resourceSafety: { state: 'recovering', phase: 'loading', reason: '' } },
    admin: { talk: talkOk, panel: { host } },
  })
  assert.equal(model.pill.text, 'Starting')
  assert.equal(model.setup[2].state, 'Loading')
})

test('other people are told to ask an administrator, or that captions are starting', () => {
  const blocked = viewModel({ status: fresh, admin: null })
  assert.equal(blocked.pill.text, 'Captions are not set up yet')
  assert.match(blocked.userNote, /Ask an administrator/)
  const starting = viewModel({ status: { ...fresh, resourceSafety: { state: 'recovering', phase: 'loading' } }, admin: null })
  assert.equal(starting.pill.text, 'Captions are starting')
})

test('no answer from the app is said plainly', () => {
  const model = viewModel({ status: null, admin: null })
  assert.equal(model.pill.kind, 'off')
  assert.match(model.userNote, /did not answer/)
})
