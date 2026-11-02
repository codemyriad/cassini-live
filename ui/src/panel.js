/**
 * The transcript view: renders the store's timeline (speaker blocks and "…
 * joined" dividers) plus a transient partials area, into a shadow root the
 * caller supplies (the sidebar tab owns it; see sidebar.js).
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
.diagnostics {
  flex: none; padding: 2px 6px; border: 1px solid var(--border); border-radius: 4px;
  color: var(--accent); font-size: 11px; text-decoration: none;
}
.diagnostics:hover { background: var(--accent-soft); }
.diagnostics:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.body { position: relative; flex: 1 1 auto; display: flex; min-height: 0; }
.scroll { flex: 1 1 auto; overflow-y: auto; padding: 8px 2px; overscroll-behavior: contain; }
.hint { color: var(--muted); padding: 12px 4px; text-align: center; }
.marker {
  display: flex; align-items: center; gap: 8px;
  color: var(--muted); font-size: 11px; margin: 6px 0 8px;
}
.marker::before, .marker::after { content: ""; flex: 1; border-top: 1px solid var(--border); }
.marker.you { color: var(--accent); }
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
.partials { flex: none; padding: 0 6px 6px; }
.partial-line { margin: 0 0 4px; color: var(--muted); font-style: italic; }
.partial-speaker { font-weight: 600; font-style: normal; margin-right: 4px; }
.partial-line::after { content: "▏"; opacity: 0.7; }
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

function currentUid() {
  try {
    return window.OC?.getCurrentUser?.()?.uid || null
  } catch {
    return null
  }
}

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
  const diagnostics = el('a', 'diagnostics', 'diagnostics')
  const diagnosticsPath = '/apps/app_api/proxy/live_transcription/diagnostics/'
  diagnostics.href = '/index.php' + diagnosticsPath
  try {
    if (typeof window.OC?.generateUrl === 'function') {
      diagnostics.href = window.OC.generateUrl(diagnosticsPath)
    }
  } catch {
    /* keep the default AppAPI proxy URL */
  }
  diagnostics.target = '_blank'
  diagnostics.rel = 'noopener noreferrer'
  diagnostics.title = 'Open diagnostics in a new tab (administrators only)'
  header.appendChild(diagnostics)

  const body = el('div', 'body')
  const scroll = el('div', 'scroll')
  scroll.setAttribute('role', 'log')
  scroll.setAttribute('aria-label', 'Live transcript')
  const hint = el('div', 'hint', 'Waiting for a Talk call on this page…')
  const itemsHostEl = el('div')
  const partialsEl = el('div', 'partials')
  scroll.append(hint, itemsHostEl, partialsEl)
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
  const itemEls = new Map() // item.id -> entry
  const uid = currentUid()

  function renderSpeaker(entry, item) {
    const resolved = names.resolve(item.sid)
    entry.speakerEl.textContent = resolved.name
    entry.resolved = resolved.resolved
    entry.root.classList.toggle('own', resolved.own)
    entry.youChip.hidden = !resolved.own
    return resolved
  }

  function addBlock(item) {
    const root = el('div', 'block')
    const blockHeader = el('div', 'block-header')
    const speakerEl = el('span', 'speaker')
    const youChip = el('span', 'you-chip', 'you')
    youChip.hidden = true
    const time = el('span', 'time', formatTime(item.startedAt))
    time.title = new Date(item.startedAt).toLocaleString()
    blockHeader.append(speakerEl, youChip, time)
    const linesEl = el('div')
    root.append(blockHeader, linesEl)
    const entry = { kind: 'block', root, linesEl, speakerEl, youChip, lineCount: 0, item, resolved: false }
    itemEls.set(item.id, entry)
    itemsHostEl.appendChild(root)
    renderSpeaker(entry, item)
    hint.hidden = true
    return entry
  }

  function joinOwn(item) {
    const resolved = names.resolve(item.sid)
    if (resolved.own) return true
    return Boolean(uid && item.userId && item.userId === uid)
  }

  function joinLabel(item) {
    if (joinOwn(item)) return 'You joined here'
    const name = item.displayName || names.resolve(item.sid).name
    return `${name} joined`
  }

  function addJoin(item) {
    const root = el('div', 'marker')
    root.textContent = joinLabel(item)
    if (joinOwn(item)) root.classList.add('you')
    const entry = { kind: 'join', root, item, resolved: Boolean(item.displayName) }
    itemEls.set(item.id, entry)
    itemsHostEl.appendChild(root)
    hint.hidden = true
    return entry
  }

  function addItem(item) {
    return item.kind === 'join' ? addJoin(item) : addBlock(item)
  }

  function syncBlock(item) {
    let entry = itemEls.get(item.id)
    if (!entry) entry = addItem(item)
    const resolved = renderSpeaker(entry, item)
    while (entry.lineCount < item.lines.length) {
      const line = item.lines[entry.lineCount]
      const lineEl = el('p', 'line', line.text)
      lineEl.setAttribute('dir', 'auto')
      if (line.lang) lineEl.lang = line.lang
      entry.linesEl.appendChild(lineEl)
      entry.lineCount++
    }
    return resolved
  }

  function renderPartials() {
    partialsEl.replaceChildren()
    for (const partial of store.activePartials) {
      const resolved = names.resolve(partial.sid)
      const line = el('p', 'partial-line')
      line.setAttribute('dir', 'auto')
      if (partial.lang) line.lang = partial.lang
      line.append(el('span', 'partial-speaker', `${resolved.name}:`), document.createTextNode(partial.text))
      partialsEl.appendChild(line)
    }
  }

  function renderItem(item) {
    if (item.kind === 'join') {
      if (!itemEls.has(item.id)) addJoin(item)
      return
    }
    syncBlock(item)
  }

  function clearRendered() {
    itemsHostEl.replaceChildren()
    partialsEl.replaceChildren()
    itemEls.clear()
  }

  // Render whatever the store already holds, so a tab (re)created mid-call shows
  // the current transcript, not only future entries.
  for (const item of store.items) renderItem(item)
  renderPartials()

  const unsubscribe = store.subscribe((change) => {
    if (change.type === 'reset') {
      clearRendered()
      hint.hidden = false
      return
    }
    if (change.type === 'partial') {
      renderPartials()
      if (following) scrollToBottom()
      return
    }
    renderItem(change.item)
    if (change.type === 'final' && change.line) {
      const resolved = names.resolve(change.item.sid)
      liveRegion.textContent = `${resolved.name}: ${change.line.text}`
    }
    if (following) scrollToBottom()
  })

  // A name may not be in the roster yet when a block or join first renders
  // (prefix-keyed lookup); re-resolve the unresolved ones so a short id is
  // replaced by the real name once it is known.
  const reresolve = setInterval(() => {
    for (const entry of itemEls.values()) {
      if (entry.resolved) continue
      if (entry.kind === 'block') renderSpeaker(entry, entry.item)
      else {
        entry.root.textContent = joinLabel(entry.item)
        entry.resolved = names.resolve(entry.item.sid).resolved
      }
    }
    renderPartials()
  }, RERESOLVE_MS)

  function setStatus(status) {
    dot.classList.toggle('attached', status.state === 'attached')
    if (store.items.length) {
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
    itemEls.clear()
    view.remove()
  }

  return { setStatus, destroy }
}
