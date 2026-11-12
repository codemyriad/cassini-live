/**
 * The app id of the ExApp this bundle talks to through the AppAPI proxy.
 *
 * The same bundle serves both registrations (cassini_live from the App Store,
 * live_transcription by hand), so the id is not known at build time. The ExApp
 * fills it in from its APP_ID when it serves the bundle and the bookmarklets
 * (cassini_live/panel_page.py, APP_ID_PLACEHOLDER: keep the two in step).
 * Test and replay servers fill in their own.
 *
 * Only the entry points (main.js, loader.js) import APP_ID; everything below
 * them gets the id passed in.
 */

export const APP_ID = '__CASSINI_LIVE_APP_ID__'

/**
 * The URL of one of the app's routes, e.g. proxyUrl(appId, 'history'):
 * through OC.generateUrl when Nextcloud provides it (it adds the web root and
 * /index.php as the server is configured), else the default /index.php prefix.
 */
export function proxyUrl(appId, path) {
  const route = `/apps/app_api/proxy/${appId}/${path}`
  try {
    if (window.OC && typeof window.OC.generateUrl === 'function') {
      return window.OC.generateUrl(route)
    }
  } catch {
    /* fall through to the default prefix */
  }
  return '/index.php' + route
}
