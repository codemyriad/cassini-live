/**
 * Turn Talk's live captions on for the current session — the same thing the
 * call's closed-captions button does — so the viewer is on cassini-live's HPB
 * feed as soon as they activate the panel (D-830). Best-effort and idempotent:
 * Talk returns 200 if captions are already on, and we ignore failures (e.g. not
 * yet joined to the call), since the panel still works off the /history poll.
 */

export async function enableCaptions(token) {
  if (!token) return
  try {
    const base =
      window.OC && typeof window.OC.generateUrl === 'function'
        ? window.OC.generateUrl(`/ocs/v2.php/apps/spreed/api/v1/live-transcription/${token}`)
        : `/ocs/v2.php/apps/spreed/api/v1/live-transcription/${token}`
    await fetch(base, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'OCS-APIRequest': 'true',
        requesttoken: (window.OC && window.OC.requestToken) || '',
        Accept: 'application/json',
      },
    })
  } catch {
    /* best-effort: the poll still delivers the transcript */
  }
}
