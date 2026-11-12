// Appended by the build after a `window.CASSINI_LIVE_BOOKMARKLETS` preamble
// holding { version, loader, inline }.
;(() => {
  // Back to Nextcloud: everything before the AppAPI proxy path, so a sub-directory
  // install (/nc/index.php/...) and one without index.php both land on their root.
  const back = document.getElementById('back')
  const root = window.location.pathname.replace(/\/apps\/app_api\/proxy\/.*$/, '/')
  if (back && root !== window.location.pathname) back.setAttribute('href', root)

  const data = window.CASSINI_LIVE_BOOKMARKLETS
  if (!data) return

  const link = document.getElementById('bookmarklet')
  link.setAttribute('href', data.loader)
  // Clicking on this page does nothing useful (no Talk here) and the page's
  // own CSP would block the javascript: navigation anyway — keep it a drag
  // handle.
  link.addEventListener('click', (event) => event.preventDefault())

  async function copy(text, resultEl) {
    try {
      await navigator.clipboard.writeText(text)
      resultEl.textContent = 'Copied.'
    } catch {
      resultEl.textContent = 'Copy failed — select the bookmark link and copy its address instead.'
    }
    setTimeout(() => {
      resultEl.textContent = ''
    }, 4000)
  }

  document.getElementById('copy-loader').addEventListener('click', () => {
    copy(data.loader, document.getElementById('copy-result'))
  })
  document.getElementById('copy-inline').addEventListener('click', () => {
    copy(data.inline, document.getElementById('copy-inline-result'))
  })

  document.getElementById('version').textContent = `Cassini Live panel v${data.version}`
})()
