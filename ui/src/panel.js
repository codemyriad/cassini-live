/**
 * The transcript view: renders the caption store into a scrollable,
 * speaker-grouped list inside a shadow root the caller supplies (the sidebar
 * tab panel owns the shadow root; see sidebar.js).
 *
 * CSP discipline (same rules that keep the waveforms bookmarklet alive on
 * hardened pages): no innerHTML anywhere, styles through adoptedStyleSheets,
 * everything built with createElement. It keeps working when Talk internals
 * are missing — it then shows session ids and a status hint instead of failing.
 */

const FOLLOW_SLACK_PX = 48
const RERESOLVE_MS = 2000 // re-check names, so late roster fills replace short ids

const CSS = `
:host {
  display: block;
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Ubuntu, Cantarell, sans-serif;
  color-scheme: light dark;
}
.view {
  --bg: transparent;
  --fg: inherit;
  --muted: #767676;
  --border: #dadada;
  --accent: #0082c9;
  --accent-soft: rgba(0, 130, 201, 0.08);
  --chip-bg: #f0f0f0;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  color: var(--fg);
  font-size: 13px;
  line-height: 1.45;
}
.view.dark {
  --muted: #9e9e9e;
  --border: #3c3c3c;
  --accent: #4db1e8;
  --accent-soft: rgba(77, 177, 232, 0.12);
  --chip-bg: #2c2c2c;
}
@media (prefers-color-scheme: dark) {
  .view.auto {
    --muted: #9e9e9e;
    --border: #3c3c3c;
    --accent: #4db1e8;
    --accent-soft: rgba(77, 177, 232, 0.12);
    --chip-bg: #2c2c2c;
  }
}
.header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 2px;
  border-bottom: 1px solid var(--border);
  flex: none;
}
.dot { width: 8px; height: 8px; border-radius: 50%; background: #b0b0b0; flex: none; }
.dot.attached { background: #2d7b41; }
.title { font-weight: 600; flex: 1 1 auto; min-width: 0; }
.ai-label { font-size: 11px; color: var(--muted); flex: none; }
.body { position: relative; flex: 1 1 auto; display: flex; min-height: 0; }
.scroll { flex: 1 1 auto; overflow-y: auto; padding: 8px 2px; overscroll-behavior: contain; }
.hint { color: var(--muted); padding: 12px 4px; text-align: center; }
.marker {
  display: flex; align-items: center; gap: 8px;
  color: var(--muted); font-size: 11px; margin: 2px 0 8px;
}
.marker::before, .marker::after { content: ""; flex: 1; border-top: 1px solid var(--border); }
.block { margin: 0 0 10px; border-radius: 6px; padding: 2px 6px; }
.block.own { background: var(--accent-soft); }
.block-header { display: flex; align-items: baseline; gap: 6px; margin-bottom: 1px; }
.speaker { font-weight: 600; font-size: 12px; }
.block.own .speaker { color: var(--accent); }
.you-chip {
  font-size: 10px; color: var(--accent);
  border: 1px solid var(--accent); border-radius: 7px; padding: 0 5px;
}
.time { font-size: 10px; color: var(--muted); margin-left: auto; opacity: 0; transition: opacity 0.15s; }
.block:hover .time { opacity: 1; }
@media (prefers-reduced-motion: reduce) { .time { transition: none; } }
.line { margin: 0; }
.partial { color: var(--muted); font-style: italic; }
.partial::after { content: "▏"; opacity: 0.7; }
.jump {
  all: unset; position: absolute; bottom: 10px; left: 50%; transform: translateX(-50%);
  background: var(--accent); color: #ffffff; font-size: 12px; padding: 4px 12px;
  border-radius: 12px; cursor: pointer; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}
.jump:focus-visible { outline: 2px solid currentColor; }
.sr-only {
  position: absolute; width: 1px; height: 1px; overflow: hidden;
  clip-path: inset(50%); white-space: nowrap;
}
[hidden] { display: none !important; }
`

