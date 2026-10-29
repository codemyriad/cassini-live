/**
 * Speaker name resolution: a caption's speaker id → display name.
 *
 * The caption payload carries only the speaker's signalling session id. Talk's
 * 'transcript' event re-emits it as a SHORTER form than the session ids that
 * peers, participant cards and the signalling roster are keyed by: measured on
 * Talk 25 + the standalone HPB, the event id is a ~30-char prefix of the
 * full ~43-char session id (`…A5` boundary + a per-connection suffix). Exact
 * matching therefore resolves only the viewer's own lines (whose own id Talk
 * happens to report in the same short form) and leaves every remote speaker as
 * a raw id — the bug this module now fixes with prefix-aware matching.
 *
 * Sources, best first:
 *  1. own session → the logged-in user's display name, marked `you`
 *  2. signalling roster `connection.joinedUsers` — {sessionid: {userid,
 *     user:{displayname}}}, the authoritative map and present for everyone in
 *     the call, not only those with a media peer
 *  3. SimpleWebRTC peers `.nick`
 *  4. 'nick' events
 *  5. the participant card in the DOM
 *
 * Everything is feature-detected; the worst case is a shortened session id.
 */

export function createNames({ tap }) {
  const known = new Map() // full-or-prefix sid -> name, filled from 'nick' events
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

  // The event id and the "real" session id may differ by a suffix (see header),
  // so treat either being a prefix of the other as a match. Guard against a
  // too-short id matching everything.
  function sidMatch(candidate, sid) {
    if (!candidate || !sid) return false
    if (candidate === sid) return true
    const min = Math.min(candidate.length, sid.length)
    if (min < 8) return false
    return candidate.startsWith(sid) || sid.startsWith(candidate)
  }

  function currentUser() {
    try {
      return window.OC?.getCurrentUser?.() || null
    } catch {
      return null
    }
  }

  function roster() {
    try {
      const users = tap.webrtc?.connection?.joinedUsers
      return users && typeof users === 'object' ? users : null
    } catch {
      return null
    }
  }

  // Returns { displayName, userId } for the roster entry whose session id
  // matches, or null.
  function fromRoster(sid) {
    const users = roster()
    if (!users) return null
    for (const key of Object.keys(users)) {
      if (!sidMatch(key, sid)) continue
      const entry = users[key] || {}
      const displayName = entry.user?.displayname || entry.user?.displayName || null
      return { displayName, userId: entry.userid || entry.userId || null }
    }
    return null
  }

  function fromPeers(sid) {
    try {
      const peers = tap.webrtc?.webrtc?.peers
      if (!Array.isArray(peers)) return null
      const peer = peers.find((p) => p && sidMatch(p.id, sid) && typeof p.nick === 'string' && p.nick)
      return peer ? peer.nick : null
    } catch {
      return null
    }
  }

  function fromNickEvents(sid) {
    for (const [id, name] of known) {
      if (sidMatch(id, sid)) return name
    }
    return null
  }

  function fromDom(sid) {
    try {
      // The card id carries the FULL session id, so an exact lookup misses the
      // shorter event id; scan and prefix-match instead.
      for (const card of document.querySelectorAll('.video-container[id]')) {
        const m = card.id.match(/^(?:placeholder-)?container_(.+)_video_incoming$/)
        if (!m || !sidMatch(m[1], sid)) continue
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

  function isOwn(sid) {
    const ownSid = tap.ownSessionId()
    if (ownSid && sidMatch(ownSid, sid)) return true
    // Fall back to the roster: the entry matching sid is ours if its userid is
    // the logged-in user's. Covers builds where the own session id reported to
    // us differs from the caption's speaker id.
    const user = currentUser()
    const rosterHit = fromRoster(sid)
    if (user && rosterHit && rosterHit.userId && rosterHit.userId === user.uid) return true
    return false
  }

  /** @returns {{ name: string, own: boolean, resolved: boolean }} */
  function resolve(sid) {
    ensureNickListener()

    if (isOwn(sid)) {
      const user = currentUser()
      const name = user?.displayName || fromRoster(sid)?.displayName || 'You'
      return { name, own: true, resolved: true }
    }

    const rosterHit = fromRoster(sid)
    const name =
      (rosterHit && (rosterHit.displayName || rosterHit.userId)) ||
      fromPeers(sid) ||
      fromNickEvents(sid) ||
      fromDom(sid)
    if (name) {
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
