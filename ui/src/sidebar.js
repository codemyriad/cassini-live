/**
 * Integrates the transcript view into Talk's right sidebar as an extra tab,
 * next to Chat / Participants / Shared items, instead of a floating window.
 *
 * Talk's sidebar is Vue-owned and re-renders, and its tab classes are
 * build-hashed (e.g. `_sidebarTabsButton_selected_3SBK3`), so nothing here is
 * hardcoded to a class name: we clone a live native tab button's className for
 * the selected / unselected look, and a reconcile loop re-injects our nodes
 * whenever Vue wipes them. Our content is shown as an overlay filling the tab
 * content area, which sidesteps Talk's tabpanel visibility state machine — we
 * only have to show/hide our own overlay.
 *
 * While mounted we also hide Talk's native caption overlay (`.transcript`):
 * the sidebar replaces it. CSP-safe throughout (direct `.style`/`createElement`,
 * no innerHTML, no injected <style> tags).
 */

import { createTranscriptView } from './panel.js'

const TAB_ID = 'cassini-live-tab'
const PANEL_ID = 'cassini-live-tabpanel'
const RECONCILE_MS = 500

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
    const btn = el('button')
    btn.id = TAB_ID
    btn.type = 'button'
    btn.setAttribute('role', 'tab')
    btn.setAttribute('aria-controls', PANEL_ID)
    btn.setAttribute('aria-label', 'Live transcription')
    btn.title = 'Live transcription'
    btn.dataset.cassiniLiveTab = '1'
    const icon = el('span', undefined, '📝')
    icon.style.fontSize = '18px'
    icon.style.lineHeight = '1'
    btn.appendChild(icon)
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

  function tryOpenSidebar() {
    if (sidebar()) return
    const openers = [
      'button[aria-label*="conversation information" i]',
      'button[aria-label*="open sidebar" i]',
      'button[aria-label*="show details" i]',
    ]
    for (const sel of openers) {
      const btn = document.querySelector(sel)
      if (btn) {
        btn.click()
        return
      }
    }
  }

  // --- reconcile loop --------------------------------------------------------
  const timer = setInterval(() => {
    if (destroyed) return
    suppressNativeCaptions()
    if (!sidebar()) {
      tryOpenSidebar()
      return
    }
    const injected = ensureInjected()
    if (injected && !autoSelected) {
      autoSelected = true
      select() // land on our tab the first time, so captions are visible at once
    }
    if (active) paintNativeTabs()
  }, RECONCILE_MS)

  document.addEventListener('click', onDocClick, true)

  // kick off immediately as well as on the interval
  tryOpenSidebar()
  ensureInjected()
  if (sidebar() && !autoSelected) {
    autoSelected = true
    select()
  }

  function setStatus(status) {
    lastStatus = status
    if (view) view.setStatus(status)
  }

  function destroy() {
    destroyed = true
    clearInterval(timer)
    document.removeEventListener('click', onDocClick, true)
    restoreNativeCaptions()
    if (view) view.destroy()
    if (tabButton) tabButton.remove()
    if (overlay) overlay.remove()
    tabButton = overlay = view = null
  }

  return { setStatus, destroy }
}
