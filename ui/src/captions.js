/**
 * Turn live captions on for the current session — what Talk's closed-captions
 * button does — so the viewer is on cassini-live's HPB feed as soon as the
 * panel sees the call (D-830).
 *
 * Talk's own endpoint (POST /live-transcription/{token}) only reaches an app
 * registered as live_transcription (nextcloud/spreed#20296), so this asks the
 * ExApp's stand-in for it: POST mock_talk_proxy/live-transcription/{token} on the
 * AppAPI proxy. That route makes Talk's checks (the caller's own session, in the
 * call) and sends the provider what Talk would. See
 * cassini_live/mock_talk_proxy.py. When Talk can call this app, this becomes a
 * call to Talk's endpoint.
 *
 * No language is sent: Talk's endpoint takes none, and the provider's default
 * (automatic) applies.
 *
 * `ncSessionId` is Talk's Nextcloud session id for this tab (the signalling
 * connection's nextcloudSessionId), not the HPB session id.
 *
 * Best-effort and idempotent: enabling twice is harmless, and failures (not yet
 * in the call, rate-limited, app absent) are ignored, since the panel still
 * works off the /history poll.
 */

import { proxyUrl } from './app-id.js'

// What the panel shows when the ExApp itself is unreachable under this id.
const UNREACHABLE = 'Cassini Live is not reachable here: is it installed and enabled?'

/**
 * One attempt. Resolves to { ok, retry, retryAfterMs, message }: `retry` says a later
 * attempt can succeed (not in the call yet, captions not ready, rate-limited,
 * network); `message` is the server's reason, for the panel to show.
 */
export async function enableCaptions(appId, token, ncSessionId) {
  // Talk sets the session id when the room is joined: try again shortly.
  if (!token || !ncSessionId) return { ok: false, retry: true, retryAfterMs: null, message: null }
  let response
  try {
    const route = `mock_talk_proxy/live-transcription/${encodeURIComponent(token)}`
    response = await fetch(proxyUrl(appId, route), {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        // The ExApp's CSRF guard: a header no cross-site page can send unasked.
        'X-Cassini-Request': '1',
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      // Talk knows the caller's session; here it is sent, and the route checks it.
      body: JSON.stringify({ ncSessionId }),
    })
  } catch {
    return { ok: false, retry: true, retryAfterMs: null, message: null }
  }
  if (response.ok) return { ok: true, retry: false, retryAfterMs: null, message: null }
  let message = null
  try {
    const body = await response.json()
    if (typeof body?.detail === 'string') message = body.detail
  } catch {
    /* not the ExApp's JSON: AppAPI's or Nextcloud's own error page */
  }
  const seconds = Number(response.headers.get('Retry-After'))
  const retryAfterMs = seconds > 0 ? seconds * 1000 : null
  const status = response.status
  if (status === 401 || status === 404 || (!message && status === 403)) {
    return { ok: false, retry: false, retryAfterMs: null, message: UNREACHABLE }
  }
  // 403: Talk has not seen this session join the call yet; 429: rate limit;
  // 5xx: captions not ready (Talk not connected yet, engine loading, overloaded).
  const retry = status === 403 || status === 429 || status >= 500
  return { ok: false, retry, retryAfterMs, message }
}
