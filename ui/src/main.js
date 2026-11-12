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
import { APP_ID } from './app-id.js'
import { fetchHistory } from './history.js'
import { enableCaptions } from './captions.js'
import { createSidebarPanel } from './sidebar.js'

const GLOBAL = '__CASSINI_LIVE_PANEL__'

function mount() {
  const store = createStore()

  let panel = null
  let lastStatus = { state: 'waiting' }
  const tap = createTap({
    // The live HPB feed: finals carry the server seq and merge with the poll;
    // partials are transient liveness.
    onFinal: (entry) => store.applyLiveFinal(entry),
    onPartial: (p) => store.setPartial(p.sid, p.text, p.lang),
    onStatus: (status) => {
      lastStatus = status
      if (panel) panel.setStatus(status)
    },
  })
  const names = createNames({ tap })
  // Declared before createSession: its first watch() may start a call at once.
  let captionRun = 0
  let destroyed = false
  // Scope per conversation/call, poll /history for gaps, and turn captions on
  // for this session when a call starts so it is on the HPB feed.
  const session = createSession({
    tap,
    store,
    fetchHistory: (room, since) => fetchHistory(APP_ID, room, since),
    onCallStart: (room) => startCaptions(room),
  })

  // Turn captions on for this session, retrying while the same call is on: Talk may
  // not have recorded the join yet (403), Cassini Live may still be connecting to
  // Talk or loading its engine (503). The panel shows the server's reason meanwhile.
  async function startCaptions(room) {
    const run = ++captionRun
    const call = tap.callToken()
    let delay = 2000
    for (;;) {
      if (destroyed || run !== captionRun || tap.roomToken() !== room || tap.callToken() !== call) return
      const result = await enableCaptions(APP_ID, room, tap.nextcloudSessionId())
      if (destroyed || run !== captionRun) return
      if (panel) panel.setCaptionProblem(result.ok ? null : result.message, result.retry)
      if (result.ok || !result.retry) return
      await new Promise((resolve) => setTimeout(resolve, result.retryAfterMs ?? delay))
      delay = Math.min(delay * 2, 30000)
    }
  }

  function destroy() {
    destroyed = true
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

  panel = createSidebarPanel({ store, names, appId: APP_ID })
  panel.setStatus(lastStatus)

  window[GLOBAL] = {
    destroy,
    // Feed one live partial (final text comes from the polled server timeline).
    injectPartial(data) {
      store.setPartial(
        typeof data.id === 'string' ? data.id : '',
        typeof data.message === 'string' ? data.message : '',
        typeof data.languageId === 'string' ? data.languageId : null,
      )
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
