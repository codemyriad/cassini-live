/* Cassini Live transcript panel v0.1.0 — https://github.com/codemyriad/cassini-live */
(()=>{var Q=1;function z({now:c=Date.now}={}){let i=[],r=new Set;function o(e){for(let l of r)l(e,i)}function f(){for(let e=i.length-1;e>=0;e--)if(!i[e].historical)return i[e];return null}function h(e){let l=f();return l&&l.sid===e&&l===i[i.length-1]?l:null}function g(e){return i.find(l=>!l.historical&&l.sid===e&&l.partial)||null}function m(e){if(e.lines.length||e.partial)return;let l=i.indexOf(e);l!==-1&&(i.splice(l,1),o({type:"remove",block:e}))}function b(e,l){for(let p of[...i])p.sid===e&&p!==l&&p.partial&&(p.partial=null,o({type:"update",block:p}),m(p))}function k(e,l,p=!1){let t={id:Q++,sid:e,startedAt:l,lastActivityAt:l,lines:[],partial:null,historical:p};return i.push(t),o({type:"add",block:t}),t}function x(e){let{sid:l,text:p,final:t,lang:u}=e;if(typeof l!="string"||l==="")return;let y=c(),s=h(l);if(t){if(typeof p!="string"||p==="")return;s||(s=k(l,y)),s.lines.push({text:p,lang:u||null,at:y}),s.partial=null,s.lastActivityAt=y,b(l,s),o({type:"final",block:s,line:s.lines[s.lines.length-1]});return}if(typeof p!="string"||p===""){b(l,null);return}s||(s=g(l)),s||(s=k(l,y)),b(l,s),s.partial={text:p,lang:u||null},s.lastActivityAt=y,o({type:"partial",block:s})}function v(e){if(Array.isArray(e))for(let l of e){let p=l&&l.speakerSessionId,t=l&&l.message;if(typeof p!="string"||p===""||typeof t!="string"||t==="")continue;let u=typeof l.t=="number"?l.t:c(),y=typeof l.langId=="string"?l.langId:null,s=i.length?i[i.length-1]:null;(!s||!s.historical||s.sid!==p)&&(s=k(p,u,!0)),s.lines.push({text:t,lang:y,at:u}),s.lastActivityAt=u,o({type:"final",block:s,line:s.lines[s.lines.length-1]})}}function A(){i=[],o({type:"reset"})}return{apply:x,seedHistory:v,reset:A,get blocks(){return i},get hasHistory(){return i.some(e=>e.historical)},subscribe(e){return r.add(e),()=>r.delete(e)}}}function W({onEvent:c,onStatus:i}){let r=null,o=null,f=!1,h=e=>{!e||typeof e!="object"||c({sid:typeof e.id=="string"?e.id:"",text:typeof e.message=="string"?e.message:"",final:e.final!==!1,lang:typeof e.languageId=="string"?e.languageId:null})};function g(){try{let e=window.OCA?.Talk?.SimpleWebRTC;if(e&&typeof e.on=="function")return e}catch{}return null}function m(){if(f||r)return;let e=g();if(!e){i({state:"waiting"}),o=setTimeout(m,1e3);return}r=e,r.on("transcript",h),i({state:"attached"}),b()}function b(){f||(o=setTimeout(()=>{f||(g()!==r?(r&&typeof r.off=="function"&&r.off("transcript",h),r=null,m()):b())},1e3*5))}function k(){try{return r?.connection?.getSessionId?.()||null}catch{return null}}function x(){f=!0,o&&clearTimeout(o),r&&typeof r.off=="function"&&r.off("transcript",h),r=null}m();function v(){try{let e=r?.connection?.currentRoomToken;if(typeof e=="string"&&e)return e}catch{}try{let e=location.pathname.match(/\/call\/([^/?#]+)/);if(e)return e[1]}catch{}return null}function A(){try{let e=r?.connection?.currentCallToken;return typeof e=="string"&&e?e:null}catch{return null}}return{destroy:x,ownSessionId:k,roomToken:v,callToken:A,get attached(){return r!==null},get webrtc(){return r}}}function $({tap:c}){let i=new Map,r=null,o=t=>{t&&typeof t.id=="string"&&typeof t.name=="string"&&t.name&&i.set(t.id,t.name)};function f(){let t=c.webrtc;!t||t===r||typeof t.on!="function"||(r&&typeof r.off=="function"&&r.off("nick",o),r=t,t.on("nick",o))}function h(t,u){return!t||!u?!1:t===u?!0:Math.min(t.length,u.length)<8?!1:t.startsWith(u)||u.startsWith(t)}function g(){try{return window.OC?.getCurrentUser?.()||null}catch{return null}}function m(){try{let t=c.webrtc?.connection?.joinedUsers;return t&&typeof t=="object"?t:null}catch{return null}}function b(t){let u=m();if(!u)return null;for(let y of Object.keys(u)){if(!h(y,t))continue;let s=u[y]||{};return{displayName:s.user?.displayname||s.user?.displayName||null,userId:s.userid||s.userId||null}}return null}function k(t){try{let u=c.webrtc?.webrtc?.peers;if(!Array.isArray(u))return null;let y=u.find(s=>s&&h(s.id,t)&&typeof s.nick=="string"&&s.nick);return y?y.nick:null}catch{return null}}function x(t){for(let[u,y]of i)if(h(u,t))return y;return null}function v(t){try{for(let u of document.querySelectorAll(".video-container[id]")){let y=u.id.match(/^(?:placeholder-)?container_(.+)_video_incoming$/);if(!y||!h(y[1],t))continue;let s=u.querySelector(".video-container__user-name, .video-container__name, .displayname"),I=s&&s.textContent?s.textContent.trim():"";if(I)return I}}catch{}return null}function A(t){return t.length>12?`${t.slice(0,6)}\u2026${t.slice(-4)}`:t}function e(t){let u=c.ownSessionId();if(u&&h(u,t))return!0;let y=g(),s=b(t);return!!(y&&s&&s.userId&&s.userId===y.uid)}function l(t){if(f(),e(t))return{name:g()?.displayName||b(t)?.displayName||"You",own:!0,resolved:!0};let u=b(t),y=u&&(u.displayName||u.userId)||k(t)||x(t)||v(t);return y?{name:y,own:!1,resolved:!0}:{name:A(t),own:!1,resolved:!1}}function p(){r&&typeof r.off=="function"&&r.off("nick",o),r=null,i.clear()}return{resolve:l,destroy:p}}function F({tap:c,store:i,fetchHistory:r,onChange:o=()=>{}}){let f=null,h=null,g=0,m=null,b=!1;async function k(v){let A=++g;i.reset(),o({roomToken:v,inCall:!0});let e=await r(v);b||A!==g||i.seedHistory(e)}function x(){if(b)return;let v=c.roomToken(),A=c.callToken(),e=!!A;(e&&!h||e&&v!==f)&&k(v),f=v,h=A}return m=setInterval(x,1e3),x(),{get inCall(){return!!c.callToken()},roomToken:()=>c.roomToken(),destroy(){b=!0,m&&clearInterval(m)}}}var X="/apps/app_api/proxy/live_transcription/history";function ee(c){let i=`?token=${encodeURIComponent(c)}`;try{if(window.OC&&typeof window.OC.generateUrl=="function")return window.OC.generateUrl(X)+i}catch{}return"/index.php"+X+i}async function Y(c){if(!c)return[];try{let i=await fetch(ee(c),{credentials:"same-origin",headers:{Accept:"application/json"}});if(!i.ok)return[];let r=await i.json();return Array.isArray(r?.entries)?r.entries:[]}catch{return[]}}var K=`
:host {
  display: block;
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Ubuntu, Cantarell, sans-serif;
  color-scheme: light dark;
}
.view {
  --bg: transparent;
  --fg: inherit;
  --muted: #767676;
  --border: #dadada;
  --accent: #0082c9;
  --accent-soft: rgba(0, 130, 201, 0.08);
  --chip-bg: #f0f0f0;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  color: var(--fg);
  font-size: 13px;
  line-height: 1.45;
}
.view.dark {
  --muted: #9e9e9e;
  --border: #3c3c3c;
  --accent: #4db1e8;
  --accent-soft: rgba(77, 177, 232, 0.12);
  --chip-bg: #2c2c2c;
}
@media (prefers-color-scheme: dark) {
  .view.auto {
    --muted: #9e9e9e;
    --border: #3c3c3c;
    --accent: #4db1e8;
    --accent-soft: rgba(77, 177, 232, 0.12);
    --chip-bg: #2c2c2c;
  }
}
.header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 2px;
  border-bottom: 1px solid var(--border);
  flex: none;
}
.dot { width: 8px; height: 8px; border-radius: 50%; background: #b0b0b0; flex: none; }
.dot.attached { background: #2d7b41; }
.title { font-weight: 600; flex: 1 1 auto; min-width: 0; }
.ai-label { font-size: 11px; color: var(--muted); flex: none; }
.body { position: relative; flex: 1 1 auto; display: flex; min-height: 0; }
.scroll { flex: 1 1 auto; overflow-y: auto; padding: 8px 2px; overscroll-behavior: contain; }
.hint { color: var(--muted); padding: 12px 4px; text-align: center; }
.marker {
  display: flex; align-items: center; gap: 8px;
  color: var(--muted); font-size: 11px; margin: 2px 0 8px;
}
.marker::before, .marker::after { content: ""; flex: 1; border-top: 1px solid var(--border); }
.block { margin: 0 0 10px; border-radius: 6px; padding: 2px 6px; }
.block.own { background: var(--accent-soft); }
.block-header { display: flex; align-items: baseline; gap: 6px; margin-bottom: 1px; }
.speaker { font-weight: 600; font-size: 12px; }
.block.own .speaker { color: var(--accent); }
.you-chip {
  font-size: 10px; color: var(--accent);
  border: 1px solid var(--accent); border-radius: 7px; padding: 0 5px;
}
.time { font-size: 10px; color: var(--muted); margin-left: auto; opacity: 0; transition: opacity 0.15s; }
.block:hover .time { opacity: 1; }
@media (prefers-reduced-motion: reduce) { .time { transition: none; } }
.line { margin: 0; }
.partial { color: var(--muted); font-style: italic; }
.partial::after { content: "\u258F"; opacity: 0.7; }
.jump {
  all: unset; position: absolute; bottom: 10px; left: 50%; transform: translateX(-50%);
  background: var(--accent); color: #ffffff; font-size: 12px; padding: 4px 12px;
  border-radius: 12px; cursor: pointer; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}
.jump:focus-visible { outline: 2px solid currentColor; }
.sr-only {
  position: absolute; width: 1px; height: 1px; overflow: hidden;
  clip-path: inset(50%); white-space: nowrap;
}
[hidden] { display: none !important; }
`;function S(c,i,r){let o=document.createElement(c);return i&&(o.className=i),r!==void 0&&(o.textContent=r),o}function te(c){try{return new Date(c).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}catch{return""}}function ne(){try{let c=document.body||document.documentElement;if(!c)return null;if(c.matches("[data-theme-dark], [data-theme-dark-highcontrast], .theme--dark"))return!0;if(c.matches("[data-theme-light], .theme--light"))return!1}catch{}return null}function G({store:c,names:i,shadow:r}){try{let a=new CSSStyleSheet;a.replaceSync(K),r.adoptedStyleSheets=[...r.adoptedStyleSheets,a]}catch{let a=document.createElement("style");a.textContent=K,r.appendChild(a)}let o=S("div","view auto"),f=ne();f===!0&&(o.className="view dark"),f===!1&&(o.className="view");let h=S("div","header"),g=S("span","dot"),m=S("span","title","Live transcript"),b=S("span","ai-label","AI-generated");h.append(g,m,b);let k=S("div","body"),x=S("div","scroll");x.setAttribute("role","log"),x.setAttribute("aria-label","Live transcript");let v=S("div","hint","Waiting for a Talk call on this page\u2026"),A=S("div","marker","You joined here"),e=!1,l=S("div");x.append(v,l);let p=S("button","jump","\u2193 Jump to live");p.hidden=!0;let t=S("div","sr-only");t.setAttribute("aria-live","polite"),k.append(x,p,t),o.append(h,k),r.appendChild(o);let u=!0,y=(()=>{try{return window.matchMedia("(prefers-reduced-motion: reduce)").matches}catch{return!1}})();function s(){return x.scrollHeight-x.scrollTop-x.clientHeight<=48}function I(){x.scrollTo({top:x.scrollHeight,behavior:y?"auto":"smooth"})}x.addEventListener("scroll",()=>{u=s(),p.hidden=u}),p.addEventListener("click",()=>{u=!0,p.hidden=!0,I()});let _=new Map;function E(a,w){let L=i.resolve(w.sid);return a.speakerEl.textContent=L.name,a.resolved=L.resolved,a.root.classList.toggle("own",L.own),a.youChip.hidden=!L.own,L}function V(a){let w=S("div","block"),L=S("div","block-header"),n=S("span","speaker"),d=S("span","you-chip","you");d.hidden=!0;let C=S("span","time",te(a.startedAt));C.title=new Date(a.startedAt).toLocaleString(),L.append(n,d,C);let H=S("div"),O=S("p","line partial");O.setAttribute("dir","auto"),O.hidden=!0,w.append(L,H,O);let D={root:w,linesEl:H,partialEl:O,speakerEl:n,youChip:d,lineCount:0,block:a,resolved:!1};return _.set(a.id,D),!e&&!a.historical&&c.hasHistory&&(l.appendChild(A),e=!0),l.appendChild(w),E(D,a),v.hidden=!0,D}function T(a){let w=_.get(a.id);w||(w=V(a));let L=E(w,a);for(;w.lineCount<a.lines.length;){let n=a.lines[w.lineCount],d=S("p","line",n.text);d.setAttribute("dir","auto"),n.lang&&(d.lang=n.lang),w.linesEl.appendChild(d),w.lineCount++}return a.partial?(w.partialEl.textContent=a.partial.text,a.partial.lang&&(w.partialEl.lang=a.partial.lang),w.partialEl.hidden=!1):(w.partialEl.hidden=!0,w.partialEl.textContent=""),L}function M(){for(let a of _.values())a.root.remove();_.clear(),A.remove(),e=!1,l.replaceChildren()}for(let a of c.blocks)T(a);let B=c.subscribe(a=>{if(a.type==="reset"){M(),v.hidden=!1;return}if(a.type==="remove"){let L=_.get(a.block.id);L&&(L.root.remove(),_.delete(a.block.id));return}let w=T(a.block);a.type==="final"&&a.line&&(t.textContent=`${w.name}: ${a.line.text}`),u&&I()}),R=setInterval(()=>{for(let a of _.values())a.resolved||E(a,a.block)},2e3);function N(a){if(g.classList.toggle("attached",a.state==="attached"),c.blocks.length){v.hidden=!0;return}v.hidden=!1,v.textContent=a.state==="attached"?"Connected. Turn on captions in the call (\u22EF \u2192 closed captions) to see the transcript. Lines appear here for everyone once Cassini Live starts listening.":"Waiting for a Talk call on this page \u2014 join the call, then turn on captions."}function j(){clearInterval(R),B(),_.clear(),o.remove()}return{setStatus:N,destroy:j}}var re="cassini-live-tab",J="cassini-live-tabpanel",oe=500,ie="M20,4A2,2 0 0,1 22,6V18A2,2 0 0,1 20,20H4A2,2 0 0,1 2,18V6A2,2 0 0,1 4,4H20M20,18V6H4V18H20M6,10H8V12H6V10M6,14H14V16H6V14M16,14H18V16H16V14M10,10H18V12H10V10Z";function q(c,i,r){let o=document.createElement(c);return i&&(o.className=i),r!==void 0&&(o.textContent=r),o}function Z({store:c,names:i}){let r=!1,o=null,f=null,h=null,g=!1,m=!1,b=null,k=null,x={state:"waiting"},v=new Set,A=()=>document.querySelector("#app-sidebar-vue"),e=()=>document.querySelector("#app-sidebar-vue .app-sidebar-tabs__nav"),l=()=>document.querySelector("#app-sidebar-vue .app-sidebar-tabs__content"),p=()=>[...document.querySelectorAll('#app-sidebar-vue .app-sidebar-tabs__nav button[role="tab"]')].filter(n=>n!==o);function t(){for(let n of p()){let d=n.getAttribute("aria-selected")==="true";d&&!b&&(b=n.className),!d&&!k&&(k=n.className)}}function u(){if(!o)return;t();let n=g?b:k;n&&(o.className=n),o.setAttribute("aria-selected",g?"true":"false")}function y(){if(g)for(let n of p())n.getAttribute("aria-selected")==="true"&&(b||(b=n.className),n.setAttribute("aria-selected","false"),k&&(n.className=k))}function s(){let n=p()[0],d;if(n){d=n.cloneNode(!0),d.removeAttribute("id"),d.removeAttribute("tabindex");for(let H of d.querySelectorAll("svg path"))H.setAttribute("d",ie);let C=d.querySelector('[class*="__name" i]');C?C.textContent="Live transcription":d.textContent="Live transcription"}else d=q("button",void 0,"Live transcription");return d.id=re,d.type="button",d.setAttribute("role","tab"),d.setAttribute("aria-controls",J),d.setAttribute("aria-label","Live transcription"),d.title="Live transcription",d.dataset.cassiniLiveTab="1",d.addEventListener("click",C=>{C.preventDefault(),C.stopPropagation(),E()}),d}function I(){let n=q("div");n.id=J,n.style.position="absolute",n.style.inset="0",n.style.zIndex="10",n.style.display="none",n.style.padding="0 10px",n.style.boxSizing="border-box",n.style.background="var(--color-main-background, #fff)",n.style.overflow="hidden";let d=q("div");d.style.height="100%";let C=d.attachShadow({mode:"open"});return n.appendChild(d),h=G({store:c,names:i,shadow:C}),h.setStatus(x),n}function _(){let n=e(),d=l();return!n||!d?!1:((!o||!o.isConnected)&&(o=s(),n.appendChild(o)),(!f||!f.isConnected)&&(getComputedStyle(d).position==="static"&&(d.style.position="relative"),f=I(),d.appendChild(f),f.style.display=g?"block":"none"),u(),!0)}function E(){g=!0,f&&(f.style.display="block"),u(),y()}function V(){g=!1,f&&(f.style.display="none"),u()}function T(n){if(!g)return;let d=n.composedPath?n.composedPath():[n.target];for(let C of d){if(C===o)return;if(C instanceof HTMLElement&&C.getAttribute&&C.getAttribute("role")==="tab"&&C.closest("#app-sidebar-vue .app-sidebar-tabs__nav")){V();return}}}function M(){for(let n of document.querySelectorAll(".transcript"))n instanceof HTMLElement&&(f&&f.contains(n)||n.dataset.cassiniLiveHidden||(n.dataset.cassiniLiveHidden="1",n.dataset.cassiniLivePrevDisplay=n.style.display,n.style.display="none",v.add(n)))}function B(){for(let n of v)n.dataset.cassiniLiveHidden&&(n.style.display=n.dataset.cassiniLivePrevDisplay||"",delete n.dataset.cassiniLiveHidden,delete n.dataset.cassiniLivePrevDisplay);v.clear()}function R(){try{return/\/call\/[^/]+/.test(location.pathname)&&!!document.querySelector("#app-content-vue")}catch{return!1}}function N(){B(),h&&(h.destroy(),h=null),o&&o.remove(),f&&f.remove(),o=f=null,m=!1}let j=setInterval(a,oe);function a(){if(r)return;if(!R()||!e()||!l()){(o||f)&&N();return}M(),_()&&!m&&(m=!0,E()),g&&y()}document.addEventListener("click",T,!0),a();function w(n){x=n,h&&h.setStatus(n)}function L(){r=!0,clearInterval(j),document.removeEventListener("click",T,!0),N()}return{setStatus:w,destroy:L}}var P="__CASSINI_LIVE_PANEL__";function se(){let c=z(),i=null,r={state:"waiting"},o=W({onEvent:m=>c.apply(m),onStatus:m=>{r=m,i&&i.setStatus(m)}}),f=$({tap:o}),h=F({tap:o,store:c,fetchHistory:Y});function g(){h.destroy(),o.destroy(),f.destroy(),i&&i.destroy();try{delete window[P]}catch{window[P]=void 0}}i=Z({store:c,names:f}),i.setStatus(r),window[P]={destroy:g,injectTranscript(m){c.apply({sid:typeof m.id=="string"?m.id:"",text:typeof m.message=="string"?m.message:"",final:m.final!==!1,lang:typeof m.languageId=="string"?m.languageId:null})},get attached(){return o.attached},version:"0.1.0"}}var U=window[P];U&&typeof U.destroy=="function"?U.destroy():se();})();
