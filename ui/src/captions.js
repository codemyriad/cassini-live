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

export async function enableCaptions(appId, token, ncSessionId) {
  if (!token || !ncSessionId) return
  try {
    await fetch(proxyUrl(appId, 'captions/transcribe'), {
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
    /* best-effort: the poll still delivers the transcript */
  }
}
