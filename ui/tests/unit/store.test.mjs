import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createStore } from '../../src/store.js'

const final = (sid, text, lang = 'en') => ({ sid, text, final: true, lang })
const partial = (sid, text, lang = 'en') => ({ sid, text, final: false, lang })

test('finals from one speaker group into one block', () => {
  const store = createStore()
  store.apply(final('a', 'hello'))
  store.apply(final('a', 'world'))
  assert.equal(store.blocks.length, 1)
  assert.deepEqual(
    store.blocks[0].lines.map((l) => l.text),
    ['hello', 'world'],
  )
})

test('a speaker change opens a new block', () => {
  const store = createStore()
  store.apply(final('a', 'hello'))
  store.apply(final('b', 'hi'))
  store.apply(final('a', 'again'))
  assert.deepEqual(
    store.blocks.map((b) => [b.sid, b.lines.map((l) => l.text)]),
    [
      ['a', ['hello']],
      ['b', ['hi']],
      ['a', ['again']],
    ],
  )
})

test('a partial replaces the previous partial, a final commits it', () => {
  const store = createStore()
  store.apply(partial('a', 'hel'))
  store.apply(partial('a', 'hello wor'))
  assert.equal(store.blocks.length, 1)
  assert.equal(store.blocks[0].partial.text, 'hello wor')
  store.apply(final('a', 'hello world'))
  assert.equal(store.blocks[0].partial, null)
  assert.deepEqual(
    store.blocks[0].lines.map((l) => l.text),
    ['hello world'],
  )
})

test('an empty partial clears the preview and drops an empty block', () => {
  const store = createStore()
  store.apply(partial('a', 'typing…'))
  assert.equal(store.blocks.length, 1)
  store.apply(partial('a', ''))
  assert.equal(store.blocks.length, 0)
})

test('concurrent speakers keep their partials in place (no block churn)', () => {
  const store = createStore()
  store.apply(partial('a', 'one'))
  store.apply(partial('b', 'two'))
  const [blockA, blockB] = store.blocks
  store.apply(partial('a', 'one more'))
  store.apply(partial('b', 'two more'))
  assert.equal(store.blocks.length, 2)
  assert.equal(store.blocks[0], blockA)
  assert.equal(store.blocks[1], blockB)
  assert.equal(blockA.partial.text, 'one more')
  assert.equal(blockB.partial.text, 'two more')
})

test("a final relocates a speaker with an older open partial to the end", () => {
  const store = createStore()
  store.apply(partial('a', 'start'))
  store.apply(final('b', 'interjection'))
  store.apply(final('a', 'started late'))
  assert.deepEqual(
    store.blocks.map((b) => b.sid),
    ['b', 'a'],
  )
  const blockA = store.blocks[1]
  assert.deepEqual(blockA.lines.map((l) => l.text), ['started late'])
  // the stale preview in the removed/old position is gone everywhere
  assert.ok(store.blocks.every((b) => b.sid !== 'a' || b.partial === null))
})

test('events without a session id or final without text are ignored', () => {
  const store = createStore()
  store.apply(final('', 'nope'))
  store.apply({ sid: 'a', text: '', final: true, lang: 'en' })
  assert.equal(store.blocks.length, 0)
})

test('subscribers hear adds, partials, finals and removals', () => {
  const store = createStore()
  const seen = []
  store.subscribe((change) => seen.push(change.type))
  store.apply(partial('a', 'x'))
  store.apply(final('a', 'x!'))
  store.apply(partial('b', 'y'))
  store.apply(partial('b', ''))
  assert.deepEqual(seen, ['add', 'partial', 'final', 'add', 'partial', 'update', 'remove'])
})
