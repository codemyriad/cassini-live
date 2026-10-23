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

test('seedHistory groups consecutive same-speaker finals into historical blocks', () => {
  const store = createStore()
  store.seedHistory([
    { speakerSessionId: 'a', message: 'hello', langId: 'en', t: 100 },
    { speakerSessionId: 'a', message: 'there', langId: 'en', t: 200 },
    { speakerSessionId: 'b', message: 'hi', langId: 'en', t: 300 },
  ])
  assert.equal(store.hasHistory, true)
  assert.deepEqual(
    store.blocks.map((b) => [b.sid, b.historical, b.lines.map((l) => l.text)]),
    [
      ['a', true, ['hello', 'there']],
      ['b', true, ['hi']],
    ],
  )
  // timestamps come from the entries
  assert.equal(store.blocks[0].lines[0].at, 100)
})

test('a live final after history opens a fresh block, never merging into history', () => {
  const store = createStore()
  store.seedHistory([{ speakerSessionId: 'a', message: 'before you joined', langId: 'en', t: 1 }])
  store.apply(final('a', 'after you joined'))
  assert.equal(store.blocks.length, 2)
  assert.equal(store.blocks[0].historical, true)
  assert.equal(store.blocks[1].historical, false)
  assert.deepEqual(store.blocks[1].lines.map((l) => l.text), ['after you joined'])
})

test('reset clears everything and emits a reset change', () => {
  const store = createStore()
  const seen = []
  store.subscribe((c) => seen.push(c.type))
  store.seedHistory([{ speakerSessionId: 'a', message: 'x', langId: 'en', t: 1 }])
  store.apply(final('b', 'y'))
  store.reset()
  assert.equal(store.blocks.length, 0)
  assert.equal(store.hasHistory, false)
  assert.equal(seen.at(-1), 'reset')
  // usable again after reset
  store.apply(final('c', 'z'))
  assert.equal(store.blocks.length, 1)
  assert.equal(store.blocks[0].sid, 'c')
})

test('no history means no divider boundary (hasHistory false)', () => {
  const store = createStore()
  store.apply(final('a', 'only live'))
  assert.equal(store.hasHistory, false)
})
