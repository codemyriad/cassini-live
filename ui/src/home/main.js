/**
 * The Cassini Live home page: what opens from the "Cassini Live" entry in
 * Nextcloud's app menu.
 *
 * AppAPI's embedded page is a bare <div id="content"> on a Nextcloud page; this
 * script draws the whole page into a shadow root there, so Nextcloud's styles and
 * ours stay apart while Nextcloud's theme colours (CSS variables) still apply.
 *
 * Everyone gets the three steps to captions and the "Live transcript" bookmark.
 * Administrators also get what is left to set up and links to the settings and
 * diagnostics pages. Those routes are ADMIN at the proxy: hiding the block here
 * is a courtesy, not the boundary.
 */

import { APP_ID, proxyUrl } from '../app-id.js'
import { viewModel } from './model.js'
import { STYLES, TEMPLATE } from './template.js'

const DOCS = 'https://github.com/codemyriad/cassini-live/blob/main/docs/install.md'
const LIMITS_DOCS = `${DOCS}#with-an-existing-deploy-daemon`
const LIMITATIONS = 'https://github.com/codemyriad/cassini-live/blob/main/docs/known-limitations.md'
const ISSUES = 'https://github.com/codemyriad/cassini-live/issues'
const READY_POLL_MS = 15000
const SETUP_POLL_MS = 4000
const MOUNT_ID = 'cassini-live-home'

function nextcloudUrl(path) {
  try {
    if (window.OC && typeof window.OC.generateUrl === 'function') return window.OC.generateUrl(path)
  } catch {
    /* fall through */
  }
  return `/index.php${path}`
}

function isAdmin() {
  try {
    return Boolean(window.OC && typeof window.OC.isUserAdmin === 'function' && window.OC.isUserAdmin())
  } catch {
    return false
  }
}

async function getJson(path) {
  try {
    const response = await fetch(proxyUrl(APP_ID, path), {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { Accept: 'application/json', 'X-Cassini-Request': '1' },
    })
    if (!response.ok) return null
    return await response.json()
  } catch {
    return null
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

function mount(container) {
  const host = el('div')
  host.id = MOUNT_ID
  const root = host.attachShadow({ mode: 'open' })
  const style = el('style')
  style.textContent = STYLES
  const page = el('div', 'page')
  // Static markup written in this repository (template.js); every value that
  // comes from the server is set with textContent below.
  page.innerHTML = TEMPLATE
  root.append(style, page)
  container.replaceChildren(host)

  const $ = (id) => root.getElementById(id)
  const loader = __CASSINI_LIVE_LOADER__

  $('icon').src = proxyUrl(APP_ID, 'img/app.svg')
  $('open-talk').href = nextcloudUrl('/apps/spreed/')
  $('settings-link').href = proxyUrl(APP_ID, 'settings/index.html')
  $('diagnostics-link').href = proxyUrl(APP_ID, 'diagnostics/')
  $('docs-link').href = DOCS
  $('limitations-link').href = LIMITATIONS
  $('issues-link').href = ISSUES

  // The bookmark: its address is the loader, and it is only ever dragged from here.
  const bookmark = $('bookmark')
  bookmark.setAttribute('href', loader)
  bookmark.addEventListener('click', (event) => {
    event.preventDefault()
    say('Drag it up to your bookmarks bar instead of clicking it here. It works inside a Talk call.')
  })
  bookmark.addEventListener('dragstart', () => {
    $('drop-hint').hidden = false
  })
  bookmark.addEventListener('dragend', () => {
    $('drop-hint').hidden = true
    say('If Live transcript is now in your bookmarks bar, you are done here: join a Talk call and click it.')
  })
  $('copy').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(loader)
      say('Copied. Add a new bookmark, name it Live transcript, and paste this as its address (URL).')
    } catch {
      say('Copying is not allowed here. Right-click Live transcript above and copy its link address instead.')
    }
  })

  function say(message) {
    const note = $('bookmark-note')
    note.textContent = message
    note.hidden = false
  }

  function renderSetup(steps) {
    const box = $('setup')
    const list = $('setup-list')
    box.hidden = !steps
    if (!steps) return
    list.replaceChildren(
      ...steps.map((step, index) => {
        const row = el('div', `setup-step${step.done ? ' done' : ''}`)
        row.dataset.step = step.id
        row.append(el('span', 'num', step.done ? '✓' : String(index + 1)))
        const text = el('div', 'setup-text')
        text.append(el('strong', '', step.title), el('p', '', step.detail))
        row.append(text)
        if (step.done) {
          row.append(el('span', 'setup-state', 'Done'))
        } else if (step.action === 'settings') {
          const link = el('a', 'primary', 'Open settings')
          link.href = proxyUrl(APP_ID, 'settings/index.html')
          row.append(link)
        } else if (step.action === 'limits-docs') {
          const link = el('a', 'secondary', 'How to set them')
          link.href = LIMITS_DOCS
          link.target = '_blank'
          link.rel = 'noopener noreferrer'
          row.append(link)
        } else {
          row.append(el('span', 'setup-state', step.state))
        }
        return row
      }),
    )
  }

  function render(model, status) {
    const pill = $('pill')
    pill.className = `pill ${model.pill.kind}`
    $('pill-text').textContent = model.pill.text
    const note = $('user-note')
    note.hidden = !model.userNote
    note.textContent = model.userNote || ''
    renderSetup(model.setup)
    const admin = $('admin')
    admin.hidden = !model.facts
    if (model.facts) {
      $('fact-engine').textContent = model.facts.engine
      const talk = $('fact-talk')
      talk.textContent = model.facts.talk.text
      talk.className = `fact-value ${model.facts.talk.tone}`
      $('fact-calls').textContent = model.facts.calls
    }
    $('version').textContent = status?.version ? `Cassini Live ${status.version}` : 'Cassini Live'
  }

  let timer = null
  let stopped = false
  async function refresh() {
    clearTimeout(timer)
    if (stopped) return
    const [status, admin] = await Promise.all([
      getJson('status'),
      isAdmin() ? getJson('settings/api/state') : Promise.resolve(null),
    ])
    if (stopped) return
    const model = viewModel({ status, admin })
    render(model, status)
    if (!document.hidden) {
      timer = setTimeout(refresh, model.pill.kind === 'ready' ? READY_POLL_MS : SETUP_POLL_MS)
    }
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh()
  })
  refresh()

  return {
    destroy() {
      stopped = true
      clearTimeout(timer)
      host.remove()
    },
  }
}

function start() {
  const container = document.getElementById('content')
  if (!container || document.getElementById(MOUNT_ID)) return
  window.__CASSINI_LIVE_HOME__ = mount(container)
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start)
else start()
