/**
 * Transcript store: a single, ordered timeline built from the server's polled
 * history (D-830). The server is the source of truth — it transcribes every
 * speaker in the call (not only those who enabled captions) and records who
 * joined when, each entry tagged with a monotonic `seq`. The store applies
 * those entries idempotently, so a poll that re-sends earlier entries is a
 * no-op and nothing is dropped between joining and enabling captions.
 *
 * Items, in order:
 * - `block`: a run of consecutive FINAL lines by one speaker.
 * - `join`:  a participant join marker ("… joined").
 *
 * Partials (in-progress speech) are a separate, transient channel fed by the
 * live caption hook when the viewer has captions on — they are never part of
 * the durable timeline and clear when the final for that speaker lands.
 */

let nextItemId = 1

export function createStore() {
  let items = []
  let lastSeq = 0
  const partials = new Map() // sid -> { text, lang }
  const listeners = new Set()

  function emit(change) {
    for (const listener of listeners) listener(change, items)
  }

  function lastBlock() {
    const last = items.length ? items[items.length - 1] : null
    return last && last.kind === 'block' ? last : null
  }

  function applyFinal(entry) {
    const sid = entry.speakerSessionId
    const text = entry.message
    if (typeof sid !== 'string' || sid === '' || typeof text !== 'string' || text === '') return
    const at = typeof entry.t === 'number' ? entry.t : Date.now()
    const lang = typeof entry.langId === 'string' ? entry.langId : null
    let block = lastBlock()
    if (!block || block.sid !== sid) {
      block = { id: nextItemId++, kind: 'block', sid, startedAt: at, lines: [] }
      items.push(block)
      emit({ type: 'add', item: block })
    }
    block.lines.push({ text, lang, at })
    emit({ type: 'final', item: block, line: block.lines[block.lines.length - 1] })
    // A committed final for this speaker supersedes their in-progress partial.
    if (partials.has(sid)) {
      partials.delete(sid)
      emit({ type: 'partial', sid })
    }
  }

  function applyJoin(entry) {
    const item = {
      id: nextItemId++,
      kind: 'join',
      sid: typeof entry.speakerSessionId === 'string' ? entry.speakerSessionId : '',
      displayName: typeof entry.displayName === 'string' ? entry.displayName : null,
      userId: typeof entry.userId === 'string' ? entry.userId : null,
      at: typeof entry.t === 'number' ? entry.t : Date.now(),
    }
    items.push(item)
    emit({ type: 'add', item })
  }

  // Apply a batch of server timeline entries (each with a `seq`), skipping any
  // at or below the last seq we have seen. Entries are assumed oldest-first.
  function applyTimeline(entries) {
    if (!Array.isArray(entries)) return
    for (const entry of entries) {
      const seq = entry && typeof entry.seq === 'number' ? entry.seq : null
      if (seq !== null && seq <= lastSeq) continue
      if (entry.type === 'join') applyJoin(entry)
      else applyFinal(entry)
      if (seq !== null) lastSeq = seq
    }
  }

  // In-progress speech for a speaker (from the live hook). Empty text clears it.
  function setPartial(sid, text, lang) {
    if (typeof sid !== 'string' || sid === '') return
    if (typeof text !== 'string' || text === '') {
      if (partials.delete(sid)) emit({ type: 'partial', sid })
      return
    }
    partials.set(sid, { text, lang: typeof lang === 'string' ? lang : null })
    emit({ type: 'partial', sid })
  }

  function reset() {
    items = []
    lastSeq = 0
    partials.clear()
    emit({ type: 'reset' })
  }

  return {
    applyTimeline,
    setPartial,
    reset,
    get items() {
      return items
    },
    get lastSeq() {
      return lastSeq
    },
    partialFor(sid) {
      return partials.get(sid) || null
    },
    get activePartials() {
      return [...partials.entries()].map(([sid, p]) => ({ sid, ...p }))
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}
