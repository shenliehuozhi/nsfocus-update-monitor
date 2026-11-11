// 经典脚本，无构建；按 index.html 中的顺序加载，跨文件共享全局作用域。
const API='/api';
// 会话凭证在 HttpOnly Cookie 里，JS 不保存 token；localStorage 仅用于兼容升级前
// 已登录的旧标签页（/auth/me 成功后由 bootstrap 清掉）。
let T=localStorage.getItem('token'),U=null;
let _authExpired=false,_currentPage=null,_navSeq=0;
// 页面渲染代际：await 之后写 #app 前必须校验，迟到的响应不能覆盖用户已切到的页面
function navStale(seq){return seq!==_navSeq;}

// CSRF 双提交：Cookie 鉴权的非安全方法必须回传服务端下发的 nsm_csrf
function _csrfToken(){
  const m=document.cookie.match(/(?:^|;\s*)nsm_csrf=([^;]*)/);
  return m?decodeURIComponent(m[1]):'';
}
function _authHeaders(extra){
  const h={'Content-Type':'application/json',...(extra||{})};
  if(T)h['Authorization']='Bearer '+T;
  const c=_csrfToken();
  if(c)h['X-CSRF-Token']=c;
  return h;
}

// 401 统一出口：token 过期/被吊销时所有页面都只是静默空数据，必须主动回登录页。
// 只处理「本来已登录」的情况：凭证在 HttpOnly Cookie 里，不能再用 T 判断。
function _handleUnauthorized(){
  if(!U||_authExpired)return;
  _authExpired=true;
  _clearAllTimers();
  T=null;U=null;localStorage.removeItem('token');
  toast('登录已过期，请重新登录','error');
  renderLogin();
}

 function api(path,opts={}){
  return fetch(API+path,{...opts,headers:_authHeaders(),credentials:'same-origin'}).then(r=>{
    if(r.status===401&&path!=='/auth/login'){
      _handleUnauthorized();
      return {code:401,message:'登录已过期'};
    }
    if(!r.ok){
      // Try to parse server's JSON error message first
      return r.json().catch(()=>({})).then(body=>{
        return Promise.reject({code:r.status,message:body.message||`服务器错误 ${r.status}`});
      });
    }
    return r.json();
  }).catch(e=>{console.error('API error:',e);return {code:-1,message:e.message||'网络错误'};});
}

function toast(m,s='success'){
  const e=document.createElement('div');e.className=`toast toast-${s}`;e.textContent=m;document.body.appendChild(e);setTimeout(()=>e.remove(),4000);
}

