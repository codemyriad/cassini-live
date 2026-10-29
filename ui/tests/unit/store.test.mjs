import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createStore } from '../../src/store.js'

const final = (seq, sid, message, t = seq) => ({ seq, type: 'final', speakerSessionId: sid, message, langId: 'en', t })
const join = (seq, sid, displayName, t = seq) => ({ seq, type: 'join', speakerSessionId: sid, displayName, t })

test('finals from one speaker group into one block', () => {
  const store = createStore()
  store.applyTimeline([final(1, 'a', 'hello'), final(2, 'a', 'world')])
  assert.equal(store.items.length, 1)
  assert.deepEqual(store.items[0].lines.map((l) => l.text), ['hello', 'world'])
  assert.equal(store.lastSeq, 2)
})

test('a speaker change opens a new block', () => {
  const store = createStore()
  store.applyTimeline([final(1, 'a', 'hi'), final(2, 'b', 'yo'), final(3, 'a', 'again')])
  assert.deepEqual(
    store.items.map((i) => [i.kind, i.sid, i.lines.map((l) => l.text)]),
    [
      ['block', 'a', ['hi']],
      ['block', 'b', ['yo']],
      ['block', 'a', ['again']],
    ],
  )
})

test('applyTimeline is idempotent by seq (re-sent entries are ignored)', () => {
  const store = createStore()
  store.applyTimeline([final(1, 'a', 'one'), final(2, 'a', 'two')])
  // a poll re-sends 1..3; only 3 is new
  store.applyTimeline([final(1, 'a', 'one'), final(2, 'a', 'two'), final(3, 'a', 'three')])
  assert.deepEqual(store.items[0].lines.map((l) => l.text), ['one', 'two', 'three'])
  assert.equal(store.lastSeq, 3)
})

test('join entries become join items in timeline order', () => {
  const store = createStore()
  store.applyTimeline([
    join(1, 'sess-alice', 'Alice'),
    final(2, 'sess-alice', 'hello everyone'),
    join(3, 'sess-bob', 'Bob'),
    final(4, 'sess-bob', 'hi alice'),
  ])
  assert.deepEqual(
    store.items.map((i) => (i.kind === 'join' ? ['join', i.displayName] : ['block', i.sid])),
    [
      ['join', 'Alice'],
      ['block', 'sess-alice'],
      ['join', 'Bob'],
      ['block', 'sess-bob'],
    ],
  )
})

test('timestamps come from the entries', () => {
  const store = createStore()
  store.applyTimeline([final(1, 'a', 'x', 1700)])
  assert.equal(store.items[0].lines[0].at, 1700)
  assert.equal(store.items[0].startedAt, 1700)
})

test('an empty or session-less final is ignored', () => {
  const store = createStore()
  store.applyTimeline([final(1, '', 'nope'), { seq: 2, type: 'final', speakerSessionId: 'a', message: '', t: 2 }])
  assert.equal(store.items.length, 0)
  assert.equal(store.lastSeq, 2) // seq still advances so we don't refetch them
})

test('partials are a transient channel and a final clears the speaker partial', () => {
  const store = createStore()
  store.setPartial('a', 'typing some')
  assert.deepEqual(store.activePartials, [{ sid: 'a', text: 'typing some', lang: null }])
  store.setPartial('a', 'typing something')
  assert.equal(store.partialFor('a').text, 'typing something')
  store.applyTimeline([final(1, 'a', 'typing something done')])
  assert.equal(store.partialFor('a'), null) // committed final cleared it
  assert.equal(store.items.length, 1)
})

test('an empty partial clears it', () => {
  const store = createStore()
  store.setPartial('a', 'half')
  store.setPartial('a', '')
  assert.equal(store.partialFor('a'), null)
})

test('reset clears items, seq and partials and emits a reset', () => {
  const store = createStore()
  const seen = []
  store.subscribe((c) => seen.push(c.type))
  store.applyTimeline([join(1, 'a', 'A'), final(2, 'a', 'hi')])
  store.setPartial('a', 'typing')
  store.reset()
  assert.equal(store.items.length, 0)
  assert.equal(store.lastSeq, 0)
  assert.equal(store.activePartials.length, 0)
  assert.equal(seen.at(-1), 'reset')
  // usable again, seq restarts
  store.applyTimeline([final(1, 'b', 'fresh')])
  assert.equal(store.items.length, 1)
  assert.equal(store.items[0].sid, 'b')
})

test('subscribers hear adds, finals, joins, partials and reset', () => {
  const store = createStore()
  const seen = []
  store.subscribe((c) => seen.push(c.type))
  store.applyTimeline([join(1, 'a', 'A'), final(2, 'a', 'hi')])
  store.setPartial('b', 'typing')
  store.reset()
  assert.deepEqual(seen, ['add', 'add', 'final', 'partial', 'reset'])
})
