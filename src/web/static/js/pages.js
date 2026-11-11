// 经典脚本，无构建；按 index.html 中的顺序加载，跨文件共享全局作用域。
// ── Sessions ──
const SES_PURPOSE={'discover':'发现','collect':'采集'};
const SES_MODE={'standard':'标品','vm':'虚拟化'};

// 浮动日志窗口统一工厂(discover / confirm / refresh-pkg 共用)
// 4 处原本分散硬编码(z-index 9999/2000, max-height 200/180px, 关闭按钮样式各异),
// 抽取后改样式只动这一处。refresh-pkg 单独用 .log-body class (updateRing 用 querySelector 拿),
// 其他 3 处用 ${id}-body id(2 处兼容旧引用)。
// 边框用浅紫 #c4b5fd(violet-300)替代默认 --gray-border(#e2e7ee),在浅蓝灰
// 环境(--bg:#f1f4f8)上能看出边界。disc/confirm/refresh-pkg 都是采集任务,
// 用同主题色统一。
const FLOAT_LOG_CSS = 'position:fixed;bottom:16px;right:16px;width:340px;background:#fff;border:1px solid #c4b5fd;border-radius:10px;box-shadow:0 8px 32px rgba(124,58,237,0.18);z-index:9999;display:none';
function _createFloatLog(logId, title, opts){
  // opts.bodyClass: 'class' (refresh-pkg 用) | undefined (默认 id=${logId}-body)
  const bodyClass = opts && opts.bodyClass;
  const bodyAttrs = bodyClass
    ? `class="${bodyClass}"`
    : `id="${logId}-body"`;
  const el = document.createElement('div');
  el.id = logId;
  el.style.cssText = FLOAT_LOG_CSS;
  el.innerHTML = `<div class="table-hd-row"><span class="flex-1">${title}</span><button class="btn-x-plain" data-on-click="_hideById" data-args='${_dataArgs(logId)}'>×</button></div><div class="json-pre" ${bodyAttrs}></div>`;
  return el;
}

// formatDiscPhase(state) — 统一 4 处 phase 文案(banner / modal / confirm)。
// 后端并发跑 Stage 2,progress 是"已完成数",running_products 是当前并发做的产品名列表。
// 输出示例:
//   scanning_products      → "🔍 扫描产品列表…"
//   discovering_pkg_types  → "📦 发现包类型 (5/73) · 正在并发:A, B, C"
//   done                   → "✅ 发现完成"
//   error                  → "❌ 发现出错"
// 不含 ETA — ETA 由调用方决定是否拼接。
function formatDiscPhase(state){
  const base={
    'scanning_products':'🔍 扫描产品列表…',
    'discovering_pkg_types':'',
    'done':'✅ 发现完成',
    'error':'❌ 发现出错',
  }[state.phase]||state.phase||'';
  if(state.phase==='discovering_pkg_types' && state.total){
    const prog=state.progress||0;
    let txt=`📦 发现包类型 (${prog}/${state.total})`;
    const running=state.running_products||[];
    if(running.length>0){
      // 截断显示前 3 个,过长省略
      const shown=running.length<=3?running:running.slice(0,3);
      const suffix=running.length>3?` 等 ${running.length} 个`:'';
      txt+=` · 正在并发:${shown.join(', ')}${suffix}`;
    }
    return txt;
  }
  return base;
}

// 顶部导航栏「产品管理」右侧的待确认角标
// 单一数据源是 window._pendingDiscResult(_checkDiscBanner / products() / confirmDiscoverResult() 写)。
// _pendingDiscStale 只用于决定弹窗文案,不影响角标显示 — 有 pending 就有角标。
function updateNavPendingBadge(){
  const badge=document.getElementById('nav-pending-badge');
  if(badge){
    if(window._pendingDiscResult){badge.classList.add('show');}
    else{badge.classList.remove('show');}
  }
  // 2026-08-24: 移动端汉堡按钮上同步显示红点,nav 被折叠时也可见
  const tb=document.getElementById('nav-toggle-badge');
  if(tb){
    if(window._pendingDiscResult){tb.classList.add('show');}
    else{tb.classList.remove('show');}
  }
}

function toggleMobileNav(){
  const n=document.getElementById('mainNav');
  if(n)n.classList.toggle('open');
}

// _cancelDisc() — 用户点 banner/modal 上的 ⏹ 按钮 → 调后端 cancel,等后端返回 cancelled 后刷新 UI。
// 后端策略:取消后已 submit 的 worker 会跑完(网络请求无法中断),但主线程不再收新结果;
// 本次所有产品结果丢弃,不写 pending,不进入 confirm 流程。
async function _cancelDisc(){
  const r=await api('/system/products/discover/cancel',{method:'POST'});
  if(r.code===0){
    toast('取消请求已提交,正在停止…','warn');
    // 后端 worker pool 5-15s 自然结束后 phase 切 cancelled,UI 自动响应
  }else{
    toast('取消失败: '+r.message,'error');
  }
}
function sesBadge(p,m){
  if(p==='discover')return'<span class="badge tone-purple">发现</span>';
  if(m==='vm')return'<span class="badge tone-cyan">采集-虚拟化</span>';
  return'<span class="badge badge-blue">采集-标品</span>';
}
async function sessions(){
  const _seq=_navSeq;
  const r=await api('/sessions');const my=r.data?.my_sessions||[],pool=r.data?.pool_status||{};
  let rows=my.map(s=>{
    let hbInfo='—';
    if(s.heartbeat_status){
      const hbColor=s.heartbeat_status==='正常'?'ok':s.heartbeat_status==='过期'?'err':s.heartbeat_status==='污染'?'warn':'err';
      hbInfo=`<span class="badge badge-${hbColor} pointer" data-on-click="showHbHistory" data-args='${_dataArgs(s.id)}' title="点击查看心跳历史">${s.heartbeat_status}</span>`;
    }
    let hbTime=s.last_heartbeat_at?fmtTZ(s.last_heartbeat_at,true):'—';
    return `<tr><td>${s.id}</td><td>${sesBadge(s.purpose,s.collect_mode)}</td><td><span class="badge badge-${s.status==='active'?'ok':s.status==='expired'?'err':'warn'}">${s.status==='active'?'活跃':s.status==='expired'?'失效':'未知'}</span></td><td>${hbInfo}</td><td>${hbTime}</td><td class="fs11">${s.heartbeat_count||0}次</td><td>${s.last_valid||'—'}</td><td>${s.expires_at||'—'}</td><td><button class="btn btn-xs btn-outline" data-on-click="validateSes" data-args='${_dataArgs(s.id)}'>验证</button> <button class="btn btn-soft-danger btn-xs" data-on-click="delSes" data-args='${_dataArgs(s.id)}'>删除</button></td></tr>`;
  }).join('');
  if(navStale(_seq))return;
  document.getElementById('app').innerHTML=`<div class="card"><div class="card-hd">🔑 Session <span class="muted-12 fw400">活跃 ${pool.active}/${pool.total}${pool.active_but_expired?` <span class="text-danger">⚠️${pool.active_but_expired}个已过期</span>`:''}</span><button class="btn btn-primary" data-on-click="openAddSesModal" data-css="margin-left:auto;padding:5px 14px;font-size:12px">+ 添加 Session</button></div><div class="card-bd">
  <!-- 健康度概览 -->
  <div class="flex gap12 mb16">
    <div class="stat-card-success">
      <div class="stat-num text-success-text">${pool.active||0}</div>
      <div class="text-success-dark fs11">活跃</div>
    </div>
    <div class="stat-card-danger">
      <div class="stat-num text-danger">${pool.expired||0}</div>
      <div class="danger-text-11">失效</div>
    </div>
    <div class="stat-card-warn">
      <div class="stat-num stat-num-warn">${pool.total-pool.active-pool.expired||0}</div>
      <div class="text-warn-dark fs11">未知</div>
    </div>
  </div>

  <table><tr><th>ID</th><th>类型</th><th title="活跃=可用且心跳正常 | 失效=已过期或被污染 | 未知=从未验证">状态 ↕</th><th title="正常=心跳OK | 过期=302跳转/失效 | 污染=访问过虚拟化上传页面，上下文被Vm格式干扰 | 错误=网络问题">心跳 ↕</th><th>最近心跳</th><th>次数</th><th>最后有效</th><th>过期</th><th>操作</th></tr>${rows||'<tr><td colspan="9" class="empty">暂无</td></tr>'}</table></div></div>
  <div id="hbModal"></div>`;}
window._sesPurpose='collect';window._sesMode='standard';

function _etPurposeChanged(el){
  const m=document.getElementById('etM');
  m.disabled=el.value==='discover';
  if(el.value==='discover')m.value='standard';
}
async function _saveSessionEdit(id,mid,p,m){
  const newP=document.getElementById('etP').value||p, newM=document.getElementById('etM').value||m;
  _removeById(mid);
  const r=await api(`/sessions/${id}`,{method:'PATCH',body:JSON.stringify({protocol:newP,mode:newM})});
  if(r.code===0){toast('已更新');sessions();}else toast(r.message||'失败','error');
}
function _addSelectedChannel(){
  const v=parseInt(document.getElementById('newChanSel').value);
  if(v&&!(window._selChans||[]).includes(v)){window._selChans.push(v);renderChanTags();updateEmailFields();}
  _removeById('chanSelModal');
}

function openAddSesModal(){
  const existing=document.getElementById('addSesModal');
  if(existing)existing.remove();
  const m=document.createElement('div');
  m.id='addSesModal';
  m.innerHTML=`<div class="overlay-998" data-on-click="_removeSelf" data-self>
    <div class="card w420" >
      <div class="card-hd">添加 Session <button data-on-click="_removeClosestId" class="icon-btn">×</button></div>
      <div class="card-bd p16">
        <div class="mb12">
          <div class="muted-11 mb4">PHPSESSID <span class="text-amber-600">★ 必填</span></div>
          <input class="input-search" id="addSesCookie" placeholder="粘贴 PHPSESSID 值">
        </div>
        <div class="flex wrap items-start gap8 mb12">
          <div>
            <div class="muted-10 mb3">用途</div>
            <div class="seg">
              <button class="seg-btn-active" id="modalBtnCollect" data-on-click="setPurpose" data-args='${_dataArgs('collect')}'>采集</button>
              <button class="tab-flat" id="modalBtnDiscover" data-on-click="setPurpose" data-args='${_dataArgs('discover')}'>发现</button>
            </div>
          </div>
          <div id="modalModeRow">
            <div class="muted-10 mb3">格式 <span class="fs9 fw400">标品稳定，虚拟化预留</span></div>
            <div class="seg">
              <button class="seg-btn-active" id="modalBtnStandard" data-on-click="setMode" data-args='${_dataArgs('standard')}'>标品</button>
              <button class="tab-flat-off" id="modalBtnVm" data-on-click="setMode" data-args='${_dataArgs('vm')}' title="虚拟化暂未支持，预留">虚拟化</button>
            </div>
          </div>
        </div>
        <div class="hint-success">
          <span class="fw600">采集</span>：获取产品升级包（规则包 / 系统包等）用于变更监控<br>
          <span class="fw600">发现</span>：探测绿盟产品线变化，自动识别新出现的产品和包类型，默认已内置当前全量产品及类型，可不添加。<span class="danger-600">注意：发现过程可能触发证书上传页面，所有升级包会被误判为虚拟化包；若需探测产品目录结构变化，请单独添加一个专用 session 用于发现。</span>
        </div>
        <button id="addSesConfirm" class="btn btn-soft-primary w-full" data-on-click="confirmAddSes">验证并添加</button>
        <div data-css="margin-top:10px;font-size:11px;color:var(--muted);text-align:center">获取方式：浏览器登录 update.nsfocus.com → F12 开发者工具 → Application → Cookies → 复制 PHPSESSID</div>
      </div>
    </div>
  </div>`;
  document.body.appendChild(m);
  window._sesPurpose='collect';window._sesMode='standard';
  syncModalButtons();
  document.getElementById('addSesCookie').focus();
}
function syncModalButtons(){
  const p=window._sesPurpose;
  const collectBtn=document.getElementById('modalBtnCollect');
  const discoverBtn=document.getElementById('modalBtnDiscover');
  if(collectBtn){collectBtn.style.background=p==='collect'?'#fff':'transparent';collectBtn.style.color=p==='collect'?'var(--text)':'var(--gray-muted)';collectBtn.style.boxShadow=p==='collect'?'0 1px 3px rgba(0,0,0,.15)':'none';}
  if(discoverBtn){discoverBtn.style.background=p==='discover'?'#fff':'transparent';discoverBtn.style.color=p==='discover'?'var(--text)':'var(--gray-muted)';discoverBtn.style.boxShadow=p==='discover'?'0 1px 3px rgba(0,0,0,.15)':'none';}
  const m=window._sesMode;
  const stdBtn=document.getElementById('modalBtnStandard');
  const vmBtn=document.getElementById('modalBtnVm');
  if(stdBtn){stdBtn.style.background=m==='standard'?'#fff':'transparent';stdBtn.style.color=m==='standard'?'var(--text)':'var(--gray-muted)';stdBtn.style.boxShadow=m==='standard'?'0 1px 3px rgba(0,0,0,.15)':'none';}
  if(vmBtn){vmBtn.style.background=m==='vm'?'#fff':'transparent';vmBtn.style.color=m==='vm'?'var(--text)':'var(--gray-disabled)';vmBtn.style.textDecoration=m==='vm'?'none':'line-through';}
  // 发现模式不需要选择标品/虚拟化，隐藏格式选择
  const modeRow=document.getElementById('modalModeRow');
  if(modeRow)modeRow.style.display=p==='discover'?'none':'block';
}
function setPurpose(p){
  window._sesPurpose=p;
  syncModalButtons();
}
function setMode(m){
  window._sesMode=m;
  syncModalButtons();
}
async function confirmAddSes(){
  const v=document.getElementById('addSesCookie').value.trim();
  if(!v)return toast('请输入 PHPSESSID','error');
  const p=window._sesPurpose||'collect',m=window._sesMode||'standard';
  const btn=document.getElementById('addSesConfirm');
  btn.disabled=true;btn.textContent='验证中...';
  const r=await api('/sessions',{method:'POST',body:JSON.stringify({cookie_value:v,purpose:p,collect_mode:m})});
  btn.disabled=false;btn.textContent='验证并添加';
  if(r.code===0){
    document.getElementById('addSesModal')?.remove();
    toast(r.data?.message||'添加成功','success');
    sessions();
  }else{
    toast(r.message,'error');
  }
}
async function validateSes(id){const r=await api('/sessions/'+id+'/validate',{method:'POST'});toast(r.code===0?r.message:r.message,r.code===0?'success':'error');sessions();}
async function delSes(id){await confirmAct('删除此Session？', async ()=>{const r=await api('/sessions/'+id,{method:'DELETE'});if(r.code===0){toast('已删除');sessions();}else toast(r.message,'error');}, '🗑️', '删除');}
async function editSesType(id,curP,curM){
  const p=curP==='discover'?'discover':'collect';
  const m=curM==='vm'?'vm':'standard';
  const mid='_sesTypeModal';
  const existing=document.getElementById(mid);
  if(existing)existing.remove();
  const overlay=document.createElement('div');
  overlay.className='confirm-modal active';
  overlay.id=mid;
  overlay.innerHTML=`<div class="confirm-box w400">
    <div class="confirm-title">修改 Session 类型</div>
    <div data-css="display:flex;gap:10px;align-items:center;justify-content:center;margin-bottom:18px">
      <select class="dd" id="etP" data-on-change="_etPurposeChanged" data-args='${_dataArgs('$el')}'>
        <option value="discover" ${p==='discover'?'selected':''}>发现</option>
        <option value="collect" ${p==='collect'?'selected':''}>采集</option>
      </select>
      <select class="dd" id="etM" ${p==='discover'?'disabled':''}>
        <option value="standard" ${m==='standard'?'selected':''}>标品</option>
        <option value="vm" ${m==='vm'?'selected':''}>虚拟化</option>
      </select>
    </div>
    <div class="confirm-btns">
      <button class="confirm-btn-cancel" data-on-click="_removeById" data-args='${_dataArgs(mid)}'>取消</button>
      <button class="confirm-btn-ok" data-on-click="_saveSessionEdit" data-args='${_dataArgs(id, mid, p, m)}'>确认</button>
    </div>
  </div>`;
  overlay.addEventListener('click',(e)=>{if(e.target===overlay)overlay.remove();});
  document.body.appendChild(overlay);
}


