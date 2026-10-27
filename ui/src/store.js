/**
 * Transcript store: a single, ordered timeline merged from two sources that
 * share ONE seq space (D-830):
 *
 *  - the live HPB feed — finals carry the server's buffer `seq` (read from the
 *    raw signalling "message" event); near-instant;
 *  - the `/history` poll — the authoritative record, used to fill gaps and to
 *    deliver joins and initial catch-up.
 *
 * Both flow through a reorder buffer keyed by seq, so rendering is append-only
 * and in order, nothing is double-counted, and a live final that arrives before
 * the entries it follows is held until the gap is filled:
 *
 *   seq == applied+1 → render, advance, flush anything now contiguous
 *   seq >  applied+1 → hold (out of order / a gap the poll will fill)
 *   seq <= applied   → already have it, skip
 *
 * Items, in order:
 * - `block`: a run of consecutive FINAL lines by one speaker.
 * - `join`:  a participant join marker ("… joined").
 *
 * Partials (in-progress speech) are a separate, transient channel fed by the
 * live feed — never part of the durable timeline, cleared when the speaker's
 * final lands.
 */

let nextItemId = 1

export function createStore() {
  let items = []
  let appliedSeq = 0 // highest contiguously-applied seq; what the poll asks after
  let pending = new Map() // seq -> entry, held until the gap below it fills
  const partials = new Map() // sid -> { text, lang }
  const listeners = new Set()

  function emit(change) {
    for (const listener of listeners) listener(change, items)
  }

  function lastBlock() {
    const last = items.length ? items[items.length - 1] : null
    return last && last.kind === 'block' ? last : null
  }

  function renderFinal(entry) {
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

  function renderJoin(entry) {
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

  function render(entry) {
    if (entry.type === 'join') renderJoin(entry)
    else renderFinal(entry)
  }

  // Place one seq-tagged entry into the timeline via the reorder buffer.
  function ingest(entry) {
    const seq = entry && typeof entry.seq === 'number' ? entry.seq : null
    if (seq === null) return // only seq-tagged entries belong on the timeline
    if (seq <= appliedSeq || pending.has(seq)) return // already have it
    if (seq === appliedSeq + 1) {
      render(entry)
      appliedSeq = seq
      while (pending.has(appliedSeq + 1)) {
        const next = pending.get(appliedSeq + 1)
        pending.delete(appliedSeq + 1)
        render(next)
        appliedSeq += 1
      }
    } else {
      pending.set(seq, entry)
    }
  }

  // A single live final from the HPB feed (held if it arrives before its gap).
  function applyLiveFinal(entry) {
    ingest(entry)
  }

  // The authoritative poll delta (oldest-first). It establishes what the server
  // still holds: if its smallest seq is past the gap we are waiting on, those
  // entries were evicted and never coming, so step over them.
  function applyPoll(entries) {
    if (!Array.isArray(entries) || entries.length === 0) return
    const first = entries.find((e) => typeof e.seq === 'number')
    if (first && first.seq > appliedSeq + 1) {
      appliedSeq = first.seq - 1
      for (const seq of [...pending.keys()]) if (seq <= appliedSeq) pending.delete(seq)
    }
    for (const entry of entries) ingest(entry)
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
    appliedSeq = 0
    pending = new Map()
    partials.clear()
    emit({ type: 'reset' })
  }

  return {
    applyLiveFinal,
    applyPoll,
    setPartial,
    reset,
    get items() {
      return items
    },
    // The last CONTIGUOUS seq — what the poll should ask after, so a gap is
    // re-requested rather than skipped.
    get confirmedSeq() {
      return appliedSeq
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
