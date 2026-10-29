/**
 * The loader bookmarklet: a tiny javascript: URL that injects the full panel
 * bundle from the cassini-live ExApp through the AppAPI proxy.
 *
 * Same origin as Nextcloud, so two things hold at once:
 *  - the injected <script src> passes Nextcloud's CSP once it carries the
 *    page's nonce (Nextcloud ships `script-src 'nonce-…' 'strict-dynamic'`)
 *  - future fetches from the panel to ExApp routes pass `connect-src 'self'`
 *
 * Users drag this once; the panel itself updates on every ExApp redeploy.
 */

;(() => {
  const existing = window.__CASSINI_LIVE_PANEL__
  if (existing && typeof existing.destroy === 'function') {
    existing.destroy()
    return
  }
  const doc = document
  const nonceCarrier = doc.querySelector('script[nonce]')
  const script = doc.createElement('script')
  if (nonceCarrier && nonceCarrier.nonce) script.nonce = nonceCarrier.nonce
  const path = '/apps/app_api/proxy/live_transcription/panel/panel.js'
  const base =
    window.OC && typeof window.OC.generateUrl === 'function'
      ? window.OC.generateUrl(path)
      : '/index.php' + path
  script.src = base + '?_=' + Date.now()
  script.onerror = () => {
    alert(
      'Cassini Live transcript panel could not be loaded.\n' +
        'Is the cassini-live ExApp installed and enabled on this Nextcloud?',
    )
  }
  ;(doc.head || doc.documentElement).appendChild(script)
})()
