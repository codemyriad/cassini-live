/**
 * Entry point. The bundle is executed either as a bookmarklet (the whole
 * bundle inside a javascript: URL) or as a classic script served by the
 * cassini-live ExApp through the AppAPI proxy. Both paths run this once per
 * click/load:
 *
 *   - no panel on the page → mount one
 *   - panel already mounted → tear it down (the bookmarklet is its own off
 *     switch, like the waveforms one)
 *
 * The public API lives on window.__CASSINI_LIVE_PANEL__ so tests, the
 * install page and curious people can drive it:
 *
 *   __CASSINI_LIVE_PANEL__.destroy()               tear down
 *   __CASSINI_LIVE_PANEL__.injectTranscript(evt)   feed one event through the
 *     real pipeline: { id, message, final, languageId } (hook payload shape)
 */

import { createStore } from './store.js'
import { createTap } from './tap.js'
import { createNames } from './names.js'
import { createSession } from './session.js'
import { fetchHistory } from './history.js'
import { createSidebarPanel } from './sidebar.js'

const GLOBAL = '__CASSINI_LIVE_PANEL__'

function mount() {
  const store = createStore()

  let panel = null
  let lastStatus = { state: 'waiting' }
  const tap = createTap({
    onEvent: (event) => store.apply(event),
    onStatus: (status) => {
      lastStatus = status
      if (panel) panel.setStatus(status)
    },
  })
  const names = createNames({ tap })
  // Scope the transcript per conversation/call and seed catch-up history.
  const session = createSession({ tap, store, fetchHistory })

  function destroy() {
    session.destroy()
    tap.destroy()
    names.destroy()
    if (panel) panel.destroy()
    try {
      delete window[GLOBAL]
    } catch {
      window[GLOBAL] = undefined
    }
  }

  panel = createSidebarPanel({ store, names })
  panel.setStatus(lastStatus)

  window[GLOBAL] = {
    destroy,
    injectTranscript(data) {
      store.apply({
        sid: typeof data.id === 'string' ? data.id : '',
        text: typeof data.message === 'string' ? data.message : '',
        final: data.final !== false,
        lang: typeof data.languageId === 'string' ? data.languageId : null,
      })
    },
    get attached() {
      return tap.attached
    },
    version: typeof __CASSINI_LIVE_UI_VERSION__ !== 'undefined' ? __CASSINI_LIVE_UI_VERSION__ : 'dev',
  }
}

const existing = window[GLOBAL]
if (existing && typeof existing.destroy === 'function') {
  // Second click is the off switch, like the waveforms bookmarklet.
  existing.destroy()
} else {
  // Mounting outside a call page is fine: the status hint in the panel says
  // what it is waiting for, which beats a click doing nothing.
  mount()
}