async function showHbHistory(sid){
  const r=await api('/sessions/'+sid+'/heartbeat');
  const history=r.data?.history||[];
  let rows=history.map(h=>{
    const stMap={'正常':'ok','过期':'err','污染':'warn','错误':'err','ok':'ok','expired':'err'};
    const labelMap={'正常':'正常','过期':'过期','污染':'污染','错误':'错误','ok':'正常','expired':'过期'};
    const stColor=stMap[h.status]||'warn';
    const label=labelMap[h.status]||h.status||'未知';
    return `<tr><td class="fs11">${fmtTZ(h.created_at,true)||h.created_at||''}</td><td><span class="badge badge-${stColor}">${label}</span></td><td>${h.latency_ms||0}ms</td><td class="fs11">${h.error_msg||h.error_message||'—'}</td></tr>`;
  }).join('');
  document.getElementById('hbModal').innerHTML=`
    <div class="overlay-998" data-on-click="_clearHtmlById" data-self data-args='${_dataArgs('hbModal')}'>
      <div class="card modal-card-600" >
        <div class="card-hd">💓 心跳历史 — Session #${sid} <button class="btn btn-xs btn-outline ml-auto" data-on-click="_clearHtmlById" data-args='${_dataArgs('hbModal')}'>关闭</button></div>
        <div class="card-bd pt0"><table><tr><th>时间</th><th>状态</th><th>延迟</th><th>说明</th></tr>${rows||'<tr><td colspan="4" class="empty">暂无心跳记录</td></tr>'}</table></div>
      </div>
    </div>`;
}

// ── Channels ──
let editingChId=0;let chList=[];let _origChConfig={};
function _maskWebhook(url){if(!url)return'';return url.replace(/key=[^&]+/,'key=********');}
async function channels(){
  const _seq=_navSeq;
  const r=await api('/channels');chList=r.data||[];
  const typeMap={'wecom':'企微','dingtalk':'钉钉','feishu':'飞书','apprise':'Apprise','email':'邮件'};
  const typeCNMap={'wecom':'企微','dingtalk':'钉钉','feishu':'飞书','email':'邮件','apprise':'Apprise'};
let rows=chList.map(c=>{let typeLabel=escHtml(typeMap[c.type]||c.type);let extra='';if(c.type==='email'&&c.config){extra=`<br><span class="muted-11">SMTP:${escHtml(c.config.smtp_host||'-')}:${escHtml(c.config.smtp_port||'-')} 发件人:${escHtml(c.config.smtp_user||'-')}</span>`;}
  // 5 个渠道(dingtalk/feishu/wecom/apprise/email)都支持「📋 日志」按钮 — 测试按钮触发后写入 /tmp/<type>_test_<id>.log,失败时常驻可见
  const testLogBtn=`<button class="btn btn-xs btn-outline" data-on-click="showTestLog" data-args='${_dataArgs(c.id, c.name)}'>📋 日志</button>`;
  return `<tr><td class="muted-12-w50">${c.id}</td><td>${escHtml(c.name)}${extra}</td><td>${chTypeChip(c.type)}</td><td><span class="badge badge-${c.is_active?'ok':'err'}">${c.is_active?'启用':'停用'}</span></td><td><button class="btn btn-xs btn-outline" data-on-click="openChModal" data-args='${_dataArgs(c.id)}'>编辑</button> <button class="btn btn-xs btn-success" data-on-click="testCh" data-args='${_dataArgs(c.id, c.type)}'>测试</button> ${testLogBtn} <button class="btn btn-xs btn-outline" data-on-click="delCh" data-args='${_dataArgs(c.id)}'>删除</button></td></tr>`;}).join('');
  if(navStale(_seq))return;
  document.getElementById('app').innerHTML=`<div class="card"><div class="card-hd">📡 通知渠道</div><div class="card-bd"><button class="btn btn-primary btn-sm" data-on-click="openChModal">+ 添加渠道</button><table class="mt16"><tr><th class="w50">ID</th><th>名称</th><th>类型</th><th>状态</th><th>操作</th></tr>${rows||'<tr><td colspan="5" class="empty">暂无渠道</td></tr>'}</table></div></div>`;}
function chTypeCh(){
  const t=document.getElementById('cht').value,d=document.getElementById('chcfg');
  d.innerHTML=t==='email'?`
    <div class="cfg-row">
      <div class="form-group">
        <label>邮箱模板</label>
        <select class="dd" id="chtmpl" data-on-change="onEmailTmplChange">
          ${Object.entries(EMAIL_PROVIDERS).map(([k,p])=>{
            const detail = p.host ? `${p.host}:${p.port}` : '';
            return `<option value="${k}">${escHtml(p.name)}${detail ? ' ('+escHtml(detail)+')' : ''}</option>`;
          }).join('')}
        </select>
      </div>
    </div>
    <p class="cfg-hint mt-neg6 text-amber-dark fw600">⚠️ 建议使用公司邮箱(@nsfocus.com),可避免客户收到邮件时误判为钓鱼邮件。163/QQ 等公共邮箱发出的通知,因发件域名与公司不匹配,容易被客户质疑真实性。</p>
    <div class="cfg-row">
      <div class="form-group"><label>SMTP</label><input id="sh"></div>
      <div class="form-group">
        <label>加密方式</label>
        <select class="dd" id="senc" data-on-change="onEmailEncChange">
          <option value="ssl">SSL (端口 465)</option>
          <option value="starttls">STARTTLS (端口 587)</option>
        </select>
      </div>
    </div>
    <div class="cfg-row">
      <div class="form-group">
        <label>端口 <span class="muted-10">根据加密方式自动填,通常无需修改</span></label>
        <input id="sp" value="465">
      </div>
    </div>
    <div class="cfg-row">
      <div class="form-group"><label>用户</label><input id="su" placeholder="user@example.com"></div>
      <div class="form-group"><label>密码</label><input id="sw" type="password" placeholder="留空则不修改"></div>
    </div>
    <p class="cfg-hint mt-neg6 text-amber-dark">⚠️ 用户名必须填写完整邮箱地址(含 @domain 后缀),SMTP 服务器会用它作为登录账号和发件人。</p>
    <p id="emailTmplHint" class="cfg-hint mt-neg6 text-gray-muted"></p>
    <div class="cfg-row">
      <div class="form-group"><label>发件名</label><input id="sf" value="绿盟升级通知"></div>
    </div>
    <p class="cfg-hint mt-neg6 text-gray-muted">收件人由订阅规则的"客户邮箱"决定,不在此处配置。手动推送时可指定收件人。</p>
    <div class="cfg-row">
      <div class="form-group"><label>每小时上限 <span class="muted-10">0=不限</span></label><input id="seh" type="number" value="0" min="0"></div>
      <div class="form-group"><label>每天上限 <span class="muted-10">0=不限</span></label><input id="sed" type="number" value="0" min="0"></div>
    </div>
  ` : t==='dingtalk' ? `
    <div class="form-group"><label>Webhook URL</label><input id="cw" placeholder="https://oapi.dingtalk.com/robot/send?access_token=xxx"></div>
    <div class="form-group"><label>Secret (加签密钥 - 可选)</label><input id="cs" placeholder="留空则不修改;后台启用加签时必填"></div>
    <p class="cfg-hint warn-strong">🔐 钉钉机器人后台的安全设置 <strong>3 选 1,推荐选「加签」</strong></p>
    <p class="cfg-hint cfg-note">
      钉钉的 Secret 是<strong>可选字段</strong>:
      <br>· 后台 <strong>未勾选「加签」</strong> → Secret 留空也可以推送(走裸 webhook)
      <br>· 后台 <strong>勾选了「加签」</strong> → Secret 必须填写,否则推送被服务端拒收(errcode 300024 / 310000)
    </p>
    <p class="cfg-hint cfg-note">
      操作步骤(选加签时):
      钉钉群 → 群设置 → 智能群助手 → 添加机器人 → 自定义 →
      <strong>安全设置勾选「加签」</strong>(勾上后会显示一段密钥串)→
      把这段密钥<strong>完整复制到上方 Secret 输入框</strong>。
    </p>
    <p class="cfg-hint cfg-note">
      💡 <strong>Secret 留空的 fallback 风险</strong>:若后台未启用加签,推送仍会成功,
      但 webhook URL 本身没加密保护 — 任何拿到 URL 的人都能冒充机器人推送。建议至少启用
      「加签」或「IP 白名单」中的一种。
    </p>
  ` : t==='feishu' ? `
    <div class="form-group"><label>Webhook URL</label><input id="cw" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxx"></div>
    <div class="form-group"><label>Secret (签名校验密钥 - 可选)</label><input id="cs" placeholder="留空则不修改;后台启用签名校验时必填"></div>
    <p class="cfg-hint warn-strong">🔐 飞书机器人后台的安全设置 <strong>3 选 1,推荐选「签名校验」</strong></p>
    <p class="cfg-hint cfg-note">
      飞书的 Secret 是<strong>可选字段</strong>:
      <br>· 后台 <strong>未勾选「签名校验」</strong> → Secret 留空也可以推送(走裸 webhook)
      <br>· 后台 <strong>勾选了「签名校验」</strong> → Secret 必须填写,否则推送被服务端拒收(errcode 230xxx)
    </p>
    <p class="cfg-hint cfg-note">
      操作步骤(选签名校验时):
      飞书群 → 群设置 → 群机器人 → 添加机器人 → 自定义机器人 →
      <strong>安全设置勾选「签名校验」</strong>(勾上后会显示一段密钥串)→
      把这段密钥<strong>完整复制到上方 Secret 输入框</strong>。
    </p>
    <p class="cfg-hint cfg-note">
      💡 <strong>Secret 留空的 fallback 风险</strong>:若后台未启用签名校验,推送仍会成功,
      但 webhook URL 本身没加密保护 — 任何拿到 URL 的人都能冒充机器人推送。建议至少启用
      「签名校验」或「IP 白名单」中的一种。
    </p>
  ` : `
    <div class="form-group"><label>Webhook URL</label><input id="cw" placeholder="留空则不修改"></div>
    <div class="form-group"><label>Secret (可选)</label><input id="cs" placeholder="留空则不修改"></div>
    <p class="cfg-hint cfg-note">企业微信机器人为可选,后台有密钥就填,没有就留空。当前<strong>不要配置 Secret</strong>(字段为预留位,本项目<strong>未实现</strong>企业微信机器人加签验签;企业微信机器人相比钉钉暂无强制安全设置,无需此密钥)。</p>
  `;
  // Reset template selection when channel type changes
  if(t==='email'){document.getElementById('chtmpl').value='custom';onEmailTmplChange();}
}

