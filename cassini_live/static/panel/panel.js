/* Cassini Live transcript panel v0.1.0 — https://github.com/codemyriad/cassini-live */
(()=>{var J=1;function Y(){let a=[],l=0,n=new Map,i=new Set;function c(o){for(let u of i)u(o,a)}function y(){let o=a.length?a[a.length-1]:null;return o&&o.kind==="block"?o:null}function b(o){let u=o.speakerSessionId,s=o.message;if(typeof u!="string"||u===""||typeof s!="string"||s==="")return;let L=typeof o.t=="number"?o.t:Date.now(),_=typeof o.langId=="string"?o.langId:null,e=y();(!e||e.sid!==u)&&(e={id:J++,kind:"block",sid:u,startedAt:L,lines:[]},a.push(e),c({type:"add",item:e})),e.lines.push({text:s,lang:_,at:L}),c({type:"final",item:e,line:e.lines[e.lines.length-1]}),n.has(u)&&(n.delete(u),c({type:"partial",sid:u}))}function f(o){let u={id:J++,kind:"join",sid:typeof o.speakerSessionId=="string"?o.speakerSessionId:"",displayName:typeof o.displayName=="string"?o.displayName:null,userId:typeof o.userId=="string"?o.userId:null,at:typeof o.t=="number"?o.t:Date.now()};a.push(u),c({type:"add",item:u})}function x(o){if(Array.isArray(o))for(let u of o){let s=u&&typeof u.seq=="number"?u.seq:null;s!==null&&s<=l||(u.type==="join"?f(u):b(u),s!==null&&(l=s))}}function w(o,u,s){if(!(typeof o!="string"||o==="")){if(typeof u!="string"||u===""){n.delete(o)&&c({type:"partial",sid:o});return}n.set(o,{text:u,lang:typeof s=="string"?s:null}),c({type:"partial",sid:o})}}function g(){a=[],l=0,n.clear(),c({type:"reset"})}return{applyTimeline:x,setPartial:w,reset:g,get items(){return a},get lastSeq(){return l},partialFor(o){return n.get(o)||null},get activePartials(){return[...n.entries()].map(([o,u])=>({sid:o,...u}))},subscribe(o){return i.add(o),()=>i.delete(o)}}}function K({onEvent:a,onStatus:l}){let n=null,i=null,c=!1,y=s=>{!s||typeof s!="object"||a({sid:typeof s.id=="string"?s.id:"",text:typeof s.message=="string"?s.message:"",final:s.final!==!1,lang:typeof s.languageId=="string"?s.languageId:null})};function b(){try{let s=window.OCA?.Talk?.SimpleWebRTC;if(s&&typeof s.on=="function")return s}catch{}return null}function f(){if(c||n)return;let s=b();if(!s){l({state:"waiting"}),i=setTimeout(f,1e3);return}n=s,n.on("transcript",y),l({state:"attached"}),x()}function x(){c||(i=setTimeout(()=>{c||(b()!==n?(n&&typeof n.off=="function"&&n.off("transcript",y),n=null,f()):x())},1e3*5))}function w(){try{return n?.connection?.getSessionId?.()||null}catch{return null}}function g(){c=!0,i&&clearTimeout(i),n&&typeof n.off=="function"&&n.off("transcript",y),n=null}f();function o(){try{let s=n?.connection?.currentRoomToken;if(typeof s=="string"&&s)return s}catch{}try{let s=location.pathname.match(/\/call\/([^/?#]+)/);if(s)return s[1]}catch{}return null}function u(){try{let s=n?.connection?.currentCallToken;return typeof s=="string"&&s?s:null}catch{return null}}return{destroy:g,ownSessionId:w,roomToken:o,callToken:u,get attached(){return n!==null},get webrtc(){return n}}}function G({tap:a}){let l=new Map,n=null,i=e=>{e&&typeof e.id=="string"&&typeof e.name=="string"&&e.name&&l.set(e.id,e.name)};function c(){let e=a.webrtc;!e||e===n||typeof e.on!="function"||(n&&typeof n.off=="function"&&n.off("nick",i),n=e,e.on("nick",i))}function y(e,d){return!e||!d?!1:e===d?!0:Math.min(e.length,d.length)<8?!1:e.startsWith(d)||d.startsWith(e)}function b(){try{return window.OC?.getCurrentUser?.()||null}catch{return null}}function f(){try{let e=a.webrtc?.connection?.joinedUsers;return e&&typeof e=="object"?e:null}catch{return null}}function x(e){let d=f();if(!d)return null;for(let m of Object.keys(d)){if(!y(m,e))continue;let h=d[m]||{};return{displayName:h.user?.displayname||h.user?.displayName||null,userId:h.userid||h.userId||null}}return null}function w(e){try{let d=a.webrtc?.webrtc?.peers;if(!Array.isArray(d))return null;let m=d.find(h=>h&&y(h.id,e)&&typeof h.nick=="string"&&h.nick);return m?m.nick:null}catch{return null}}function g(e){for(let[d,m]of l)if(y(d,e))return m;return null}function o(e){try{for(let d of document.querySelectorAll(".video-container[id]")){let m=d.id.match(/^(?:placeholder-)?container_(.+)_video_incoming$/);if(!m||!y(m[1],e))continue;let h=d.querySelector(".video-container__user-name, .video-container__name, .displayname"),C=h&&h.textContent?h.textContent.trim():"";if(C)return C}}catch{}return null}function u(e){return e.length>12?`${e.slice(0,6)}\u2026${e.slice(-4)}`:e}function s(e){let d=a.ownSessionId();if(d&&y(d,e))return!0;let m=b(),h=x(e);return!!(m&&h&&h.userId&&h.userId===m.uid)}function L(e){if(c(),s(e))return{name:b()?.displayName||x(e)?.displayName||"You",own:!0,resolved:!0};let d=x(e),m=d&&(d.displayName||d.userId)||w(e)||g(e)||o(e);return m?{name:m,own:!1,resolved:!0}:{name:u(e),own:!1,resolved:!1}}function _(){n&&typeof n.off=="function"&&n.off("nick",i),n=null,l.clear()}return{resolve:L,destroy:_}}function Z({tap:a,store:l,fetchHistory:n,onChange:i=()=>{}}){let c=null,y=null,b=null,f=0,x=null,w=null,g=!1;function o(){w&&(clearTimeout(w),w=null)}async function u(e,d){if(g||d!==f)return;let{entries:m}=await n(e,l.lastSeq);g||d!==f||(l.applyTimeline(m),w=setTimeout(()=>u(e,d),1200))}function s(e){f++,b=e,o(),l.reset(),i({roomToken:e,inCall:!0}),u(e,f)}function L(){f++,o(),b=null}function _(){if(g)return;let e=a.roomToken(),d=a.callToken(),m=!!d,h=m&&!y,C=e!==c;h||m&&C&&e!==b?s(e):!m&&y?L():C&&!m&&(l.items.length&&l.reset(),L()),c=e,y=d}return x=setInterval(_,1200),_(),{get inCall(){return!!a.callToken()},roomToken:()=>a.roomToken(),destroy(){g=!0,x&&clearInterval(x),o()}}}var Q="/apps/app_api/proxy/live_transcription/history";function ie(a,l){let n=`?token=${encodeURIComponent(a)}&since=${l|0}`;try{if(window.OC&&typeof window.OC.generateUrl=="function")return window.OC.generateUrl(Q)+n}catch{}return"/index.php"+Q+n}async function ee(a,l=0){if(!a)return{entries:[],head:0};try{let n=await fetch(ie(a,l),{credentials:"same-origin",headers:{Accept:"application/json"}});if(!n.ok)return{entries:[],head:0};let i=await n.json();return{entries:Array.isArray(i?.entries)?i.entries:[],head:typeof i?.head=="number"?i.head:0}}catch{return{entries:[],head:0}}}var te=`
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
  color: var(--muted); font-size: 11px; margin: 6px 0 8px;
}
.marker::before, .marker::after { content: ""; flex: 1; border-top: 1px solid var(--border); }
.marker.you { color: var(--accent); }
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
.partials { flex: none; padding: 0 6px 6px; }
.partial-line { margin: 0 0 4px; color: var(--muted); font-style: italic; }
.partial-speaker { font-weight: 600; font-style: normal; margin-right: 4px; }
.partial-line::after { content: "\u258F"; opacity: 0.7; }
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
`;function k(a,l,n){let i=document.createElement(a);return l&&(i.className=l),n!==void 0&&(i.textContent=n),i}function se(a){try{return new Date(a).toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"})}catch{return""}}function ae(){try{let a=document.body||document.documentElement;if(!a)return null;if(a.matches("[data-theme-dark], [data-theme-dark-highcontrast], .theme--dark"))return!0;if(a.matches("[data-theme-light], .theme--light"))return!1}catch{}return null}function le(){try{return window.OC?.getCurrentUser?.()?.uid||null}catch{return null}}function ne({store:a,names:l,shadow:n}){try{let t=new CSSStyleSheet;t.replaceSync(te),n.adoptedStyleSheets=[...n.adoptedStyleSheets,t]}catch{let t=document.createElement("style");t.textContent=te,n.appendChild(t)}let i=k("div","view auto"),c=ae();c===!0&&(i.className="view dark"),c===!1&&(i.className="view");let y=k("div","header"),b=k("span","dot"),f=k("span","title","Live transcript"),x=k("span","ai-label","AI-generated");y.append(b,f,x);let w=k("div","body"),g=k("div","scroll");g.setAttribute("role","log"),g.setAttribute("aria-label","Live transcript");let o=k("div","hint","Waiting for a Talk call on this page\u2026"),u=k("div"),s=k("div","partials");g.append(o,u,s);let L=k("button","jump","\u2193 Jump to live");L.hidden=!0;let _=k("div","sr-only");_.setAttribute("aria-live","polite"),w.append(g,L,_),i.append(y,w),n.appendChild(i);let e=!0,d=(()=>{try{return window.matchMedia("(prefers-reduced-motion: reduce)").matches}catch{return!1}})();function m(){return g.scrollHeight-g.scrollTop-g.clientHeight<=48}function h(){g.scrollTo({top:g.scrollHeight,behavior:d?"auto":"smooth"})}g.addEventListener("scroll",()=>{e=m(),L.hidden=e}),L.addEventListener("click",()=>{e=!0,L.hidden=!0,h()});let C=new Map,O=le();function T(t,v){let S=l.resolve(v.sid);return t.speakerEl.textContent=S.name,t.resolved=S.resolved,t.root.classList.toggle("own",S.own),t.youChip.hidden=!S.own,S}function q(t){let v=k("div","block"),S=k("div","block-header"),N=k("span","speaker"),A=k("span","you-chip","you");A.hidden=!0;let F=k("span","time",se(t.startedAt));F.title=new Date(t.startedAt).toLocaleString(),S.append(N,A,F);let X=k("div");v.append(S,X);let z={kind:"block",root:v,linesEl:X,speakerEl:N,youChip:A,lineCount:0,item:t,resolved:!1};return C.set(t.id,z),u.appendChild(v),T(z,t),o.hidden=!0,z}function E(t){return l.resolve(t.sid).own?!0:!!(O&&t.userId&&t.userId===O)}function j(t){return E(t)?"You joined here":`${t.displayName||l.resolve(t.sid).name} joined`}function M(t){let v=k("div","marker");v.textContent=j(t),E(t)&&v.classList.add("you");let S={kind:"join",root:v,item:t,resolved:!!t.displayName};return C.set(t.id,S),u.appendChild(v),o.hidden=!0,S}function B(t){return t.kind==="join"?M(t):q(t)}function V(t){let v=C.get(t.id);v||(v=B(t));let S=T(v,t);for(;v.lineCount<t.lines.length;){let N=t.lines[v.lineCount],A=k("p","line",N.text);A.setAttribute("dir","auto"),N.lang&&(A.lang=N.lang),v.linesEl.appendChild(A),v.lineCount++}return S}function P(){s.replaceChildren();for(let t of a.activePartials){let v=l.resolve(t.sid),S=k("p","partial-line");S.setAttribute("dir","auto"),t.lang&&(S.lang=t.lang),S.append(k("span","partial-speaker",`${v.name}:`),document.createTextNode(t.text)),s.appendChild(S)}}function H(t){if(t.kind==="join"){C.has(t.id)||M(t);return}V(t)}function D(){u.replaceChildren(),s.replaceChildren(),C.clear()}for(let t of a.items)H(t);P();let U=a.subscribe(t=>{if(t.type==="reset"){D(),o.hidden=!1;return}if(t.type==="partial"){P(),e&&h();return}if(H(t.item),t.type==="final"&&t.line){let v=l.resolve(t.item.sid);_.textContent=`${v.name}: ${t.line.text}`}e&&h()}),r=setInterval(()=>{for(let t of C.values())t.resolved||(t.kind==="block"?T(t,t.item):(t.root.textContent=j(t.item),t.resolved=l.resolve(t.item.sid).resolved));P()},2e3);function p(t){if(b.classList.toggle("attached",t.state==="attached"),a.items.length){o.hidden=!0;return}o.hidden=!1,o.textContent=t.state==="attached"?"Connected. Turn on captions in the call (\u22EF \u2192 closed captions) to see the transcript. Lines appear here for everyone once Cassini Live starts listening.":"Waiting for a Talk call on this page \u2014 join the call, then turn on captions."}function I(){clearInterval(r),U(),C.clear(),i.remove()}return{setStatus:p,destroy:I}}var ce="cassini-live-tab",oe="cassini-live-tabpanel",ue=500,de="M20,4A2,2 0 0,1 22,6V18A2,2 0 0,1 20,20H4A2,2 0 0,1 2,18V6A2,2 0 0,1 4,4H20M20,18V6H4V18H20M6,10H8V12H6V10M6,14H14V16H6V14M16,14H18V16H16V14M10,10H18V12H10V10Z";function $(a,l,n){let i=document.createElement(a);return l&&(i.className=l),n!==void 0&&(i.textContent=n),i}function re({store:a,names:l}){let n=!1,i=null,c=null,y=null,b=!1,f=!1,x=null,w=null,g={state:"waiting"},o=new Set,u=()=>document.querySelector("#app-sidebar-vue"),s=()=>document.querySelector("#app-sidebar-vue .app-sidebar-tabs__nav"),L=()=>document.querySelector("#app-sidebar-vue .app-sidebar-tabs__content"),_=()=>[...document.querySelectorAll('#app-sidebar-vue .app-sidebar-tabs__nav button[role="tab"]')].filter(r=>r!==i);function e(){for(let r of _()){let p=r.getAttribute("aria-selected")==="true";p&&!x&&(x=r.className),!p&&!w&&(w=r.className)}}function d(){if(!i)return;e();let r=b?x:w;r&&(i.className=r),i.setAttribute("aria-selected",b?"true":"false")}function m(){if(b)for(let r of _())r.getAttribute("aria-selected")==="true"&&(x||(x=r.className),r.setAttribute("aria-selected","false"),w&&(r.className=w))}function h(){let r=_()[0],p;if(r){p=r.cloneNode(!0),p.removeAttribute("id"),p.removeAttribute("tabindex");for(let t of p.querySelectorAll("svg path"))t.setAttribute("d",de);let I=p.querySelector('[class*="__name" i]');I?I.textContent="Live transcription":p.textContent="Live transcription"}else p=$("button",void 0,"Live transcription");return p.id=ce,p.type="button",p.setAttribute("role","tab"),p.setAttribute("aria-controls",oe),p.setAttribute("aria-label","Live transcription"),p.title="Live transcription",p.dataset.cassiniLiveTab="1",p.addEventListener("click",I=>{I.preventDefault(),I.stopPropagation(),T()}),p}function C(){let r=$("div");r.id=oe,r.style.position="absolute",r.style.inset="0",r.style.zIndex="10",r.style.display="none",r.style.padding="0 10px",r.style.boxSizing="border-box",r.style.background="var(--color-main-background, #fff)",r.style.overflow="hidden";let p=$("div");p.style.height="100%";let I=p.attachShadow({mode:"open"});return r.appendChild(p),y=ne({store:a,names:l,shadow:I}),y.setStatus(g),r}function O(){let r=s(),p=L();return!r||!p?!1:((!i||!i.isConnected)&&(i=h(),r.appendChild(i)),(!c||!c.isConnected)&&(getComputedStyle(p).position==="static"&&(p.style.position="relative"),c=C(),p.appendChild(c),c.style.display=b?"block":"none"),d(),!0)}function T(){b=!0,c&&(c.style.display="block"),d(),m()}function q(){b=!1,c&&(c.style.display="none"),d()}function E(r){if(!b)return;let p=r.composedPath?r.composedPath():[r.target];for(let I of p){if(I===i)return;if(I instanceof HTMLElement&&I.getAttribute&&I.getAttribute("role")==="tab"&&I.closest("#app-sidebar-vue .app-sidebar-tabs__nav")){q();return}}}function j(){for(let r of document.querySelectorAll(".transcript"))r instanceof HTMLElement&&(c&&c.contains(r)||r.dataset.cassiniLiveHidden||(r.dataset.cassiniLiveHidden="1",r.dataset.cassiniLivePrevDisplay=r.style.display,r.style.display="none",o.add(r)))}function M(){for(let r of o)r.dataset.cassiniLiveHidden&&(r.style.display=r.dataset.cassiniLivePrevDisplay||"",delete r.dataset.cassiniLiveHidden,delete r.dataset.cassiniLivePrevDisplay);o.clear()}function B(){try{return/\/call\/[^/]+/.test(location.pathname)&&!!document.querySelector("#app-content-vue")}catch{return!1}}function V(){M(),y&&(y.destroy(),y=null),i&&i.remove(),c&&c.remove(),i=c=null,f=!1}let P=setInterval(H,ue);function H(){if(n)return;if(!B()||!s()||!L()){(i||c)&&V();return}j(),O()&&!f&&(f=!0,T()),b&&m()}document.addEventListener("click",E,!0),H();function D(r){g=r,y&&y.setStatus(r)}function U(){n=!0,clearInterval(P),document.removeEventListener("click",E,!0),V()}return{setStatus:D,destroy:U}}var R="__CASSINI_LIVE_PANEL__";function fe(){let a=Y(),l=null,n={state:"waiting"},i=K({onEvent:f=>{f.final||a.setPartial(f.sid,f.text,f.lang)},onStatus:f=>{n=f,l&&l.setStatus(f)}}),c=G({tap:i}),y=Z({tap:i,store:a,fetchHistory:ee});function b(){y.destroy(),i.destroy(),c.destroy(),l&&l.destroy();try{delete window[R]}catch{window[R]=void 0}}l=re({store:a,names:c}),l.setStatus(n),window[R]={destroy:b,injectPartial(f){a.setPartial(typeof f.id=="string"?f.id:"",typeof f.message=="string"?f.message:"",typeof f.languageId=="string"?f.languageId:null)},get attached(){return i.attached},version:"0.1.0"}}var W=window[R];W&&typeof W.destroy=="function"?W.destroy():fe();})();
