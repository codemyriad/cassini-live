/**
 * Polls the catch-up transcript for a conversation from the cassini-live ExApp
 * (D-830). Same-origin through the AppAPI proxy, so it passes the Talk page's
 * `connect-src 'self'`. The backend authorizes by room membership and returns
 * `{ entries: [{ seq, type, … }, …], head }` — the timeline newer than `since`,
 * oldest first (finals from every speaker, plus participant joins).
 *
 * Tolerant by design: any error (not a participant, app absent, network) yields
 * an empty delta so the panel keeps whatever it already has.
 */

import { proxyUrl } from './app-id.js'

function historyUrl(appId, token, since) {
  return proxyUrl(appId, 'history') + `?token=${encodeURIComponent(token)}&since=${since | 0}`
}

export async function fetchHistory(appId, token, since = 0) {
  if (!token) return { entries: [], head: 0 }
  try {
    const res = await fetch(historyUrl(appId, token, since), {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    })
    if (!res.ok) return { entries: [], head: 0 }
    const body = await res.json()
    return {
      entries: Array.isArray(body?.entries) ? body.entries : [],
      head: typeof body?.head === 'number' ? body.head : 0,
    }
  } catch {
    return { entries: [], head: 0 }
  }
}
