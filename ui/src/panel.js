/**
 * The transcript panel: a draggable, resizable overlay in a shadow root.
 *
 * CSP discipline (same rules that keep the waveforms bookmarklet alive on
 * hardened pages): no innerHTML anywhere, styles through adoptedStyleSheets,
 * everything built with createElement. The panel must keep working when any
 * Talk internals are missing — it then shows session ids and a status hint
 * instead of failing.
 */

const POSITION_KEY = 'cassini-live-panel-pos'
const FOLLOW_SLACK_PX = 48

const CSS = `
:host {
  all: initial;
  position: fixed;
  top: var(--clp-top, 72px);
  right: var(--clp-right, 16px);
  left: var(--clp-left, auto);
  z-index: 2147483000;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Ubuntu, Cantarell, sans-serif;
  color-scheme: light dark;
}
.panel {
  --bg: #ffffff;
  --fg: #222222;
  --muted: #767676;
  --border: #dadada;
  --accent: #0082c9;
  --accent-soft: rgba(0, 130, 201, 0.08);
  --chip-bg: #f0f0f0;
  display: flex;
  flex-direction: column;
  width: 340px;
  height: 420px;
  min-width: 260px;
  min-height: 200px;
  max-width: 90vw;
  max-height: 85vh;
  resize: both;
  overflow: hidden;
  background: var(--bg);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.25);
  font-size: 13px;
  line-height: 1.45;
}
.panel.dark {
  --bg: #1b1b1b;
  --fg: #e6e6e6;
  --muted: #9e9e9e;
  --border: #3c3c3c;
  --accent: #4db1e8;
  --accent-soft: rgba(77, 177, 232, 0.12);
  --chip-bg: #2c2c2c;
}
@media (prefers-color-scheme: dark) {
  .panel.auto {
    --bg: #1b1b1b;
    --fg: #e6e6e6;
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
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  cursor: grab;
  user-select: none;
  flex: none;
}
.header:active { cursor: grabbing; }
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #b0b0b0;
  flex: none;
}
.dot.attached { background: #2d7b41; }
.title { font-weight: 600; flex: 1 1 auto; min-width: 0; }
.ai-label {
  font-size: 11px;
  color: var(--muted);
  flex: none;
}
.close {
  all: unset;
  cursor: pointer;
  color: var(--muted);
  font-size: 15px;
  line-height: 1;
  padding: 2px 4px;
  border-radius: 4px;
}
.close:hover { background: var(--chip-bg); color: var(--fg); }
.close:focus-visible { outline: 2px solid var(--accent); }
.scroll {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 8px 10px;
  overscroll-behavior: contain;
}
.hint {
  color: var(--muted);
  padding: 12px 4px;
  text-align: center;
}
.marker {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-size: 11px;
  margin: 2px 0 8px;
}
.marker::before, .marker::after {
  content: "";
  flex: 1;
  border-top: 1px solid var(--border);
}
.block { margin: 0 0 10px; border-radius: 6px; padding: 2px 6px; }
.block.own { background: var(--accent-soft); }
.block-header {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 1px;
}
.speaker { font-weight: 600; font-size: 12px; }
.block.own .speaker { color: var(--accent); }
.you-chip {
  font-size: 10px;
  color: var(--accent);
  border: 1px solid var(--accent);
  border-radius: 7px;
  padding: 0 5px;
}
.time {
  font-size: 10px;
  color: var(--muted);
  margin-left: auto;
  opacity: 0;
  transition: opacity 0.15s;
}
.block:hover .time { opacity: 1; }
@media (prefers-reduced-motion: reduce) {
  .time { transition: none; }
}
.line { margin: 0; }
.partial { color: var(--muted); font-style: italic; }
.partial::after { content: "▏"; opacity: 0.7; }
.jump {
  all: unset;
  position: absolute;
  bottom: 10px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--accent);
  color: #ffffff;
  font-size: 12px;
  padding: 4px 12px;
  border-radius: 12px;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}
.jump:focus-visible { outline: 2px solid var(--fg); }
.body { position: relative; flex: 1 1 auto; display: flex; min-height: 0; }
.body .scroll { flex: 1 1 auto; }
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
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

export function createPanel({ store, names, onClose }) {
  const host = el('div')
  host.id = 'cassini-live-panel'
  const shadow = host.attachShadow({ mode: 'open' })

  try {
    const sheet = new CSSStyleSheet()
    sheet.replaceSync(CSS)
    shadow.adoptedStyleSheets = [sheet]
  } catch {
    const style = document.createElement('style')
    style.textContent = CSS
    shadow.appendChild(style)
  }

  const panel = el('div', 'panel auto')
  const dark = detectNextcloudDark()
  if (dark === true) panel.className = 'panel dark'
  if (dark === false) panel.className = 'panel'

  // Header
  const header = el('div', 'header')
  const dot = el('span', 'dot')
  const title = el('span', 'title', 'Live transcript')
  const aiLabel = el('span', 'ai-label', 'AI-generated')
  const close = el('button', 'close', '✕')
  close.setAttribute('aria-label', 'Close transcript panel')
  close.addEventListener('click', (event) => {
    event.stopPropagation()
    onClose()
  })
  header.append(dot, title, aiLabel, close)

  // Body
  const body = el('div', 'body')
  const scroll = el('div', 'scroll')
  scroll.setAttribute('role', 'log')
  scroll.setAttribute('aria-label', 'Live transcript')
  const hint = el('div', 'hint', 'Waiting for a Talk call on this page…')
  const marker = el('div', 'marker', 'Transcript started here')
  marker.hidden = true
  const blocksHostEl = el('div')
  scroll.append(hint, marker, blocksHostEl)
  const jump = el('button', 'jump', '↓ Jump to live')
  jump.hidden = true
  const liveRegion = el('div', 'sr-only')
  liveRegion.setAttribute('aria-live', 'polite')
  body.append(scroll, jump, liveRegion)

  panel.append(header, body)
  shadow.appendChild(panel)

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
    if (atBottom()) {
      following = true
      jump.hidden = true
    } else {
      following = false
      jump.hidden = false
    }
  })

  jump.addEventListener('click', () => {
    following = true
    jump.hidden = true
    scrollToBottom()
  })

  // --- Rendering -------------------------------------------------------------
  const blockEls = new Map() // block.id -> { root, lines, partialEl, speakerEl }

  function renderSpeaker(entry, block) {
    const resolved = names.resolve(block.sid)
    entry.speakerEl.textContent = resolved.name
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
    const entry = { root, linesEl, partialEl, speakerEl, youChip, lineCount: 0 }
    blockEls.set(block.id, entry)
    blocksHostEl.appendChild(root)
    renderSpeaker(entry, block)
    hint.hidden = true
    marker.hidden = false
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

  const unsubscribe = store.subscribe((change) => {
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

  // --- Status ----------------------------------------------------------------
  function setStatus(status) {
    dot.classList.toggle('attached', status.state === 'attached')
    if (store.blocks.length) return
    hint.textContent =
      status.state === 'attached'
        ? 'Connected. Turn on captions in the call (⋯ → closed captions) to see the transcript. Lines appear here for everyone once Cassini Live starts listening.'
        : 'Waiting for a Talk call on this page — join the call, then turn on captions.'
  }

  // --- Dragging + persistence ------------------------------------------------
  function loadPosition() {
    try {
      const raw = localStorage.getItem(POSITION_KEY)
      if (!raw) return
      const pos = JSON.parse(raw)
      if (typeof pos.left === 'number' && typeof pos.top === 'number') {
        applyPosition(pos.left, pos.top)
      }
    } catch {
      /* per-viewer convenience only */
    }
  }

  function applyPosition(left, top) {
    const clampedLeft = Math.min(Math.max(left, 0), Math.max(window.innerWidth - 80, 0))
    const clampedTop = Math.min(Math.max(top, 0), Math.max(window.innerHeight - 40, 0))
    host.style.setProperty('--clp-left', `${clampedLeft}px`)
    host.style.setProperty('--clp-right', 'auto')
    host.style.setProperty('--clp-top', `${clampedTop}px`)
  }

  function savePosition(left, top) {
    try {
      localStorage.setItem(POSITION_KEY, JSON.stringify({ left, top }))
    } catch {
      /* ignore */
    }
  }

  let drag = null
  header.addEventListener('pointerdown', (event) => {
    if (event.target === close) return
    const rect = host.getBoundingClientRect()
    drag = { dx: event.clientX - rect.left, dy: event.clientY - rect.top }
    try {
      header.setPointerCapture(event.pointerId)
    } catch {
      /* optional */
    }
  })
  header.addEventListener('pointermove', (event) => {
    if (!drag) return
    applyPosition(event.clientX - drag.dx, event.clientY - drag.dy)
  })
  header.addEventListener('pointerup', (event) => {
    if (!drag) return
    savePosition(event.clientX - drag.dx, event.clientY - drag.dy)
    drag = null
  })

  loadPosition()
  ;(document.body || document.documentElement).appendChild(host)

  function destroy() {
    unsubscribe()
    host.remove()
    blockEls.clear()
  }

  return { destroy, setStatus, host, shadow }
}
