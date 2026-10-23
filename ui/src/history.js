/**
 * Fetches the catch-up transcript for a conversation from the cassini-live
 * ExApp (D-830). Same-origin through the AppAPI proxy, so it passes the Talk
 * page's `connect-src 'self'`. The backend authorizes by room membership and
 * returns `{ entries: [{ speakerSessionId, message, langId, t }, …] }` oldest
 * first, or an empty list when nothing has been transcribed yet.
 *
 * Tolerant by design: any error (not a participant, app absent, network) yields
 * an empty list so the panel still shows live captions from the join onward.
 */

const PROXY_PATH = '/apps/app_api/proxy/live_transcription/history'

function historyUrl(token) {
  const query = `?token=${encodeURIComponent(token)}`
  try {
    if (window.OC && typeof window.OC.generateUrl === 'function') {
      return window.OC.generateUrl(PROXY_PATH) + query
    }
  } catch {
    /* fall through */
  }
  return '/index.php' + PROXY_PATH + query
}

export async function fetchHistory(token) {
  if (!token) return []
  try {
    const res = await fetch(historyUrl(token), {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    })
    if (!res.ok) return []
    const body = await res.json()
    return Array.isArray(body?.entries) ? body.entries : []
  } catch {
    return []
  }
}
