/**
 * Transcript tap: subscribes to Talk's SimpleWebRTC 'transcript' events.
 *
 * Talk's own caption overlay filters these events through per-participant
 * models, which drops the viewer's own captions. The raw emitter sees every
 * caption message the HPB delivers to this session (validated against
 * spreed's src/utils/webrtc/simplewebrtc/simplewebrtc.js — the 'transcript'
 * emit happens before any filtering), so the panel renders own lines too.
 *
 * The global appears only once the user is in a call, so the tap polls for
 * it and reports its status transitions to the caller.
 */

const POLL_INTERVAL_MS = 1000

export function createTap({ onEvent, onStatus }) {
  let webrtc = null
  let pollTimer = null
  let stopped = false

  const handler = (data) => {
    if (!data || typeof data !== 'object') return
    onEvent({
      sid: typeof data.id === 'string' ? data.id : '',
      text: typeof data.message === 'string' ? data.message : '',
      final: data.final !== false,
      lang: typeof data.languageId === 'string' ? data.languageId : null,
    })
  }

  function locate() {
    try {
      const candidate = window.OCA?.Talk?.SimpleWebRTC
      if (candidate && typeof candidate.on === 'function') return candidate
    } catch {
      // Defensive: a Talk refactor may turn any of these into throwing getters.
    }
    return null
  }

  function attach() {
    if (stopped || webrtc) return
    const candidate = locate()
    if (!candidate) {
      onStatus({ state: 'waiting' })
      pollTimer = setTimeout(attach, POLL_INTERVAL_MS)
      return
    }
    webrtc = candidate
    webrtc.on('transcript', handler)
    onStatus({ state: 'attached' })
    watch()
  }

  // Talk replaces the SimpleWebRTC instance when the user rejoins a call;
  // keep following the live one.
  function watch() {
    if (stopped) return
    pollTimer = setTimeout(() => {
      if (stopped) return
      if (locate() !== webrtc) {
        if (webrtc && typeof webrtc.off === 'function') {
          webrtc.off('transcript', handler)
        }
        webrtc = null
        attach()
      } else {
        watch()
      }
    }, POLL_INTERVAL_MS * 5)
  }

  function ownSessionId() {
    try {
      return webrtc?.connection?.getSessionId?.() || null
    } catch {
      return null
    }
  }

  function destroy() {
    stopped = true
    if (pollTimer) clearTimeout(pollTimer)
    if (webrtc && typeof webrtc.off === 'function') {
      webrtc.off('transcript', handler)
    }
    webrtc = null
  }

  attach()

  return {
    destroy,
    ownSessionId,
    get attached() {
      return webrtc !== null
    },
    get webrtc() {
      return webrtc
    },
  }
}
