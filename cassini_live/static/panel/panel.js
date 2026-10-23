/* Cassini Live transcript panel v0.1.0 — https://github.com/codemyriad/cassini-live */
(()=>{var K=1;function z({now:d=Date.now}={}){let l=[],r=new Set;function o(n){for(let s of r)s(n,l)}function u(){return l.length?l[l.length-1]:null}function p(n){let s=u();return s&&s.sid===n?s:null}function c(n){return l.find(s=>s.sid===n&&s.partial)||null}function L(n){if(n.lines.length||n.partial)return;let s=l.indexOf(n);s!==-1&&(l.splice(s,1),o({type:"remove",block:n}))}function x(n,s){for(let m of[...l])m.sid===n&&m!==s&&m.partial&&(m.partial=null,o({type:"update",block:m}),L(m))}function S(n,s){let m={id:K++,sid:n,startedAt:s,lastActivityAt:s,lines:[],partial:null};return l.push(m),o({type:"add",block:m}),m}function v(n){let{sid:s,text:m,final:_,lang:E}=n;if(typeof s!="string"||s==="")return;let t=d(),i=p(s);if(_){if(typeof m!="string"||m==="")return;i||(i=S(s,t)),i.lines.push({text:m,lang:E||null,at:t}),i.partial=null,i.lastActivityAt=t,x(s,i),o({type:"final",block:i,line:i.lines[i.lines.length-1]});return}if(typeof m!="string"||m===""){x(s,null);return}i||(i=c(s)),i||(i=S(s,t)),x(s,i),i.partial={text:m,lang:E||null},i.lastActivityAt=t,o({type:"partial",block:i})}return{apply:v,get blocks(){return l},subscribe(n){return r.add(n),()=>r.delete(n)}}}function q({onEvent:d,onStatus:l}){let r=null,o=null,u=!1,p=n=>{!n||typeof n!="object"||d({sid:typeof n.id=="string"?n.id:"",text:typeof n.message=="string"?n.message:"",final:n.final!==!1,lang:typeof n.languageId=="string"?n.languageId:null})};function c(){try{let n=window.OCA?.Talk?.SimpleWebRTC;if(n&&typeof n.on=="function")return n}catch{}return null}function L(){if(u||r)return;let n=c();if(!n){l({state:"waiting"}),o=setTimeout(L,1e3);return}r=n,r.on("transcript",p),l({state:"attached"}),x()}function x(){u||(o=setTimeout(()=>{u||(c()!==r?(r&&typeof r.off=="function"&&r.off("transcript",p),r=null,L()):x())},1e3*5))}function S(){try{return r?.connection?.getSessionId?.()||null}catch{return null}}function v(){u=!0,o&&clearTimeout(o),r&&typeof r.off=="function"&&r.off("transcript",p),r=null}return L(),{destroy:v,ownSessionId:S,get attached(){return r!==null},get webrtc(){return r}}}function W({tap:d}){let l=new Map,r=null,o=t=>{t&&typeof t.id=="string"&&typeof t.name=="string"&&t.name&&l.set(t.id,t.name)};function u(){let t=d.webrtc;!t||t===r||typeof t.on!="function"||(r&&typeof r.off=="function"&&r.off("nick",o),r=t,t.on("nick",o))}function p(t,i){return!t||!i?!1:t===i?!0:Math.min(t.length,i.length)<8?!1:t.startsWith(i)||i.startsWith(t)}function c(){try{return window.OC?.getCurrentUser?.()||null}catch{return null}}function L(){try{let t=d.webrtc?.connection?.joinedUsers;return t&&typeof t=="object"?t:null}catch{return null}}function x(t){let i=L();if(!i)return null;for(let b of Object.keys(i)){if(!p(b,t))continue;let y=i[b]||{};return{displayName:y.user?.displayname||y.user?.displayName||null,userId:y.userid||y.userId||null}}return null}function S(t){try{let i=d.webrtc?.webrtc?.peers;if(!Array.isArray(i))return null;let b=i.find(y=>y&&p(y.id,t)&&typeof y.nick=="string"&&y.nick);return b?b.nick:null}catch{return null}}function v(t){for(let[i,b]of l)if(p(i,t))return b;return null}function n(t){try{for(let i of document.querySelectorAll(".video-container[id]")){let b=i.id.match(/^(?:placeholder-)?container_(.+)_video_incoming$/);if(!b||!p(b[1],t))continue;let y=i.querySelector(".video-container__user-name, .video-container__name, .displayname"),C=y&&y.textContent?y.textContent.trim():"";if(C)return C}}catch{}return null}function s(t){return t.length>12?`${t.slice(0,6)}\u2026${t.slice(-4)}`:t}function m(t){let i=d.ownSessionId();if(i&&p(i,t))return!0;let b=c(),y=x(t);return!!(b&&y&&y.userId&&y.userId===b.uid)}function _(t){if(u(),m(t))return{name:c()?.displayName||x(t)?.displayName||"You",own:!0,resolved:!0};let i=x(t),b=i&&(i.displayName||i.userId)||S(t)||v(t)||n(t);return b?{name:b,own:!1,resolved:!0}:{name:s(t),own:!1,resolved:!1}}function E(){r&&typeof r.off=="function"&&r.off("nick",o),r=null,l.clear()}return{resolve:_,destroy:E}}var U=`
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
`;function g(d,l,r){let o=document.createElement(d);return l&&(o.className=l),r!==void 0&&(o.textContent=r),o}function G(d){try{return new Date(d).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}catch{return""}}function J(){try{let d=document.body||document.documentElement;if(!d)return null;if(d.matches("[data-theme-dark], [data-theme-dark-highcontrast], .theme--dark"))return!0;if(d.matches("[data-theme-light], .theme--light"))return!1}catch{}return null}function F({store:d,names:l,shadow:r}){try{let a=new CSSStyleSheet;a.replaceSync(U),r.adoptedStyleSheets=[...r.adoptedStyleSheets,a]}catch{let a=document.createElement("style");a.textContent=U,r.appendChild(a)}let o=g("div","view auto"),u=J();u===!0&&(o.className="view dark"),u===!1&&(o.className="view");let p=g("div","header"),c=g("span","dot"),L=g("span","title","Live transcript"),x=g("span","ai-label","AI-generated");p.append(c,L,x);let S=g("div","body"),v=g("div","scroll");v.setAttribute("role","log"),v.setAttribute("aria-label","Live transcript");let n=g("div","hint","Waiting for a Talk call on this page\u2026"),s=g("div","marker","Transcript started here");s.hidden=!0;let m=g("div");v.append(n,s,m);let _=g("button","jump","\u2193 Jump to live");_.hidden=!0;let E=g("div","sr-only");E.setAttribute("aria-live","polite"),S.append(v,_,E),o.append(p,S),r.appendChild(o);let t=!0,i=(()=>{try{return window.matchMedia("(prefers-reduced-motion: reduce)").matches}catch{return!1}})();function b(){return v.scrollHeight-v.scrollTop-v.clientHeight<=48}function y(){v.scrollTo({top:v.scrollHeight,behavior:i?"auto":"smooth"})}v.addEventListener("scroll",()=>{t=b(),_.hidden=t}),_.addEventListener("click",()=>{t=!0,_.hidden=!0,y()});let C=new Map;function A(a,h){let k=l.resolve(h.sid);return a.speakerEl.textContent=k.name,a.resolved=k.resolved,a.root.classList.toggle("own",k.own),a.youChip.hidden=!k.own,k}function I(a){let h=g("div","block"),k=g("div","block-header"),e=g("span","speaker"),f=g("span","you-chip","you");f.hidden=!0;let w=g("span","time",G(a.startedAt));w.title=new Date(a.startedAt).toLocaleString(),k.append(e,f,w);let V=g("div"),O=g("p","line partial");O.setAttribute("dir","auto"),O.hidden=!0,h.append(k,V,O);let j={root:h,linesEl:V,partialEl:O,speakerEl:e,youChip:f,lineCount:0,block:a,resolved:!1};return C.set(a.id,j),m.appendChild(h),A(j,a),n.hidden=!0,s.hidden=!1,j}function H(a){let h=C.get(a.id);h||(h=I(a));let k=A(h,a);for(;h.lineCount<a.lines.length;){let e=a.lines[h.lineCount],f=g("p","line",e.text);f.setAttribute("dir","auto"),e.lang&&(f.lang=e.lang),h.linesEl.appendChild(f),h.lineCount++}return a.partial?(h.partialEl.textContent=a.partial.text,a.partial.lang&&(h.partialEl.lang=a.partial.lang),h.partialEl.hidden=!1):(h.partialEl.hidden=!0,h.partialEl.textContent=""),k}let N=d.subscribe(a=>{if(a.type==="remove"){let k=C.get(a.block.id);k&&(k.root.remove(),C.delete(a.block.id));return}let h=H(a.block);a.type==="final"&&a.line&&(E.textContent=`${h.name}: ${a.line.text}`),t&&y()}),M=setInterval(()=>{for(let a of C.values())a.resolved||A(a,a.block)},2e3);function R(a){if(c.classList.toggle("attached",a.state==="attached"),d.blocks.length){n.hidden=!0;return}n.hidden=!1,n.textContent=a.state==="attached"?"Connected. Turn on captions in the call (\u22EF \u2192 closed captions) to see the transcript. Lines appear here for everyone once Cassini Live starts listening.":"Waiting for a Talk call on this page \u2014 join the call, then turn on captions."}function T(){clearInterval(M),N(),C.clear(),o.remove()}return{setStatus:R,destroy:T}}var Y="cassini-live-tab",$="cassini-live-tabpanel",Q=500;function P(d,l,r){let o=document.createElement(d);return l&&(o.className=l),r!==void 0&&(o.textContent=r),o}function X({store:d,names:l}){let r=!1,o=null,u=null,p=null,c=!1,L=!1,x=null,S=null,v={state:"waiting"},n=new Set,s=()=>document.querySelector("#app-sidebar-vue"),m=()=>document.querySelector("#app-sidebar-vue .app-sidebar-tabs__nav"),_=()=>document.querySelector("#app-sidebar-vue .app-sidebar-tabs__content"),E=()=>[...document.querySelectorAll('#app-sidebar-vue .app-sidebar-tabs__nav button[role="tab"]')].filter(e=>e!==o);function t(){for(let e of E()){let f=e.getAttribute("aria-selected")==="true";f&&!x&&(x=e.className),!f&&!S&&(S=e.className)}}function i(){if(!o)return;t();let e=c?x:S;e&&(o.className=e),o.setAttribute("aria-selected",c?"true":"false")}function b(){if(c)for(let e of E())e.getAttribute("aria-selected")==="true"&&(x||(x=e.className),e.setAttribute("aria-selected","false"),S&&(e.className=S))}function y(){let e=P("button");e.id=Y,e.type="button",e.setAttribute("role","tab"),e.setAttribute("aria-controls",$),e.setAttribute("aria-label","Live transcription"),e.title="Live transcription",e.dataset.cassiniLiveTab="1";let f=P("span",void 0,"\u{1F4DD}");return f.style.fontSize="18px",f.style.lineHeight="1",e.appendChild(f),e.addEventListener("click",w=>{w.preventDefault(),w.stopPropagation(),I()}),e}function C(){let e=P("div");e.id=$,e.style.position="absolute",e.style.inset="0",e.style.zIndex="10",e.style.display="none",e.style.padding="0 10px",e.style.boxSizing="border-box",e.style.background="var(--color-main-background, #fff)",e.style.overflow="hidden";let f=P("div");f.style.height="100%";let w=f.attachShadow({mode:"open"});return e.appendChild(f),p=F({store:d,names:l,shadow:w}),p.setStatus(v),e}function A(){let e=m(),f=_();return!e||!f?!1:((!o||!o.isConnected)&&(o=y(),e.appendChild(o)),(!u||!u.isConnected)&&(getComputedStyle(f).position==="static"&&(f.style.position="relative"),u=C(),f.appendChild(u),u.style.display=c?"block":"none"),i(),!0)}function I(){c=!0,u&&(u.style.display="block"),i(),b()}function H(){c=!1,u&&(u.style.display="none"),i()}function N(e){if(!c)return;let f=e.composedPath?e.composedPath():[e.target];for(let w of f){if(w===o)return;if(w instanceof HTMLElement&&w.getAttribute&&w.getAttribute("role")==="tab"&&w.closest("#app-sidebar-vue .app-sidebar-tabs__nav")){H();return}}}function M(){for(let e of document.querySelectorAll(".transcript"))e instanceof HTMLElement&&(u&&u.contains(e)||e.dataset.cassiniLiveHidden||(e.dataset.cassiniLiveHidden="1",e.dataset.cassiniLivePrevDisplay=e.style.display,e.style.display="none",n.add(e)))}function R(){for(let e of n)e.dataset.cassiniLiveHidden&&(e.style.display=e.dataset.cassiniLivePrevDisplay||"",delete e.dataset.cassiniLiveHidden,delete e.dataset.cassiniLivePrevDisplay);n.clear()}function T(){if(s())return;let e=['button[aria-label*="conversation information" i]','button[aria-label*="open sidebar" i]','button[aria-label*="show details" i]'];for(let f of e){let w=document.querySelector(f);if(w){w.click();return}}}let a=setInterval(()=>{if(r)return;if(M(),!s()){T();return}A()&&!L&&(L=!0,I()),c&&b()},Q);document.addEventListener("click",N,!0),T(),A(),s()&&!L&&(L=!0,I());function h(e){v=e,p&&p.setStatus(e)}function k(){r=!0,clearInterval(a),document.removeEventListener("click",N,!0),R(),p&&p.destroy(),o&&o.remove(),u&&u.remove(),o=u=p=null}return{setStatus:h,destroy:k}}var B="__CASSINI_LIVE_PANEL__";function Z(){let d=z(),l=null,r={state:"waiting"},o=q({onEvent:c=>d.apply(c),onStatus:c=>{r=c,l&&l.setStatus(c)}}),u=W({tap:o});function p(){o.destroy(),u.destroy(),l&&l.destroy();try{delete window[B]}catch{window[B]=void 0}}l=X({store:d,names:u}),l.setStatus(r),window[B]={destroy:p,injectTranscript(c){d.apply({sid:typeof c.id=="string"?c.id:"",text:typeof c.message=="string"?c.message:"",final:c.final!==!1,lang:typeof c.languageId=="string"?c.languageId:null})},get attached(){return o.attached},version:"0.1.0"}}var D=window[B];D&&typeof D.destroy=="function"?D.destroy():Z();})();
