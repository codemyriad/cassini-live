/* Cassini Live transcript panel v0.1.0 — https://github.com/codemyriad/cassini-live */
(()=>{var Q=1;function R({now:s=Date.now}={}){let l=[],o=new Set;function i(e){for(let r of o)r(e,l)}function u(){return l.length?l[l.length-1]:null}function f(e){let r=u();return r&&r.sid===e?r:null}function d(e){return l.find(r=>r.sid===e&&r.partial)||null}function h(e){if(e.lines.length||e.partial)return;let r=l.indexOf(e);r!==-1&&(l.splice(r,1),i({type:"remove",block:e}))}function y(e,r){for(let n of[...l])n.sid===e&&n!==r&&n.partial&&(n.partial=null,i({type:"update",block:n}),h(n))}function b(e,r){let n={id:Q++,sid:e,startedAt:r,lastActivityAt:r,lines:[],partial:null};return l.push(n),i({type:"add",block:n}),n}function S(e){let{sid:r,text:n,final:g,lang:x}=e;if(typeof r!="string"||r==="")return;let v=s(),c=f(r);if(g){if(typeof n!="string"||n==="")return;c||(c=b(r,v)),c.lines.push({text:n,lang:x||null,at:v}),c.partial=null,c.lastActivityAt=v,y(r,c),i({type:"final",block:c,line:c.lines[c.lines.length-1]});return}if(typeof n!="string"||n===""){y(r,null);return}c||(c=d(r)),c||(c=b(r,v)),y(r,c),c.partial={text:n,lang:x||null},c.lastActivityAt=v,i({type:"partial",block:c})}return{apply:S,get blocks(){return l},subscribe(e){return o.add(e),()=>o.delete(e)}}}function V({onEvent:s,onStatus:l}){let o=null,i=null,u=!1,f=e=>{!e||typeof e!="object"||s({sid:typeof e.id=="string"?e.id:"",text:typeof e.message=="string"?e.message:"",final:e.final!==!1,lang:typeof e.languageId=="string"?e.languageId:null})};function d(){try{let e=window.OCA?.Talk?.SimpleWebRTC;if(e&&typeof e.on=="function")return e}catch{}return null}function h(){if(u||o)return;let e=d();if(!e){l({state:"waiting"}),i=setTimeout(h,1e3);return}o=e,o.on("transcript",f),l({state:"attached"}),y()}function y(){u||(i=setTimeout(()=>{u||(d()!==o?(o&&typeof o.off=="function"&&o.off("transcript",f),o=null,h()):y())},1e3*5))}function b(){try{return o?.connection?.getSessionId?.()||null}catch{return null}}function S(){u=!0,i&&clearTimeout(i),o&&typeof o.off=="function"&&o.off("transcript",f),o=null}return h(),{destroy:S,ownSessionId:b,get attached(){return o!==null},get webrtc(){return o}}}function $({tap:s}){let l=new Map,o=null,i=e=>{e&&typeof e.id=="string"&&typeof e.name=="string"&&e.name&&l.set(e.id,e.name)};function u(){let e=s.webrtc;!e||e===o||typeof e.on!="function"||(o&&typeof o.off=="function"&&o.off("nick",i),o=e,e.on("nick",i))}function f(){try{let e=window.OC?.getCurrentUser?.();if(e&&e.displayName)return e.displayName}catch{}return null}function d(e){try{let r=s.webrtc?.webrtc?.peers;if(!Array.isArray(r))return null;let n=r.find(g=>g&&g.id===e&&typeof g.nick=="string"&&g.nick);return n?n.nick:null}catch{return null}}function h(e){try{for(let r of["container_","placeholder-container_"]){let n=document.getElementById(`${r}${e}_video_incoming`);if(!n)continue;let g=n.querySelector(".video-container__user-name, .video-container__name, .displayname"),x=g&&g.textContent?g.textContent.trim():"";if(x)return x}}catch{}return null}function y(e){return e.length>12?`${e.slice(0,6)}\u2026${e.slice(-4)}`:e}function b(e){u();let r=s.ownSessionId();if(r&&e===r)return{name:f()||"You",own:!0,resolved:!0};let n=d(e)||l.get(e)||h(e);return n?(l.set(e,n),{name:n,own:!1,resolved:!0}):{name:y(e),own:!1,resolved:!1}}function S(){o&&typeof o.off=="function"&&o.off("nick",i),o=null,l.clear()}return{resolve:b,destroy:S}}var j="cassini-live-panel-pos";var H=`
:host {
  all: initial;
  position: fixed;
  top: var(--clp-top, 72px);
  right: var(--clp-right, 16px);
  left: var(--clp-left, auto);
  z-index: 2147483000;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Ubuntu, Cantarell, sans-serif;
  color-scheme: light dark;
}
.panel {
  --bg: #ffffff;
  --fg: #222222;
  --muted: #767676;
  --border: #dadada;
  --accent: #0082c9;
  --accent-soft: rgba(0, 130, 201, 0.08);
  --chip-bg: #f0f0f0;
  display: flex;
  flex-direction: column;
  width: 340px;
  height: 420px;
  min-width: 260px;
  min-height: 200px;
  max-width: 90vw;
  max-height: 85vh;
  resize: both;
  overflow: hidden;
  background: var(--bg);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.25);
  font-size: 13px;
  line-height: 1.45;
}
.panel.dark {
  --bg: #1b1b1b;
  --fg: #e6e6e6;
  --muted: #9e9e9e;
  --border: #3c3c3c;
  --accent: #4db1e8;
  --accent-soft: rgba(77, 177, 232, 0.12);
  --chip-bg: #2c2c2c;
}
@media (prefers-color-scheme: dark) {
  .panel.auto {
    --bg: #1b1b1b;
    --fg: #e6e6e6;
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
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  cursor: grab;
  user-select: none;
  flex: none;
}
.header:active { cursor: grabbing; }
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #b0b0b0;
  flex: none;
}
.dot.attached { background: #2d7b41; }
.title { font-weight: 600; flex: 1 1 auto; min-width: 0; }
.ai-label {
  font-size: 11px;
  color: var(--muted);
  flex: none;
}
.close {
  all: unset;
  cursor: pointer;
  color: var(--muted);
  font-size: 15px;
  line-height: 1;
  padding: 2px 4px;
  border-radius: 4px;
}
.close:hover { background: var(--chip-bg); color: var(--fg); }
.close:focus-visible { outline: 2px solid var(--accent); }
.scroll {
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 8px 10px;
  overscroll-behavior: contain;
}
.hint {
  color: var(--muted);
  padding: 12px 4px;
  text-align: center;
}
.marker {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
  font-size: 11px;
  margin: 2px 0 8px;
}
.marker::before, .marker::after {
  content: "";
  flex: 1;
  border-top: 1px solid var(--border);
}
.block { margin: 0 0 10px; border-radius: 6px; padding: 2px 6px; }
.block.own { background: var(--accent-soft); }
.block-header {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 1px;
}
.speaker { font-weight: 600; font-size: 12px; }
.block.own .speaker { color: var(--accent); }
.you-chip {
  font-size: 10px;
  color: var(--accent);
  border: 1px solid var(--accent);
  border-radius: 7px;
  padding: 0 5px;
}
.time {
  font-size: 10px;
  color: var(--muted);
  margin-left: auto;
  opacity: 0;
  transition: opacity 0.15s;
}
.block:hover .time { opacity: 1; }
@media (prefers-reduced-motion: reduce) {
  .time { transition: none; }
}
.line { margin: 0; }
.partial { color: var(--muted); font-style: italic; }
.partial::after { content: "\u258F"; opacity: 0.7; }
.jump {
  all: unset;
  position: absolute;
  bottom: 10px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--accent);
  color: #ffffff;
  font-size: 12px;
  padding: 4px 12px;
  border-radius: 12px;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}
.jump:focus-visible { outline: 2px solid var(--fg); }
.body { position: relative; flex: 1 1 auto; display: flex; min-height: 0; }
.body .scroll { flex: 1 1 auto; }
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
[hidden] { display: none !important; }
`;function p(s,l,o){let i=document.createElement(s);return l&&(i.className=l),o!==void 0&&(i.textContent=o),i}function Z(s){try{return new Date(s).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}catch{return""}}function ee(){try{let s=document.body||document.documentElement;if(!s)return null;if(s.matches("[data-theme-dark], [data-theme-dark-highcontrast], .theme--dark"))return!0;if(s.matches("[data-theme-light], .theme--light"))return!1}catch{}return null}function W({store:s,names:l,onClose:o}){let i=p("div");i.id="cassini-live-panel";let u=i.attachShadow({mode:"open"});try{let t=new CSSStyleSheet;t.replaceSync(H),u.adoptedStyleSheets=[t]}catch{let t=document.createElement("style");t.textContent=H,u.appendChild(t)}let f=p("div","panel auto"),d=ee();d===!0&&(f.className="panel dark"),d===!1&&(f.className="panel");let h=p("div","header"),y=p("span","dot"),b=p("span","title","Live transcript"),S=p("span","ai-label","AI-generated"),e=p("button","close","\u2715");e.setAttribute("aria-label","Close transcript panel"),e.addEventListener("click",t=>{t.stopPropagation(),o()}),h.append(y,b,S,e);let r=p("div","body"),n=p("div","scroll");n.setAttribute("role","log"),n.setAttribute("aria-label","Live transcript");let g=p("div","hint","Waiting for a Talk call on this page\u2026"),x=p("div","marker","Transcript started here");x.hidden=!0;let v=p("div");n.append(g,x,v);let c=p("button","jump","\u2193 Jump to live");c.hidden=!0;let A=p("div","sr-only");A.setAttribute("aria-live","polite"),r.append(n,c,A),f.append(h,r),u.appendChild(f);let L=!0,D=(()=>{try{return window.matchMedia("(prefers-reduced-motion: reduce)").matches}catch{return!1}})();function X(){return n.scrollHeight-n.scrollTop-n.clientHeight<=48}function P(){n.scrollTo({top:n.scrollHeight,behavior:D?"auto":"smooth"})}n.addEventListener("scroll",()=>{X()?(L=!0,c.hidden=!0):(L=!1,c.hidden=!1)}),c.addEventListener("click",()=>{L=!0,c.hidden=!0,P()});let C=new Map;function O(t,a){let m=l.resolve(a.sid);return t.speakerEl.textContent=m.name,t.root.classList.toggle("own",m.own),t.youChip.hidden=!m.own,m}function F(t){let a=p("div","block"),m=p("div","block-header"),k=p("span","speaker"),_=p("span","you-chip","you");_.hidden=!0;let B=p("span","time",Z(t.startedAt));B.title=new Date(t.startedAt).toLocaleString(),m.append(k,_,B);let z=p("div"),E=p("p","line partial");E.setAttribute("dir","auto"),E.hidden=!0,a.append(m,z,E);let T={root:a,linesEl:z,partialEl:E,speakerEl:k,youChip:_,lineCount:0};return C.set(t.id,T),v.appendChild(a),O(T,t),g.hidden=!0,x.hidden=!1,T}function U(t){let a=C.get(t.id);a||(a=F(t));let m=O(a,t);for(;a.lineCount<t.lines.length;){let k=t.lines[a.lineCount],_=p("p","line",k.text);_.setAttribute("dir","auto"),k.lang&&(_.lang=k.lang),a.linesEl.appendChild(_),a.lineCount++}return t.partial?(a.partialEl.textContent=t.partial.text,t.partial.lang&&(a.partialEl.lang=t.partial.lang),a.partialEl.hidden=!1):(a.partialEl.hidden=!0,a.partialEl.textContent=""),m}let Y=s.subscribe(t=>{if(t.type==="remove"){let m=C.get(t.block.id);m&&(m.root.remove(),C.delete(t.block.id));return}let a=U(t.block);t.type==="final"&&t.line&&(A.textContent=`${a.name}: ${t.line.text}`),L&&P()});function J(t){y.classList.toggle("attached",t.state==="attached"),!s.blocks.length&&(g.textContent=t.state==="attached"?"Connected. Turn on captions in the call (\u22EF \u2192 closed captions) to see the transcript. Lines appear here for everyone once Cassini Live starts listening.":"Waiting for a Talk call on this page \u2014 join the call, then turn on captions.")}function K(){try{let t=localStorage.getItem(j);if(!t)return;let a=JSON.parse(t);typeof a.left=="number"&&typeof a.top=="number"&&M(a.left,a.top)}catch{}}function M(t,a){let m=Math.min(Math.max(t,0),Math.max(window.innerWidth-80,0)),k=Math.min(Math.max(a,0),Math.max(window.innerHeight-40,0));i.style.setProperty("--clp-left",`${m}px`),i.style.setProperty("--clp-right","auto"),i.style.setProperty("--clp-top",`${k}px`)}function q(t,a){try{localStorage.setItem(j,JSON.stringify({left:t,top:a}))}catch{}}let w=null;h.addEventListener("pointerdown",t=>{if(t.target===e)return;let a=i.getBoundingClientRect();w={dx:t.clientX-a.left,dy:t.clientY-a.top};try{h.setPointerCapture(t.pointerId)}catch{}}),h.addEventListener("pointermove",t=>{w&&M(t.clientX-w.dx,t.clientY-w.dy)}),h.addEventListener("pointerup",t=>{w&&(q(t.clientX-w.dx,t.clientY-w.dy),w=null)}),K(),(document.body||document.documentElement).appendChild(i);function G(){Y(),i.remove(),C.clear()}return{destroy:G,setStatus:J,host:i,shadow:u}}var I="__CASSINI_LIVE_PANEL__";function te(){let s=R(),l=null,o={state:"waiting"},i=V({onEvent:d=>s.apply(d),onStatus:d=>{o=d,l&&l.setStatus(d)}}),u=$({tap:i});function f(){i.destroy(),u.destroy(),l&&l.destroy();try{delete window[I]}catch{window[I]=void 0}}l=W({store:s,names:u,onClose:f}),l.setStatus(o),window[I]={destroy:f,injectTranscript(d){s.apply({sid:typeof d.id=="string"?d.id:"",text:typeof d.message=="string"?d.message:"",final:d.final!==!1,lang:typeof d.languageId=="string"?d.languageId:null})},get attached(){return i.attached},version:"0.1.0"}}var N=window[I];N&&typeof N.destroy=="function"?N.destroy():te();})();
