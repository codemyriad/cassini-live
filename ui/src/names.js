/**
 * Speaker name resolution: HPB session id → display name.
 *
 * The caption payload carries only the speaker's signalling session id, so
 * names are resolved in the browser, best source first:
 *
 *  1. own session → the logged-in user's display name, marked `you`
 *  2. SimpleWebRTC peers — Peer objects carry `.id` (session id) and `.nick`
 *  3. 'nick' events (Talk emits them for participants without a Peer)
 *  4. the participant card in the DOM (`container_<sid>_video_incoming`)
 *
 * Everything is feature-detected; the worst case is a shortened session id.
 */

export function createNames({ tap }) {
  const known = new Map() // sid -> name
  let nickWebrtc = null
  const nickHandler = (data) => {
    if (data && typeof data.id === 'string' && typeof data.name === 'string' && data.name) {
      known.set(data.id, data.name)
    }
  }

  function ensureNickListener() {
    const webrtc = tap.webrtc
    if (!webrtc || webrtc === nickWebrtc || typeof webrtc.on !== 'function') return
    if (nickWebrtc && typeof nickWebrtc.off === 'function') nickWebrtc.off('nick', nickHandler)
    nickWebrtc = webrtc
    webrtc.on('nick', nickHandler)
  }

  function ownDisplayName() {
    try {
      const user = window.OC?.getCurrentUser?.()
      if (user && user.displayName) return user.displayName
    } catch {
      /* fall through */
    }
    return null
  }

  function fromPeers(sid) {
    try {
      const peers = tap.webrtc?.webrtc?.peers
      if (!Array.isArray(peers)) return null
      const peer = peers.find((p) => p && p.id === sid && typeof p.nick === 'string' && p.nick)
      return peer ? peer.nick : null
    } catch {
      return null
    }
  }

  function fromDom(sid) {
    try {
      for (const prefix of ['container_', 'placeholder-container_']) {
        const card = document.getElementById(`${prefix}${sid}_video_incoming`)
        if (!card) continue
        const label = card.querySelector(
          '.video-container__user-name, .video-container__name, .displayname',
        )
        const text = label && label.textContent ? label.textContent.trim() : ''
        if (text) return text
      }
    } catch {
      /* fall through */
    }
    return null
  }

  function shortSid(sid) {
    return sid.length > 12 ? `${sid.slice(0, 6)}…${sid.slice(-4)}` : sid
  }

  /** @returns {{ name: string, own: boolean, resolved: boolean }} */
  function resolve(sid) {
    ensureNickListener()
    const ownSid = tap.ownSessionId()
    if (ownSid && sid === ownSid) {
      return { name: ownDisplayName() || 'You', own: true, resolved: true }
    }
    const name = fromPeers(sid) || known.get(sid) || fromDom(sid)
    if (name) {
      known.set(sid, name)
      return { name, own: false, resolved: true }
    }
    return { name: shortSid(sid), own: false, resolved: false }
  }

  function destroy() {
    if (nickWebrtc && typeof nickWebrtc.off === 'function') nickWebrtc.off('nick', nickHandler)
    nickWebrtc = null
    known.clear()
  }

  return { resolve, destroy }
}
