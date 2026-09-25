function e(){r(),c(`You have been logged out`,`info`,3e3),setTimeout(()=>{window.location.href=`/login`},500)}function t(){return sessionStorage.getItem(`auth_token`)}function n(e){sessionStorage.setItem(`auth_token`,e)}function r(){sessionStorage.removeItem(`auth_token`)}function i(){return!!t()}function a(){window.location.href=`/login`}function o(e={}){let n=t();return n&&(e.headers=e.headers||{},e.headers.Authorization=`Bearer ${n}`),e}async function s(e,t={}){t=o(t);let n=await fetch(e,t);if(n.status===401)throw r(),a(),Error(`Authentication required`);return n}function c(e,t=`success`,n=5e3){let r=document.querySelector(`.toast-container`);r||(r=document.createElement(`div`),r.className=`toast-container`,document.body.appendChild(r));let i=document.createElement(`div`);i.className=`toast toast-${t}`,i.setAttribute(`role`,`alert`),i.setAttribute(`aria-live`,`assertive`),i.setAttribute(`aria-atomic`,`true`);let a={success:`bi-check-circle-fill`,error:`bi-x-circle-fill`,warning:`bi-exclamation-triangle-fill`,info:`bi-info-circle-fill`},o={success:`Success`,error:`Error`,warning:`Warning`,info:`Information`};i.innerHTML=`
        <div class="toast-header">
            <i class="bi ${a[t]||a.info} me-2"></i>
            <strong class="me-auto">${o[t]||o.info}</strong>
            <button type="button" class="btn-close" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
        <div class="toast-body">
            ${l(e)}
        </div>
    `,r.appendChild(i);let s=new bootstrap.Toast(i,{autohide:n>0,delay:n});return s.show(),i.addEventListener(`hidden.bs.toast`,()=>{i.remove()}),s}function l(e){let t=document.createElement(`div`);return t.textContent=e,t.innerHTML}function u(e){return new Promise(t=>{let{title:n=`Confirm Action`,message:r=`Are you sure you want to proceed?`,confirmText:i=`Confirm`,cancelText:a=`Cancel`,confirmClass:o=`btn-primary`,dangerous:s=!1}=e,c=`confirmModal-`+Date.now(),u=document.createElement(`div`);u.className=`modal fade`,u.id=c,u.setAttribute(`tabindex`,`-1`),u.setAttribute(`aria-labelledby`,c+`Label`),u.setAttribute(`aria-hidden`,`true`);let d=s?`btn-danger`:o;u.innerHTML=`
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="${c}Label">
                            ${s?`<i class="bi bi-exclamation-triangle-fill text-danger me-2"></i>`:``}
                            ${l(n)}
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        ${l(r)}
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">${l(a)}</button>
                        <button type="button" class="btn ${d}" id="${c}-confirm">${l(i)}</button>
                    </div>
                </div>
            </div>
        `,document.body.appendChild(u);let f=new bootstrap.Modal(u);u.querySelector(`#${c}-confirm`).addEventListener(`click`,()=>{f.hide(),t(!0)}),u.addEventListener(`hidden.bs.modal`,()=>{u.remove(),t(!1)}),f.show()})}function d(e){return new Promise(t=>{let{title:n=`Confirm Action`,message:r=`Are you sure you want to proceed?`,checkboxLabel:i=`Additional option`,checkboxDefault:a=!1,confirmText:o=`Confirm`,cancelText:s=`Cancel`,dangerous:c=!1}=e,u=`confirmModal-`+Date.now(),d=`checkbox-`+Date.now(),f=document.createElement(`div`);f.className=`modal fade`,f.id=u,f.setAttribute(`tabindex`,`-1`);let p=c?`btn-danger`:`btn-primary`;f.innerHTML=`
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">
                            ${c?`<i class="bi bi-exclamation-triangle-fill text-danger me-2"></i>`:``}
                            ${l(n)}
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        <p>${l(r)}</p>
                        <div class="form-check mt-3">
                            <input class="form-check-input" type="checkbox" id="${d}" ${a?`checked`:``}>
                            <label class="form-check-label" for="${d}">
                                ${l(i)}
                            </label>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">${l(s)}</button>
                        <button type="button" class="btn ${p}" id="${u}-confirm">${l(o)}</button>
                    </div>
                </div>
            </div>
        `,document.body.appendChild(f);let m=new bootstrap.Modal(f),h=f.querySelector(`#${u}-confirm`),g=f.querySelector(`#${d}`);h.addEventListener(`click`,()=>{m.hide(),t({confirmed:!0,checked:g.checked})}),f.addEventListener(`hidden.bs.modal`,()=>{f.remove(),t({confirmed:!1,checked:!1})}),m.show()})}function f(e){if(e===0)return`0 Bytes`;let t=1024,n=[`Bytes`,`KB`,`MB`,`GB`,`TB`],r=Math.floor(Math.log(e)/Math.log(t));return Math.round(e/t**r*100)/100+` `+n[r]}function p(e){let t=new Date,n=new Date(e),r=Math.floor((t-n)/1e3);return r<60?`just now`:r<3600?Math.floor(r/60)+` minutes ago`:r<86400?Math.floor(r/3600)+` hours ago`:r<2592e3?Math.floor(r/86400)+` days ago`:r<31536e3?Math.floor(r/2592e3)+` months ago`:Math.floor(r/31536e3)+` years ago`}function m(e,t){let n;return function(...r){clearTimeout(n),n=setTimeout(()=>{clearTimeout(n),e(...r)},t)}}function h(e){return e?typeof e==`string`?document.querySelector(e):e:null}function g(e={}){let{loading:t,content:n,empty:r,error:i,state:a=`content`,errorMessage:o=``,showContentOnEmpty:s=!1}=e,c=h(t),u=h(n),d=h(r),f=h(i);if(c&&(c.style.display=a===`loading`?``:`none`),u){let e=a===`content`||a===`empty`&&s;u.style.display=e?``:`none`}d&&(d.style.display=a===`empty`?``:`none`),f?(f.style.display=a===`error`?``:`none`,a===`error`&&o&&(f.innerHTML=`
                <div class="alert alert-danger mb-0" role="alert">
                    <i class="bi bi-exclamation-triangle-fill me-2"></i>${l(o)}
                </div>
            `)):c&&a===`error`&&o&&(c.style.display=``,c.innerHTML=`
            <div class="alert alert-danger mb-0" role="alert">
                <i class="bi bi-exclamation-triangle-fill me-2"></i>${l(o)}
            </div>
        `)}function _(e,t=`page`){let n=e.filter(e=>!document.getElementById(e));if(n.length>0){let e=`Missing required ${t} element(s): ${n.join(`, `)}`;throw console.error(e),Error(e)}}function v(){b(),navigator.onLine?(c(`You are back online`,`success`,3e3),setTimeout(()=>{window.location.reload()},1e3)):c(`You are offline. Some features may not be available.`,`warning`,5e3)}window.addEventListener(`online`,v),window.addEventListener(`offline`,v);function y(e){let t=document.getElementById(`app-shell`),n=document.getElementById(`sidebar-toggle`);t&&n&&(t.classList.toggle(`app-shell--nav-open`,e),n.setAttribute(`aria-expanded`,String(e)))}function b(){let e=document.getElementById(`connection-status-pill`);e&&(navigator.onLine?(e.textContent=`Online`,e.className=`status-pill status-pill--success`):(e.textContent=`Offline mode`,e.className=`status-pill status-pill--warning`))}var x=!1;function S(){if(x)return;if(x=!0,T(),window.location.pathname!==`/login`&&!i()){a();return}document.querySelectorAll(`.alert:not(.toast)`).forEach(function(e){setTimeout(function(){let t=bootstrap.Alert.getInstance(e);t&&t.close()},5e3)}),document.querySelectorAll(`form[data-confirm]`).forEach(function(e){e.addEventListener(`submit`,async function(t){t.preventDefault(),await u({title:`Confirm Deletion`,message:e.getAttribute(`data-confirm`)||`Are you sure you want to delete this item?`,confirmText:`Delete`,dangerous:!0})&&e.submit()})});let e=document.getElementById(`sidebar-toggle`),t=document.getElementById(`app-shell-backdrop`),n=window.matchMedia(`(min-width: 992px)`);e&&e.addEventListener(`click`,()=>{y(!document.getElementById(`app-shell`)?.classList.contains(`app-shell--nav-open`))}),t&&t.addEventListener(`click`,()=>y(!1)),document.addEventListener(`keydown`,e=>{e.key===`Escape`&&y(!1)}),n.addEventListener(`change`,e=>{e.matches&&y(!1)}),b(),window.fileFridgeAppReady=!0,window.__fileFridgeReadyQueue&&window.__fileFridgeReadyQueue.splice(0).forEach(e=>{try{e()}catch(e){console.error(`File Fridge queued callback failed:`,e)}}),window.dispatchEvent(new Event(`filefridge:app-ready`))}function C(e){if(window.fileFridgeAppReady){e();return}window.__fileFridgeReadyQueue||(window.__fileFridgeReadyQueue=[]),window.__fileFridgeReadyQueue.push(e)}function w(){window.showToast=c,window.showConfirmModal=u,window.showConfirmModalWithCheckbox=d,window.getAuthToken=t,window.setAuthToken=n,window.isAuthenticated=i,window.addAuthHeader=o,window.authenticatedFetch=s,window.formatBytes=f,window.formatRelativeTime=p,window.debounce=m,window.escapeHtml=l,window.setRegionState=g,window.assertRequiredElements=_,window.loadAppInfo=T,window.handleLogout=e,window.clearAuthToken=r,window.installPWA=D,window.runWhenFileFridgeReady=C}w(),document.readyState===`loading`?document.addEventListener(`DOMContentLoaded`,S,{once:!0}):S();function T(){fetch(`/health`).then(e=>e.json()).then(e=>{let t=e.version||`0.0.0`,n=e.app_name||`File Fridge`;document.title=document.title.replace(`File Fridge`,n),[`app-name-navbar`,`app-name-footer`].forEach(e=>{let t=document.getElementById(e);t&&(t.textContent=n)});let r=document.getElementById(`app-version`);r&&(r.textContent=`v`+t);let i=document.getElementById(`footer-app-version`);i&&(i.textContent=t)}).catch(e=>{console.error(`Failed to fetch app info:`,e);let t=document.getElementById(`app-version`);t&&(t.textContent=`v0.0.0`);let n=document.getElementById(`footer-app-version`);n&&(n.textContent=`0.0.0`)})}var E=null;window.addEventListener(`beforeinstallprompt`,e=>{e.preventDefault(),E=e;let t=document.getElementById(`pwa-install-btn`);t&&t.classList.remove(`d-none`)}),window.addEventListener(`appinstalled`,()=>{let e=document.getElementById(`pwa-install-btn`);e&&e.classList.add(`d-none`),E=null,c(`App installed successfully!`,`success`,3e3)});async function D(){if(!E){c(`Installation is not available`,`warning`,3e3);return}E.prompt();let{outcome:e}=await E.userChoice;e===`accepted`?c(`App installed successfully!`,`success`,3e3):c(`App installation was cancelled`,`info`,3e3),E=null;let t=document.getElementById(`pwa-install-btn`);t&&t.classList.add(`d-none`)}w();