function escHtml(s){
  return String(s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// 历史模板的内联样式统一改写为 data-css：CSP 的 style-src 不放行内联 style 属性，
// 而 CSSOM（el.style）不受限制。节点插入后由 MutationObserver 应用，视觉与语义不变。
// 新代码请直接写 CSS 类，不要再新增 data-css。
function _applyDataCss(root){
  if(!root||root.nodeType!==1&&root.nodeType!==9&&root.nodeType!==11)return;
  const els=root.nodeType===1&&root.hasAttribute('data-css')?[root]:[];
  if(root.querySelectorAll)root.querySelectorAll('[data-css]').forEach(el=>els.push(el));
  els.forEach(el=>{
    const css=el.getAttribute('data-css');
    el.removeAttribute('data-css');
    // data-css 在前、脚本已设置的样式在后，保持「脚本覆盖模板」的原有优先级
    if(css)el.style.cssText=css+';'+el.style.cssText;
  });
}
(function(){
  if(typeof MutationObserver==='undefined'){_applyDataCss(document);return;}
  new MutationObserver(ms=>{
    ms.forEach(m=>m.addedNodes.forEach(n=>_applyDataCss(n)));
  }).observe(document.documentElement,{childList:true,subtree:true});
  _applyDataCss(document);
})();

// data-args 的 JSON 必须走 HTML 属性转义（escHtml），不能用 escAttr：
// escAttr 会给 ' 和 \\ 加反斜杠，破坏 JSON.parse。
function _dataArgs(){
  return escHtml(JSON.stringify(Array.prototype.slice.call(arguments)));
}

// ── 事件委托（CSP：模板里不再写内联 on*）──────────────────────────────
// 写法：data-on-click="fn" data-args='[...]'
// 参数占位符：$el（当前元素）、$event、$value（el.value）、$checked（el.checked）。
// 修饰：data-stop 阻止冒泡，data-self 仅 e.target===元素自身时触发，
//       data-key 限定按键，data-prevent 等价内联的 return false。
// 从目标向上逐层执行，data-stop 后停止，等价于原生内联处理器的冒泡语义。
function _actArg(v,el,ev){
  if(v==='$el')return el;
  if(v==='$event')return ev;
  if(v==='$value')return el.value;
  if(v==='$checked')return el.checked;
  return v;
}
function _actRun(el,ev){
  if(el.hasAttribute('data-stop'))ev.stopPropagation();
  if(el.hasAttribute('data-self')&&ev.target!==el)return;
  if(el.hasAttribute('data-key')&&ev.key!==el.getAttribute('data-key'))return;
  const name=el.getAttribute('data-on-'+ev.type);
  if(!name)return;
  if(!/^[A-Za-z_$][\w$]*$/.test(name)){console.error('非法动作名:',name);return;}
  const fn=window[name];
  if(typeof fn!=='function'){console.error('缺少动作函数:',name);return;}
  let args=[];
  const raw=el.getAttribute('data-args');
  if(raw){
    try{args=JSON.parse(raw).map(v=>_actArg(v,el,ev));}
    catch(err){console.error('data-args 解析失败:',el,err);return;}
  }
  if(el.hasAttribute('data-prevent'))ev.preventDefault();
  fn.apply(el,args);
}
['click','change','input','keydown'].forEach(type=>{
  document.addEventListener(type,ev=>{
    if(!ev.target||!ev.target.closest)return;
    for(let el=ev.target;el&&el!==document;el=el.parentElement){
      if(el.hasAttribute&&(el.hasAttribute('data-on-'+type)||el.hasAttribute('data-stop'))){
        _actRun(el,ev);
        if(ev.cancelBubble)break;
      }
    }
  });
});

// 常用小动作（复杂内联表达式的等价实现）
function _noop(){}
function _removeById(id){const el=document.getElementById(id);if(el)el.remove();}
function _clearHtmlById(id){const el=document.getElementById(id);if(el)el.innerHTML='';}
function _showById(id){const el=document.getElementById(id);if(el)el.style.display='block';}
function _hideById(id){const el=document.getElementById(id);if(el)el.style.display='none';}
function _setDisplay(id,show){const el=document.getElementById(id);if(el)el.style.display=show?'':'none';}
function _removeSelf(){this.remove();}
function _removeClosestId(){const el=this.closest('[id]');if(el)el.remove();}
function _removeClosest(sel){const el=this.closest(sel);if(el)el.remove();}
function _removeParent(){if(this.parentElement)this.parentElement.remove();}
function _toggleClass(cls){this.classList.toggle(cls);}
function _toggleByIdClass(id,cls){const el=document.getElementById(id);if(el)el.classList.toggle(cls);}
function _setBlock(id,show){const el=document.getElementById(id);if(el)el.style.display=show?'block':'none';}
function _showPicker(){if(this.showPicker)this.showPicker();}
function _menuShowPwd(){showPwdModal();closeUserMenu();}
function _menuLogout(){logout();closeUserMenu();}

function _confirmRun(id,ctr){
  const b=this;b.disabled=true;b.textContent='处理中...';
  const box=document.getElementById(id);if(box)box.remove();
  const fn=window._confirmCBs[ctr];
  if(fn){delete window._confirmCBs[ctr];fn();}
}
function _discConfirmRun(ctr){
  const b=this;b.disabled=true;b.textContent='处理中...';
  closeDiscConfirm();
  const fn=window._confirmCBs[ctr];
  if(fn){delete window._confirmCBs[ctr];fn();}
}

// confirmAct(msg, asyncFn) — shows modal, calls asyncFn() on confirm
// Replaces all browser confirm() calls for a consistent look
// 闭包不再通过字符串序列化传递，改用全局回调表（避免某些 WebView 环境下闭包上下文丢失）
window._confirmCBs = {};
window._confirmCtr = 0;
function confirmAct(msg, asyncFn, icon='⚠️', okLabel='确认', isWarning=false, bodyHtml='') {
  const id = '_confirmBox';
  const existing = document.getElementById(id);
  if (existing) existing.remove();
  const ctr = window._confirmCtr++;
  window._confirmCBs[ctr] = asyncFn;
  const overlay = document.createElement('div');
  overlay.className = 'confirm-modal active';
  overlay.id = id;
  // 防双击/防重复触发:
  // 1) 第一次 onclick 同步 disable 确认按钮 + 改文字,阻止第二次 click 触达 callback
  //    (浏览器对 disabled button 不触发 click event)
  // 2) 同步 delete window._confirmCBs[ctr],即使 disabled 没生效,第二次 onclick
  //    进来 fn 也是 undefined,if(fn) 不进 → callback 只调 1 次
  // 3) overlay.remove() 在 callback 执行前同步完成 → DOM 移除,后续点击无元素可点
  overlay.innerHTML = `<div class="confirm-box">
    <div class="confirm-icon">${icon}</div>
    <div class="confirm-title">${msg}</div>
    ${bodyHtml}
    <div class="confirm-btns">
      <button class="confirm-btn-cancel" data-on-click="_removeById" data-args='${_dataArgs(id)}'>取消</button>
      <button class="confirm-btn-ok${isWarning?' warning':''}" data-on-click="_confirmRun" data-args='${_dataArgs(id, ctr)}'>${okLabel}</button>
    </div>
  </div>`;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
  document.body.appendChild(overlay);
  overlay.querySelector('.confirm-btn-cancel').focus();
}

// discConfirmAct() — 跟 confirmAct() 同语义,但用 _showModal 弹窗(580px 紫边框),
// 跟手动添加弹窗视觉一致。两个弹窗都"产品管理"操作的入口,放一起合理。
// icon/title/bodyHtml 参数语义跟 confirmAct 一样。
function discConfirmAct(msg, asyncFn, icon='🗂️', okLabel='确认', bodyHtml='') {
  const id = '_discConfirmBox';
  const existing = document.getElementById(id);
  if (existing) existing.remove();
  const ctr = window._confirmCtr++;
  window._confirmCBs[ctr] = asyncFn;
  // 复用 _showModal 的 disc 主题样式
  document.getElementById('app').insertAdjacentHTML('beforeend',
    `<div class="overlay-1000" id="${id}-overlay" data-on-click="closeDiscConfirm" data-self>
       <div id="${id}-box" data-css="background:#fff;border-radius:12px;border:2px solid #7c3aed;box-shadow:0 8px 24px rgba(124,58,237,0.15),0 20px 60px rgba(0,0,0,0.25);width:100%;max-width:580px;max-height:85vh;overflow-y:auto;padding:24px">
         <div data-css="display:flex;align-items:flex-start;gap:12px;margin-bottom:16px">
           <div data-css="font-size:36px;line-height:1;flex-shrink:0">${icon}</div>
           <div class="flex-1">
             <div data-css="font-size:16px;font-weight:700;color:#1a2332;margin-bottom:6px">${msg}</div>
           </div>
           <div data-on-click="closeDiscConfirm" data-css="cursor:pointer;font-size:20px;color:var(--gray-disabled);line-height:1">×</div>
         </div>
         ${bodyHtml}
         <div data-css="display:flex;gap:10px;margin-top:20px">
           <button class="btn" data-on-click="closeDiscConfirm" data-css="flex:1;background:var(--gray-bg);color:var(--text);border:1px solid var(--gray-border)">取消</button>
           <button class="btn" data-on-click="_discConfirmRun" data-args='${_dataArgs(ctr)}' data-css="flex:1;background:#7c3aed;color:#fff;border:none;font-weight:600">${okLabel}</button>
         </div>
       </div>
     </div>`);
}
function closeDiscConfirm(){
  const o=document.getElementById('_discConfirmBox-overlay');
  const b=document.getElementById('_discConfirmBox-box');
  if(o)o.remove();
  if(b)b.remove();
}

// ── Login Background Rotation ──
const _bgImages=['wallhaven-6d1336_compressed.jpg','wallhaven-85w8xj_compressed.jpg','wallhaven-eyqeyl_compressed.jpg'];
let _bgIdx=0,_bgTimer=null;
function _startBgRotation(){
  if(_bgTimer)return;
  document.body.style.backgroundImage='url(/static/'+_bgImages[0]+')';
  _bgTimer=setInterval(()=>{_bgIdx=(_bgIdx+1)%_bgImages.length;document.body.style.backgroundImage='url(/static/'+_bgImages[_bgIdx]+')';},5000);
}
function _stopBgRotation(){if(_bgTimer){clearInterval(_bgTimer);_bgTimer=null;}}

// ── Auth ──
function renderLogin(){
  document.body.className='login-bg';
  _startBgRotation();
  document.getElementById('header').style.display='none';
  document.getElementById('app').innerHTML=`<div data-css="display:flex;flex-direction:column;align-items:center;justify-content:center;height:70vh;gap:24px">
    <div data-css="text-align:center">
      <div data-css="font-size:48px;margin-bottom:16px">🛡️</div>
      <div data-css="font-size:16px;color:var(--gray-muted);margin-bottom:24px">绿盟升级监控</div>
      <button class="btn btn-primary" data-on-click="showLoginForm" data-css="padding:10px 32px;font-size:14px">登录系统</button>
    </div>
  </div>`;
}

function showLoginForm(){
  document.getElementById('app').innerHTML=`<div class="login-box card"><div class="card-bd"><h2>🛡️ 绿盟升级监控</h2><div class="form-group"><label>用户名</label><input id="lu" placeholder="admin" autofocus></div><div class="form-group"><label>密码</label><input id="lp" type="password" data-on-keydown="doLogin" data-key="Enter"></div><button class="btn btn-primary" data-css="width:100%;justify-content:center" data-on-click="doLogin">登录</button></div></div>`;
  document.getElementById('lu').focus();
}
async function doLogin(){
  const r=await api('/auth/login',{method:'POST',body:JSON.stringify({username:document.getElementById('lu').value,password:document.getElementById('lp').value})});
  if(r.code===0){_authExpired=false;T=null;localStorage.removeItem('token');U=r.data.user;_stopBgRotation();document.body.className='';document.body.style.backgroundImage='';document.getElementById('unameText').textContent=U.username;document.getElementById('header').style.display='flex';showPage('dashboard');_refreshVersionBadge();}else toast(r.message,'error');
}
function _clearPageTimers(page){
  if(page==='dashboard'){
    if(_uptimeTimer){clearInterval(_uptimeTimer);_uptimeTimer=null;}
    if(window._eventsRefreshTimer){clearInterval(window._eventsRefreshTimer);window._eventsRefreshTimer=null;}
    if(window._dashPollTimer){clearInterval(window._dashPollTimer);window._dashPollTimer=null;}
  }else if(page==='logs'){
    if(_logTimer){clearInterval(_logTimer);_logTimer=null;}
  }else if(page==='observability'){
    if(window._obsTimer){clearInterval(window._obsTimer);window._obsTimer=null;}
  }
}
function _clearAllTimers(){
  _clearPageTimers('dashboard');_clearPageTimers('logs');_clearPageTimers('observability');
  if(window._eventsHeaderTimer){clearInterval(window._eventsHeaderTimer);window._eventsHeaderTimer=null;}
  if(window._eventsCenterModalTimer){clearInterval(window._eventsCenterModalTimer);window._eventsCenterModalTimer=null;}
  if(_collectPollTimer){clearInterval(_collectPollTimer);_collectPollTimer=null;}
  if(_collectBannerPollTimer){clearInterval(_collectBannerPollTimer);_collectBannerPollTimer=null;}
  if(_singleCollTimer){clearInterval(_singleCollTimer);_singleCollTimer=null;}
  if(window._discTimer){clearInterval(window._discTimer);window._discTimer=null;}
  if(_banTimer){clearInterval(_banTimer);_banTimer=null;}
  if(typeof stopProductsPolls==='function')stopProductsPolls();
  _stopBgRotation();
}
function logout(){
  api('/auth/logout',{method:'POST'}).catch(()=>{});
  _clearAllTimers();_currentPage=null;
  T=null;U=null;localStorage.removeItem('token');renderLogin();
}

// ── User dropdown menu (header right corner) ─────────────
function toggleUserMenu(e){
  e && e.stopPropagation();
  const dd = document.getElementById('userDropdown');
  if(!dd) return;
  dd.style.display = dd.style.display === 'none' || dd.style.display === '' ? 'block' : 'none';
}
function closeUserMenu(){
  const dd = document.getElementById('userDropdown');
  if(dd) dd.style.display = 'none';
}
// Click outside the user menu closes it
document.addEventListener('click', (e) => {
  const menu = document.getElementById('userMenu');
  const dd = document.getElementById('userDropdown');
  if(!menu || !dd) return;
  if(dd.style.display === 'none' || dd.style.display === '') return;
  if(!menu.contains(e.target)) dd.style.display = 'none';
});

/* ── 自定义下拉浮层(渐进增强) ──────────────────────────────────────────
   原生 <select class="dd"> 的选项列表由系统绘制, 无法跟随全站圆角/主色。这里只替换
   指针展开的那一层: select 本身仍在 DOM 里持有 value, 选中后照常派发
   change, 所以内联 onchange / 键盘操作 / 表单语义全部保持原样。
   触屏设备不接管(系统 popup 在移动端更好用)。 */
const _dd = { sel: null, pop: null, idx: -1 };
const _ddTouch = () => matchMedia('(pointer: coarse)').matches;

function ddClose(){
  if(_dd.pop){ _dd.pop.remove(); _dd.pop = null; }
  _dd.sel = null; _dd.idx = -1;
}

function _ddMark(){
  if(!_dd.pop) return;
  [..._dd.pop.children].forEach((d, i) => d.classList.toggle('active', i === _dd.idx));
}

function ddOpen(sel){
  ddClose();
  const items = [...sel.options];
  if(!items.length) return;
  const pop = document.createElement('div');
  pop.className = 'dd-pop';
  pop.setAttribute('role', 'listbox');
  const cur = sel.selectedIndex;
  items.forEach((o, i) => {
    const d = document.createElement('div');
    d.className = 'dd-opt' + (i === cur ? ' on' : '') + (o.disabled ? ' off' : '');
    d.setAttribute('role', 'option');
    d.setAttribute('aria-selected', i === cur ? 'true' : 'false');
    d.dataset.i = i;
    d.textContent = o.text;
    pop.appendChild(d);
  });
  document.body.appendChild(pop);
  const r = sel.getBoundingClientRect();
  const w = Math.max(Math.round(r.width), 132);
  const h = pop.offsetHeight;
  const below = r.bottom + 4;
  const above = r.top - h - 4;
  pop.style.minWidth = w + 'px';
  pop.style.top = (below + h > innerHeight - 8 && above > 8 ? above : below) + 'px';
  pop.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
  _dd.sel = sel; _dd.pop = pop; _dd.idx = Math.max(cur, 0);
  _ddMark();
  const on = pop.querySelector('.dd-opt.on') || pop.firstChild;
  on.scrollIntoView({ block: 'nearest' });
}

function ddPick(i){
  const sel = _dd.sel, o = sel && sel.options[i];
  if(!sel || !o || o.disabled) return;
  const changed = sel.value !== o.value;
  sel.value = o.value;
  ddClose();
  if(sel.isConnected) sel.focus();
  if(changed && sel.isConnected) sel.dispatchEvent(new Event('change', { bubbles: true }));
}

document.addEventListener('mousedown', e => {
  const t = e.target;
  if(!t || !t.closest || e.button !== 0) return;
  if(t.closest('.dd-opt')){ e.preventDefault(); return; }   // 选中放到 click 里, 拖出选项再松手就不选
  const sel = t.closest('select.dd');
  if(sel && (sel.disabled || sel.multiple || sel.size > 1)) return;   // 禁用/列表式不接管
  if(_dd.pop && !t.closest('.dd-pop')){
    const same = sel === _dd.sel;   // 点同一个控件 = 收起
    ddClose();
    if(same) return;
  }
  if(!sel || _ddTouch()) return;
  e.preventDefault();              // 关掉系统 popup
  sel.focus();
  ddOpen(sel);
}, true);

document.addEventListener('click', e => {
  const t = e.target;
  const opt = t && t.closest && t.closest('.dd-opt');
  if(opt){ e.preventDefault(); ddPick(+opt.dataset.i); }
});

document.addEventListener('keydown', e => {
  if(!_dd.pop) return;             // 未展开时键盘完全交给原生 select
  if(e.key === 'Escape'){ e.preventDefault(); const s = _dd.sel; ddClose(); if(s && s.isConnected) s.focus(); }
  else if(e.key === 'ArrowDown'){ e.preventDefault(); _dd.idx = Math.min(_dd.idx + 1, _dd.pop.children.length - 1); _ddMark(); }
  else if(e.key === 'ArrowUp'){ e.preventDefault(); _dd.idx = Math.max(_dd.idx - 1, 0); _ddMark(); }
  else if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); ddPick(_dd.idx); }
  else if(e.key === 'Tab') ddClose();
});

