/**
 * The home page's static markup and styles. Written here, never built from server
 * text: main.js fills the dynamic parts with textContent.
 *
 * Wording rule: the thing people drag is "the Live transcript bookmark", never a
 * "button", so it cannot be confused with Talk's controls or Nextcloud's menu.
 * The one place the page says "button" is the note about Talk's own control.
 *
 * Colours, radii and type are Cassini's saturn-light / saturn-dark themes
 * (gocassini: cassini-viewer/src/app.css), light by default and dark with
 * prefers-color-scheme, as in the Cassini app. In the picture of a Talk call the
 * sidebar follows the theme and the call itself stays dark, as in Talk.
 */

const KEY = (label) => `<kbd>${label}</kbd>`

export const TEMPLATE = `
<div class="wrap">
  <header class="head">
    <span class="tile"><img id="icon" alt="" width="32" height="32"></span>
    <div class="titles">
      <h1>Cassini Live</h1>
      <p class="muted">Live captions and a scrollable transcript for Talk calls</p>
    </div>
    <span id="pill" class="pill loading" role="status"><span class="dot"></span><span id="pill-text">Checking</span></span>
  </header>

  <p id="user-note" class="note warning" hidden></p>

  <section id="setup" class="card setup" aria-labelledby="setup-title" hidden>
    <div class="card-head">
      <h2 id="setup-title">Finish setting up</h2>
      <span class="tag">Administrators only</span>
    </div>
    <div id="setup-list" class="setup-list"></div>
    <div class="engine-line" data-step="engine"><span id="engine-text"></span><span id="engine-state" class="setup-state"></span></div>
  </section>

  <section id="admin" class="card" aria-labelledby="admin-title" hidden>
    <div class="card-head">
      <h2 id="admin-title">Administration</h2>
      <span class="tag">Administrators only</span>
    </div>
    <dl class="facts">
      <div><dt>Speech engine</dt><dd id="fact-engine" class="fact-value">–</dd></div>
      <div><dt>Talk connection</dt><dd id="fact-talk" class="fact-value">–</dd></div>
      <div><dt>Calls being captioned</dt><dd id="fact-calls" class="fact-value">–</dd></div>
    </dl>
    <div class="links">
      <a id="settings-link" class="link-card" href="#">
        <span class="link-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg></span>
        <span><strong>Settings</strong><span class="muted small">Talk connection, speech engine, capacity and model files.</span></span>
      </a>
      <a id="diagnostics-link" class="link-card" href="#">
        <span class="link-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 12h4l3-7 4 14 3-7h4"/></svg></span>
        <span><strong>Live diagnostics</strong><span class="muted small">Who is speaking, how far captions trail, each step of the pipeline.</span></span>
      </a>
    </div>
  </section>

  <section class="card" aria-labelledby="steps-title">
    <h2 id="steps-title">Get captions in your calls</h2>
    <div class="steps">
      <div class="step">
        <span class="step-no">Once per browser</span>
        <strong>Add the Live transcript bookmark</strong>
        <p>Drag this to your bookmarks bar:</p>
        <a id="bookmark" class="bookmark" draggable="true" href="#">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>
          Live transcript
        </a>
        <figure id="how-add" class="browser">
          <div class="tabs"><span class="light"></span><span class="light"></span><span class="light"></span><span class="tab">Cassini Live</span></div>
          <div class="address"><div></div></div>
          <div class="bar">
            <span class="mark">Mail</span><span class="mark">News</span><span class="mark slot">Live transcript</span>
            <span class="bar-label">Bookmarks bar</span>
          </div>
          <svg class="drag" viewBox="0 0 420 96" role="img" aria-label="An arrow from Live transcript on the page up to the bookmarks bar">
            <rect class="i-chip" x="28" y="52" width="128" height="30" rx="6"/>
            <text class="i-chip-text" x="92" y="72" text-anchor="middle">Live transcript</text>
            <path class="i-arrow" d="M160 66 C 212 62, 208 34, 190 12"/>
            <path class="i-arrow" d="M183 22 L190 10 L201 19"/>
          </svg>
        </figure>
        <p class="small muted">Can't see the bar? Press ${KEY('Ctrl')} + ${KEY('Shift')} + ${KEY('B')} (${KEY('Cmd')} + ${KEY('Shift')} + ${KEY('B')} on a Mac).</p>
        <button id="copy" type="button" class="linklike">Can't drag, or on a phone? Copy it instead</button>
        <p id="bookmark-note" class="note info" role="status" hidden></p>
      </div>
      <div class="step">
        <span class="step-no">In every call</span>
        <strong>Click Live transcript</strong>
        <p>A Live transcription tab opens in the call's sidebar, next to Shared items.</p>
        <figure id="how-click" class="browser" role="img" aria-label="A Talk call in a browser. Live transcript is highlighted in the bookmarks bar, and the transcript is open in a Live transcription tab in the right sidebar, after Chat, Participants and Shared items.">
          <div class="tabs"><span class="light"></span><span class="light"></span><span class="light"></span><span class="tab">Talk</span></div>
          <div class="address"><div></div></div>
          <div class="bar"><span class="mark click">Live transcript</span><span class="bar-hint">← Click</span></div>
          <div class="callview">
            <div class="stage">
              <div class="stage-mid"><span class="av"></span></div>
              <div class="ctl"><span><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg></span><span><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="7" width="12" height="10" rx="2"/><path d="M15 11l6-3v8l-6-3"/></svg></span><span><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M8 14c1.2 1.6 2.4 2 4 2s2.8-.4 4-2M9 10v.5M15 10v.5"/></svg></span><span><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 11h4M14 11h3M7 14.5h10"/></svg></span><span class="leave"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 14c5-5 11-5 16 0"/></svg></span></div>
            </div>
            <div class="side">
              <div class="side-tabs"><span>Chat</span><span>Participants</span><span>Shared items</span><span class="on"><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="6" width="18" height="12" rx="2"/><path d="M7 11h4M14 11h3M7 14.5h10"/></svg>Live transcription</span></div>
              <div class="side-body"><span class="who">Ana</span><span class="line"></span><span class="line short"></span><span class="who">Ben</span><span class="line"></span></div>
            </div>
          </div>
        </figure>
        <p id="talk-note" class="small muted">Note: Talk's Enable live transcription button will not control it.</p>
        <a id="open-talk" class="plain" href="#">Open Talk</a>
      </div>
    </div>
    <div class="know-wrap">
      <h3>What to expect</h3>
      <dl class="know">
        <div><dt>Languages</dt><dd>English, German, French, Spanish, Italian, Dutch and Polish, detected automatically.</dd></div>
        <div><dt>Privacy</dt><dd>Audio and captions stay on this server. Nothing is saved, and the transcript is gone when you close the call.</dd></div>
        <div><dt>Guests</dt><dd>People who join from a public link are captioned, but can't see the Live transcription tab themselves.</dd></div>
      </dl>
    </div>
  </section>

  <footer class="foot">
    <span id="version">Cassini Live</span>
    <a id="docs-link" href="#" target="_blank" rel="noopener noreferrer">Documentation</a>
    <a id="limitations-link" href="#" target="_blank" rel="noopener noreferrer">Known limitations</a>
    <a id="issues-link" href="#" target="_blank" rel="noopener noreferrer">Report a problem</a>
  </footer>
</div>
<div id="drop-hint" class="drop-hint" role="status" hidden>Drop it on the bookmarks bar</div>
`