function onEmailTmplChange(){
  const k=document.getElementById('chtmpl')?.value;
  if(!k) return;
  const p=EMAIL_PROVIDERS[k];
  if(!p) return;
  // Autofill host/port, but only if those fields are empty or still match a
  // previous template's defaults — never clobber the user's manual edits.
  const sh=document.getElementById('sh');
  const sp=document.getElementById('sp');
  if(sh && (sh.value==='' || Object.values(EMAIL_PROVIDERS).some(x=>x.host===sh.value))){
    sh.value=p.host;
  }
  if(sp){
    sp.value=p.port;
  }
  // SSL is the default encryption for all built-in templates
  const senc=document.getElementById('senc');
  if(senc){ senc.value='ssl'; }
  // Update user input placeholder to suggest the right format per provider
  const su=document.getElementById('su');
  if(su && p.user_ph && !su.value){
    su.placeholder=p.user_ph;
  }
  const hint=document.getElementById('emailTmplHint');
  if(hint) hint.textContent=p.hint||'';
}

// Switch port when encryption mode changes
function onEmailEncChange(){
  const enc=document.getElementById('senc')?.value;
  const sp=document.getElementById('sp');
  if(!enc || !sp) return;
  if(enc==='ssl'){ sp.value=465; }
  else if(enc==='starttls'){ sp.value=587; }
}

// Detect when user manually edits the SMTP host and sync the template dropdown
function _onEmailHostChange(){
  const host=document.getElementById('sh')?.value||'';
  const matched=_matchEmailProvider(host);
  const sel=document.getElementById('chtmpl');
  if(sel && sel.value!==matched){
    sel.value=matched;
    const p=EMAIL_PROVIDERS[matched];
    const su=document.getElementById('su');
    if(su && p?.user_ph && !su.value){ su.placeholder=p.user_ph; }
    const hint=document.getElementById('emailTmplHint');
    if(hint) hint.textContent=p?.hint||'';
  }
}

function openChModal(id){
  editingChId=id||0;
  const title=id?'编辑渠道':'添加渠道';
  const typeOptionsHtml=['wecom','dingtalk','feishu','apprise','email'].map(t=>{
    return `<div class="ch-type-option picker-box" data-t="${t}" data-on-click="selectChType" data-args='${_dataArgs(t)}'>${chTypeChip(t)}</div>`;
  }).join('');
  const formHtml=`<div class="grid2"><div class="form-group"><label>名称</label><input id="chn"></div><div class="form-group"><label>类型</label><div class="pos-rel" id="chTypeDropdown"><div class="pointer min-h22" id="chTypeCurrent" data-on-click="toggleChTypeDropdown"></div><div id="chTypeMenu" data-css="display:none;position:absolute;top:100%;left:0;right:0;margin-top:4px;background:var(--card);border:1px solid var(--border);border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,0.12);z-index:10;padding:4px">${typeOptionsHtml}</div><input type="hidden" id="cht" value="wecom"></div></div></div><div id="chcfg"></div>`;
  const modal=document.createElement('div');
  modal.id='chModalWrap';
  modal.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.4);z-index:998;display:flex;align-items:center;justify-content:center';
  modal.onclick=function(e){if(e.target===this)this.remove();};
  modal.innerHTML=`<div class="card modal-card-scroll"><div class="card-hd">${title} <button data-on-click="_removeClosestId" class="icon-btn">×</button></div><div class="card-bd p16">${formHtml}<div class="modal-form-actions"><button class="btn btn-primary flex-1" data-on-click="saveCh">保存</button><button class="btn btn-outline" data-on-click="_removeById" data-args='${_dataArgs('chModalWrap')}'>取消</button></div></div></div>`;
  document.body.appendChild(modal);
  // 初始化"当前选中"的 chip 显示(无 id 时默认 wecom,有 id 时由 if 分支处理)
  if(!id){selectChType('wecom',false);}
  _origChConfig={};
  if(id){
    const c=chList.find(x=>x.id===id);
    if(!c)return;
    _origChConfig={...c.config};
    document.getElementById('chn').value=c.name||'';
    document.getElementById('cht').value=c.type||'wecom';
    selectChType(c.type||'wecom',false);
    chTypeCh();


    setTimeout(()=>{
      if(c.type==='email'){
        document.getElementById('sh').value=(c.config||{}).smtp_host||'';
        document.getElementById('sp').value=(c.config||{}).smtp_port||465;
        // smtp_encryption: new field. Fall back to port-based inference for
        // legacy channel configs (port 465 → ssl, else → starttls).
        let enc=(c.config||{}).smtp_encryption;
        if(!enc){
          enc=((c.config||{}).smtp_port===465) ? 'ssl' : 'starttls';
        }
        const senc=document.getElementById('senc');
        if(senc){ senc.value=enc; }
        document.getElementById('su').value=(c.config||{}).smtp_user||'';
        document.getElementById('sw').value='';
        document.getElementById('sf').value=(c.config||{}).from_name||'绿盟升级通知';
        document.getElementById('seh').value=c.email_hourly_limit||0;
        document.getElementById('sed').value=c.email_daily_limit||0;
        // Auto-match template from existing host so the dropdown reflects
        // what the user actually configured, even if the host differs.
        const tmpl=_matchEmailProvider((c.config||{}).smtp_host||'');
        const tmplSel=document.getElementById('chtmpl');
        if(tmplSel){ tmplSel.value=tmpl; }
        // Apply template's user placeholder (only when current user field is empty)
        const su=document.getElementById('su');
        if(su && !su.value && EMAIL_PROVIDERS[tmpl]?.user_ph){
          su.placeholder=EMAIL_PROVIDERS[tmpl].user_ph;
        }
        const hint=document.getElementById('emailTmplHint');
        if(hint){ hint.textContent=EMAIL_PROVIDERS[tmpl]?.hint||''; }
        // Bind host → template sync (in case user manually edits host later)
        const sh=document.getElementById('sh');
        if(sh && !sh.dataset.bound){ sh.dataset.bound='1'; sh.addEventListener('input',_onEmailHostChange); }
      }else{
        document.getElementById('cw').value=_maskWebhook((c.config||{}).webhook_url||'');
        document.getElementById('cs').value='';
      }
    },50);
  }else{
    chTypeCh();
    // Bind host → template sync for new channels too
    setTimeout(()=>{
      const sh=document.getElementById('sh');
      if(sh && !sh.dataset.bound){ sh.dataset.bound='1'; sh.addEventListener('input',_onEmailHostChange); }
    },50);
  }
}
async function saveCh(){
  const t=document.getElementById('cht').value,n=document.getElementById('chn').value.trim();
  if(!n){toast('请输入渠道名称','error');return}
  let c={},eh=0,ed=0;
  if(t==='email'){
    const pw=document.getElementById('sw')?.value||'';
    c={
      smtp_host:document.getElementById('sh')?.value||'',
      smtp_port:parseInt(document.getElementById('sp')?.value||'465'),
      smtp_encryption:document.getElementById('senc')?.value||'ssl',
      smtp_user:document.getElementById('su')?.value||'',
      smtp_password:pw||(_origChConfig.smtp_password||''),
      from_name:document.getElementById('sf')?.value||'绿盟升级通知',
    };
    if(c.smtp_encryption!=='ssl' && c.smtp_encryption!=='starttls'){
      toast('加密方式仅支持 SSL 或 STARTTLS','error');return;
    }
    eh=parseInt(document.getElementById('seh')?.value)||0;
    ed=parseInt(document.getElementById('sed')?.value)||0;
    // Recipients no longer configured at channel level — they're set on
    // subscription rules (customer_emails) or specified per manual push.
    // Strip any legacy to_list so the channel config reflects the new model.
    if(!editingChId){ c.to_list=[]; }
    if(!c.smtp_host||!c.smtp_user){toast('请填写 SMTP 服务器和用户','error');return}
    if(!c.smtp_user.includes('@')){toast('用户名必须是完整邮箱地址(含 @domain 后缀),如 user@163.com','error');return}
  }else{
    const wu=document.getElementById('cw')?.value||'';
    if(wu&&_origChConfig.webhook_url&&wu===_maskWebhook(_origChConfig.webhook_url))wu=_origChConfig.webhook_url;
    c={webhook_url:wu||(_origChConfig.webhook_url||''),secret:document.getElementById('cs')?.value||(_origChConfig.secret||'')}
  }
  let r;
  if(editingChId){r=await api('/channels/'+editingChId,{method:'PUT',body:JSON.stringify({name:n,config:c,email_hourly_limit:eh,email_daily_limit:ed})})}
  else{r=await api('/channels',{method:'POST',body:JSON.stringify({name:n,type:t,config:c,email_hourly_limit:eh,email_daily_limit:ed})})}
  if(r.code===0){toast(editingChId?'已更新':'已添加');editingChId=0;document.getElementById('chModalWrap')?.remove();channels();}
  else toast(r.message,'error')
}
async function testCh(id, type){
  const r=await api('/channels/'+id+'/test',{method:'POST'});
  if(r.data?.success){
    // type 优先用调用时传入的(channel list 行内调);否则回退到 modal #cht
    const map={'wecom':'企微','dingtalk':'钉钉','feishu':'飞书','email':'邮件','apprise':'Apprise'};
    const effectiveType=type||document.getElementById('cht')?.value||'';
    toast('✅ '+(map[effectiveType]||'渠道')+' 测试发送成功','success');
  } else {
    const err=r.data?.error||'失败';
    toast('❌ '+err,'error',8000);
    // For email channels, auto-open the log so the user can see why
    const ch=chList.find(c=>c.id===id);
    if(ch && ch.type==='email'){
      setTimeout(()=>showTestLog(id, ch.name), 300);
    }
  }
}

async function showTestLog(id, name){
  const existing=document.getElementById('testLogModal');if(existing)existing.remove();
  const r=await api('/channels/'+id+'/test-log');
  const content=r.code===0 ? r.data.content : `(${r.message||'尚未运行测试'})`;
  const logPath=r.code===0 ? r.data.path : `/tmp/email_test_${id}.log`;
  const m=document.createElement('div');
  m.id='testLogModal';
  m.className='confirm-modal active';
  m.innerHTML=`<div class="confirm-box confirm-box-lg">
    <div class="confirm-icon">📋</div>
    <div class="confirm-title">测试日志 - ${escHtml(name)}</div>
    <div class="muted-11 mb10">文件: ${escHtml(logPath)} | ${r.code===0 ? new Date(r.data.mtime*1000).toLocaleString('zh-CN') : ''}</div>
    <pre class="log-viewer">${escHtml(content)}</pre>
    <div class="confirm-btns mt14">
      <button class="confirm-btn-cancel" data-on-click="_removeById" data-args='${_dataArgs('testLogModal')}'>关闭</button>
    </div>
  </div>`;
  m.addEventListener('click', e=>{if(e.target===m)m.remove();});
  document.body.appendChild(m);
}
async function delCh(id){await confirmAct('删除此渠道？', async ()=>{const r=await api('/channels/'+id,{method:'DELETE'});if(r.code===0){toast('已删除');channels();}else toast(r.message,'error');}, '🗑️', '删除');}
// ── Customers ──
let custProdOpts='';
async function customers(){
  const _seq=_navSeq;
  const r=await api('/customers');const list=r.data||[];
  // Pre-load product options
  const oR=await api('/options/products').catch(()=>({data:['WEB应用防护系统(WAF)','网络入侵防护系统(IPS)','网络入侵检测系统(IDS)','远程安全评估系统(RSAS)','下一代防火墙(NF/SG)','绿盟综合威胁探针(UTS)']}));
  PROD=(oR.data||[]);if(!PROD.length)PROD=['WEB应用防护系统(WAF)','网络入侵防护系统(IPS)','网络入侵检测系统(IDS)','远程安全评估系统(RSAS)','下一代防火墙(NF/SG)','绿盟综合威胁探针(UTS)'];
  custProdOpts=`<option value="">全部产品</option>`+PROD.map(p=>`<option>${p}</option>`).join('');

  let rows=list.map(c=>`<tr><td class="muted-12-w50">${c.id}</td><td>${esc(c.name)}</td><td>${esc(c.company||'')}</td><td>${esc(c.email||'')}</td><td class="fs11">${esc((c.owned_products||[]).join(', '))}</td><td>${c.attachment_max_mb?'<span class="muted-11">附件≤'+c.attachment_max_mb+'MB</span>':''}</td><td><button class="btn btn-xs btn-outline" data-on-click="openCModal" data-args='${_dataArgs(c.id, c.name, c.company||'', c.email||'', c.phone||'', (c.owned_products||[]).join('|'), c.attachment_max_mb||0)}'>编辑</button> <button class="btn btn-danger btn-xs" data-on-click="delC" data-args='${_dataArgs(c.id)}'>删除</button></td></tr>`).join('');
  if(navStale(_seq))return;
  document.getElementById('app').innerHTML=`<div class="card"><div class="card-hd">👥 客户</div><div class="card-bd"><button class="btn btn-primary btn-sm" data-on-click="openCModal">+ 添加客户</button><table class="mt16"><tr><th class="w50">ID</th><th>名称</th><th>公司</th><th>邮箱</th><th>产品</th><th>附件</th><th>操作</th></tr>${rows||'<tr><td colspan="7" class="empty">暂无</td></tr>'}</table></div></div>`;}

function removeCProd(btn){
  btn.closest('div').remove();
}

