/**
 * Integrates the transcript view into Talk's right sidebar as an extra tab,
 * next to Chat / Participants / Shared items, instead of a floating window.
 *
 * Reactive to Talk: we inject only while Talk itself renders the sidebar on a
 * call page, and remove our tab the moment it is gone (sidebar closed, or the
 * user navigated away from the call) — the pane follows Talk's own rendering.
 *
 * Talk's sidebar is Vue-owned and re-renders, and its tab markup is build-hashed
 * (e.g. `_sidebarTabsButton__name_ZY51V`), so nothing here is hardcoded to a
 * class name: we CLONE a live native tab button for pixel-matched styling, swap
 * its glyph for a captions icon and its label for "Live transcription", and a
 * reconcile loop re-injects whenever Vue wipes our nodes. Content shows as an
 * overlay filling the tab content area, sidestepping Talk's tabpanel visibility
 * state machine — we only show/hide our own overlay.
 *
 * While mounted we also hide Talk's native caption overlay (`.transcript`):
 * the sidebar replaces it. CSP-safe throughout (direct `.style`/`createElement`,
 * no innerHTML, no injected <style> tags).
 */

import { createTranscriptView } from './panel.js'

const TAB_ID = 'cassini-live-tab'
const PANEL_ID = 'cassini-live-tabpanel'
const RECONCILE_MS = 500
// mdiSubtitlesOutline — a captions glyph for the tab.
const CAPTIONS_ICON =
  'M20,4A2,2 0 0,1 22,6V18A2,2 0 0,1 20,20H4A2,2 0 0,1 2,18V6A2,2 0 0,1 4,4H20M20,' +
  '18V6H4V18H20M6,10H8V12H6V10M6,14H14V16H6V14M16,14H18V16H16V14M10,10H18V12H10V10Z'