export const STYLES = `
/* Nextcloud's #content is a fixed-height flex box: fill it and scroll inside. */
:host { all: initial; display: block; flex: 1 1 100%; width: 100%; height: 100%; overflow-y: auto; }
.page {
  /* saturn-light */
  --b1: oklch(99.2% 0.008 104.031); --b2: oklch(91.8% 0.016 103.394); --b3: oklch(85.8% 0.014 103.384); --bc: oklch(13.1% 0.029 65.152);
  --p: oklch(62% 0.085 215); --pc: oklch(100% 0 0);
  --su: oklch(70.5% 0.147 152.3); --wa: oklch(82% 0.189 84.429);
  --bg: var(--b1); --fg: var(--bc); --soft: var(--b2); --border: var(--b3);
  --muted: color-mix(in oklab, var(--bc) 68%, var(--b1));
  --primary: var(--p); --on-primary: var(--pc);
  /* The light primary is too pale for small text: darken it towards the text colour. */
  --primary-text: color-mix(in oklab, var(--p) 62%, var(--bc));
  --info-bg: color-mix(in oklab, var(--p) 18%, var(--b1)); --info-fg: var(--bc);
  --good: color-mix(in oklch, var(--su) 50%, var(--bc)); --good-bg: color-mix(in oklab, var(--su) 18%, var(--b1));
  --warn: color-mix(in oklch, var(--wa) 50%, var(--bc)); --warn-bg: color-mix(in oklab, var(--wa) 18%, var(--b1));
  --quiet-border: color-mix(in oklab, var(--bc) 30%, transparent);
  --r-box: 0.5rem; --r-field: 0.5rem; --r-sel: 0.32rem;
  /* Talk's call area is dark in both themes; its sidebar follows the theme. */
  --call-bg: #0d0f11; --call-fg: #e9ecee; --call-ctl: #24332d; --call-line: #2e3438; --call-leave: #e5484d;
  color-scheme: light;
  color: var(--fg); background: var(--bg);
  font-family: ui-sans-serif, system-ui, sans-serif;
  font-size: 15px; line-height: 1.5; min-height: 100%;
}
@media (prefers-color-scheme: dark) {
  .page {
    /* saturn-dark */
    --b1: oklch(26% 0 0); --b2: oklch(14% 0 0); --b3: oklch(36% 0 0); --bc: oklch(91.8% 0.016 103.394);
    --p: oklch(80.5% 0.0775 215); --pc: oklch(0% 0 0);
    color-scheme: dark;
  }
}
*, *::before, *::after { box-sizing: border-box; }
[hidden] { display: none !important; }
.wrap { max-width: 960px; margin: 0 auto; padding: 32px 24px 40px; display: flex; flex-direction: column; gap: 28px; }
h1 { margin: 0; font-size: 28px; line-height: 1.2; font-weight: 700; }
h2 { margin: 0; font-size: 19px; font-weight: 600; }
h3 { margin: 0; font-size: 15px; font-weight: 600; }
p { margin: 0; }
a { color: var(--primary-text); }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.head { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.tile { display: flex; align-items: center; justify-content: center; width: 56px; height: 56px; border-radius: var(--r-box); background: var(--primary); flex: none; }
.titles { flex: 1 1 280px; min-width: 0; }
.pill { display: inline-flex; align-items: center; gap: 8px; padding: 8px 14px; border-radius: var(--r-sel); font-weight: 600; font-size: 14px; background: var(--soft); color: var(--muted); }
.pill .dot { width: 9px; height: 9px; border-radius: 50%; background: currentColor; }
.pill.ready { background: var(--good-bg); color: var(--good); }
.pill.setup { background: var(--warn-bg); color: var(--warn); }
.pill.off { background: var(--warn-bg); color: var(--warn); }
.card { border: 1px solid var(--border); border-radius: var(--r-box); padding: 24px; display: flex; flex-direction: column; gap: 18px; }
.card.setup { border: 2px solid var(--warn); }
.card-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.card-head .tag { margin-left: auto; }
.tag { padding: 2px 10px; border-radius: var(--r-sel); border: 1px solid var(--border); font-size: 12px; font-weight: 600; }
.note { padding: 12px 14px; border-radius: var(--r-box); font-size: 14px; }
.note.info { background: var(--info-bg); color: var(--info-fg); }
.note.warning { background: var(--warn-bg); color: var(--warn); font-weight: 600; }
.setup-list { display: flex; flex-direction: column; gap: 12px; }
.setup-step { display: flex; gap: 14px; align-items: flex-start; flex-wrap: wrap; padding: 14px; border: 1px solid var(--border); border-radius: var(--r-box); }
.setup-step .num { display: flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 50%; border: 2px solid var(--warn); color: var(--warn); font-weight: 700; font-size: 14px; flex: none; }
.setup-step.done .num { border-color: var(--good); color: var(--good); }
.setup-text { flex: 1 1 320px; min-width: 0; }
.setup-text p { margin-top: 2px; font-size: 14px; }
.setup-state { display: inline-flex; align-items: center; color: var(--muted); font-size: 14px; }
.setup-step .setup-state { min-height: 44px; }
.engine-line { display: flex; flex-wrap: wrap; gap: 4px 12px; justify-content: space-between; padding: 0 2px; font-size: 14px; color: var(--muted); }
.primary, .secondary { display: inline-flex; align-items: center; min-height: 44px; padding: 0 18px; border-radius: var(--r-field); font-weight: 600; text-decoration: none; }
.primary { background: var(--primary); color: var(--on-primary); }
.secondary { border: 1px solid var(--quiet-border); color: var(--fg); }
.steps { display: flex; flex-wrap: wrap; gap: 16px; }
.step { flex: 1 1 300px; min-width: 0; background: var(--soft); border-radius: var(--r-box); padding: 18px; display: flex; flex-direction: column; gap: 10px; }
.step p { font-size: 14px; }
.step p.small { font-size: 13px; }
.step-no { font-size: 13px; font-weight: 600; color: var(--primary-text); }
.bookmark { align-self: flex-start; display: inline-flex; align-items: center; gap: 8px; min-height: 44px; padding: 0 16px 0 10px; border: 2px dashed var(--primary); border-radius: var(--r-field); background: var(--bg); color: var(--primary-text); font-weight: 600; text-decoration: none; cursor: grab; }
.bookmark:active { cursor: grabbing; }
kbd { padding: 1px 5px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg); font: inherit; font-size: 12px; }
.linklike { align-self: flex-start; min-height: 44px; padding: 0; border: 0; background: none; color: var(--primary-text); font: inherit; font-size: 13px; text-align: left; text-decoration: underline; cursor: pointer; }
.plain { align-self: flex-start; display: inline-flex; align-items: center; min-height: 44px; font-size: 14px; font-weight: 600; }
.browser { margin: 0; max-width: 420px; border: 1px solid var(--border); border-radius: var(--r-box); background: var(--bg); overflow: hidden; }
.tabs { display: flex; align-items: center; gap: 6px; padding: 8px 10px; background: var(--soft); border-bottom: 1px solid var(--border); }
.light { width: 9px; height: 9px; border-radius: 50%; background: var(--muted); opacity: 0.6; }
.tab { margin-left: 8px; padding: 3px 14px; border-radius: 6px 6px 0 0; background: var(--bg); font-size: 11px; }
.address { padding: 6px 10px; border-bottom: 1px solid var(--border); }
.address div { height: 16px; border-radius: 8px; background: var(--soft); }
.bar { display: flex; align-items: center; gap: 8px; padding: 6px 10px; background: var(--info-bg); border-bottom: 1px solid var(--border); }
.mark { padding: 2px 10px; border-radius: 5px; background: var(--bg); border: 1px solid var(--border); font-size: 11px; }
.mark.slot { border: 2px dashed var(--primary); font-weight: 600; color: var(--primary-text); }
.mark.click { background: var(--primary); border-color: var(--primary); color: var(--on-primary); font-weight: 600; }
.bar-label, .bar-hint { font-size: 11px; font-weight: 600; color: var(--info-fg); }
.bar-label { margin-left: auto; }
.drag { display: block; width: 100%; height: auto; }
.i-chip { fill: var(--bg); stroke: var(--primary); stroke-width: 2; stroke-dasharray: 5 4; }
.i-chip-text { font-size: 12px; font-weight: 600; fill: var(--primary-text); }
.i-arrow { fill: none; stroke: var(--primary); stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; }
.callview { display: flex; min-height: 150px; background: var(--call-bg); color: var(--call-fg); }
.callview svg { width: 11px; height: 11px; fill: none; stroke: currentColor; stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round; flex: none; }
.stage { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; }
.stage-mid { flex: 1; display: flex; align-items: center; justify-content: center; }
.av { width: 38px; height: 38px; border-radius: 50%; background: var(--call-line); }
.ctl { display: flex; align-items: center; gap: 4px; padding: 8px; }
.ctl span { width: 18px; height: 18px; border-radius: 5px; background: var(--call-ctl); flex: none; display: flex; align-items: center; justify-content: center; }
.ctl .leave { width: 22px; margin-left: auto; background: var(--call-leave); }
.side { flex: 0 0 50%; min-width: 0; background: var(--bg); color: var(--fg); border-left: 1px solid var(--border); display: flex; flex-direction: column; font-size: 9px; }
.side-tabs { display: flex; gap: 6px; padding: 7px 8px 0; border-bottom: 1px solid var(--border); color: var(--muted); }
.side-tabs span { flex: 1 1 0; min-width: 0; padding-bottom: 5px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; text-align: center; }
.side-tabs .on { flex: none; color: var(--primary-text); font-weight: 700; border-bottom: 2px solid var(--primary); }
.side-tabs .on svg { display: inline-block; vertical-align: -2px; margin-right: 3px; }
.side-body { padding: 8px 10px; display: flex; flex-direction: column; gap: 5px; }
.who { font-weight: 600; margin-top: 2px; }
.line { height: 5px; border-radius: 3px; background: var(--border); }
.line.short { width: 60%; }
.know-wrap { display: flex; flex-direction: column; gap: 10px; padding-top: 16px; border-top: 1px solid var(--border); }
.know { margin: 0; display: flex; flex-direction: column; gap: 8px; font-size: 14px; }
.know div { display: flex; flex-wrap: wrap; gap: 2px 12px; }
.know dt { flex: 0 0 90px; font-weight: 600; }
.know dd { flex: 1 1 280px; min-width: 0; margin: 0; color: var(--muted); }
.facts { margin: 0; display: flex; flex-wrap: wrap; gap: 12px 32px; }
.facts div { flex: 1 1 180px; min-width: 0; }
.facts dt { color: var(--muted); font-size: 13px; }
.facts dd { margin: 0; font-weight: 600; }
.fact-value.good { color: var(--good); }
.fact-value.warning { color: var(--warn); }
.links { display: flex; flex-wrap: wrap; gap: 16px; }
.link-card { flex: 1 1 280px; min-width: 0; display: flex; gap: 14px; align-items: flex-start; padding: 16px; border: 1px solid var(--border); border-radius: var(--r-box); text-decoration: none; color: var(--fg); }
.link-card:hover { border-color: var(--primary); }
.link-card strong { display: block; color: var(--primary-text); }
.link-card .small { display: block; }
.link-icon { display: flex; align-items: center; justify-content: center; width: 40px; height: 40px; border-radius: var(--r-box); background: var(--info-bg); color: var(--primary-text); flex: none; }
.foot { display: flex; flex-wrap: wrap; gap: 8px 20px; padding-top: 16px; border-top: 1px solid var(--border); color: var(--muted); font-size: 14px; }
a:focus-visible, button:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.drop-hint { position: fixed; top: 60px; left: 50%; transform: translateX(-50%); z-index: 2000; padding: 10px 18px; border-radius: var(--r-field); background: var(--primary); color: var(--on-primary); font-weight: 600; box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25); pointer-events: none; }
`
