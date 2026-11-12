/**
 * Turn live captions on for the current session — what Talk's closed-captions
 * button does — so the viewer is on cassini-live's HPB feed as soon as the
 * panel sees the call (D-830).
 *
 * This asks the ExApp directly, through its USER proxy route
 * /captions/transcribe, with the body Talk itself sends to the provider. Talk's
 * own endpoint only reaches an app registered as live_transcription, so it
 * cannot start captions for the App Store install (cassini_live); the ExApp's
 * route works under either id and re-checks what Talk would have (the caller's
 * own session, in the call). See cassini_live/call_access.py.
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
    response = await fetch(proxyUrl(appId, 'captions/transcribe'), {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        // The ExApp's CSRF guard: a header no cross-site page can send unasked.
        'X-Cassini-Request': '1',
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify({ roomToken: token, ncSessionId, enable: true, langId: 'auto' }),
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
