/* Transport bar for the replay page: moves the server's meeting clock, mounts
 * the real panel bundle the way the loader bookmarklet does, remounts it on a
 * seek (so the panel catches up from the timeline exactly as a late joiner
 * would), and feeds it the partial each speaker had on screen at this moment. */
;(() => {
  const cfg = window.__REPLAY__
  const NONCE = (document.currentScript && document.currentScript.nonce) || 'replay-nonce'
  const PANEL_SRC = '/index.php/apps/app_api/proxy/live_transcription/panel/panel.js'
  const TICK_MS = 250
  const $ = (id) => document.getElementById(id)
  const play = $('tr-play')
  const seek = $('tr-seek')
  const nowEl = $('tr-now')
  const endEl = $('tr-end')
  const clockEl = $('tr-clock')
  const speedEl = $('tr-speed')
  const jumpEl = $('tr-jump')
  const asEl = $('tr-as')

  const clock = (ms) => {
    const s = Math.max(0, Math.floor(ms / 1000))
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    const sec = String(s % 60).padStart(2, '0')
    return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
  }
  const parseClock = (text) => {
    const parts = String(text).trim().split(':').map(Number)
    if (!parts.length || parts.some((p) => Number.isNaN(p))) return null
    return parts.reduce((acc, p) => acc * 60 + p, 0) * 1000
  }

  async function control(action, extra = {}) {
    const res = await fetch('/replay/control', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ action, ...extra }),
    })
    return res.json()
  }

  // Mounting the bundle from a nonce'd script keeps strict-dynamic happy, as the
  // loader bookmarklet's injected <script> does on a real Talk page.
  let mountGeneration = 0
  function mountPanel() {
    const existing = window.__CASSINI_LIVE_PANEL__
    if (existing && typeof existing.destroy === 'function') existing.destroy()
    injected.clear()
    const script = document.createElement('script')
    script.nonce = NONCE
    script.src = `${PANEL_SRC}?_=${++mountGeneration}`
    ;(document.head || document.documentElement).appendChild(script)
  }

  const injected = new Map() // sid -> message currently shown as partial
  function applyPartials(partials) {
    const panel = window.__CASSINI_LIVE_PANEL__
    if (!panel || typeof panel.injectPartial !== 'function') return
    const seen = new Set()
    for (const p of partials) {
      seen.add(p.sid)
      if (injected.get(p.sid) !== p.message) {
        injected.set(p.sid, p.message)
        panel.injectPartial({ id: p.sid, message: p.message, languageId: p.lang || 'en' })
      }
    }
    for (const sid of [...injected.keys()]) {
      if (!seen.has(sid)) {
        injected.delete(sid)
        panel.injectPartial({ id: sid, message: '' })
      }
    }
  }

  let scrubbing = false
  let state = { t: cfg.fromMs, playing: false, speed: 1 }
  function render() {
    play.textContent = state.playing ? '❚❚' : '▶'
    play.setAttribute('aria-pressed', state.playing ? 'true' : 'false')
    if (!scrubbing) {
      seek.value = String(state.t)
      nowEl.textContent = clock(state.t)
    }
    endEl.textContent = clock(cfg.toMs)
    if (cfg.epoch) clockEl.textContent = new Date(cfg.epoch + state.t).toLocaleTimeString()
    document.title = `${clock(state.t)} · ${cfg.title}`
  }

  async function tick() {
    try {
      const res = await fetch('/replay/state', { cache: 'no-store' })
      state = await res.json()
      render()
      applyPartials(state.partials || [])
    } catch {
      /* the server went away; keep the last frame */
    }
    setTimeout(tick, TICK_MS)
  }

  async function seekTo(ms, { remount = true } = {}) {
    const t = Math.max(cfg.fromMs, Math.min(cfg.toMs, ms))
    state = { ...state, ...(await control('seek', { t })) }
    render()
    // The panel's store is append-only by seq, like a live call: moving the
    // clock means mounting afresh and letting it catch up from the timeline.
    if (remount) mountPanel()
  }

  play.addEventListener('click', async () => {
    state = { ...state, ...(await control(state.playing ? 'pause' : 'play')) }
    render()
  })
  $('tr-back').addEventListener('click', () => seekTo(state.t - 10000))
  $('tr-fwd').addEventListener('click', () => seekTo(state.t + 10000))
  seek.addEventListener('input', () => {
    scrubbing = true
    nowEl.textContent = clock(Number(seek.value))
  })
  seek.addEventListener('change', async () => {
    scrubbing = false
    await seekTo(Number(seek.value))
  })
  speedEl.addEventListener('change', async () => {
    state = { ...state, ...(await control('speed', { speed: Number(speedEl.value) })) }
    render()
  })
  jumpEl.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return
    const ms = parseClock(jumpEl.value)
    if (ms != null) seekTo(ms)
  })
  asEl.addEventListener('change', () => {
    const url = new URL(location.href)
    url.searchParams.set('as', asEl.value)
    location.href = url.toString()
  })
  document.addEventListener('keydown', (e) => {
    if (e.target && /^(input|select|textarea)$/i.test(e.target.tagName)) return
    if (e.key === ' ') {
      e.preventDefault()
      play.click()
    } else if (e.key === 'ArrowLeft') seekTo(state.t - 10000)
    else if (e.key === 'ArrowRight') seekTo(state.t + 10000)
  })
  // Native tab clicks select native tabs, as Talk does; the panel handles its own.
  document.addEventListener('click', (e) => {
    const b = e.target.closest && e.target.closest('.app-sidebar-tabs__nav button[role=tab]')
    if (!b || !b.id.startsWith('tab-button-')) return
    const id = b.id.slice('tab-button-'.length)
    if (!document.getElementById('tab-' + id)) return
    document.querySelectorAll('.app-sidebar-tabs__nav button[role=tab]').forEach((x) => {
      const on = x === b
      x.setAttribute('aria-selected', on ? 'true' : 'false')
      x.className = on ? 'tabBtn tabBtn--selected' : 'tabBtn'
    })
    document.querySelectorAll('.app-sidebar__tab').forEach((s) => {
      s.classList.toggle('app-sidebar__tab--active', s.id === 'tab-' + id)
    })
  })

  // Expose the controls for tests and for driving from the devtools console.
  window.__REPLAY_CONTROL__ = { seekTo, control, mountPanel, get state() { return state } }

  const startAt = new URL(location.href).searchParams.get('t')
  render()
  mountPanel()
  if (startAt) seekTo(parseClock(startAt) ?? cfg.fromMs, { remount: false })
  tick()
})()
