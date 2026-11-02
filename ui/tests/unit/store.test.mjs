import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createStore } from '../../src/store.js'

const final = (seq, sid, message, t = seq) => ({ seq, type: 'final', speakerSessionId: sid, message, langId: 'en', t })
const join = (seq, sid, displayName, t = seq) => ({ seq, type: 'join', speakerSessionId: sid, displayName, t })
const texts = (store) => store.items.filter((i) => i.kind === 'block').flatMap((b) => b.lines.map((l) => l.text))

test('a contiguous live feed renders in order and advances the confirmed seq', () => {
  const store = createStore()
  store.applyLiveFinal(final(1, 'a', 'one'))
  store.applyLiveFinal(final(2, 'a', 'two'))
  assert.deepEqual(texts(store), ['one', 'two'])
  assert.equal(store.confirmedSeq, 2)
})

test('an out-of-order live final is held until the gap is filled, then flushes in order', () => {
  const store = createStore()
  store.applyLiveFinal(final(1, 'a', 'one'))
  store.applyLiveFinal(final(3, 'a', 'three')) // seq 2 missing → held
  assert.deepEqual(texts(store), ['one'])
  assert.equal(store.confirmedSeq, 1)
  // the poll fills the gap; the held entry then renders, in order
  store.applyPoll([final(2, 'a', 'two'), final(3, 'a', 'three')])
  assert.deepEqual(texts(store), ['one', 'two', 'three'])
  assert.equal(store.confirmedSeq, 3)
})

test('the live feed and the poll never double-count a shared seq', () => {
  const store = createStore()
  store.applyLiveFinal(final(1, 'a', 'one'))
  store.applyLiveFinal(final(2, 'a', 'two'))
  // the poll re-sends 1..2 and adds 3; only 3 is new
  store.applyPoll([final(1, 'a', 'one'), final(2, 'a', 'two'), final(3, 'a', 'three')])
  assert.deepEqual(texts(store), ['one', 'two', 'three'])
})

test('joins (poll-only) interleave with finals by seq', () => {
  const store = createStore()
  store.applyPoll([join(1, 's-al', 'Alice'), final(2, 's-al', 'hello'), join(3, 's-bob', 'Bob')])
  assert.deepEqual(
    store.items.map((i) => (i.kind === 'join' ? ['join', i.displayName] : ['block', i.sid])),
    [
      ['join', 'Alice'],
      ['block', 's-al'],
      ['join', 'Bob'],
    ],
  )
})

test('poll steps over entries the server has evicted (gap that will never fill)', () => {
  const store = createStore()
  store.applyLiveFinal(final(10, 'a', 'later')) // held: huge gap
  assert.deepEqual(texts(store), [])
  // the server's oldest available is 8 — 1..7 are gone; accept the gap
  store.applyPoll([final(8, 'a', 'eight'), final(9, 'a', 'nine'), final(10, 'a', 'later')])
  assert.deepEqual(texts(store), ['eight', 'nine', 'later'])
  assert.equal(store.confirmedSeq, 10)
})

test('entries without a seq are ignored on the timeline', () => {
  const store = createStore()
  store.applyLiveFinal({ type: 'final', speakerSessionId: 'a', message: 'no seq' })
  assert.deepEqual(texts(store), [])
})

test('consecutive same-speaker finals group into one block; a change opens a new one', () => {
  const store = createStore()
  store.applyPoll([final(1, 'a', 'hi'), final(2, 'a', 'again'), final(3, 'b', 'yo')])
  assert.deepEqual(
    store.items.map((i) => [i.sid, i.lines.map((l) => l.text)]),
    [
      ['a', ['hi', 'again']],
      ['b', ['yo']],
    ],
  )
})

test('partials are transient and a final clears the speaker partial', () => {
  const store = createStore()
  store.setPartial('a', 'typing some')
  assert.deepEqual(store.activePartials, [{ sid: 'a', text: 'typing some', lang: null }])
  store.applyLiveFinal(final(1, 'a', 'typing some done'))
  assert.equal(store.partialFor('a'), null)
  assert.deepEqual(texts(store), ['typing some done'])
})

test('reset clears items, seq, pending and partials and emits a reset', () => {
  const store = createStore()
  const seen = []
  store.subscribe((c) => seen.push(c.type))
  store.applyLiveFinal(final(3, 'a', 'held'))
  store.applyPoll([join(1, 'a', 'A'), final(2, 'a', 'hi')])
  store.setPartial('a', 'typing')
  store.reset()
  assert.equal(store.items.length, 0)
  assert.equal(store.confirmedSeq, 0)
  assert.equal(store.activePartials.length, 0)
  assert.equal(seen.at(-1), 'reset')
  // usable again, seq restarts, no stale pending leaks through
  store.applyLiveFinal(final(1, 'b', 'fresh'))
  assert.deepEqual(texts(store), ['fresh'])
})

test('timestamps come from the entries', () => {
  const store = createStore()
  store.applyPoll([final(1, 'a', 'x', 1700)])
  assert.equal(store.items[0].lines[0].at, 1700)
  assert.equal(store.items[0].startedAt, 1700)
})


test('poll eviction releases an already pending first retained final and its successors', () => {
  const store = createStore()
  store.applyLiveFinal(final(3, 'a', 'three'))
  store.applyLiveFinal(final(4, 'a', 'four'))
  store.applyPoll([final(3, 'a', 'three')])
  assert.deepEqual(texts(store), ['three', 'four'])
  assert.equal(store.confirmedSeq, 4)
  store.applyPoll([final(3, 'a', 'three'), final(4, 'a', 'four')])
  assert.deepEqual(texts(store), ['three', 'four'])
})