function el(tag, className, text) {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

function formatTime(ms) {
  try {
    return new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}

function detectNextcloudDark() {
  try {
    const probe = document.body || document.documentElement
    if (!probe) return null
    if (probe.matches('[data-theme-dark], [data-theme-dark-highcontrast], .theme--dark')) return true
    if (probe.matches('[data-theme-light], .theme--light')) return false
  } catch {
    /* fall through */
  }
  return null
}

/**
 * @param {{ store, names, shadow: ShadowRoot }} opts
 * @returns {{ setStatus(status): void, destroy(): void }}
 */
export function createTranscriptView({ store, names, shadow }) {
  try {
    const sheet = new CSSStyleSheet()
    sheet.replaceSync(CSS)
    shadow.adoptedStyleSheets = [...shadow.adoptedStyleSheets, sheet]
  } catch {
    const style = document.createElement('style')
    style.textContent = CSS
    shadow.appendChild(style)
  }

  const view = el('div', 'view auto')
  const dark = detectNextcloudDark()
  if (dark === true) view.className = 'view dark'
  if (dark === false) view.className = 'view'

  const header = el('div', 'header')
  const dot = el('span', 'dot')
  const title = el('span', 'title', 'Live transcript')
  const aiLabel = el('span', 'ai-label', 'AI-generated')
  header.append(dot, title, aiLabel)

  const body = el('div', 'body')
  const scroll = el('div', 'scroll')
  scroll.setAttribute('role', 'log')
  scroll.setAttribute('aria-label', 'Live transcript')
  const hint = el('div', 'hint', 'Waiting for a Talk call on this page…')
  // "You joined here" divider, inserted into the block flow before the first
  // live block when the store holds any history (see addBlock).
  const dividerEl = el('div', 'marker', 'You joined here')
  let dividerPlaced = false
  const blocksHostEl = el('div')
  scroll.append(hint, blocksHostEl)
  const jump = el('button', 'jump', '↓ Jump to live')
  jump.hidden = true
  const liveRegion = el('div', 'sr-only')
  liveRegion.setAttribute('aria-live', 'polite')
  body.append(scroll, jump, liveRegion)

  view.append(header, body)
  shadow.appendChild(view)

  // --- Follow / scroll behaviour -------------------------------------------
  let following = true
  const reducedMotion = (() => {
    try {
      return window.matchMedia('(prefers-reduced-motion: reduce)').matches
    } catch {
      return false
    }
  })()

  function atBottom() {
    return scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight <= FOLLOW_SLACK_PX
  }
  function scrollToBottom() {
    scroll.scrollTo({ top: scroll.scrollHeight, behavior: reducedMotion ? 'auto' : 'smooth' })
  }
  scroll.addEventListener('scroll', () => {
    following = atBottom()
    jump.hidden = following
  })
  jump.addEventListener('click', () => {
    following = true
    jump.hidden = true
    scrollToBottom()
  })

  // --- Rendering -------------------------------------------------------------
  const blockEls = new Map() // block.id -> entry

  function renderSpeaker(entry, block) {
    const resolved = names.resolve(block.sid)
    entry.speakerEl.textContent = resolved.name
    entry.resolved = resolved.resolved
    entry.root.classList.toggle('own', resolved.own)
    entry.youChip.hidden = !resolved.own
    return resolved
  }

  function addBlock(block) {
    const root = el('div', 'block')
    const blockHeader = el('div', 'block-header')
    const speakerEl = el('span', 'speaker')
    const youChip = el('span', 'you-chip', 'you')
    youChip.hidden = true
    const time = el('span', 'time', formatTime(block.startedAt))
    time.title = new Date(block.startedAt).toLocaleString()
    blockHeader.append(speakerEl, youChip, time)
    const linesEl = el('div')
    const partialEl = el('p', 'line partial')
    partialEl.setAttribute('dir', 'auto')
    partialEl.hidden = true
    root.append(blockHeader, linesEl, partialEl)
    const entry = { root, linesEl, partialEl, speakerEl, youChip, lineCount: 0, block, resolved: false }
    blockEls.set(block.id, entry)
    // Place the "You joined here" divider before the first live block, but only
    // when there is history above it to divide from.
    if (!dividerPlaced && !block.historical && store.hasHistory) {
      blocksHostEl.appendChild(dividerEl)
      dividerPlaced = true
    }
    blocksHostEl.appendChild(root)
    renderSpeaker(entry, block)
    hint.hidden = true
    return entry
  }

  function syncBlock(block) {
    let entry = blockEls.get(block.id)
    if (!entry) entry = addBlock(block)
    const resolved = renderSpeaker(entry, block)
    while (entry.lineCount < block.lines.length) {
      const line = block.lines[entry.lineCount]
      const lineEl = el('p', 'line', line.text)
      lineEl.setAttribute('dir', 'auto')
      if (line.lang) lineEl.lang = line.lang
      entry.linesEl.appendChild(lineEl)
      entry.lineCount++
    }
    if (block.partial) {
      entry.partialEl.textContent = block.partial.text
      if (block.partial.lang) entry.partialEl.lang = block.partial.lang
      entry.partialEl.hidden = false
    } else {
      entry.partialEl.hidden = true
      entry.partialEl.textContent = ''
    }
    return resolved
  }

  function clearRendered() {
    for (const entry of blockEls.values()) entry.root.remove()
    blockEls.clear()
    dividerEl.remove()
    dividerPlaced = false
    blocksHostEl.replaceChildren()
  }

  // Render whatever the store already holds, so a tab (re)created mid-call — by
  // the first mount, or after Talk re-rendered the sidebar — shows the current
  // transcript, not only events from here on. The store is the source of truth.
  for (const block of store.blocks) syncBlock(block)

  const unsubscribe = store.subscribe((change) => {
    if (change.type === 'reset') {
      // Room/call changed: wipe the pane and show the waiting hint again.
      clearRendered()
      hint.hidden = false
      return
    }
    if (change.type === 'remove') {
      const entry = blockEls.get(change.block.id)
      if (entry) {
        entry.root.remove()
        blockEls.delete(change.block.id)
      }
      return
    }
    const resolved = syncBlock(change.block)
    if (change.type === 'final' && change.line) {
      liveRegion.textContent = `${resolved.name}: ${change.line.text}`
    }
    if (following) scrollToBottom()
  })

  // A speaker's name may not be in the roster yet when their first caption
  // lands (it is a prefix-keyed lookup). Re-resolve the still-unresolved blocks
  // on a timer so a short id is replaced by the real name once it is known.
  const reresolve = setInterval(() => {
    for (const entry of blockEls.values()) {
      if (!entry.resolved) renderSpeaker(entry, entry.block)
    }
  }, RERESOLVE_MS)

  function setStatus(status) {
    dot.classList.toggle('attached', status.state === 'attached')
    if (store.blocks.length) {
      hint.hidden = true
      return
    }
    hint.hidden = false
    hint.textContent =
      status.state === 'attached'
        ? 'Connected. Turn on captions in the call (⋯ → closed captions) to see the transcript. Lines appear here for everyone once Cassini Live starts listening.'
        : 'Waiting for a Talk call on this page — join the call, then turn on captions.'
  }

  function destroy() {
    clearInterval(reresolve)
    unsubscribe()
    blockEls.clear()
    view.remove()
  }

  return { setStatus, destroy }
}
