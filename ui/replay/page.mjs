/**
 * The replay page: a Talk-call-shaped document with the sidebar structure the
 * panel mounts into, the SimpleWebRTC surface it reads names and tokens from,
 * and a transport bar to move through the meeting. Same CSP posture as a Talk
 * page: only nonce'd scripts run, and scripts they create inherit the trust.
 */

export const NONCE = 'replay-nonce'

const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])

// Serialised for a <script>: no "</script>" or U+2028/9 surprises.
const jsonForScript = (value) =>
  JSON.stringify(value).replace(/</g, '\\u003c').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029')

export function renderPage({ replay, viewer, token }) {
  const participants = replay.participants
  const me = participants.find((p) => p.sid === viewer) || participants[0] || { sid: viewer, name: viewer, userId: viewer }
  const joinedUsers = {}
  for (const p of participants) {
    joinedUsers[p.sid] = { sessionid: p.sid, userid: p.userId || p.sid, user: { displayname: p.name } }
  }
  const config = {
    token,
    viewer: me.sid,
    participants,
    fromMs: replay.fromMs,
    toMs: replay.toMs,
    epoch: replay.epoch,
    title: replay.body.source?.title || 'Meeting replay',
    startedAt: replay.body.source?.startedAt || null,
    engine: replay.body.engine || {},
    stats: replay.body.stats || {},
  }
  const talkShim = `
    const listeners = new Map()
    const bucket = (name) => { if (!listeners.has(name)) listeners.set(name, new Set()); return listeners.get(name) }
    const connection = {
      on(name, cb) { bucket(name).add(cb) },
      off(name, cb) { bucket(name).delete(cb) },
      getSessionId: () => ${jsonForScript(me.sid)},
      currentRoomToken: ${jsonForScript(token)},
      currentCallToken: ${jsonForScript(token)},
      joinedUsers: ${jsonForScript(joinedUsers)},
    }
    window.OCA = { Talk: { SimpleWebRTC: {
      on(name, cb) { bucket(name).add(cb) },
      off(name, cb) { bucket(name).delete(cb) },
      connection,
      webrtc: { peers: ${jsonForScript(participants.filter((p) => p.sid !== me.sid).map((p) => ({ id: p.sid, type: 'video', nick: p.name })))} },
    } } }
    window.OC = {
      getCurrentUser: () => ({ uid: ${jsonForScript(me.userId || me.sid)}, displayName: ${jsonForScript(me.name)} }),
      generateUrl: (p) => '/index.php' + p,
    }
    window.__REPLAY__ = ${jsonForScript(config)}
  `
  const tiles = participants
    .map(
      (p) => `
      <div class="video-container tile${p.sid === me.sid ? ' tile--me' : ''}" id="container_${escapeHtml(p.sid)}_video_incoming" data-sid="${escapeHtml(p.sid)}">
        <div class="tile__avatar">${escapeHtml((p.name || '?').slice(0, 1).toUpperCase())}</div>
        <span class="video-container__user-name">${escapeHtml(p.name)}${p.sid === me.sid ? ' (you)' : ''}</span>
      </div>`,
    )
    .join('')
  const asOptions = participants
    .map((p) => `<option value="${escapeHtml(p.sid)}"${p.sid === me.sid ? ' selected' : ''}>${escapeHtml(p.name)}</option>`)
    .join('')
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Replay · ${escapeHtml(config.title)}</title>
  <link rel="stylesheet" href="/replay/transport.css">
  <script nonce="${NONCE}">${talkShim}</script>
</head>
<body class="replay">
  <header class="topbar">
    <span class="topbar__brand">Cassini Live replay</span>
    <span class="topbar__title">${escapeHtml(config.title)}</span>
    <span class="topbar__meta">${escapeHtml(config.engine.id || '')}${config.engine.speed != null ? ` · speed ${escapeHtml(config.engine.speed)}` : ''}${config.startedAt ? ` · ${escapeHtml(config.startedAt)}` : ''}</span>
  </header>
  <main id="app-content-vue" class="call">
    <div class="call__grid">${tiles}</div>
    <div class="localVideoContainer"></div>
    <div class="transcript" id="native-captions"></div>
  </main>
  <aside id="app-sidebar-vue">
    <div class="app-sidebar-header"><h2 class="app-sidebar-header__mainname">${escapeHtml(config.title)}</h2></div>
    <div class="app-sidebar-tabs">
      <div class="app-sidebar-tabs__nav" role="tablist">
        <button id="tab-button-chat" role="tab" aria-controls="tab-chat" aria-selected="true" class="tabBtn tabBtn--selected"><span class="tabBtn__name">Chat</span></button>
        <button id="tab-button-participants" role="tab" aria-controls="tab-participants" aria-selected="false" class="tabBtn"><span class="tabBtn__name">Participants</span></button>
        <button id="tab-button-shared-items" role="tab" aria-controls="tab-shared-items" aria-selected="false" class="tabBtn"><span class="tabBtn__name">Shared items</span></button>
      </div>
      <div class="app-sidebar-tabs__content app-sidebar-tabs__content--multiple">
        <section id="tab-chat" role="tabpanel" class="app-sidebar__tab app-sidebar__tab--active"><p class="placeholder">Chat is not part of the replay.</p></section>
        <section id="tab-participants" role="tabpanel" class="app-sidebar__tab"><ul class="placeholder">${participants.map((p) => `<li>${escapeHtml(p.name)}</li>`).join('')}</ul></section>
        <section id="tab-shared-items" role="tabpanel" class="app-sidebar__tab"><p class="placeholder">Nothing shared.</p></section>
      </div>
    </div>
  </aside>
  <footer class="transport" id="transport">
    <button id="tr-back" title="Back 10 s (←)">−10 s</button>
    <button id="tr-play" title="Play / pause (space)">▶</button>
    <button id="tr-fwd" title="Forward 10 s (→)">+10 s</button>
    <span class="transport__time"><span id="tr-now">0:00</span> / <span id="tr-end">0:00</span></span>
    <input id="tr-seek" type="range" min="${config.fromMs}" max="${config.toMs}" step="100" value="${config.fromMs}" aria-label="Meeting time">
    <label>Speed <select id="tr-speed"><option>0.5</option><option selected>1</option><option>2</option><option>4</option><option>8</option><option>16</option></select></label>
    <label>Jump to <input id="tr-jump" type="text" size="6" placeholder="mm:ss" aria-label="Jump to time"></label>
    <label>View as <select id="tr-as">${asOptions}</select></label>
    <span class="transport__clock" id="tr-clock" title="Wall-clock time at this point of the meeting"></span>
  </footer>
  <script nonce="${NONCE}" src="/replay/transport.js"></script>
</body>
</html>`
}
