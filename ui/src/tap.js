/**
 * Live caption feed from Talk's signalling connection.
 *
 * We subscribe to `connection.on('message', …)` rather than
 * `SimpleWebRTC.on('transcript', …)`: the filtered transcript event keeps only
 * {id, message, languageId, final} and drops cassini-live's `seq`, but the raw
 * "message" event (what Talk's own SimpleWebRTC listens to) carries the full
 * payload — including the `seq` that lets the panel merge this live feed with
 * the /history poll on one shared seq space (D-830). It also still sees the
 * viewer's own lines (no CallParticipantModel filtering).
 *
 * The connection appears only once in a call and is replaced on rejoin, so we
 * poll for it and re-attach.
 */

const POLL_INTERVAL_MS = 1000

export function createTap({ onFinal, onPartial, onStatus }) {
  let webrtc = null
  let connection = null
  let pollTimer = null
  let stopped = false

  const handler = (data) => {
    if (!data || typeof data !== 'object' || data.type !== 'transcript') return
    const sid = typeof data.speakerSessionId === 'string' ? data.speakerSessionId : ''
    const text = typeof data.message === 'string' ? data.message : ''
    const lang = typeof data.langId === 'string' ? data.langId : null
    if (data.final === false) {
      onPartial({ sid, text, lang })
    } else if (typeof data.seq === 'number') {
      onFinal({ seq: data.seq, type: 'final', speakerSessionId: sid, message: text, langId: lang })
    }
    // A final without a seq (e.g. the disabled translation path) is ignored —
    // the /history poll is the source of truth for anything the live feed drops.
  }

  function locate() {
    try {
      const w = window.OCA?.Talk?.SimpleWebRTC
      if (w && w.connection && typeof w.connection.on === 'function') return w
    } catch {
      // Defensive: a Talk refactor may turn any of these into a throwing getter.
    }
    return null
  }

  function detach() {
    if (connection && typeof connection.off === 'function') {
      connection.off('message', handler)
    }
    connection = null
  }

  function attach() {
    if (stopped) return
    const candidate = locate()
    if (!candidate) {
      detach()
      onStatus({ state: 'waiting' })
      pollTimer = setTimeout(attach, POLL_INTERVAL_MS)
      return
    }
    if (candidate !== webrtc || candidate.connection !== connection) {
      detach()
      webrtc = candidate
      connection = candidate.connection
      connection.on('message', handler)
      onStatus({ state: 'attached' })
    }
    // Keep following connection replacement (rejoin) and disappearance.
    pollTimer = setTimeout(attach, POLL_INTERVAL_MS)
  }

  function readConn(key) {
    try {
      const v = connection?.[key] ?? webrtc?.connection?.[key]
      return typeof v === 'string' && v ? v : null
    } catch {
      return null
    }
  }

  function ownSessionId() {
    try {
      return (connection || webrtc?.connection)?.getSessionId?.() || null
    } catch {
      return null
    }
  }

  function roomToken() {
    const t = readConn('currentRoomToken')
    if (t) return t
    try {
      const m = location.pathname.match(/\/call\/([^/?#]+)/)
      if (m) return m[1]
    } catch {
      /* fall through */
    }
    return null
  }

  const callToken = () => readConn('currentCallToken')

  function destroy() {
    stopped = true
    if (pollTimer) clearTimeout(pollTimer)
    detach()
    webrtc = null
  }

  attach()

  return {
    destroy,
    ownSessionId,
    roomToken,
    callToken,
    get attached() {
      return connection !== null
    },
    get webrtc() {
      return webrtc
    },
  }
}
