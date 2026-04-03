function S(){y(),f("You have been logged out","info",3e3),setTimeout(()=>{window.location.href="/login"},500)}function g(){return sessionStorage.getItem("auth_token")}function R(e){sessionStorage.setItem("auth_token",e)}function y(){sessionStorage.removeItem("auth_token")}function k(){return!!g()}function C(){window.location.href="/login"}function x(e={}){const t=g();return t&&(e.headers=e.headers||{},e.headers.Authorization=`Bearer ${t}`),e}async function q(e,t={}){t=x(t);const o=await fetch(e,t);if(o.status===401)throw y(),C(),new Error("Authentication required");return o}function f(e,t="success",o=5e3){let n=document.querySelector(".toast-container");n||(n=document.createElement("div"),n.className="toast-container",document.body.appendChild(n));const a=document.createElement("div");a.className=`toast toast-${t}`,a.setAttribute("role","alert"),a.setAttribute("aria-live","assertive"),a.setAttribute("aria-atomic","true");const i={success:"bi-check-circle-fill",error:"bi-x-circle-fill",warning:"bi-exclamation-triangle-fill",info:"bi-info-circle-fill"},s={success:"Success",error:"Error",warning:"Warning",info:"Information"},c=i[t]||i.info,r=s[t]||s.info;a.innerHTML=`
        <div class="toast-header">
            <i class="bi ${c} me-2"></i>
            <strong class="me-auto">${r}</strong>
            <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
        <div class="toast-body">
            ${u(e)}
        </div>
    `,n.appendChild(a);const l=new bootstrap.Toast(a,{autohide:o>0,delay:o});return l.show(),a.addEventListener("hidden.bs.toast",()=>{a.remove()}),l}function u(e){const t=document.createElement("div");return t.textContent=e,t.innerHTML}function L(e){return new Promise(t=>{const{title:o="Confirm Action",message:n="Are you sure you want to proceed?",confirmText:a="Confirm",cancelText:i="Cancel",confirmClass:s="btn-primary",dangerous:c=!1}=e,r="confirmModal-"+Date.now(),l=document.createElement("div");l.className="modal fade",l.id=r,l.setAttribute("tabindex","-1"),l.setAttribute("aria-labelledby",r+"Label"),l.setAttribute("aria-hidden","true");const m=c?"btn-danger":s;l.innerHTML=`
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="${r}Label">
                            ${c?'<i class="bi bi-exclamation-triangle-fill text-danger me-2"></i>':""}
                            ${u(o)}
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        ${u(n)}
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">${u(i)}</button>
                        <button type="button" class="btn ${m}" id="${r}-confirm">${u(a)}</button>
                    </div>
                </div>
            </div>
        `,document.body.appendChild(l);const d=new bootstrap.Modal(l);l.querySelector(`#${r}-confirm`).addEventListener("click",()=>{d.hide(),t(!0)}),l.addEventListener("hidden.bs.modal",()=>{l.remove(),t(!1)}),d.show()})}function _(e){return new Promise(t=>{const{title:o="Confirm Action",message:n="Are you sure you want to proceed?",checkboxLabel:a="Additional option",checkboxDefault:i=!1,confirmText:s="Confirm",cancelText:c="Cancel",dangerous:r=!1}=e,l="confirmModal-"+Date.now(),m="checkbox-"+Date.now(),d=document.createElement("div");d.className="modal fade",d.id=l,d.setAttribute("tabindex","-1");const b=r?"btn-danger":"btn-primary";d.innerHTML=`
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">
                            ${r?'<i class="bi bi-exclamation-triangle-fill text-danger me-2"></i>':""}
                            ${u(o)}
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        <p>${u(n)}</p>
                        <div class="form-check mt-3">
                            <input class="form-check-input" type="checkbox" id="${m}" ${i?"checked":""}>
                            <label class="form-check-label" for="${m}">
                                ${u(a)}
                            </label>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">${u(c)}</button>
                        <button type="button" class="btn ${b}" id="${l}-confirm">${u(s)}</button>
                    </div>
                </div>
            </div>
        `,document.body.appendChild(d);const v=new bootstrap.Modal(d),I=d.querySelector(`#${l}-confirm`),F=d.querySelector(`#${m}`);I.addEventListener("click",()=>{v.hide(),t({confirmed:!0,checked:F.checked})}),d.addEventListener("hidden.bs.modal",()=>{d.remove(),t({confirmed:!1,checked:!1})}),v.show()})}function D(e){if(e===0)return"0 Bytes";const t=1024,o=["Bytes","KB","MB","GB","TB"],n=Math.floor(Math.log(e)/Math.log(t));return Math.round(e/Math.pow(t,n)*100)/100+" "+o[n]}function H(e){const t=new Date,o=new Date(e),n=Math.floor((t-o)/1e3);return n<60?"just now":n<3600?Math.floor(n/60)+" minutes ago":n<86400?Math.floor(n/3600)+" hours ago":n<2592e3?Math.floor(n/86400)+" days ago":n<31536e3?Math.floor(n/2592e3)+" months ago":Math.floor(n/31536e3)+" years ago"}function N(e,t){let o;return function(...a){const i=()=>{clearTimeout(o),e(...a)};clearTimeout(o),o=setTimeout(i,t)}}function h(e){return e?typeof e=="string"?document.querySelector(e):e:null}function W(e={}){const{loading:t,content:o,empty:n,error:a,state:i="content",errorMessage:s="",showContentOnEmpty:c=!1}=e,r=h(t),l=h(o),m=h(n),d=h(a);if(r&&(r.style.display=i==="loading"?"":"none"),l){const b=i==="content"||i==="empty"&&c;l.style.display=b?"":"none"}m&&(m.style.display=i==="empty"?"":"none"),d?(d.style.display=i==="error"?"":"none",i==="error"&&s&&(d.innerHTML=`
                <div class="alert alert-danger mb-0" role="alert">
                    <i class="bi bi-exclamation-triangle-fill me-2"></i>${u(s)}
                </div>
            `)):r&&i==="error"&&s&&(r.style.display="",r.innerHTML=`
            <div class="alert alert-danger mb-0" role="alert">
                <i class="bi bi-exclamation-triangle-fill me-2"></i>${u(s)}
            </div>
        `)}function P(e,t="page"){const o=e.filter(n=>!document.getElementById(n));if(o.length>0){const n=`Missing required ${t} element(s): ${o.join(", ")}`;throw console.error(n),new Error(n)}}function M(){T(),navigator.onLine?(f("You are back online","success",3e3),setTimeout(()=>{window.location.reload()},1e3)):f("You are offline. Some features may not be available.","warning",5e3)}window.addEventListener("online",M);window.addEventListener("offline",M);function w(e){const t=document.getElementById("app-shell"),o=document.getElementById("sidebar-toggle");!t||!o||(t.classList.toggle("app-shell--nav-open",e),o.setAttribute("aria-expanded",String(e)))}function T(){const e=document.getElementById("connection-status-pill");e&&(navigator.onLine?(e.textContent="Online",e.className="status-pill status-pill--success"):(e.textContent="Offline mode",e.className="status-pill status-pill--warning"))}let E=!1;function A(){if(E)return;if(E=!0,$(),window.location.pathname!=="/login"&&!k()){C();return}document.querySelectorAll(".alert:not(.toast)").forEach(function(i){setTimeout(function(){const s=bootstrap.Alert.getInstance(i);s&&s.close()},5e3)}),document.querySelectorAll("form[data-confirm]").forEach(function(i){i.addEventListener("submit",async function(s){s.preventDefault();const c=i.getAttribute("data-confirm")||"Are you sure you want to delete this item?";await L({title:"Confirm Deletion",message:c,confirmText:"Delete",dangerous:!0})&&i.submit()})});const o=document.getElementById("sidebar-toggle"),n=document.getElementById("app-shell-backdrop"),a=window.matchMedia("(min-width: 992px)");o&&o.addEventListener("click",()=>{const s=!document.getElementById("app-shell")?.classList.contains("app-shell--nav-open");w(s)}),n&&n.addEventListener("click",()=>w(!1)),document.addEventListener("keydown",i=>{i.key==="Escape"&&w(!1)}),a.addEventListener("change",i=>{i.matches&&w(!1)}),T(),window.fileFridgeAppReady=!0,window.__fileFridgeReadyQueue&&window.__fileFridgeReadyQueue.splice(0).forEach(s=>{try{s()}catch(c){console.error("File Fridge queued callback failed:",c)}}),window.dispatchEvent(new Event("filefridge:app-ready"))}function Q(e){if(window.fileFridgeAppReady){e();return}window.__fileFridgeReadyQueue||(window.__fileFridgeReadyQueue=[]),window.__fileFridgeReadyQueue.push(e)}function B(){window.showToast=f,window.showConfirmModal=L,window.showConfirmModalWithCheckbox=_,window.getAuthToken=g,window.setAuthToken=R,window.isAuthenticated=k,window.addAuthHeader=x,window.authenticatedFetch=q,window.formatBytes=D,window.formatRelativeTime=H,window.debounce=N,window.escapeHtml=u,window.setRegionState=W,window.assertRequiredElements=P,window.loadAppInfo=$,window.handleLogout=S,window.clearAuthToken=y,window.installPWA=j,window.runWhenFileFridgeReady=Q}B();document.readyState==="loading"?document.addEventListener("DOMContentLoaded",A,{once:!0}):A();function $(){fetch("/health").then(e=>e.json()).then(e=>{const t=e.version||"0.0.0",o=e.app_name||"File Fridge";document.title=document.title.replace("File Fridge",o),["app-name-navbar","app-name-footer"].forEach(s=>{const c=document.getElementById(s);c&&(c.textContent=o)});const a=document.getElementById("app-version");a&&(a.textContent="v"+t);const i=document.getElementById("footer-app-version");i&&(i.textContent=t)}).catch(e=>{console.error("Failed to fetch app info:",e);const t=document.getElementById("app-version");t&&(t.textContent="v0.0.0");const o=document.getElementById("footer-app-version");o&&(o.textContent="0.0.0")})}let p=null;window.addEventListener("beforeinstallprompt",e=>{e.preventDefault(),p=e;const t=document.getElementById("pwa-install-btn");t&&t.classList.remove("d-none")});window.addEventListener("appinstalled",()=>{const e=document.getElementById("pwa-install-btn");e&&e.classList.add("d-none"),p=null,f("App installed successfully!","success",3e3)});async function j(){if(!p){f("Installation is not available","warning",3e3);return}p.prompt();const{outcome:e}=await p.userChoice;e==="accepted"?f("App installed successfully!","success",3e3):f("App installation was cancelled","info",3e3),p=null;const t=document.getElementById("pwa-install-btn");t&&t.classList.add("d-none")}B();
