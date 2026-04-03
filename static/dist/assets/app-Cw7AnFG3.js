function F(){y(),f("You have been logged out","info",3e3),setTimeout(()=>{window.location.href="/login"},500)}function g(){return sessionStorage.getItem("auth_token")}function S(e){sessionStorage.setItem("auth_token",e)}function y(){sessionStorage.removeItem("auth_token")}function C(){return!!g()}function k(){window.location.href="/login"}function x(e={}){const t=g();return t&&(e.headers=e.headers||{},e.headers.Authorization=`Bearer ${t}`),e}async function R(e,t={}){t=x(t);const n=await fetch(e,t);if(n.status===401)throw y(),k(),new Error("Authentication required");return n}function f(e,t="success",n=5e3){let o=document.querySelector(".toast-container");o||(o=document.createElement("div"),o.className="toast-container",document.body.appendChild(o));const a=document.createElement("div");a.className=`toast toast-${t}`,a.setAttribute("role","alert"),a.setAttribute("aria-live","assertive"),a.setAttribute("aria-atomic","true");const i={success:"bi-check-circle-fill",error:"bi-x-circle-fill",warning:"bi-exclamation-triangle-fill",info:"bi-info-circle-fill"},s={success:"Success",error:"Error",warning:"Warning",info:"Information"},u=i[t]||i.info,r=s[t]||s.info;a.innerHTML=`
        <div class="toast-header">
            <i class="bi ${u} me-2"></i>
            <strong class="me-auto">${r}</strong>
            <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
        <div class="toast-body">
            ${c(e)}
        </div>
    `,o.appendChild(a);const l=new bootstrap.Toast(a,{autohide:n>0,delay:n});return l.show(),a.addEventListener("hidden.bs.toast",()=>{a.remove()}),l}function c(e){const t=document.createElement("div");return t.textContent=e,t.innerHTML}function L(e){return new Promise(t=>{const{title:n="Confirm Action",message:o="Are you sure you want to proceed?",confirmText:a="Confirm",cancelText:i="Cancel",confirmClass:s="btn-primary",dangerous:u=!1}=e,r="confirmModal-"+Date.now(),l=document.createElement("div");l.className="modal fade",l.id=r,l.setAttribute("tabindex","-1"),l.setAttribute("aria-labelledby",r+"Label"),l.setAttribute("aria-hidden","true");const m=u?"btn-danger":s;l.innerHTML=`
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="${r}Label">
                            ${u?'<i class="bi bi-exclamation-triangle-fill text-danger me-2"></i>':""}
                            ${c(n)}
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        ${c(o)}
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">${c(i)}</button>
                        <button type="button" class="btn ${m}" id="${r}-confirm">${c(a)}</button>
                    </div>
                </div>
            </div>
        `,document.body.appendChild(l);const d=new bootstrap.Modal(l);l.querySelector(`#${r}-confirm`).addEventListener("click",()=>{d.hide(),t(!0)}),l.addEventListener("hidden.bs.modal",()=>{l.remove(),t(!1)}),d.show()})}function q(e){return new Promise(t=>{const{title:n="Confirm Action",message:o="Are you sure you want to proceed?",checkboxLabel:a="Additional option",checkboxDefault:i=!1,confirmText:s="Confirm",cancelText:u="Cancel",dangerous:r=!1}=e,l="confirmModal-"+Date.now(),m="checkbox-"+Date.now(),d=document.createElement("div");d.className="modal fade",d.id=l,d.setAttribute("tabindex","-1");const b=r?"btn-danger":"btn-primary";d.innerHTML=`
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">
                            ${r?'<i class="bi bi-exclamation-triangle-fill text-danger me-2"></i>':""}
                            ${c(n)}
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        <p>${c(o)}</p>
                        <div class="form-check mt-3">
                            <input class="form-check-input" type="checkbox" id="${m}" ${i?"checked":""}>
                            <label class="form-check-label" for="${m}">
                                ${c(a)}
                            </label>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">${c(u)}</button>
                        <button type="button" class="btn ${b}" id="${l}-confirm">${c(s)}</button>
                    </div>
                </div>
            </div>
        `,document.body.appendChild(d);const v=new bootstrap.Modal(d),$=d.querySelector(`#${l}-confirm`),I=d.querySelector(`#${m}`);$.addEventListener("click",()=>{v.hide(),t({confirmed:!0,checked:I.checked})}),d.addEventListener("hidden.bs.modal",()=>{d.remove(),t({confirmed:!1,checked:!1})}),v.show()})}function _(e){if(e===0)return"0 Bytes";const t=1024,n=["Bytes","KB","MB","GB","TB"],o=Math.floor(Math.log(e)/Math.log(t));return Math.round(e/Math.pow(t,o)*100)/100+" "+n[o]}function D(e){const t=new Date,n=new Date(e),o=Math.floor((t-n)/1e3);return o<60?"just now":o<3600?Math.floor(o/60)+" minutes ago":o<86400?Math.floor(o/3600)+" hours ago":o<2592e3?Math.floor(o/86400)+" days ago":o<31536e3?Math.floor(o/2592e3)+" months ago":Math.floor(o/31536e3)+" years ago"}function H(e,t){let n;return function(...a){const i=()=>{clearTimeout(n),e(...a)};clearTimeout(n),n=setTimeout(i,t)}}function h(e){return e?typeof e=="string"?document.querySelector(e):e:null}function N(e={}){const{loading:t,content:n,empty:o,error:a,state:i="content",errorMessage:s="",showContentOnEmpty:u=!1}=e,r=h(t),l=h(n),m=h(o),d=h(a);if(r&&(r.style.display=i==="loading"?"":"none"),l){const b=i==="content"||i==="empty"&&u;l.style.display=b?"":"none"}m&&(m.style.display=i==="empty"?"":"none"),d?(d.style.display=i==="error"?"":"none",i==="error"&&s&&(d.innerHTML=`
                <div class="alert alert-danger mb-0" role="alert">
                    <i class="bi bi-exclamation-triangle-fill me-2"></i>${c(s)}
                </div>
            `)):r&&i==="error"&&s&&(r.style.display="",r.innerHTML=`
            <div class="alert alert-danger mb-0" role="alert">
                <i class="bi bi-exclamation-triangle-fill me-2"></i>${c(s)}
            </div>
        `)}function W(e,t="page"){const n=e.filter(o=>!document.getElementById(o));if(n.length>0){const o=`Missing required ${t} element(s): ${n.join(", ")}`;throw console.error(o),new Error(o)}}function B(){M(),navigator.onLine?(f("You are back online","success",3e3),setTimeout(()=>{window.location.reload()},1e3)):f("You are offline. Some features may not be available.","warning",5e3)}window.addEventListener("online",B);window.addEventListener("offline",B);function w(e){const t=document.getElementById("app-shell"),n=document.getElementById("sidebar-toggle");!t||!n||(t.classList.toggle("app-shell--nav-open",e),n.setAttribute("aria-expanded",String(e)))}function M(){const e=document.getElementById("connection-status-pill");e&&(navigator.onLine?(e.textContent="Online",e.className="status-pill status-pill--success"):(e.textContent="Offline mode",e.className="status-pill status-pill--warning"))}let E=!1;function A(){if(E)return;if(E=!0,T(),window.location.pathname!=="/login"&&!C()){k();return}document.querySelectorAll(".alert:not(.toast)").forEach(function(i){setTimeout(function(){const s=bootstrap.Alert.getInstance(i);s&&s.close()},5e3)}),document.querySelectorAll("form[data-confirm]").forEach(function(i){i.addEventListener("submit",async function(s){s.preventDefault();const u=i.getAttribute("data-confirm")||"Are you sure you want to delete this item?";await L({title:"Confirm Deletion",message:u,confirmText:"Delete",dangerous:!0})&&i.submit()})});const n=document.getElementById("sidebar-toggle"),o=document.getElementById("app-shell-backdrop"),a=window.matchMedia("(min-width: 992px)");n&&n.addEventListener("click",()=>{const s=!document.getElementById("app-shell")?.classList.contains("app-shell--nav-open");w(s)}),o&&o.addEventListener("click",()=>w(!1)),document.addEventListener("keydown",i=>{i.key==="Escape"&&w(!1)}),a.addEventListener("change",i=>{i.matches&&w(!1)}),M()}function P(e){if(window.fileFridgeAppReady){e();return}window.__fileFridgeReadyQueue||(window.__fileFridgeReadyQueue=[]),window.__fileFridgeReadyQueue.push(e)}document.readyState==="loading"?document.addEventListener("DOMContentLoaded",A,{once:!0}):A();function T(){fetch("/health").then(e=>e.json()).then(e=>{const t=e.version||"0.0.0",n=e.app_name||"File Fridge";document.title=document.title.replace("File Fridge",n),["app-name-navbar","app-name-footer"].forEach(u=>{const r=document.getElementById(u);r&&(r.textContent=n)});const a=document.getElementById("app-version");a&&(a.textContent="v"+t);const i=document.getElementById("footer-app-version");i&&(i.textContent=t);const s=document.getElementById("footer-app-version");s&&(s.textContent=t)}).catch(e=>{console.error("Failed to fetch app info:",e);const t=document.getElementById("app-version");t&&(t.textContent="v0.0.0");const n=document.getElementById("footer-app-version");n&&(n.textContent="0.0.0")})}let p=null;window.addEventListener("beforeinstallprompt",e=>{e.preventDefault(),p=e;const t=document.getElementById("pwa-install-btn");t&&t.classList.remove("d-none")});window.addEventListener("appinstalled",()=>{const e=document.getElementById("pwa-install-btn");e&&e.classList.add("d-none"),p=null,f("App installed successfully!","success",3e3)});async function Q(){if(!p){f("Installation is not available","warning",3e3);return}p.prompt();const{outcome:e}=await p.userChoice;e==="accepted"?f("App installed successfully!","success",3e3):f("App installation was cancelled","info",3e3),p=null;const t=document.getElementById("pwa-install-btn");t&&t.classList.add("d-none")}window.showToast=f;window.showConfirmModal=L;window.showConfirmModalWithCheckbox=q;window.getAuthToken=g;window.setAuthToken=S;window.isAuthenticated=C;window.addAuthHeader=x;window.authenticatedFetch=R;window.formatBytes=_;window.formatRelativeTime=D;window.debounce=H;window.escapeHtml=c;window.setRegionState=N;window.assertRequiredElements=W;window.loadAppInfo=T;window.handleLogout=F;window.clearAuthToken=y;window.installPWA=Q;window.runWhenFileFridgeReady=P;window.fileFridgeAppReady=!0;window.__fileFridgeReadyQueue&&window.__fileFridgeReadyQueue.splice(0).forEach(t=>{try{t()}catch(n){console.error("File Fridge queued callback failed:",n)}});window.dispatchEvent(new Event("filefridge:app-ready"));
