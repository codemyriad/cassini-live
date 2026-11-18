/* Cassini Live home page v0.1.0 — https://github.com/codemyriad/cassini-live */
(()=>{var b="__CASSINI_LIVE_APP_ID__";function m(e,t){let a=`/apps/app_api/proxy/${e}/${t}`;try{if(window.OC&&typeof window.OC.generateUrl=="function")return window.OC.generateUrl(a)}catch{}return"/index.php"+a}var O={kind:"ready",text:"Captions ready"};function P(e,t){let a=e?.check,s=!!e?.secretConfigured&&(a?a.state==="passed":!!t?.signallingConfigured),r="Enter the internal secret of Talk's high-performance backend. Cassini Live checks it right away.";return e?.unreadable?r="The saved Talk connection could not be read. Open settings to see how to restore it.":e?.secretConfigured&&a&&a.state!=="passed"?r=`The last check did not pass: ${T(a.message)}`:e?.secretConfigured&&!s?r="The secret is saved. Cassini Live is still loading Talk's signalling settings.":s&&(r="Connected."),{id:"talk",title:"Connect to Talk",done:s,detail:r,action:"settings"}}function $(e,t,a){let s=!!e?.cpuQuota&&!!e?.memoryLimitBytes,r=a&&t?.state==="blocked"&&t?.phase==="configuration",i=s&&!r,p="Give the container CPU, memory and process limits, and turn swap off. You do this on the server that runs the container.";return r&&t.reason?p=T(t.reason):i&&(p="Limits are set."),{id:"limits",title:"Set container limits",done:i,detail:p,action:"limits-docs"}}function D(e,t){let a=e?.state==="ready",s="The speech engine starts on its own once both are done.",r="Waiting";return a?(s="The speech engine is loaded.",r="Done"):t&&(r=e?.phase==="loading"?"Loading":"Starting",e?.reason&&(s=T(e.reason))),{id:"engine",title:"Speech engine",done:a,detail:s,state:r}}function T(e){let t=String(e||"").trim();return t?/[.!?…]$/.test(t)?t:`${t}.`:""}function U(e){if(!e?.secretConfigured)return{text:"Not connected",tone:"warning"};let t=e.check;return t?t.state==="passed"?{text:"Connected",tone:"good"}:{text:t.state==="failed"?"Check failed":"Backend unreachable",tone:"warning"}:{text:"Not checked since restart",tone:"neutral"}}function _({status:e,admin:t}){if(!e)return{pill:{kind:"off",text:"Not responding"},userNote:"Cassini Live isn't responding. Reload the page. If that doesn't help, tell an administrator.",setup:null,facts:null};let a=e.resourceSafety||{},s=!!e.enabled&&!!e.signallingConfigured&&a.state==="ready",r=null,i=null;if(t){let g=t.talk||{},d=P(g,e),w=$(t.panel?.host,a,d.done),C=D(a,d.done&&w.done);if(!s)r=[d,w,C];else{let x=Number(e.activeRooms)||0;i={engine:e.model||"Unknown",talk:U(g),calls:x===0?"None":x===1?"1 call":`${x} calls`}}}if(s)return{pill:O,userNote:null,setup:r,facts:i};if(t)return{pill:{kind:"setup",text:r.slice(0,2).some(d=>!d.done)?"Setup needed":"Starting"},userNote:null,setup:r,facts:i};let p=a.state==="recovering"&&!a.adminActionRequired;return{pill:p?{kind:"setup",text:"Starting"}:{kind:"setup",text:"Not set up yet"},userNote:p?"Cassini Live is starting. Give it a moment.":"Captions aren't working yet. An administrator needs to finish setting up Cassini Live.",setup:null,facts:null}}var k=e=>`<kbd>${e}</kbd>`,A=`
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
      <div><dt>Speech engine</dt><dd id="fact-engine" class="fact-value">\u2013</dd></div>
      <div><dt>Talk connection</dt><dd id="fact-talk" class="fact-value">\u2013</dd></div>
      <div><dt>Calls being captioned</dt><dd id="fact-calls" class="fact-value">\u2013</dd></div>
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
        <p class="small muted">Can't see the bar? Press ${k("Ctrl")} + ${k("Shift")} + ${k("B")} (${k("Cmd")} + ${k("Shift")} + ${k("B")} on a Mac).</p>
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
          <div class="bar"><span class="mark click">Live transcript</span><span class="bar-hint">\u2190 Click</span></div>
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
`,E=`
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
`;var I="https://github.com/codemyriad/cassini-live/blob/main/docs/install.md",j=`${I}#with-an-existing-deploy-daemon`,R="https://github.com/codemyriad/cassini-live/blob/main/docs/known-limitations.md",Y="https://github.com/codemyriad/cassini-live/issues",G=15e3,q=4e3,N="cassini-live-home";function F(e){try{if(window.OC&&typeof window.OC.generateUrl=="function")return window.OC.generateUrl(e)}catch{}return`/index.php${e}`}function V(){try{return!!(window.OC&&typeof window.OC.isUserAdmin=="function"&&window.OC.isUserAdmin())}catch{return!1}}async function z(e){try{let t=await fetch(m(b,e),{credentials:"same-origin",cache:"no-store",headers:{Accept:"application/json","X-Cassini-Request":"1"}});return t.ok?await t.json():null}catch{return null}}function l(e,t,a){let s=document.createElement(e);return t&&(s.className=t),a!==void 0&&(s.textContent=a),s}function W(e){let t=l("div");t.id=N;let a=t.attachShadow({mode:"open"}),s=l("style");s.textContent=E;let r=l("div","page");r.innerHTML=A,a.append(s,r),e.replaceChildren(t);let i=n=>a.getElementById(n),p='javascript:(()=>{let e=window.__CASSINI_LIVE_PANEL__;if(e&&"function"==typeof e.destroy)return void e.destroy();let n=document,t=n.querySelector("script[nonce]"),o=n.createElement("script");t&&t.nonce&&(o.nonce=t.nonce),o.src=function(){let e="/apps/app_api/proxy/__CASSINI_LIVE_APP_ID__/panel/panel.js";try{if(window.OC&&"function"==typeof window.OC.generateUrl)return window.OC.generateUrl(e)}catch{}return"/index.php"+e}()+"?_="+Date.now(),o.onerror=()=>{alert("Cassini Live transcript panel could not be loaded.\\nIs the cassini-live ExApp installed and enabled on this Nextcloud?")},(n.head||n.documentElement).appendChild(o)})();';i("icon").src=m(b,"img/app.svg"),i("open-talk").href=F("/apps/spreed/"),i("settings-link").href=m(b,"settings/index.html"),i("diagnostics-link").href=m(b,"diagnostics/"),i("docs-link").href=I,i("limitations-link").href=R,i("issues-link").href=Y;let g=i("bookmark");g.setAttribute("href",p),g.addEventListener("click",n=>{n.preventDefault(),d("This only works during a Talk call. Drag it to your bookmarks bar first.")}),g.addEventListener("dragstart",()=>{i("drop-hint").hidden=!1}),g.addEventListener("dragend",()=>{i("drop-hint").hidden=!0,d("Added? Join a Talk call and click it.")}),i("copy").addEventListener("click",async()=>{try{await navigator.clipboard.writeText(p),d("Copied. Add a new bookmark, name it Live transcript, and paste this as its address (URL).")}catch{d("Couldn't copy. Right-click Live transcript above and copy the link.")}});function d(n){let c=i("bookmark-note");c.textContent=n,c.hidden=!1}function w(n){let c=i("setup"),u=i("setup-list");if(c.hidden=!n,!n)return;let h=n.find(o=>o.id==="engine");i("engine-text").textContent=h?h.detail:"",i("engine-state").textContent=h?h.state:"",u.replaceChildren(...n.filter(o=>o!==h).map((o,y)=>{let f=l("div",`setup-step${o.done?" done":""}`);f.dataset.step=o.id,f.append(l("span","num",o.done?"\u2713":String(y+1)));let M=l("div","setup-text");if(M.append(l("strong","",o.title),l("p","",o.detail)),f.append(M),o.done)f.append(l("span","setup-state","Done"));else if(o.action==="settings"){let v=l("a","primary","Open settings");v.href=m(b,"settings/index.html"),f.append(v)}else if(o.action==="limits-docs"){let v=l("a","secondary","Read the guide");v.href=j,v.target="_blank",v.rel="noopener noreferrer",f.append(v)}return f}))}function C(n,c){let u=i("pill");u.className=`pill ${n.pill.kind}`,i("pill-text").textContent=n.pill.text;let h=i("user-note");h.hidden=!n.userNote,h.textContent=n.userNote||"",w(n.setup);let o=i("admin");if(o.hidden=!n.facts,n.facts){i("fact-engine").textContent=n.facts.engine;let y=i("fact-talk");y.textContent=n.facts.talk.text,y.className=`fact-value ${n.facts.talk.tone}`,i("fact-calls").textContent=n.facts.calls}i("version").textContent=c?.version?`Cassini Live ${c.version}`:"Cassini Live"}let x=null,L=!1;async function S(){if(clearTimeout(x),L)return;let[n,c]=await Promise.all([z("status"),V()?z("settings/api/state"):Promise.resolve(null)]);if(L)return;let u=_({status:n,admin:c});C(u,n),document.hidden||(x=setTimeout(S,u.pill.kind==="ready"?G:q))}return document.addEventListener("visibilitychange",()=>{document.hidden||S()}),S(),{destroy(){L=!0,clearTimeout(x),t.remove()}}}function B(){let e=document.getElementById("content");!e||document.getElementById(N)||(window.__CASSINI_LIVE_HOME__=W(e))}document.readyState==="loading"?document.addEventListener("DOMContentLoaded",B):B();})();