function el(tag, className, text) {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

export function createSidebarPanel({ store, names }) {
  let destroyed = false
  let tabButton = null
  let overlay = null
  let view = null
  let active = false
  let autoSelected = false
  let selectedClassName = null // cached from a live native selected tab button
  let unselectedClassName = null
  let lastStatus = { state: 'waiting' }
  const suppressed = new Set() // native caption nodes we hid, with their old display

  const sidebar = () => document.querySelector('#app-sidebar-vue')
  const nav = () => document.querySelector('#app-sidebar-vue .app-sidebar-tabs__nav')
  const content = () => document.querySelector('#app-sidebar-vue .app-sidebar-tabs__content')
  const nativeTabButtons = () =>
    [...document.querySelectorAll('#app-sidebar-vue .app-sidebar-tabs__nav button[role="tab"]')].filter(
      (b) => b !== tabButton,
    )

  function cacheTabClasses() {
    for (const b of nativeTabButtons()) {
      const selected = b.getAttribute('aria-selected') === 'true'
      if (selected && !selectedClassName) selectedClassName = b.className
      if (!selected && !unselectedClassName) unselectedClassName = b.className
    }
  }

  function paintButton() {
    if (!tabButton) return
    cacheTabClasses()
    const cls = active ? selectedClassName : unselectedClassName
    if (cls) tabButton.className = cls
    tabButton.setAttribute('aria-selected', active ? 'true' : 'false')
  }

  // While our tab is active, make the native tabs look unselected; restore when
  // we step aside. Talk may re-assert its own selection, which the reconcile
  // loop reconverges.
  function paintNativeTabs() {
    if (!active) return
    for (const b of nativeTabButtons()) {
      if (b.getAttribute('aria-selected') === 'true') {
        if (!selectedClassName) selectedClassName = b.className
        b.setAttribute('aria-selected', 'false')
        if (unselectedClassName) b.className = unselectedClassName
      }
    }
  }

  function buildTabButton() {
    const native = nativeTabButtons()[0]
    let btn
    if (native) {
      // Clone a real tab for exact styling, then make it ours: captions glyph,
      // "Live transcription" label. cloneNode copies no event listeners.
      btn = native.cloneNode(true)
      btn.removeAttribute('id')
      btn.removeAttribute('tabindex')
      for (const path of btn.querySelectorAll('svg path')) path.setAttribute('d', CAPTIONS_ICON)
      const label = btn.querySelector('[class*="__name" i]')
      if (label) label.textContent = 'Live transcription'
      else btn.textContent = 'Live transcription'
    } else {
      // Fallback when there is no native tab to clone.
      btn = el('button', undefined, 'Live transcription')
    }
    btn.id = TAB_ID
    btn.type = 'button'
    btn.setAttribute('role', 'tab')
    btn.setAttribute('aria-controls', PANEL_ID)
    btn.setAttribute('aria-label', 'Live transcription')
    btn.title = 'Live transcription'
    btn.dataset.cassiniLiveTab = '1'
    btn.addEventListener('click', (e) => {
      e.preventDefault()
      e.stopPropagation()
      select()
    })
    return btn
  }

  function buildOverlay() {
    const box = el('div')
    box.id = PANEL_ID
    box.style.position = 'absolute'
    box.style.inset = '0'
    box.style.zIndex = '10'
    box.style.display = 'none'
    box.style.padding = '0 10px'
    box.style.boxSizing = 'border-box'
    box.style.background = 'var(--color-main-background, #fff)'
    box.style.overflow = 'hidden'
    const shadowHost = el('div')
    shadowHost.style.height = '100%'
    const shadow = shadowHost.attachShadow({ mode: 'open' })
    box.appendChild(shadowHost)
    view = createTranscriptView({ store, names, shadow })
    view.setStatus(lastStatus)
    return box
  }

  function ensureInjected() {
    const navEl = nav()
    const contentEl = content()
    if (!navEl || !contentEl) return false

    if (!tabButton || !tabButton.isConnected) {
      tabButton = buildTabButton()
      navEl.appendChild(tabButton)
    }
    if (!overlay || !overlay.isConnected) {
      // content must be a positioning context for our absolute overlay
      const pos = getComputedStyle(contentEl).position
      if (pos === 'static') contentEl.style.position = 'relative'
      overlay = buildOverlay()
      contentEl.appendChild(overlay)
      overlay.style.display = active ? 'block' : 'none'
    }
    paintButton()
    return true
  }

  function select() {
    active = true
    if (overlay) overlay.style.display = 'block'
    paintButton()
    paintNativeTabs()
  }

  function deselect() {
    active = false
    if (overlay) overlay.style.display = 'none'
    paintButton()
  }

  // Detect the user clicking a native tab: capture-phase, document-level, so it
  // survives the nav re-rendering. Anything that is a native tab button (or
  // inside one) steps our overlay aside.
  function onDocClick(e) {
    if (!active) return
    const path = e.composedPath ? e.composedPath() : [e.target]
    for (const node of path) {
      if (node === tabButton) return
      if (
        node instanceof HTMLElement &&
        node.getAttribute &&
        node.getAttribute('role') === 'tab' &&
        node.closest('#app-sidebar-vue .app-sidebar-tabs__nav')
      ) {
        deselect()
        return
      }
    }
  }

  // --- native caption overlay suppression ----------------------------------
  function suppressNativeCaptions() {
    for (const node of document.querySelectorAll('.transcript')) {
      if (!(node instanceof HTMLElement)) continue
      if (overlay && overlay.contains(node)) continue
      if (node.dataset.cassiniLiveHidden) continue
      node.dataset.cassiniLiveHidden = '1'
      node.dataset.cassiniLivePrevDisplay = node.style.display
      node.style.display = 'none'
      suppressed.add(node)
    }
  }

  function restoreNativeCaptions() {
    for (const node of suppressed) {
      if (node.dataset.cassiniLiveHidden) {
        node.style.display = node.dataset.cassiniLivePrevDisplay || ''
        delete node.dataset.cassiniLiveHidden
        delete node.dataset.cassiniLivePrevDisplay
      }
    }
    suppressed.clear()
  }

  // On a Talk call page? Our pane exists only where Talk renders the sidebar for
  // a call, and vanishes when the user navigates away.
  function onCallPage() {
    try {
      return /\/call\/[^/]+/.test(location.pathname) && Boolean(document.querySelector('#app-content-vue'))
    } catch {
      return false
    }
  }

  // Take our tab and overlay back out and restore Talk's native captions, e.g.
  // when the sidebar is closed or the user leaves the call. The view and store
  // live on, so returning to a call re-renders from the current transcript.
  function removeInjected() {
    restoreNativeCaptions()
    if (view) {
      view.destroy()
      view = null
    }
    if (tabButton) tabButton.remove()
    if (overlay) overlay.remove()
    tabButton = overlay = null
    autoSelected = false
  }

  // --- reconcile loop --------------------------------------------------------
  const timer = setInterval(reconcile, RECONCILE_MS)

  function reconcile() {
    if (destroyed) return
    // Only present while Talk shows the sidebar on a call page.
    if (!onCallPage() || !nav() || !content()) {
      if (tabButton || overlay) removeInjected()
      return
    }
    suppressNativeCaptions()
    const injected = ensureInjected()
    if (injected && !autoSelected) {
      autoSelected = true
      select() // land on our tab the first time, so the transcript is visible at once
    }
    if (active) paintNativeTabs()
  }

  document.addEventListener('click', onDocClick, true)
  reconcile() // also run immediately, not only on the interval

  function setStatus(status) {
    lastStatus = status
    if (view) view.setStatus(status)
  }

  function destroy() {
    destroyed = true
    clearInterval(timer)
    document.removeEventListener('click', onDocClick, true)
    removeInjected()
  }

  return { setStatus, destroy }
}