function onCProdChange(el){
  if(el.value===''){
    // 选"全部产品"：已有具体产品时拦截
    const existingSpecific=[...document.querySelectorAll('#cprods .cp2')].some(s=>s.value&&s.value!==''&&s!==el);
    if(existingSpecific){toast('已有具体产品，不能选"全部产品"','warn');el.value='';return;}
    // 清空其他行
    const rows=document.querySelectorAll('#cprods > div');
    rows.forEach(r=>{const s=r.querySelector('.cp2');if(s&&s!==el)s.value='';});
  }else{
    // 选具体产品：清除其他行的"全部产品"
    const rows=document.querySelectorAll('#cprods > div');
    rows.forEach(r=>{const s=r.querySelector('.cp2');if(s&&s.value===''&&s!==el)s.value='';});
  }
}

function addCProd(p){
  // p 为空表示用户手动点"+添加产品"，此时校验互斥
  if(p===undefined){
    // 已有"全部产品"时，禁止再添加
    const existingEmpty=[...document.querySelectorAll('#cprods .cp2')].some(s=>s.value==='');
    if(existingEmpty){toast('已选"全部产品"，无需再添加','warn');return;}
  }
  let c=document.getElementById('cprods');
  let d=document.createElement('div');d.style.cssText='display:flex;gap:6px;margin-bottom:4px';
  d.innerHTML=`<select class="cp2 sel-sm dd flex1" data-on-change="onCProdChange" data-args='${_dataArgs('$el')}'>${custProdOpts}</select><button class="btn btn-danger btn-xs" data-on-click="removeCProd" data-args='${_dataArgs('$el')}'>×</button>`;
  c.appendChild(d);
  if(p){
    setTimeout(()=>{d.querySelector('.cp2').value=p;},80);
  }
}

function openCModal(id,n,co,em,ph,pr,attMb){
  let modal=document.createElement('div');
  modal.id='cModalWrap';
  modal.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.4);z-index:998;display:flex;align-items:center;justify-content:center';
  modal.onclick=function(e){if(e.target===this)this.remove();};
  modal.innerHTML=`<div class="card modal-card-scroll"><div class="card-hd">${id?'编辑客户':'添加客户'} <button data-on-click="_removeClosestId" class="icon-btn">×</button></div><div class="card-bd p16"><input type="hidden" id="cid"><div class="grid2"><div class="form-group"><label class="req">名称</label><input id="cnm" required></div><div class="form-group"><label class="req">公司</label><input id="ccm" required></div></div><div class="grid2"><div class="form-group"><label class="req">邮箱</label><input id="cem" required></div><div class="form-group"><label>电话</label><input id="cph"></div></div><div class="grid2"><div class="form-group"><label>附件大小限制 (MB) <span class="muted-11-400">0=使用全局设置</span></label><input id="camb" type="number" min="0" max="50" value="0"></div><div class="form-group"></div></div><div class="form-group"><label>持有产品</label><div id="cprods"></div><button class="btn btn-xs btn-outline mt8" data-on-click="addCProd">+ 添加产品</button></div><div class="modal-form-actions"><button class="btn btn-primary flex-1" data-on-click="saveC">保存</button><button class="btn btn-outline" data-on-click="_removeById" data-args='${_dataArgs('cModalWrap')}'>取消</button></div></div></div>`;
  document.body.appendChild(modal);
  document.getElementById('cid').value=id||'';
  document.getElementById('cnm').value=n||'';
  document.getElementById('ccm').value=co||'';
  document.getElementById('cem').value=em||'';
  document.getElementById('cph').value=ph||'';
  document.getElementById('camb').value=attMb||0;
  document.getElementById('cprods').innerHTML='';
  if(pr){
    const prods=(pr||'').split('|').filter(Boolean);
    prods.forEach(x=>{
      const[p]=x.split(':');
      addCProd(p||'');
    });
  }
  // 初始一行空选择提示
  setTimeout(()=>{if(!document.querySelector('#cprods .cp2')||![...document.querySelectorAll('#cprods .cp2')].some(s=>s.value))addCProd('');},80);
}