addEventListener('scroll', e => { if(_dd.pop && !_dd.pop.contains(e.target)) ddClose(); }, true);
addEventListener('resize', ddClose);

// 页面函数多为 async：异常只落在 Promise 里时用户只会看到「点击没反应」。
// 统一在这里暴露——控制台留栈，界面给一条可读提示（401 已由 api() 单独处理）。
function _pageRenderError(page,err){
  console.error(`[page] ${page} 渲染失败:`,err);
  if(_authExpired)return;
  toast(`页面加载失败：${(err&&err.message)||err}`,'error');
}
function showPage(n){
  document.querySelectorAll('.nav a').forEach(a=>a.classList.remove('active'));
  const l=document.querySelector(`.nav a[data-page="${n}"]`);if(l)l.classList.add('active');
  // 页面级轮询只在该页存活：切走即停，避免离开后仍每 N 秒请求并写已卸载的 DOM
  _clearPageTimers(_currentPage);
  _currentPage=n;_navSeq++;
  const pages={dashboard,sessions,channels,subscriptions,customers,data:dataPage,history,settings,products,logs,observability,tools:toolsPage};
  if(pages[n]){
    try{
      const p=pages[n]();
      if(p&&typeof p.catch==='function')p.catch(e=>_pageRenderError(n,e));
    }catch(e){_pageRenderError(n,e);}
  }
  // 2026-08-25: 每次切页都检查后端是否有活动采集,有的话启动 banner 全局轮询
  // 这样从其他菜单页面也能看到进度 banner 更新(banner 是全站浮动卡片)
  api('/system/collect/progress').then(pr=>{
    const p = pr?.data?.progress || {};
    if (p.phase && p.active) {
      startCollectBannerPoll();
    }
  }).catch(()=>{});
  // 切页时检查 pending disc 状态,刷新导航栏角标
  api('/system/products/discover/status').then(s=>{
    if(s.code===0 && s.data?.phase==='done' && s.data?.result && !s.data?.active){
      if(!window._pendingDiscResult){
        window._pendingDiscResult=s.data.result;
        window._pendingDiscStale=!!s.data.is_stale;
        updateNavPendingBadge();
      }
    }else if(window._pendingDiscResult && s.data?.active){
      // 正在发现中(后续会再写 _pendingDiscResult),保持现有状态
    }
  }).catch(()=>{});
}

function typeCN(t){const m={sys:'系统',rule:'规则',nti:'威胁情报',av:'病毒库',apprule:'应用规则',url:'URL',wcs:'恶意站点',judge:'研判',geo:'地理库',interface:'接口',special:'特殊',other:'其他',merge:'合并',client:'客户端',av_stream:'流式病毒库',test:'测试'};return m[t]||t;}
