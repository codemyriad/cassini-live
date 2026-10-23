/**
 * Caption store: turns the raw transcript event stream into an append-only
 * list of speaker blocks that a panel can render incrementally.
 *
 * Input events mirror what Talk's SimpleWebRTC emits for cassini-live's
 * payload: { sid, text, final, lang }.
 *
 * Model:
 * - A block is a maximal run of consecutive FINAL lines by one speaker,
 *   plus that speaker's live partial.
 * - Finals are committed lines and never change afterwards.
 * - A partial replaces the speaker's previous partial. When another speaker
 *   has started a newer block, the partial moves to a fresh block at the end,
 *   so committed text never gets pushed around by in-progress speech.
 * - An empty partial clears the preview (the backend sends one to do that).
 * - `seedHistory` prepends blocks marked `historical` — what was said before
 *   the viewer joined (fetched from the backend). Historical blocks are closed:
 *   live finals never merge into them, so the "You joined here" divider (drawn
 *   before the first live block) stays put. `reset` clears everything when the
 *   call or conversation changes.
 */

let nextBlockId = 1

export function createStore({ now = Date.now } = {}) {
  let blocks = []
  const listeners = new Set()

  function emit(change) {
    for (const listener of listeners) listener(change, blocks)
  }

  // Only non-historical (live) blocks are "open" for appends; a historical
  // block is a frozen record of what was said before the viewer joined.
  function lastLiveBlock() {
    for (let i = blocks.length - 1; i >= 0; i--) {
      if (!blocks[i].historical) return blocks[i]
    }
    return null
  }

  function findOpenBlockFor(sid) {
    const last = lastLiveBlock()
    if (last && last.sid === sid && last === blocks[blocks.length - 1]) return last
    return null
  }

  // A speaker's live preview stays in the block where it started, even when
  // other speakers open newer blocks below it. Only a FINAL relocates the
  // speaker to the end of the list. This keeps blocks stable while several
  // people talk over each other.
  function findPartialBlockFor(sid) {
    return blocks.find((block) => !block.historical && block.sid === sid && block.partial) || null
  }

  function dropIfEmpty(block) {
    if (block.lines.length || block.partial) return
    const idx = blocks.indexOf(block)
    if (idx !== -1) {
      blocks.splice(idx, 1)
      emit({ type: 'remove', block })
    }
  }

  function clearPartialElsewhere(sid, except) {
    for (const block of [...blocks]) {
      if (block.sid === sid && block !== except && block.partial) {
        block.partial = null
        emit({ type: 'update', block })
        dropIfEmpty(block)
      }
    }
  }

  function openBlock(sid, at, historical = false) {
    const block = {
      id: nextBlockId++,
      sid,
      startedAt: at,
      lastActivityAt: at,
      lines: [],
      partial: null,
      historical,
    }
    blocks.push(block)
    emit({ type: 'add', block })
    return block
  }

  function apply(event) {
    const { sid, text, final, lang } = event
    if (typeof sid !== 'string' || sid === '') return
    const at = now()

    let block = findOpenBlockFor(sid)

    if (final) {
      if (typeof text !== 'string' || text === '') return
      if (!block) block = openBlock(sid, at)
      block.lines.push({ text, lang: lang || null, at })
      block.partial = null
      block.lastActivityAt = at
      clearPartialElsewhere(sid, block)
      emit({ type: 'final', block, line: block.lines[block.lines.length - 1] })
      return
    }

    // Partial. An empty one clears the speaker's preview wherever it lives.
    if (typeof text !== 'string' || text === '') {
      clearPartialElsewhere(sid, null)
      return
    }

    if (!block) block = findPartialBlockFor(sid)
    if (!block) block = openBlock(sid, at)
    clearPartialElsewhere(sid, block)
    block.partial = { text, lang: lang || null }
    block.lastActivityAt = at
    emit({ type: 'partial', block })
  }

  // Seed what was said before the viewer joined. Entries are the backend's
  // history shape ({ speakerSessionId, message, langId, t }), oldest first;
  // consecutive same-speaker finals group into one historical block.
  function seedHistory(entries) {
    if (!Array.isArray(entries)) return
    for (const entry of entries) {
      const sid = entry && entry.speakerSessionId
      const text = entry && entry.message
      if (typeof sid !== 'string' || sid === '' || typeof text !== 'string' || text === '') continue
      const at = typeof entry.t === 'number' ? entry.t : now()
      const lang = typeof entry.langId === 'string' ? entry.langId : null
      let block = blocks.length ? blocks[blocks.length - 1] : null
      if (!block || !block.historical || block.sid !== sid) {
        block = openBlock(sid, at, true)
      }
      block.lines.push({ text, lang, at })
      block.lastActivityAt = at
      emit({ type: 'final', block, line: block.lines[block.lines.length - 1] })
    }
  }

  function reset() {
    blocks = []
    emit({ type: 'reset' })
  }

  return {
    apply,
    seedHistory,
    reset,
    get blocks() {
      return blocks
    },
    // true once there is any historical block, so the view can place the
    // "You joined here" divider before the first live block.
    get hasHistory() {
      return blocks.some((b) => b.historical)
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}
