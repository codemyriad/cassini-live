/**
 * The home page's static markup and styles. Written here, never built from server
 * text: main.js fills the dynamic parts with textContent.
 *
 * Wording rule: the thing people drag is "the Live transcript bookmark", never a
 * "button", so it cannot be confused with Talk's controls or Nextcloud's menu.
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
    <div>
      <h2 id="setup-title">Finish setting up</h2>
      <p class="muted">Captions stay off until these are done. Only administrators see this list.</p>
    </div>
    <div id="setup-list" class="setup-list"></div>
  </section>

  <section class="card" aria-labelledby="steps-title">
    <div>
      <h2 id="steps-title">Get captions in your calls</h2>
      <p class="muted">Add one bookmark, once per browser. After that it is one click in any call.</p>
    </div>
    <div class="steps">
      <div class="step wide">
        <div class="step-text">
          <span class="step-no">Step 1</span>
          <strong>Add the Live transcript bookmark</strong>
          <p>It is an ordinary bookmark with one difference: clicking it opens the transcript in the call you are in, instead of taking you to another page.</p>
          <p class="strong">Drag this up to your bookmarks bar:</p>
          <a id="bookmark" class="bookmark" draggable="true" href="#">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="9" cy="6" r="1.6"/><circle cx="15" cy="6" r="1.6"/><circle cx="9" cy="12" r="1.6"/><circle cx="15" cy="12" r="1.6"/><circle cx="9" cy="18" r="1.6"/><circle cx="15" cy="18" r="1.6"/></svg>
            Live transcript
          </a>
          <p class="small muted">No bookmarks bar? Press ${KEY('Ctrl')} ${KEY('Shift')} ${KEY('B')}, or ${KEY('Cmd')} ${KEY('Shift')} ${KEY('B')} on a Mac.</p>
          <button id="copy" type="button" class="linklike">Can't drag it, or on a phone? Copy the bookmark and add it by hand</button>
          <p id="bookmark-note" class="note info" role="status" hidden></p>
        </div>
        <figure class="how">
          <div class="browser">
            <div class="tabs"><span class="light"></span><span class="light"></span><span class="light"></span><span class="tab">Cassini Live</span></div>
            <div class="address"><div></div></div>
            <div class="bar">
              <span class="mark">Mail</span><span class="mark">News</span><span class="mark slot">Live transcript</span>
              <span class="bar-label">Bookmarks bar</span>
            </div>
            <svg viewBox="0 0 420 120" role="img" aria-label="An arrow from Live transcript on the page up to the empty place in the bookmarks bar">
              <rect class="i-chip" x="28" y="72" width="128" height="30" rx="6"/>
              <text class="i-chip-text" x="92" y="92" text-anchor="middle">Live transcript</text>
              <path class="i-arrow" d="M160 86 C 215 82, 210 44, 188 14"/>
              <path class="i-arrow" d="M181 24 L188 12 L199 21"/>
              <text class="i-text" x="232" y="62">Press, hold and drag</text>
              <text class="i-text" x="232" y="79">it up to the bar</text>
            </svg>
          </div>
          <figcaption class="small muted">Drop it on the bar under the address field. It stays there, ready for every call.</figcaption>
        </figure>
      </div>
      <div class="step">
        <span class="step-no">Step 2</span>
        <strong>Join a Talk call</strong>
        <p>Open the conversation and start or join the call as usual.</p>
        <a id="open-talk" class="plain" href="#">Open Talk</a>
      </div>
      <div class="step">
        <span class="step-no">Step 3</span>
        <strong>Click the Live transcript bookmark</strong>
        <p>It is in your bookmarks bar. The transcript opens in the call's sidebar and captions start for you. Click the bookmark again to close it.</p>
      </div>
    </div>
    <p class="note info">Talk's own <strong>Show captions</strong> menu item does not control Cassini Live. Use the Live transcript bookmark instead.</p>
  </section>

  <section class="good">
    <div><strong>Seven languages</strong><span class="muted small">English, German, French, Spanish, Italian, Dutch and Polish, detected automatically.</span></div>
    <div><strong>Nothing is saved</strong><span class="muted small">Audio and captions stay on this server. The transcript is gone when the call page closes.</span></div>
    <div><strong>For logged-in people</strong><span class="muted small">Guests who join from a public link can be captioned, but cannot open the transcript.</span></div>
  </section>

  <section id="admin" class="card" aria-labelledby="admin-title" hidden>
    <div class="admin-head">
      <h2 id="admin-title">Administration</h2>
      <span class="tag">Only administrators see this</span>
    </div>
    <dl class="facts">
      <div><dt>Speech engine</dt><dd id="fact-engine" class="fact-value">–</dd></div>
      <div><dt>Talk connection</dt><dd id="fact-talk" class="fact-value">–</dd></div>
      <div><dt>Calls with captions</dt><dd id="fact-calls" class="fact-value">–</dd></div>
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

  <footer class="foot">
    <span id="version">Cassini Live</span>
    <a id="docs-link" href="#" target="_blank" rel="noopener noreferrer">Documentation</a>
    <a id="limitations-link" href="#" target="_blank" rel="noopener noreferrer">Known limitations</a>
    <a id="issues-link" href="#" target="_blank" rel="noopener noreferrer">Report a problem</a>
  </footer>
</div>
<div id="drop-hint" class="drop-hint" role="status" hidden>Drop it on your bookmarks bar, at the top of this window</div>
`

export const STYLES = `
/* Nextcloud's #content is a fixed-height flex box: fill it and scroll inside. */
:host { all: initial; display: block; flex: 1 1 100%; width: 100%; height: 100%; overflow-y: auto; }
.page {
  --bg: var(--color-main-background, #ffffff);
  --fg: var(--color-main-text, #222222);
  --muted: var(--color-text-maxcontrast, #5c5c5c);
  --border: var(--color-border-dark, #dbdbdb);
  --soft: var(--color-background-dark, #f5f5f5);
  --primary: var(--color-primary-element, #00679e);
  --on-primary: var(--color-primary-element-text, #ffffff);
  --info-bg: var(--color-primary-element-light, #eef5fa);
  --info-fg: var(--color-primary-element-light-text, #1d3f55);
  --good: var(--color-success-text, #1f5c30);
  --good-bg: rgba(45, 123, 65, 0.14);
  --warn: var(--color-warning-text, #6b4a00);
  --warn-bg: rgba(138, 97, 0, 0.14);
  color: var(--fg); background: var(--bg);
  font-family: var(--font-face, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif);
  font-size: 15px; line-height: 1.5; min-height: 100%;
}
*, *::before, *::after { box-sizing: border-box; }
[hidden] { display: none !important; }
.wrap { max-width: 960px; margin: 0 auto; padding: 32px 24px 40px; display: flex; flex-direction: column; gap: 28px; }
h1 { margin: 0; font-size: 28px; line-height: 1.2; font-weight: 700; }
h2 { margin: 0; font-size: 19px; font-weight: 600; }
p { margin: 0; }
a { color: var(--primary); }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.strong { font-weight: 600; }
.head { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.tile { display: flex; align-items: center; justify-content: center; width: 56px; height: 56px; border-radius: 14px; background: var(--primary); flex: none; }
.titles { flex: 1 1 280px; min-width: 0; }
.pill { display: inline-flex; align-items: center; gap: 8px; padding: 8px 14px; border-radius: 999px; font-weight: 600; font-size: 14px; background: var(--soft); color: var(--muted); }
.pill .dot { width: 9px; height: 9px; border-radius: 50%; background: currentColor; }
.pill.ready { background: var(--good-bg); color: var(--good); }
.pill.setup { background: var(--warn-bg); color: var(--warn); }
.pill.off { background: var(--warn-bg); color: var(--warn); }
.card { border: 1px solid var(--border); border-radius: 12px; padding: 24px; display: flex; flex-direction: column; gap: 18px; }
.card.setup { border: 2px solid var(--warn); }
.note { padding: 12px 14px; border-radius: 8px; font-size: 14px; }
.note.info { background: var(--info-bg); color: var(--info-fg); }
.note.warning { background: var(--warn-bg); color: var(--warn); font-weight: 600; }
.setup-list { display: flex; flex-direction: column; gap: 12px; }
.setup-step { display: flex; gap: 14px; align-items: flex-start; flex-wrap: wrap; padding: 14px; border: 1px solid var(--border); border-radius: 10px; }
.setup-step .num { display: flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 50%; border: 2px solid var(--warn); color: var(--warn); font-weight: 700; font-size: 14px; flex: none; }
.setup-step.done .num { border-color: var(--good); color: var(--good); }
.setup-text { flex: 1 1 320px; min-width: 0; }
.setup-text p { margin-top: 2px; font-size: 14px; }
.setup-state { display: inline-flex; align-items: center; min-height: 44px; color: var(--muted); font-size: 14px; }
.primary, .secondary { display: inline-flex; align-items: center; min-height: 44px; padding: 0 18px; border-radius: 8px; font-weight: 600; text-decoration: none; }
.primary { background: var(--primary); color: var(--on-primary); }
.secondary { border: 2px solid var(--primary); color: var(--primary); }
.steps { display: flex; flex-wrap: wrap; gap: 16px; }
.step { flex: 1 1 240px; min-width: 0; background: var(--soft); border-radius: 10px; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
.step p { font-size: 14px; }
.step.wide { flex: 1 1 100%; padding: 20px; flex-direction: row; flex-wrap: wrap; gap: 24px; align-items: center; }
.step-text { flex: 1 1 280px; min-width: 0; display: flex; flex-direction: column; gap: 10px; }
.step-no { font-size: 13px; font-weight: 600; color: var(--primary); }
.bookmark { align-self: flex-start; display: inline-flex; align-items: center; gap: 8px; min-height: 44px; padding: 0 16px 0 10px; border: 2px dashed var(--primary); border-radius: 8px; background: var(--bg); color: var(--primary); font-weight: 600; text-decoration: none; cursor: grab; }
.bookmark:active { cursor: grabbing; }
kbd { padding: 1px 5px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg); font: inherit; font-size: 12px; }
.linklike { align-self: flex-start; min-height: 44px; padding: 0; border: 0; background: none; color: var(--primary); font: inherit; font-size: 14px; text-align: left; text-decoration: underline; cursor: pointer; }
.plain { align-self: flex-start; display: inline-flex; align-items: center; min-height: 44px; font-size: 14px; font-weight: 600; }
.how { flex: 1 1 360px; min-width: 0; max-width: 440px; margin: 0; display: flex; flex-direction: column; gap: 8px; }
.browser { border: 1px solid var(--border); border-radius: 10px; background: var(--bg); overflow: hidden; }
.tabs { display: flex; align-items: center; gap: 6px; padding: 8px 10px; background: var(--soft); border-bottom: 1px solid var(--border); }
.light { width: 9px; height: 9px; border-radius: 50%; background: var(--muted); opacity: 0.6; }
.tab { margin-left: 8px; padding: 3px 14px; border-radius: 6px 6px 0 0; background: var(--bg); font-size: 11px; }
.address { padding: 6px 10px; border-bottom: 1px solid var(--border); }
.address div { height: 18px; border-radius: 9px; background: var(--soft); }
.bar { display: flex; align-items: center; gap: 8px; padding: 6px 10px; background: var(--info-bg); border-bottom: 1px solid var(--border); }
.mark { padding: 2px 10px; border-radius: 5px; background: var(--bg); border: 1px solid var(--border); font-size: 11px; }
.mark.slot { border: 2px dashed var(--primary); font-weight: 600; color: var(--primary); }
.bar-label { margin-left: auto; font-size: 11px; font-weight: 600; color: var(--info-fg); }
.browser svg { display: block; width: 100%; height: auto; }
.i-chip { fill: var(--bg); stroke: var(--primary); stroke-width: 2; stroke-dasharray: 5 4; }
.i-chip-text { font-size: 12px; font-weight: 600; fill: var(--primary); }
.i-arrow { fill: none; stroke: var(--primary); stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; }
.i-text { font-size: 12px; fill: var(--fg); }
.good { display: flex; flex-wrap: wrap; gap: 16px; }
.good div { flex: 1 1 240px; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.admin-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.tag { padding: 2px 10px; border-radius: 999px; border: 1px solid var(--border); font-size: 12px; font-weight: 600; }
.facts { margin: 0; display: flex; flex-wrap: wrap; gap: 12px 32px; }
.facts div { flex: 1 1 180px; min-width: 0; }
.facts dt { color: var(--muted); font-size: 13px; }
.facts dd { margin: 0; font-weight: 600; }
.fact-value.good { color: var(--good); }
.fact-value.warning { color: var(--warn); }
.links { display: flex; flex-wrap: wrap; gap: 16px; }
.link-card { flex: 1 1 280px; min-width: 0; display: flex; gap: 14px; align-items: flex-start; padding: 16px; border: 1px solid var(--border); border-radius: 10px; text-decoration: none; color: var(--fg); }
.link-card:hover { border-color: var(--primary); }
.link-card strong { display: block; color: var(--primary); }
.link-card .small { display: block; }
.link-icon { display: flex; align-items: center; justify-content: center; width: 40px; height: 40px; border-radius: 10px; background: var(--info-bg); color: var(--primary); flex: none; }
.foot { display: flex; flex-wrap: wrap; gap: 8px 20px; padding-top: 16px; border-top: 1px solid var(--border); color: var(--muted); font-size: 14px; }
a:focus-visible, button:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.drop-hint { position: fixed; top: 60px; left: 50%; transform: translateX(-50%); z-index: 2000; padding: 10px 18px; border-radius: 999px; background: var(--primary); color: var(--on-primary); font-weight: 600; box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25); pointer-events: none; }
`