function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
// 内联 on* 属性里 JS 字符串实参专用的转义。esc/escHtml 都挡不住: HTML 解析器先做
// 属性整体实体解码、再把它当 JS 编译, 所以 &#39; 会被还原成裸 ' 照样截断字符串;
// 只有反斜杠能活到 JS 层。换行同理会截断 JS 字面量, 一并转 \n。
// 单次遍历替换, 避免实体里的 & 被二次编码成 &amp;quot;。data-* 之类的普通属性仍用
// escHtml(读回 getAttribute 时实体已解码, 正好还原原文)。
function escAttr(s){return String(s==null?'':s).replace(/[\\'&<>"\n\r]/g,c=>({'\\':'\\\\',"'":"\\'",'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\n':'\\n','\r':'\\r'}[c]));}
async function saveC(){
  const id=document.getElementById('cid').value;
  const nm=document.getElementById('cnm').value.trim(),cm=document.getElementById('ccm').value.trim(),em=document.getElementById('cem').value.trim(),ph=document.getElementById('cph').value.trim();
  if(!nm)return toast('名称必填','error');
  if(!cm)return toast('公司必填','error');
  if(!em)return toast('邮箱必填','error');
  if(!/^[^@]+@[^@]+$/.test(em))return toast('邮箱格式错误','error');
  let prods=[];document.querySelectorAll('#cprods > div').forEach(r=>{let p=r.querySelector('.cp2')?.value;if(p)prods.push(esc(p));});
  const amb=parseInt(document.getElementById('camb').value)||0;
  const d={name:esc(nm),company:esc(cm),email:esc(em),phone:esc(ph),owned_products:prods,attachment_max_mb:amb};
  const r=id?await api('/customers/'+id,{method:'PUT',body:JSON.stringify(d)}):await api('/customers',{method:'POST',body:JSON.stringify(d)});
  if(r.code===0){toast('已保存');document.getElementById('cModalWrap')?.remove();customers();}else toast(r.message,'error');
}
async function delC(id){await confirmAct('删除此客户？', async ()=>{const r=await api('/customers/'+id,{method:'DELETE'});if(r.code===0){toast('已删除');customers();}else toast(r.message,'error');}, '🗑️', '删除');}

// ── Subscriptions ──
const PKG=[['sys','系统'],['rule','规则'],['nti','威胁情报'],['av','病毒库'],['apprule','应用规则'],['url','URL分类'],['wcs','恶意站点'],['judge','研判规则'],['geo','地理库'],['interface','接口'],['special','特殊'],['other','其他'],['merge','合并'],['client','客户端'],['av_stream','流式病毒库']];
let PROD=[];
const TPL_META={
  full:{key:'A',name:'完整',bg:'var(--primary-light)',fg:'#1e40af'},
  strip:{key:'B',name:'智能裁剪',bg:'var(--warn-light)',fg:'#92400e'},
  brief:{key:'C',name:'极简',bg:'#fef9c3',fg:'#854d0e'},
  feishu_full:{key:'D',name:'飞书全量',bg:'#f3e8ff',fg:'#6b21a8'},
};
function tplBadgeHtml(tpl){
  const m=TPL_META[tpl]||TPL_META.full;
  return `<span class="d-inline-block nowrap"><span class="badge" data-css="background:${m.bg};color:${m.fg};font-size:10px;padding:1px 6px;border-radius:8px">${m.key}</span> ${m.name}</span>`;
}
function _ruleTemplateValueForChannel(r,cid){
  if(!r)return '';
  if(r.template&&typeof r.template==='object')return r.template[cid]||r.template[String(cid)]||'';
  if(typeof r.template==='string')return r.template;
  return '';
}
function _channelByIdFromList(channels,cid){
  return (channels||[]).find(c=>String(c.id)===String(cid))||{};
}
function templateForRuleChannel(r,chRow,channels){
  const cid=typeof chRow==='object'&&chRow?chRow.channel_id:chRow;
  const channel=_channelByIdFromList(channels,cid);
  const chType=channel.type||(chRow&&chRow.channel_type)||'';
  if(chType==='email')return {type:chType,template:'',html:'<span class="muted-11">邮件 HTML</span>'};
  const rcTpl=typeof chRow==='object'&&chRow?(chRow.template||''):'';
  let tpl=rcTpl||_ruleTemplateValueForChannel(r,cid)||CHAN_DEFAULT_TPL[chType]||'full';
  if(chType==='feishu'&&tpl!=='feishu_full')tpl='feishu_full';
  if(!TPL_META[tpl])tpl=CHAN_DEFAULT_TPL[chType]||'full';
  return {type:chType,template:tpl,html:tplBadgeHtml(tpl)};
}
function renderRuleTemplateSummary(r,channels){
  const rows=(r.channels||[]).filter(ch=>typeof ch==='object'?ch&&ch.channel_id:ch);
  if(!rows.length){
    const tpl=(typeof r.template==='string'&&r.template)?r.template:'';
    return tpl?tplBadgeHtml(TPL_META[tpl]?tpl:'full'):'<span class="text-muted">—</span>';
  }
  return rows.map(ch=>{
    const cid=typeof ch==='object'&&ch?ch.channel_id:ch;
    const channel=_channelByIdFromList(channels,cid);
    const info=templateForRuleChannel(r,ch,channels);
    const title=escHtml(`${channel.name||'渠道'} #${cid}`);
    return `<div class="my1" title="${title}">${info.html}</div>`;
  }).join('');
}
function getChannelById(cid){
  return (window._chData||[]).find(c=>String(c.id)===String(cid))||null;
}
function feishuDefaultTplForChannelId(cid){
  const ch=getChannelById(cid);
  return ch&&ch.type==='feishu'?'feishu_full':'';
}
function ensureFeishuDefaultTemplate(cid){
  const tpl=feishuDefaultTplForChannelId(cid);
  if(!tpl)return;
  window._chanCfg=window._chanCfg||{};
  window._chanCfg[cid]=window._chanCfg[cid]||{};
  // 飞书当前只有 D · 飞书全量一个有效选项；空值或旧 A/B/C 值都归一到 D。
  if(window._chanCfg[cid].template!==tpl)window._chanCfg[cid].template=tpl;
  window._chanTpls=window._chanTpls||{};
  if(window._chanTpls[cid]!==tpl)window._chanTpls[cid]=tpl;
}
// 2026-07-12 附件大小回填:客户档案有值 → input.value 预填,placeholder 写「客户档案 X MB」;
// 客户档案 0/未选 → input.value 留空,placeholder 写「全局默认 10 MB」。两条都「不写入」rule_channels,
// 后端 router.py 走 3 级 fallback 自动取值(避免把客户档案值固化进 rule 渠道行,后续改客户档案值会失同步)。
function attPlaceholder(){
  const c=window._currentCustomer;
  if(c && Number(c.attachment_max_mb)>0) return `客户档案 ${c.attachment_max_mb} MB(留空即用)`;
  return '默认 10 MB(留空即用)';
}
function attDefaultValue(){
  // 2026-07-12 B 模式:不预填,只走 placeholder。saveRule 写 0 → router per-customer fallback
  // 客户档案附件大小变更后所有规则自动跟,避免「预填后写死」丢联动
  return '';
}
// 收件人邮箱 placeholder:跟附件大小对称,客户档案 email 自动带出
function emailPlaceholder(){
  const c=window._currentCustomer;
  if(c && (c.email||'').trim()) return `${c.email.trim()}(客户档案,留空即用)`;
  return '收件人邮箱,逗号分隔';
}
// 收件人邮箱也用 B 模式,value 永远空 → router per-customer fallback 生效
function emailDefaultValue(){ return ''; }
async function subscriptions(){
  const _seq=_navSeq;
  // Build the minimal data needed for isChainStale() — sid→vendor paths only.
  // Calling dataPage() here would reset the right-side feed state and trigger
  // an extra /latest/snapshots fetch for no benefit. We only need _nameToSid
  // and package_type.paths, so fetch lightweight snapshots once and reuse.
  if (Object.keys(_dataAll).length === 0) {
    try {
      const r = await api('/latest/snapshots');
      _dataAll = r.data || {};
      _nameToSid = {};
      Object.entries(_dataAll).forEach(([sid, src]) => {
        const nm = src.display_name || src.name;
        if (nm) _nameToSid[nm] = String(sid);
      });
      _chainStaleCache = {};
    } catch (e) { /* fall through with empty _dataAll → all chains look stale, user can still edit */ }
  }
  const[rR,cR,oR,custR]=await Promise.all([api('/subscriptions'),api('/channels'),api('/options/products'),api('/customers')]);
  const rules=rR.data||[],channels=cR.data||[],list=custR.data||[];
  PROD=(oR.data||[]);if(!PROD.length)PROD=['WEB应用防护系统(WAF)','网络入侵防护系统(IPS)','网络入侵检测系统(IDS)','远程安全评估系统(RSAS)','下一代防火墙(NF/SG)','绿盟综合威胁探针(UTS)'];
  let pOpts=PROD.map(p=>`<option>${p}</option>`).join('');
  let chOpts=channels.map(c=>`<option value="${c.id}">${escHtml(c.name)} (${escHtml(c.type)})</option>`).join('');
  let custOpts=list.map(c=>`<option value="${c.id}">${escHtml(c.name)}</option>`).join('');

let rows=rules.map(r=>{let f=r.filter_conditions||{};let digestLabel={'weekly':'每周','monthly':'每月','quarterly':'每季'}[r.digest_mode]||'即时';let rb=r.notify_rollback!==0?'📢':'';let chains=f.chains||[];let isAll=!chains.length&&!(f.products||[]).length&&!(f.versions||[]).length&&!(f.package_types||[]).length;let condHtml=isAll?'<span class="text-primary fw600">📡 全部产品</span>':(chains.length>0?chains.map(c=>{let pathStr=c.chain.join(' › ');let isLeaf=c.match!=='subtree';let staleBadge=isChainStale(c.chain, c.match)?chainWarnBadge(c.chain, c.match):'';return `<span class="pill-tip" data-tip="${escHtml(pathStr)}\n匹配: ${isLeaf ? 'leaf (精确到该节点)' : 'subtree (含子节点)'}">${staleBadge}<span class="cond-pill tree-tag"><span class="tree-label">${escHtml(pathStr)}</span><span data-css="background:${isLeaf?'var(--primary-light)':'var(--warn-light)'};color:${isLeaf?'#1e40af':'#92400e'};font-size:10px;padding:0 4px;border-radius:6px;font-weight:600;white-space:nowrap">${isLeaf?'leaf':'sub'}</span></span></span>`;}).join(''):`<span class="text-muted">—</span>`);let cust=list.find(c=>c.id==r.customer_id);let custName=cust?escHtml(cust.name):'<span class="text-muted">—</span>';let custEmail=cust&&cust.email?'<span class="muted-11 d-block ellipsis" title="'+escHtml(cust.email)+'">'+escHtml(cust.email)+'</span>':'<span class="muted-11">—</span>';return `<tr><td class="muted-12-w50">${r.id}</td><td class="max-w140 ellipsis">${rb} ${escHtml(r.name)}</td><td class="max-w130 ellipsis fs11"><div class="ellipsis" title="${custName}">${custName}</div>${custEmail}</td><td class="max-w320 fs11 lh15">${(r.notification_groups&&r.notification_groups.length?r.notification_groups:[{type_label:"",recipients:[],customer_name:"",attachment_max_mb:0}]).map(g=>{const rc=(g.recipients&&g.recipients.length)?g.recipients.join(", "):"";const custTag=g.customer_name?` (${escHtml(g.customer_name)})`:"";const mb=g.attachment_max_mb>0?` <span class="text-muted">(${g.attachment_max_mb}MB)</span>`:"";const titleParts=[g.type_label, g.channel_name, ...(g.recipients||[]), g.customer_name].filter(Boolean).join(" | ");return `<div class="ellipsis" title="${escHtml(titleParts)}">${chTypeChip(g.channel_type||'').replace(/企业微信/g,'企微')}${rc?escHtml(rc):`<span class="text-muted">—</span>`}${custTag}${mb}</div>`;}).join("")||`<span class="text-muted">—</span>`}</td><td class="fs11 max-w200 overflow-visible">${condHtml}</td><td class="nowrap-11">${digestLabel}</td><td class="nowrap-11">${renderRuleTemplateSummary(r,channels)}</td><td class="nowrap-11">${escHtml(r.valid_until||'—')}</td><td><span class="badge badge-${r.enabled?'ok':'err'} pointer" data-on-click="toggleRule" data-args='${_dataArgs(r.id)}'>${r.enabled?'启用':'停用'}</span></td><td><button class="btn btn-xs btn-outline" data-on-click="openRModal" data-args='${_dataArgs(r.id)}'>编辑</button> <button class="btn btn-danger btn-xs" data-on-click="delRule" data-args='${_dataArgs(r.id)}'>删除</button></td></tr>`;}).join('');
  if(navStale(_seq))return;
  document.getElementById('app').innerHTML=`<div class="card"><div class="card-hd">📋 订阅规则</div><div class="card-bd"><button class="btn btn-primary btn-sm" data-on-click="openRModal">+ 创建规则</button><table class="mt16 table-fixed w-full"><tr><th class="w50">ID</th><th class="w150">名称</th><th class="w130">客户 / 收件人</th><th class="w320">通知对象</th><th class="w220">订阅条件</th><th class="w70">推送</th><th class="w90">通知模板</th><th class="w100">有效期</th><th class="w60">状态</th><th class="w100">操作</th></tr>${rows||'<tr><td colspan="10" class="empty">暂无规则</td></tr>'}</table></div></div>`;

  window._custs=list;window._chOpts=chOpts;window._chData=channels;}

function onCustChange(){
  let cid=document.getElementById('rcust').value;
  let row=document.getElementById('custInfoRow');
  let emailSpan=document.getElementById('custEmailDisplay');
  let attachRow=document.getElementById('custAttachRow');
  let attachSpan=document.getElementById('custAttachDisplay');
  if(!cid||!window._custs){
    if(row)row.style.display='none';
    if(attachRow)attachRow.style.display='none';
    window._currentCustomer=null;
    try{ renderChanTags(); }catch(e){}
    return;
  }
  let c=window._custs.find(x=>x.id==cid);
  window._currentCustomer=c||null;
  if(c&&emailSpan){
    emailSpan.textContent=c.email||'（未设置）';
    row.style.display='';
  }else if(row){
    row.style.display='none';
  }
  // Display the customer's attachment_max_mb (read-only — comes from customer).
  // 全局默认 10MB = 10485760 字节,见 src/notifiers/email.py ATTACHMENT_MAX_SIZE
  if(attachRow&&attachSpan){
    if(c && Number(c.attachment_max_mb)>0){
      attachSpan.textContent = c.attachment_max_mb + ' MB（来自客户档案）';
      attachSpan.style.color = '';
    }else{
      attachSpan.textContent = '未配置(发送时默认 10 MB)';
      attachSpan.style.color = 'var(--muted)';
    }
    attachRow.style.display='';
  }
  // 已渲染的 email 渠道 tag 同步展示客户档案附件大小(仅展示,saveRule 仍写 0 让 router 自动 fallback)
  try{ renderChanTags(); }catch(e){}
}

function toggleAllMode(){
  const on=document.getElementById('rall').checked;
  document.getElementById('rcondsWrap').style.display=on?'none':'';
  if(on){
    // 订阅全部模式：清空路径
    window._condChains=[];
    renderCondPills();
  }
}

// ── 订阅条件：链路径选择器 ─────────────────────────────────────────────────

// 临时状态：当前编辑会话选中的 chain 条目
let _condChains = [];  // [{chain:[...], match:'leaf'|'subtree'}]

// 加载产品数据（填充 _dataAll）
async function loadDataAll() {
  const [r] = await Promise.all([
    api('/latest/snapshots').catch(() => ({ data: {} })),
  ]);
  _dataAll = window._dataAll = r.data || {};
  Object.entries(_dataAll).forEach(([sid, src]) => {
    if (!src.package_type?.paths) return;
    const map = {};
    src.package_type.paths.forEach(p => {
      if (p.url && Array.isArray(p.chain) && p.chain.length > 0) {
        const norm = ('/' + p.url).replace(/\/+$/, '');
        map[norm] = p.chain;
      }
    });
    _srcUrlChainMap[sid] = map;
  });
}

// 链选择器弹窗（树形选择 + match 模式切换）
async function openChainSelectorModal(existingChains) {
  // existingChains: 编辑时传入已有条件，供回显选中态
  window._condChains = existingChains || [];
  const chains = window._condChains;

  // 确保 _dataAll 已加载（从 /latest/snapshots 拉取产品路径数据）
  if (Object.keys(_dataAll).length === 0) {
    await loadDataAll();
  }

  const MAP = { sys: '系统', rule: '规则', nti: '威胁情报', av: '病毒库', apprule: '应用规则', url: 'URL库', wcs: '恶意站点', judge: '研判', geo: '地理库', interface: '接口', special: '特殊', other: '其他', merge: '合并', client: '客户端', av_stream: '流式病毒库', test: '测试', engine: '引擎' };

  // 已选中 chain 的字符串集合（用于回显）
  const selectedSet = new Set(chains.map(c => JSON.stringify(c.chain)));

  let modal = document.createElement('div');
  modal.id = 'chainSelModal';
  modal.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.4);z-index:999;display:flex;align-items:center;justify-content:center';
  modal.onclick = function(e) { if (e.target === this) this.remove(); };

  // 右侧：已选路径列表
  const selectedHtml = chains.length === 0
    ? '<div id="chainSelList"><div class="muted-12 ta-center p20">未选择路径</div></div>'
    : `<div id="chainSelList">${renderChainSelList(chains)}</div>`;

  modal.innerHTML = `
  <div class="card modal-card-860-col" >
    <div class="card-hd">选择订阅路径 <span class="muted-12 fw400 ml8">点击路径节点添加到右侧列表</span>
      <button data-on-click="_removeClosest" data-args='${_dataArgs('#chainSelModal')}' class="icon-btn">×</button>
    </div>
    <div class="split-body">
      <div class="split-left" id="chainSelTree">
        <div class="muted-12 mb10">📂 点击节点添加到右侧「已选路径」，再次点击取消</div>
        <div id="chainTreeBody"><div class="muted-13 ta-center p40">📂 加载产品数据中...</div></div>
      </div>
      <div class="split-right" id="chainSelRight">
        <div class="subtitle">已选路径（${chains.length}）</div>
        ${selectedHtml}
      </div>
    </div>
    <div class="split-foot">
      <button class="btn btn-outline" data-on-click="_removeById" data-args='${_dataArgs('chainSelModal')}'>取消</button>
      <button class="btn btn-primary" data-on-click="confirmChainSelection">确定</button>
    </div>
  </div>`;

  document.body.appendChild(modal);

  // 渲染所有产品的树
  const treeBody = document.getElementById('chainTreeBody');
  let treeHtml = '';

  Object.entries(_dataAll).forEach(([sid, src]) => {
    if (!src.is_active) return;
    const pt = src.package_type || {};
    const paths = pt.paths || [];
    if (!paths.length) return;

    const sidNum = String(sid).replace(/\D/g, '') || sid;
    const emptyMap = {};  // 订阅选择器不需要 snapshotsMap
    const alias = src.display_name || src.name || '';
    const verTypeMap = src.ver_type_map || {};

    // 复用 buildDataTreeHtml，但传入自定义 onclick
    // 直接内联简化版树渲染（避免事件委托冲突）
    treeHtml += buildChainSelectTree(pt, emptyMap, MAP, verTypeMap, alias, sid, selectedSet);
  });

  if (!treeHtml) {
    treeHtml = '<div class="muted-12">无产品数据，请先完成产品发现与采集</div>';
  }
  treeBody.innerHTML = '<div class="font-system">' + treeHtml + '</div>';
}

// 链选择器：节点点击 toggle 选中（全局函数，供 inline onclick 调用）
// nodeEl: 被点击的 tree-node div 元素
function csTreeNodeClick(nodeEl) {
  const chain = JSON.parse(nodeEl.getAttribute('data-chain') || '[]');
  const url = nodeEl.getAttribute('data-url') || '';
  const isLeafNode = nodeEl.getAttribute('data-is-leaf') === '1';
  const match = isLeafNode ? 'leaf' : 'subtree';
  const chainKey = JSON.stringify(chain);

  // 冲突检测：检查新 chain 与已选项是否有父子重叠
  for (const existing of window._condChains) {
    const existKey = JSON.stringify(existing.chain);
    if (existKey === chainKey) continue; // 取消选择不触发冲突
    // 新 chain 是否包含已选 chain（选了父，但子已存在）
    if (chain.length > existing.chain.length &&
        JSON.stringify(chain.slice(0, existing.chain.length)) === existKey) {
      nodeEl.setAttribute('data-conflict', '1');
      setTimeout(() => nodeEl.removeAttribute('data-conflict'), 2000);
      return;
    }
    // 已选 chain 是否包含新 chain（选了子，但父已存在）
    if (existing.chain.length > chain.length &&
        JSON.stringify(existing.chain.slice(0, chain.length)) === chainKey) {
      nodeEl.setAttribute('data-conflict', '1');
      setTimeout(() => nodeEl.removeAttribute('data-conflict'), 2000);
      return;
    }
  }

  const idx = window._condChains.findIndex(c => JSON.stringify(c.chain) === chainKey);
  if (idx >= 0) {
    window._condChains.splice(idx, 1);
    nodeEl.setAttribute('data-sel', '0');
    nodeEl.style.background = '';
  } else {
    window._condChains.push({ chain, match });
    nodeEl.setAttribute('data-sel', '1');
    nodeEl.style.background = 'var(--primary-light)';
  }
  const rightDiv = document.getElementById('chainSelRight');
  if (rightDiv) {
    rightDiv.innerHTML = `<div class="subtitle">已选路径（${window._condChains.length}）</div>` + renderChainSelList(window._condChains);
  }
}

// 链选择器：节点折叠/展开
function csTreeToggleById(childDivId, arrowEl) {
  const child = document.getElementById(childDivId);
  if (!child) return;
  const isHidden = child.style.display === 'none' || !child.style.display;
  child.style.display = isHidden ? 'block' : 'none';
  if (arrowEl) arrowEl.innerHTML = isHidden ? '&#9660;' : '&#9658;';
}

// 渲染链选择树（复用 buildDataTreeHtml 的树结构逻辑，节点点击 toggle 选中）
function buildChainSelectTree(pt, snapshotsMap, MAP, verTypeMap, alias, sid, selectedSet) {
  const paths = pt.paths || [];
  if (paths.length === 0) return '';

  // 构建树结构（同 buildDataTreeHtml）
  const root = {};
  paths.forEach((p, pathIdx) => {
    const chain = p.chain || [];
    if (chain.length < 2) return;
    let node = root;
    let accChain = [];
    chain.forEach((name, idx) => {
      if (!node.__children) node.__children = {};
      if (!node.__children[name]) node.__children[name] = {
        __children: {}, __isPkg: false, __pathId: '', __chain: [], __pathIdx: -1
      };
      node = node.__children[name];
      node.__chain = accChain.concat([name]);
      node.__pathIdx = pathIdx;
      if (idx === chain.length - 1) {
        node.__isPkg = true;
        node.__pathId = p.url || '';
      }
      accChain = node.__chain;
    });
  });

  function renderNode(node, name, depth) {
    const indent = depth * 16;
    const children = node.__children ? Object.entries(node.__children) : [];
    const hasChildren = children.length > 0;
    const isPkg = node.__isPkg;
    const chainJson = JSON.stringify(node.__chain || []).replace(/"/g, '&quot;');
    const nodeUrl = node.__pathId || '';
    const chainKey = JSON.stringify(node.__chain);
    const isSelected = selectedSet.has(chainKey);

    const icon = isPkg ? '&#128196;' : (hasChildren ? '&#128193;' : '&#128196;');
    const label = isPkg ? name : (MAP[name] || name);
    const childCount = hasChildren ? ` <span class="disabled-11">(${children.length})</span>` : '';
    const selClass = isSelected ? ' data-css="background:var(--primary-light);border-radius:4px"' : '';
    const childDivId = hasChildren ? ('cst_' + sid + '_' + depth + '_' + name.replace(/\W/g, '_')) : null;
    const arrow = hasChildren
      ? `<span class="tree-toggle" data-on-click="csTreeToggleById" data-stop data-args='${_dataArgs(childDivId, '$el')}'>&#9658;</span>`
      : `<span class="tree-indent"></span>`;

    const isLeafNode = !hasChildren;
    const leafAttr = ' data-is-leaf="' + (isLeafNode ? '1' : '0') + '"';
    let html = `<div class="tree-node"${leafAttr} data-chain="${chainJson}" data-url="${nodeUrl}" data-on-click="csTreeNodeClick" data-stop data-args='${_dataArgs('$el')}' data-css="padding-left:${indent}px;display:flex;align-items:center;padding-top:4px;padding-bottom:4px;min-height:28px;cursor:pointer"${selClass}>`;
    html += arrow;
    html += `<span class="gray-muted-13-shrink">${icon} ${escHtml(label)}${childCount}</span>`;
    html += `</div>`;

    if (hasChildren) {
      html += `<div id="${childDivId}" class="tree-children" data-target="${childDivId}" data-css="display:none">`;
      children.forEach(([childName, childNode]) => { html += renderNode(childNode, childName, depth + 1); });
      html += `</div>`;
    }
    return html;
  }

  let html = '';
  const rootChildren = root.__children ? Object.entries(root.__children).sort(([a], [b]) => {
    const aV = /\d/.test(a); const bV = /\d/.test(b);
    if (aV && !bV) return -1; if (!aV && bV) return 1;
    return a.localeCompare(b, 'zh');
  }) : [];
  // 产品名作为可折叠分组头
  const prodLabel = `<div class="tree-sec-hd">${escHtml(alias || String(sid))}</div>`;
  // 始终从真实根节点开始渲染，保持完整的链层级深度
  html += prodLabel;
  rootChildren.forEach(([name, node]) => { html += renderNode(node, name, 0); });
  return html;
}

// 事件委托：链选择器树节点点击
document.addEventListener('click', function(e) {
  // 链选择器模态框内的树节点点击
  const csNode = e.target.closest('[data-action="csTreeNode"]');
  if (csNode) {
    const chainStr = csNode.getAttribute('data-chain');
    const isLeaf = csNode.getAttribute('data-isleaf') === '1';
    const chain = JSON.parse(chainStr || '[]');
    const isSelected = csNode.getAttribute('data-sel') === '1';
    // Toggle: 选中→取消，取消→选中
    const newSel = isSelected ? '0' : '1';
    csNode.setAttribute('data-sel', newSel);
    csNode.style.background = newSel === '1' ? 'var(--primary-light)' : '';
    // 更新选中集合（用 Set 存 chain JSON 字符串）
    if (!window._csSelectedSet) window._csSelectedSet = new Set();
    if (newSel === '1') {
      window._csSelectedSet.add(chainStr);
    } else {
      window._csSelectedSet.delete(chainStr);
    }
    e.stopPropagation();
    return;
  }
  // 链选择器模态框内的折叠箭头点击
  const csToggle = e.target.closest('[data-action="csTreeToggle"]');
  if (csToggle) {
    const targetId = csToggle.getAttribute('data-target');
    const children = document.getElementById(targetId);
    if (children) {
      const hidden = children.style.display === 'none' || children.style.display === '';
      children.style.display = hidden ? 'block' : 'none';
      csToggle.innerHTML = hidden ? '&#9660;' : '&#9658;';
    }
    return;
  }
});

// 确认链选择：将选中节点转为条件条目
function confirmChainSelection() {
  // 直接使用 csTreeNodeClick 中已填充的 _condChains
  window._condChains = window._condChains || [];
  renderCondPills();
  document.getElementById('chainSelModal').remove();
}

// 渲染已选路径列表（在链选择器右侧面板）
function renderChainSelList(chains) {
  if (!chains || chains.length === 0) {
    return '<div class="muted-12 ta-center p20">未选择路径</div>';
  }
  return chains.map((c, i) => {
    const pathStr = c.chain.join(' › ');
    const isLeaf = c.match !== 'subtree';
    const staleBadge = isChainStale(c.chain, c.match) ? chainWarnBadge(c.chain, c.match) : '';
    const matchHtml = isLeaf
      ? `<span class="chip-primary-xs">leaf（仅当前节点）</span>`
      : `<span class="chip-warn-xs">sub（包含子节点）</span>`;
    return `<div class="tree-row">
      <span class="flex-1 min-w0 text-gray-muted break-all">${escHtml(pathStr)}</span>
      ${matchHtml}${staleBadge}
      <button class="btn-x-danger" data-on-click="removeChainCond" data-args='${_dataArgs(i)}'>×</button>
    </div>`;
  }).join('');
}

// 移除单个路径条件
function removeChainCond(idx) {
  const removed = window._condChains.splice(idx, 1);
  if (removed.length) {
    const chain = removed[0].chain;
    const chainKey = JSON.stringify(chain);
    // C2-1: 同步清理 _csSelectedSet，避免树节点状态残留
    window._csSelectedSet.delete(chainKey);
    // 取消树的该节点高亮
    const safeChainKey = chainKey.replace(/"/g, '&quot;');
    const node = document.querySelector('.tree-node[data-chain="' + safeChainKey + '"]');
    if (node) { node.setAttribute('data-sel', '0'); node.style.background = ''; }
  }
  renderCondPills();
  // 同时更新右侧面板
  const rightDiv = document.getElementById('chainSelRight');
  if (rightDiv) {
    rightDiv.innerHTML = `<div class="subtitle">已选路径（${window._condChains.length}）</div>` + renderChainSelList(window._condChains);
  }
}

// 渲染订阅条件区的路径标签（在主表单的 rconds div 中）
function renderCondPills() {
  const container = document.getElementById('rconds');
  if (!container) return;
  const chains = window._condChains || [];
  const empty = document.getElementById('rcondsEmpty');
  if (chains.length === 0) {
    if (empty) empty.style.display = '';
    container.querySelectorAll('.cond-pill').forEach(el => el.remove());
    return;
  }
  if (empty) empty.style.display = 'none';
  // 保留 empty div，删掉所有 pill
  container.querySelectorAll('.cond-pill').forEach(el => el.remove());
  chains.forEach((c, i) => {
    const pathStr = c.chain.join(' › ');
    const isLeaf = c.match !== 'subtree';
    const matchLabel = isLeaf ? 'leaf' : 'sub';
    const staleBadge = isChainStale(c.chain, c.match) ? chainWarnBadge(c.chain, c.match) : '';
    const pill = document.createElement('div');
    pill.className = 'cond-pill';
    pill.style.cssText = 'display:inline-flex;align-items:center;gap:6px;padding:4px 8px;background:var(--primary-light);border:1px solid var(--primary-light);border-radius:20px;font-size:12px;margin:0 4px 4px 0';
    pill.innerHTML = `${staleBadge}<span class="tree-label-blue" title="${escHtml(pathStr)}">${escHtml(pathStr)}</span>
      <span data-css="background:${isLeaf ? 'var(--primary-light)' : 'var(--warn-light)'};color:${isLeaf ? '#1e40af' : '#92400e'};font-size:10px;padding:1px 5px;border-radius:8px;font-weight:600">${matchLabel}</span>
      <button class="btn-x-plain-14" data-on-click="removeCondPill" data-args='${_dataArgs(i)}'>×</button>`;
    container.appendChild(pill);
  });
}

// 移除主表单中的路径标签
function removeCondPill(idx) {
  window._condChains.splice(idx, 1);
  renderCondPills();
}

async function toggleRule(id){
  const r=await api('/subscriptions/'+id+'/toggle',{method:'POST'});
  if(r.code===0){toast(r.data?.enabled?'已启用':'已停用');subscriptions();}else toast(r.message,'error');
}

function addCond(p,v,t){
  let c=document.getElementById('rconds');
  let po=PROD.map(x=>`<option>${x}</option>`).join('');
  let d=document.createElement('div');d.style.cssText='display:flex;gap:6px;margin-bottom:5px;align-items:center';
  d.innerHTML=`<select class="cp sel-sm dd flex1" data-on-change="loadCV" data-args='${_dataArgs('$el')}'>${po}</select><select class="cv sel-sm dd flex1"><option value="">全部版本</option></select><select class="ct sel-sm dd flex1"><option value="">全部类型</option></select><button class="btn btn-danger btn-xs" data-on-click="_removeParent">×</button>`;
  c.appendChild(d);
  if(p){setTimeout(()=>{d.querySelector('.cp').value=p;loadCV(d.querySelector('.cp'),true);},80);setTimeout(()=>{if(v)d.querySelector('.cv').value=v;if(t)d.querySelector('.ct').value=t;},160);}
  else{setTimeout(()=>loadCV(d.querySelector('.cp')),50);}
}

function renderChanTags(){
  const chData=window._chData||[];
  const sel=window._selChans||[];
  const robotEl=document.getElementById('rchansRobotTags');
  const emailEl=document.getElementById('rchansEmailTags');
  if(!robotEl||!emailEl)return;

  // 按渠道类型分流:robot(企微/钉钉/飞书/apprise) → 上方,email → 下方
  const robotChans=sel.map(cid=>chData.find(x=>x.id===cid)).filter(c=>c&&c.type!=='email');
  const emailChans=sel.map(cid=>chData.find(x=>x.id===cid)).filter(c=>c&&c.type==='email');

  // 计数提示
  const rc=document.getElementById('robotCount');if(rc)rc.textContent=robotChans.length?`(已选 ${robotChans.length} 个)`:'';
  const ec=document.getElementById('emailCount');if(ec)ec.textContent=emailChans.length?`(已选 ${emailChans.length} 个)`:'';

  sel.forEach(cid=>ensureFeishuDefaultTemplate(cid));

  const tagHtml=(c)=>{
    const isEmail=c.type==='email';
    const bg=isEmail?'var(--warn-light)':'var(--primary-light)';
    const bd=isEmail?'#fbbf24':'var(--primary-light)';
    const fg=isEmail?'#92400e':'#1e40af';
    const opts=getTplOptsForChannel(c.type);
    // email 行: 4 列 (渠道名 | 收件人 | 附件大小 | ×),无模板列
    // feishu 行: 4 列 (渠道名 | 模板 | ×),select 只 1 个 D 选项 (feishu.py 走 _build_post,4 模板不生效,固定 D)
    // 其他机器人 (wecom/dingtalk/apprise) 行: 4 列 (渠道名 | 模板 | ×),正常 3 模板
    if (isEmail) {
      // 2026-07-11: per-channel 客户邮箱 + 附件大小 (rule_channels 表新字段)
      // email 渠道走 _format_html_body 全量 HTML,不走 A/B/C/D 模板 → 不显示「模板」列
      return `<div data-css="display:grid;grid-template-columns:1fr 2fr 1fr auto;align-items:center;gap:8px;padding:6px 10px;background:${bg};border:1px solid ${bd};border-radius:8px;font-size:12px;color:${fg}">
        <span class="ellipsis min-w0" title="${escHtml(c.name)} #${c.id}">${escHtml(c.name)} <span class="opacity-60">(${escHtml(c.type)})</span> <span class="opacity-50 font-mono">#${c.id}</span></span>
        <input data-cid="${c.id}" data-field="customer_emails" placeholder="${escHtml(emailPlaceholder())}" value="${emailDefaultValue()}" data-on-change="onChanFieldChange" data-args='${_dataArgs(c.id, 'customer_emails', '$value')}' data-css="font-size:12px;padding:4px 6px;border-radius:4px;border:1px solid ${bd};background:#fff;color:${fg}">
        <input data-cid="${c.id}" data-field="attachment_max_mb" type="number" min="0" placeholder="${attPlaceholder()}" value="${attDefaultValue()}" data-on-change="onChanFieldChange" data-args='${_dataArgs(c.id, 'attachment_max_mb', '$value')}' data-css="font-size:12px;padding:4px 6px;border-radius:4px;border:1px solid ${bd};background:#fff;color:${fg};width:100%">
        <button class="icon-plain" data-on-click="removeChanTag" data-args='${_dataArgs(c.id)}' title="移除渠道">×</button>
      </div>`;
    }
    return `<div data-css="display:grid;grid-template-columns:1fr auto auto auto;align-items:center;gap:10px;padding:6px 10px;background:${bg};border:1px solid ${bd};border-radius:8px;font-size:12px;color:${fg}"><span class="ellipsis min-w0" title="${escHtml(c.name)} #${c.id}">${escHtml(c.name)} <span class="opacity-60">(${escHtml(c.type)})</span> <span class="opacity-50 font-mono">#${c.id}</span></span><span data-css="font-size:11px;opacity:0.75">模板</span><select class="sel-sm dd" data-cid="${c.id}" data-field="template" data-on-change="onChanFieldChange" data-args='${_dataArgs(c.id, 'template', '$value')}' data-css="border-color:${bd};color:${fg};min-width:140px">${opts}</select><button class="icon-plain" data-on-click="removeChanTag" data-args='${_dataArgs(c.id)}' title="移除渠道">×</button></div>`;
  };

  robotEl.innerHTML=robotChans.length?robotChans.map(tagHtml).join(''):'<span class="muted-12">未添加机器人渠道</span>';
  emailEl.innerHTML=emailChans.length?emailChans.map(tagHtml).join(''):'<span class="muted-12">未添加邮箱渠道</span>';

  // 回显每个渠道当前选中的模板(渲染后单独设 selected,避免 inline template 内 escaped)
  const tpls=window._chanTpls||{};
  document.querySelectorAll('select[data-cid][data-field="template"]').forEach(sel=>{
    const cid=parseInt(sel.dataset.cid);
    const ch=getChannelById(cid);
    let cur=tpls[cid]||getChanField(cid,'template')||'';
    if(ch&&ch.type==='feishu'&&cur!=='feishu_full'){
      ensureFeishuDefaultTemplate(cid);
      cur='feishu_full';
    }
    sel.value=cur;
  });
}

// 2026-07-11: per-channel 字段读写 (rule_channels 新增 4 列)
// window._chanCfg = {<channel_id>: {template, customer_emails, attachment_max_mb, extras}}
function getChanField(cid, field){
  const cfg=window._chanCfg||{};
  return (cfg[cid]||{})[field]||'';
}
function onChanFieldChange(cid, field, val){
  window._chanCfg=window._chanCfg||{};
  window._chanCfg[cid]=window._chanCfg[cid]||{};
  window._chanCfg[cid][field]=val;
  // 兼容: 模板字段同步到 window._chanTpls (老的全局 template dict)
  if (field==='template') {
    window._chanTpls=window._chanTpls||{};
    if (val) window._chanTpls[cid]=val;
    else delete window._chanTpls[cid];
  }
}

// 每个渠道类型可选模板子集(2026-07-10 §32.4 渠道矩阵)
// email 渠道不需要模板选择(发邮件不走降级策略),但 UI 仍给"默认"占位,后端走 base 默认
// key:channel.type → value:list of template ids (空字符串表示"使用默认")
const CHAN_TPL_OPTIONS={
  dingtalk:[['full','A · 完整(默认)'],['strip','B · 智能裁剪'],['brief','C · 极简']],
  wecom:   [['full','A · 完整(默认)'],['strip','B · 智能裁剪'],['brief','C · 极简']],
  // 2026-07-11: 飞书渠道固定 D (feishu.py 走 _build_post,不读 _template,A/B/C 无效)
  // 只保留 D 选项,用户看到 select 但只能选 D (跟"无 select"行为一致)
  feishu:  [['feishu_full','D · 飞书全量']],
  apprise: [['full','A · 完整(默认)']],
  email:   [['full','A · 完整(默认)']],
};
// 2026-07-11: select option 悬停说明 (跟 §32.4 弹窗模板说明表格互补)
const TPL_OPTION_TITLE={
  'full':         'A · 完整: 标题+元信息+描述,超 4KB 自动切多段。钉钉/企微默认',
  'strip':        'B · 智能裁剪: 优先完整描述 → 超长剥英文段 → 仍超则只保留 attention 关键行 → 兜底只 head+链接',
  'brief':        'C · 极简: 只 head + 链接,描述完全省略,适合「通知到人即可,详情自己点」的内部群',
  'feishu_full':  'D · 飞书全量: 飞书专属单段展示,放宽到 30KB;飞书默认',
};
const CHAN_DEFAULT_TPL={dingtalk:'full',wecom:'full',feishu:'feishu_full',apprise:'full',email:'full'};
function getTplOptsForChannel(chType){
  const list=CHAN_TPL_OPTIONS[chType]||[['full','A · 完整(默认)']];
  // 飞书固定 D,只 1 个选项 → 不显示 "默认" 那行 (默认就是 D,无意义)
  // 其他渠道 3 选项 → 显示 "默认" 让用户快速回退
  const defaultOpt = chType === 'feishu' ? '' :
    '<option value="">— 默认('+({full:'A',strip:'B',brief:'C',feishu_full:'D'}[CHAN_DEFAULT_TPL[chType]||'full'])+') —</option>';
  return defaultOpt + list.map(([v,l])=>`<option value="${v}" title="${(TPL_OPTION_TITLE[v]||'').replace(/"/g,'&quot;')}">${l}</option>`).join('');
}
function onChanTplChange(cid,val){
  window._chanTpls=window._chanTpls||{};
  if(!val){delete window._chanTpls[cid];}
  else{window._chanTpls[cid]=val;}
}

function removeChanTag(cid){
  window._selChans=(window._selChans||[]).filter(x=>x!==cid);
  renderChanTags();
  updateEmailFields();
}

function updateEmailFields(){
  // 2026-07-11: 邮件相关字段已下放到每个 email 渠道 tag 行内 (per-channel input),
  // modal 底部的 emailFields 块已删除,本函数保留为 noop 兼容旧调用
  return;
}

function openChanSelModal(filterType){
  // 2026-07-12 filterType: null=全量, 'email'=只邮箱, 'non_email'=只机器人(wecom/dingtalk/feishu/apprise)
  // 由「+ 添加机器人渠道」/「+ 添加邮箱渠道」两个独立按钮触发,弹窗只显示对应类型
  const chData=window._chData||[];
  const sel=window._selChans||[];
  let avail=chData.filter(c=>!sel.includes(c.id));
  let headerLabel='选择通知渠道';
  let emptyLabel='已添加所有渠道';
  if(filterType==='email'){
    avail=avail.filter(c=>c.type==='email');
    headerLabel='选择邮箱渠道';
    emptyLabel='没有可添加的邮箱渠道(请先在「通知渠道」页创建)';
  }else if(filterType==='non_email'){
    avail=avail.filter(c=>c.type!=='email');
    headerLabel='选择机器人渠道';
    emptyLabel='没有可添加的机器人渠道(请先在「通知渠道」页创建)';
  }
  if(!avail.length){toast(emptyLabel,'warn');return;}
  const opts=avail.map(c=>`<option value="${c.id}">${escHtml(c.name)} (${escHtml(c.type)})</option>`).join('');
  const modal=document.createElement('div');
  modal.id='chanSelModal';
  modal.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.4);z-index:9999;display:flex;align-items:center;justify-content:center';
  modal.onclick=function(e){if(e.target===this)this.remove();};
  modal.innerHTML=`<div class="card w360" ><div class="card-hd">${headerLabel} <button data-on-click="_removeById" data-args='${_dataArgs('chanSelModal')}' class="icon-btn">×</button></div><div class="card-bd p16"><div class="form-group"><label>渠道</label><select class="dd w-full" id="newChanSel">${opts}</select></div><div class="flex gap8 mt12"><button class="btn btn-primary flex-1" data-on-click="_addSelectedChannel">添加</button><button class="btn btn-outline" data-on-click="_removeById" data-args='${_dataArgs('chanSelModal')}'>取消</button></div></div></div>`;
  document.body.appendChild(modal);
}

function addChan(cid){
  let c=document.getElementById('rchans');
  let d=document.createElement('div');d.style.cssText='display:flex;gap:6px;margin-bottom:5px;align-items:center';
  d.innerHTML=`<select class="chsel sel-sm dd flex1">${window._chOpts||''}</select><button class="btn btn-danger btn-xs" data-on-click="_removeParent">×</button>`;
  c.appendChild(d);
  if(cid){setTimeout(()=>{d.querySelector('.chsel').value=cid;},50);}
}

async function loadCV(s,loadType){
  let p=s.value,row=s.parentElement,vs=row.querySelector('.cv'),ts=row.querySelector('.ct');
  vs.innerHTML='<option value="">加载...</option>';
  if(loadType||ts.tagName==='SELECT')ts.innerHTML='<option value="">加载...</option>';
  let r=await api('/options/versions?product='+encodeURIComponent(p)).catch(()=>({data:[]}));
  vs.innerHTML='<option value="">全部版本</option>'+(r.data||[]).map(v=>`<option>${v}</option>`).join('');
  let r2=await api('/options/package-types?product='+encodeURIComponent(p)).catch(()=>({data:[]}));
  let types=r2.data||[];
  ts.innerHTML='<option value="">全部类型</option>'+types.map(x=>{let l=PKG.find(([v])=>v===x);return `<option value="${x}">${l?l[1]:x}</option>`;}).join('');
}

async function openRModal(id){
  // Ensure we have channels and customers loaded
  let chOpts=window._chOpts;
  let custOpts=window._custs?window._custs.map(c=>`<option value="${c.id}" title="#${c.id}">${escHtml(c.name)} <span class="text-gray-muted">#${c.id}</span>${c.email?' ('+escHtml(c.email)+')':''}</option>`).join(''):'';
  if(!chOpts){
    const[cR,custR]=await Promise.all([api('/channels'),api('/customers')]);
    const chans=cR.data||[];
    chOpts=chans.map(c=>`<option value="${c.id}">${escHtml(c.name)} (${escHtml(c.type)})</option>`).join('');
    window._chOpts=chOpts;
    window._chData=chans; // [{id,name,type},...]
    if(custR?.data)window._custs=custR.data;
  }

  let modal=document.createElement('div');
  modal.id='rModalWrap';
  modal.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.4);z-index:998;display:flex;align-items:center;justify-content:center';
  modal.onclick=function(e){if(e.target===this)this.remove();};
  modal.innerHTML=`<div class="card modal-card-760" ><div class="card-hd">${id?'编辑规则':'创建规则'} <button data-on-click="_removeClosestId" class="icon-btn">×</button></div><div class="card-bd p16"><input type="hidden" id="rid">
    <div class="form-section"><div class="form-section-title">基本信息</div>
    <div class="form-group"><label class="req">规则名称</label><input id="rnm" required></div>
    <div class="form-group"><label class="req">关联客户</label><select class="dd" id="rcust" data-on-change="onCustChange" required><option value="">请选择客户</option>${custOpts}</select></div>
    <div id="custInfoRow" class="form-group" data-css="margin-top:2px;font-size:12px;color:var(--muted);display:none">📧 客户邮箱：<span id="custEmailDisplay"></span></div>
    <div id="custAttachRow" class="form-group" data-css="margin-top:2px;font-size:12px;color:var(--muted);display:none">📎 客户档案附件大小：<span id="custAttachDisplay"></span></div>
    <div class="form-group"><label>有效期至</label><input class="min-w140" id="rvu" type="date" data-on-click="_showPicker" placeholder="不限制"></div>
    </div>

    <div class="form-section"><div class="form-section-title">订阅条件</div>
    <label class="check-row"><input class="m0" type="checkbox" id="rall" data-on-change="toggleAllMode"> 📡 订阅全部产品与类型，不设筛选条件</label>
    <div id="rcondsWrap"><div class="min-h32" id="rconds"><div class="muted-12 mb6" id="rcondsEmpty">未选择路径</div></div><button class="btn btn-xs btn-outline mt8" data-on-click="openChainSelectorModal">+ 添加路径</button></div>
    </div>

    <div class="form-section"><div class="form-section-title">推送配置</div>
    <div class="form-group"><label>推送模式 <span class="help-icon ml4" id="rmodeTip" title=""></span></label><select class="dd" id="rdm" data-on-change="onDigestModeChange"><option value="">即时推送</option><option value="weekly">每周汇总</option><option value="monthly">每月汇总</option><option value="quarterly">每季汇总</option></select></div>
    <div id="dcfg" class="hidden info-soft-box"></div>
    <div id="delayWrap"><div class="form-group"><label>延迟(d) <span class="help-icon" title="检测到新版本后，观察 N 天确认无问题再推送（仅即时推送模式）">ℹ️</span></label><input class="w80" id="rdl" type="number" value="0" min="0"></div></div>
    <div id="winSection" class="hidden mt6"><label class="chip-click"><input type="checkbox" id="rwin" data-on-change="onWinToggle"> ⏰ 启用推送时间窗口</label></div>
    <div id="winCfg" class="hidden warn-soft-box">
      <div class="mb8" id="winDaysRow"><label class="mr8">周几：</label><label class="mr10"><input type="checkbox" class="win-day" value="1">周一</label><label class="mr10"><input type="checkbox" class="win-day" value="2">周二</label><label class="mr10"><input type="checkbox" class="win-day" value="3">周三</label><label class="mr10"><input type="checkbox" class="win-day" value="4">周四</label><label class="mr10"><input type="checkbox" class="win-day" value="5">周五</label><label class="mr10"><input type="checkbox" class="win-day" value="6">周六</label><label><input type="checkbox" class="win-day" value="0">周日</label></div>
      <div><label class="mr8">时间段：</label><input class="w100" id="winStart" type="time" value="00:00"> ～ <input class="w100" id="winEnd" type="time" value="23:59"></div>
    </div>
    </div>

    <div class="form-section"><div class="form-section-title">通知渠道</div>
    <!-- 2026-07-11: 4 模板说明 (A=完整 / B=智能裁剪 / C=极简 / D=飞书全量) -->
    <!-- 仅钉钉/企微/飞书 走模板机制;邮箱是 HTML 全量单封,不走这 4 档 -->
    <div class="info-note">
      <div data-css="font-weight:600;margin-bottom:4px;color:var(--text)827">📝 模板说明 (仅机器人渠道生效,邮箱 HTML 全量单封无 4KB 切段)</div>
      <div class="grid-kv2">
        <div><b>A · 完整</b> (默认)</div><div>完整标题+元信息+描述,超 4KB 自动切多段;钉钉/企微默认</div>
        <div><b>B · 智能裁剪</b></div><div>优先完整描述 → 超长自动剥英文段 → 仍超则只保留 attention 关键行 → 兜底只 head+链接</div>
        <div><b>C · 极简</b></div><div>只 head + 链接,描述完全省略,适合「通知到人即可,详情自己点」的内部群</div>
        <div><b>D · 飞书全量</b></div><div>飞书专属单段展示,放宽到 30KB;飞书默认</div>
      </div>
    </div>
    <div class="mb8">
      <div class="sec-title">🤖 机器人渠道 <span class="muted-11-400" id="robotCount"></span><button class="btn btn-sm btn-primary ml-auto" data-on-click="openChanSelModal" data-args='${_dataArgs('non_email')}'>📥 添加机器人渠道</button></div>
      <div class="col gap6 min-h28 mb8" id="rchansRobotTags"></div>
    </div>
    <div class="mb8">
      <div class="sec-title">📧 邮箱渠道 <span class="muted-11-400" id="emailCount"></span><button class="btn btn-sm btn-primary ml-auto" data-on-click="openChanSelModal" data-args='${_dataArgs('email')}'>📥 添加邮箱渠道</button></div>
      <div class="col gap6 min-h28 mb8" id="rchansEmailTags"></div>
    </div>
    </div>

    <div class="form-section"><label class="chip-click nowrap"><input class="m0" type="checkbox" id="rnr"> 📢 包回退通知</label></div>
    <div class="drawer-foot"><button class="btn btn-soft-primary flex-1" data-on-click="saveRule">保存</button><button class="btn btn-outline" data-on-click="_removeById" data-args='${_dataArgs('rModalWrap')}'>取消</button></div></div></div>`;
  document.body.appendChild(modal);
  // 推送模式 tooltip
  const modeTips={'':'检测到版本变化立即推送','weekly':'每周汇总推送一次','monthly':'每月汇总推送一次','quarterly':'每季汇总推送一次'};
  document.getElementById('rdm').addEventListener('mouseenter',function(){const v=this.value;document.getElementById('rmodeTip').textContent='ℹ️';document.getElementById('rmodeTip').title=modeTips[v]||'';});

  if(id){
    // Load existing rule
    const rR=await api('/subscriptions');const r=(rR.data||[]).find(x=>x.id===id);if(!r)return;
    document.getElementById('rid').value=r.id;document.getElementById('rnm').value=r.name;
    // 2026-07-11: r.channels 现在是 rule_channels 完整行 list
    //   老规则: 是数字 list (后端可能还是数字,API 升级前)
    //   新规则: 是对象 list {channel_id, customer_emails, attachment_max_mb, template, extras}
    window._selChans=(r.channels||[]).map(ch=>typeof ch==='object'&&ch?ch.channel_id:ch);
    window._chanCfg={};
    window._chanTpls={};
    (r.channels||[]).forEach(ch=>{
      if(typeof ch!=='object'||!ch)return;
      const cid=ch.channel_id;
      window._chanCfg[cid]={
        template: ch.template||'',
        customer_emails: ch.customer_emails||'',
        attachment_max_mb: ch.attachment_max_mb||0,
        extras: ch.extras||'{}'
      };
      if(ch.template)window._chanTpls[cid]=ch.template;
    });
    // 兼容老数据 (subscription_rules.template 是 dict 或 str)
    const saved=(typeof r.template==='object'&&r.template)?r.template:null;
    if(saved){
      Object.keys(saved).forEach(k=>{const cid=parseInt(k);window._chanTpls[cid]=saved[k];window._chanCfg[cid]=window._chanCfg[cid]||{};window._chanCfg[cid].template=saved[k];});
    }else if(typeof r.template==='string'&&r.template){
      (r.channels||[]).forEach(ch=>{
        const cid=typeof ch==='object'&&ch?ch.channel_id:ch;
        if(cid&&!window._chanCfg[cid]?.template){
          window._chanTpls[cid]=r.template;
          window._chanCfg[cid]=window._chanCfg[cid]||{};
          window._chanCfg[cid].template=r.template;
        }
      });
    }
    renderChanTags();
    document.getElementById('rcust').value=r.customer_id||'';
    onCustChange();
    // NOTE: subscription_rules.attachment_max_mb is deprecated. The customer's
    // value drives everything (onCustChange() above writes it to rattDisplay)
    // — we deliberately do NOT read r.attachment_max_mb here, as it would
    // mislead users into thinking the field still works.
    let f=r.filter_conditions||{};
    const isAll=!(f.chains||[]).length;
    document.getElementById('rall').checked=isAll;
    document.getElementById('rcondsWrap').style.display=isAll?'none':'';
    if(!isAll){
      const chains = f.chains || [];
      window._condChains = chains.map(c => ({
        chain: c.chain || [],
        match: c.match || 'leaf'
      }));
      renderCondPills();
    }else{
      window._condChains=[];
      renderCondPills();
    }
    updateEmailFields();
    document.getElementById('rdl').value=r.delay_days||r.delay_hours||0;document.getElementById('rvu').value=r.valid_until||'';document.getElementById('rdm').value=r.digest_mode||'';
    document.getElementById('rnr').checked=r.notify_rollback===1;
    onDigestModeChange();
    const dc=r.digest_config||{};
    if(r.digest_mode==='weekly'){setTimeout(()=>{if(document.getElementById('dc-weekday'))document.getElementById('dc-weekday').value=dc.weekday??6;},100);}
    else if(r.digest_mode==='monthly'){setTimeout(()=>{if(document.getElementById('dc-month'))document.getElementById('dc-month').value=dc.month_day||'last';},100);}
    else if(r.digest_mode==='quarterly'){setTimeout(()=>{if(document.getElementById('dc-quarter'))document.getElementById('dc-quarter').value=dc.quarter_day||'last';},100);}
    // 恢复窗口配置（仅汇总模式显示）
    const wc=r.window_config||{};
    const hasWin=!!(wc.days?.length || wc.start || wc.end);
    document.getElementById('rwin').checked=hasWin;
    if(hasWin){setTimeout(()=>{if(wc.days){wc.days.forEach(d=>{const cb=document.querySelector('.win-day[value="'+d+'"]');if(cb)cb.checked=true;});}if(wc.start)document.getElementById('winStart').value=wc.start;if(wc.end)document.getElementById('winEnd').value=wc.end;},100);}
    onWinToggle();
  }else{
    // Reset form for new rule
    document.getElementById('rid').value='';document.getElementById('rnm').value='';document.getElementById('rcust').value='';document.getElementById('rdl').value='0';document.getElementById('rvu').value='';document.getElementById('rdm').value='';document.getElementById('rnr').checked=false;document.getElementById('rall').checked=false;document.getElementById('rcondsWrap').style.display='';document.getElementById('dcfg').classList.add('hidden');document.getElementById('dcfg').innerHTML='';document.getElementById('rwin').checked=false;document.getElementById('winCfg').classList.add('hidden');document.querySelectorAll('.win-day').forEach(c=>c.checked=false);document.getElementById('winStart').value='00:00';document.getElementById('winEnd').value='23:59';window._condChains=[];window._selChans=[];window._chanTpls={};window._chanCfg={};renderCondPills();renderChanTags();onDigestModeChange();onCustChange();
  }
}

function onDigestModeChange(){
  const m=document.getElementById('rdm').value;
  const d=document.getElementById('dcfg');
  const dw=document.getElementById('delayWrap');
  const wsec=document.getElementById('winSection');
  const wdr=document.getElementById('winDaysRow');
  if(!m){
    // 即时模式：只显示延迟，隐藏窗口策略
    d.classList.add('hidden');
    dw.classList.remove('hidden');
    wsec.classList.add('hidden');
    return;
  }
  // 汇总模式：隐藏延迟，显示窗口策略（只有时间段）
  dw.classList.add('hidden');
  wsec.classList.remove('hidden');
  if(wdr)wdr.style.display='none'; // 汇总模式：隐藏周几（只有时间段）
  d.classList.remove('hidden');
  if(m==='weekly'){
    d.innerHTML=`<label class="fw600-block">⏰ 每周推送日</label><select class="dd" id="dc-weekday"><option value="0">周一</option><option value="1">周二</option><option value="2">周三</option><option value="3">周四</option><option value="4">周五</option><option value="5">周六</option><option value="6" selected>周日</option></select>`;
  }else if(m==='monthly'){
    d.innerHTML=`<label class="fw600-block">⏰ 每月推送日</label><div class="flex gap8 items-center"><select class="dd" id="dc-month"><option value="last" selected>最后一天</option>${Array.from({length:28},(_,i)=>`<option value="${i+1}">${i+1}号</option>`).join('')}</select></div>`;
  }else if(m==='quarterly'){
    d.innerHTML=`<label class="fw600-block">⏰ 每季推送日</label><select class="dd" id="dc-quarter"><option value="last" selected>季度最后一天</option><option value="first">季度第一天</option></select>`;
  }
  d.innerHTML+='<div class="mt6 text-amber-700">⚠️ 汇总只发企微/钉钉/飞书；规则里绑定的邮箱渠道会被跳过</div>';
}
function onWinToggle(){
  const w=document.getElementById('winCfg');
  const en=document.getElementById('rwin')?.checked;
  if(en){w.classList.remove('hidden');}
  else{w.classList.add('hidden');}
}
function validateRuleConf(){
  // 窗口策略：汇总模式下勾选启用时，必须配置时间段
  const enWin=document.getElementById('rwin')?.checked;
  const dm=document.getElementById('rdm').value;
  if(enWin && dm){
    const winStart=document.getElementById('winStart').value;
    const winEnd=document.getElementById('winEnd').value;
    if(!winStart||!winEnd){return {valid:false,message:'窗口策略必须配置时间段',type:'block'};}
  }
  return {valid:true};
}async function saveRule(){
  let id=document.getElementById('rid').value;
  const vc=validateRuleConf();
  if(vc.type==='block'){toast(vc.message,'error');return;}
  let chs=window._selChans||[];
  let cid=parseInt(document.getElementById('rcust').value)||0;
  if(!cid)return toast('请选择关联客户','error');
  const isAll=document.getElementById('rall').checked;
  const fc=isAll?{}:{chains:window._condChains||[]};
  const nrr=document.getElementById('rnr')?.checked?1:0;
  // 2026-07-11: channels 改成对象 list,per-channel 字段走 rule_channels 新列
  //   兼容老数字 list:自动转成 {channel_id: ch}
  const chanCfg=window._chanCfg||{};
  const channels=chs.map(ch=>{
    const channelId=typeof ch==='object'&&ch?ch.channel_id:ch;
    const cfg=chanCfg[channelId]||{};
    return {
      channel_id: channelId,
      customer_id: cid,
      customer_emails: cfg.customer_emails||'',
      attachment_max_mb: parseInt(cfg.attachment_max_mb)||0,
      template: cfg.template||(window._chanTpls||{})[channelId]||feishuDefaultTplForChannelId(channelId),
      extras: cfg.extras||'{}'
    };
  });
  let d={name:document.getElementById('rnm').value,filter_conditions:fc,delay_days:parseInt(document.getElementById('rdl').value)||0,digest_mode:document.getElementById('rdm').value,valid_until:document.getElementById('rvu').value,customer_id:cid,channels:channels,notify_rollback:nrr,template:window._chanTpls||{}};
  const dm=d.digest_mode;
  if(dm){
    let dc={};
    if(dm==='weekly'){const el=document.getElementById('dc-weekday');dc.weekday=el?parseInt(el.value):6;}
    else if(dm==='monthly'){const el=document.getElementById('dc-month');dc.month_day=el?(el.value==='last'?'last':parseInt(el.value)):'last';}
    else if(dm==='quarterly'){const el=document.getElementById('dc-quarter');dc.quarter_day=el?el.value:'last';}
    d.digest_config=dc;
  }
  // 窗口配置：仅汇总模式可配置（勾选启用时）
  if(document.getElementById('rwin')?.checked && dm){
    const start=document.getElementById('winStart').value;
    const end=document.getElementById('winEnd').value;
    d.window_config={start,end}; // 汇总模式：只限制时间段
  }
  let r=id?await api('/subscriptions/'+id,{method:'PUT',body:JSON.stringify(d)}):await api('/subscriptions',{method:'POST',body:JSON.stringify(d)});
  if(r.code===0){toast('已保存');document.getElementById('rModalWrap')?.remove();subscriptions();}else toast(r.message,'error');
}async function delRule(id){await confirmAct('删除此订阅规则？', async ()=>{const r=await api('/subscriptions/'+id,{method:'DELETE'});if(r.code===0){toast('已删除');subscriptions();}else toast(r.message,'error');}, '🗑️', '删除');}
