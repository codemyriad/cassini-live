/* Cassini Live home page v0.1.0 — https://github.com/codemyriad/cassini-live */
(()=>{var m="__CASSINI_LIVE_APP_ID__";function v(e,t){let n=`/apps/app_api/proxy/${e}/${t}`;try{if(window.OC&&typeof window.OC.generateUrl=="function")return window.OC.generateUrl(n)}catch{}return"/index.php"+n}var $={kind:"ready",text:"Captions ready"};function B(e,t){let n=e?.check,o=!!e?.secretConfigured&&(n?n.state==="passed":!!t?.signallingConfigured),r="Enter the internal secret of Talk's high-performance backend. Cassini Live checks it right away.";return e?.unreadable?r="The saved Talk connection could not be read. Open settings to see how to restore it.":e?.secretConfigured&&n&&n.state!=="passed"?r=`The last check did not pass: ${S(n.message)}`:e?.secretConfigured&&!o?r="The secret is saved. Cassini Live is still loading Talk's signalling settings.":o&&(r="The high-performance backend accepted the internal secret."),{id:"talk",title:"Connect to Talk",done:o,detail:r,action:"settings"}}function D(e,t,n){let o=!!e?.cpuQuota&&!!e?.memoryLimitBytes,r=n&&t?.state==="blocked"&&t?.phase==="configuration",i=o&&!r,c="The container needs enforced CPU, memory and process limits, with swap off. This is done on the server, not here.";return r&&t.reason?c=S(t.reason):i&&(c="CPU and memory limits are in place."),{id:"limits",title:"Set container limits",done:i,detail:c,action:"limits-docs"}}function N(e,t){let n=e?.state==="ready",o="Happens by itself once the first two are done. This page then shows Captions ready.",r="Waiting";return n?(o="The speech engine is loaded.",r="Done"):t&&(r=e?.phase==="loading"?"Loading":"Starting",e?.reason&&(o=S(e.reason))),{id:"engine",title:"Speech engine loads",done:n,detail:o,state:r}}function S(e){let t=String(e||"").trim();return t?/[.!?…]$/.test(t)?t:`${t}.`:""}function P(e){if(!e?.secretConfigured)return{text:"Internal secret not set",tone:"warning"};let t=e.check;return t?t.state==="passed"?{text:"Passed",tone:"good"}:{text:t.state==="failed"?"Check failed":"Backend unreachable",tone:"warning"}:{text:"Secret saved, not checked since restart",tone:"neutral"}}function _({status:e,admin:t}){if(!e)return{pill:{kind:"off",text:"Cannot reach Cassini Live"},userNote:"Cassini Live did not answer. Reload this page; if it stays like this, tell an administrator.",setup:null,facts:null};let n=e.resourceSafety||{},o=!!e.enabled&&!!e.signallingConfigured&&n.state==="ready",r=null,i=null;if(t){let f=t.talk||{},p=B(f,e),k=D(t.panel?.host,n,p.done),w=N(n,p.done&&k.done);o||(r=[p,k,w]);let x=Number(e.activeRooms)||0;i={engine:e.model?n.state==="ready"?e.model:`${e.model}, not loaded`:"Unknown",talk:P(f),calls:x===0?"None right now":x===1?"1 call":`${x} calls`}}if(o)return{pill:$,userNote:null,setup:r,facts:i};if(t)return{pill:{kind:"setup",text:r.slice(0,2).some(p=>!p.done)?"Setup needed":"Starting"},userNote:null,setup:r,facts:i};let c=n.state==="recovering"&&!n.adminActionRequired;return{pill:c?{kind:"setup",text:"Captions are starting"}:{kind:"setup",text:"Captions are not set up yet"},userNote:c?"Cassini Live is starting. Captions will work in a moment.":"Captions are not set up yet. Ask an administrator to finish setting up Cassini Live.",setup:null,facts:null}}var b=e=>`<kbd>${e}</kbd>`,I=`
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
          <p class="small muted">No bookmarks bar? Press ${b("Ctrl")} ${b("Shift")} ${b("B")}, or ${b("Cmd")} ${b("Shift")} ${b("B")} on a Mac.</p>
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
      <div><dt>Speech engine</dt><dd id="fact-engine" class="fact-value">\u2013</dd></div>
      <div><dt>Talk connection</dt><dd id="fact-talk" class="fact-value">\u2013</dd></div>
      <div><dt>Calls with captions</dt><dd id="fact-calls" class="fact-value">\u2013</dd></div>
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
`,E=`
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
`;var O="https://github.com/codemyriad/cassini-live/blob/main/docs/install.md",U=`${O}#with-an-existing-deploy-daemon`,R="https://github.com/codemyriad/cassini-live/blob/main/docs/known-limitations.md",j="https://github.com/codemyriad/cassini-live/issues",Y=15e3,F=4e3,M="cassini-live-home";function H(e){try{if(window.OC&&typeof window.OC.generateUrl=="function")return window.OC.generateUrl(e)}catch{}return`/index.php${e}`}function G(){try{return!!(window.OC&&typeof window.OC.isUserAdmin=="function"&&window.OC.isUserAdmin())}catch{return!1}}async function A(e){try{let t=await fetch(v(m,e),{credentials:"same-origin",cache:"no-store",headers:{Accept:"application/json","X-Cassini-Request":"1"}});return t.ok?await t.json():null}catch{return null}}function l(e,t,n){let o=document.createElement(e);return t&&(o.className=t),n!==void 0&&(o.textContent=n),o}function V(e){let t=l("div");t.id=M;let n=t.attachShadow({mode:"open"}),o=l("style");o.textContent=E;let r=l("div","page");r.innerHTML=I,n.append(o,r),e.replaceChildren(t);let i=a=>n.getElementById(a),c='javascript:(()=>{let e=window.__CASSINI_LIVE_PANEL__;if(e&&"function"==typeof e.destroy)return void e.destroy();let n=document,t=n.querySelector("script[nonce]"),o=n.createElement("script");t&&t.nonce&&(o.nonce=t.nonce),o.src=function(){let e="/apps/app_api/proxy/__CASSINI_LIVE_APP_ID__/panel/panel.js";try{if(window.OC&&"function"==typeof window.OC.generateUrl)return window.OC.generateUrl(e)}catch{}return"/index.php"+e}()+"?_="+Date.now(),o.onerror=()=>{alert("Cassini Live transcript panel could not be loaded.\\nIs the cassini-live ExApp installed and enabled on this Nextcloud?")},(n.head||n.documentElement).appendChild(o)})();';i("icon").src=v(m,"img/app.svg"),i("open-talk").href=H("/apps/spreed/"),i("settings-link").href=v(m,"settings/index.html"),i("diagnostics-link").href=v(m,"diagnostics/"),i("docs-link").href=O,i("limitations-link").href=R,i("issues-link").href=j;let f=i("bookmark");f.setAttribute("href",c),f.addEventListener("click",a=>{a.preventDefault(),p("Drag it up to your bookmarks bar instead of clicking it here. It works inside a Talk call.")}),f.addEventListener("dragstart",()=>{i("drop-hint").hidden=!1}),f.addEventListener("dragend",()=>{i("drop-hint").hidden=!0,p("If Live transcript is now in your bookmarks bar, you are done here: join a Talk call and click it.")}),i("copy").addEventListener("click",async()=>{try{await navigator.clipboard.writeText(c),p("Copied. Add a new bookmark, name it Live transcript, and paste this as its address (URL).")}catch{p("Copying is not allowed here. Right-click Live transcript above and copy its link address instead.")}});function p(a){let g=i("bookmark-note");g.textContent=a,g.hidden=!1}function k(a){let g=i("setup"),u=i("setup-list");g.hidden=!a,a&&u.replaceChildren(...a.map((s,L)=>{let d=l("div",`setup-step${s.done?" done":""}`);d.dataset.step=s.id,d.append(l("span","num",s.done?"\u2713":String(L+1)));let T=l("div","setup-text");if(T.append(l("strong","",s.title),l("p","",s.detail)),d.append(T),s.done)d.append(l("span","setup-state","Done"));else if(s.action==="settings"){let h=l("a","primary","Open settings");h.href=v(m,"settings/index.html"),d.append(h)}else if(s.action==="limits-docs"){let h=l("a","secondary","How to set them");h.href=U,h.target="_blank",h.rel="noopener noreferrer",d.append(h)}else d.append(l("span","setup-state",s.state));return d}))}function w(a,g){let u=i("pill");u.className=`pill ${a.pill.kind}`,i("pill-text").textContent=a.pill.text;let s=i("user-note");s.hidden=!a.userNote,s.textContent=a.userNote||"",k(a.setup);let L=i("admin");if(L.hidden=!a.facts,a.facts){i("fact-engine").textContent=a.facts.engine;let d=i("fact-talk");d.textContent=a.facts.talk.text,d.className=`fact-value ${a.facts.talk.tone}`,i("fact-calls").textContent=a.facts.calls}i("version").textContent=g?.version?`Cassini Live ${g.version}`:"Cassini Live"}let x=null,y=!1;async function C(){if(clearTimeout(x),y)return;let[a,g]=await Promise.all([A("status"),G()?A("settings/api/state"):Promise.resolve(null)]);if(y)return;let u=_({status:a,admin:g});w(u,a),document.hidden||(x=setTimeout(C,u.pill.kind==="ready"?Y:F))}return document.addEventListener("visibilitychange",()=>{document.hidden||C()}),C(),{destroy(){y=!0,clearTimeout(x),t.remove()}}}function z(){let e=document.getElementById("content");!e||document.getElementById(M)||(window.__CASSINI_LIVE_HOME__=V(e))}document.readyState==="loading"?document.addEventListener("DOMContentLoaded",z):z();})();
