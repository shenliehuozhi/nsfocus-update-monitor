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
// 用同主题色统一。样式见 app.css .float-log,显隐统一走 .hidden。
function _createFloatLog(logId, title, opts){
  // opts.bodyClass: 'class' (refresh-pkg 用) | undefined (默认 id=${logId}-body)
  const bodyClass = opts && opts.bodyClass;
  const bodyAttrs = bodyClass
    ? `class="${bodyClass}"`
    : `id="${logId}-body"`;
  const el = document.createElement('div');
  el.id = logId;
  el.className = 'float-log hidden';
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
    const lastCollect=s.status==='active'&&s.purpose==='collect'&&pool.active_collect===1;
    return `<tr><td>${s.id}</td><td><span class="pointer" data-on-click="editSesType" data-args='${_dataArgs(s.id,s.purpose,s.collect_mode,lastCollect)}' title="点击修改 Session 类型">${sesBadge(s.purpose,s.collect_mode)}</span></td><td><span class="badge badge-${s.status==='active'?'ok':s.status==='expired'?'err':'warn'}">${s.status==='active'?'活跃':s.status==='expired'?'失效':'未知'}</span></td><td>${hbInfo}</td><td>${hbTime}</td><td class="fs11">${s.heartbeat_count||0}次</td><td>${s.last_valid||'—'}</td><td>${s.expires_at||'—'}</td><td><button class="btn btn-xs btn-outline" data-on-click="validateSes" data-args='${_dataArgs(s.id)}'>验证</button> <button class="btn btn-soft-danger btn-xs" data-on-click="delSes" data-args='${_dataArgs(s.id,lastCollect)}'>删除</button></td></tr>`;
  }).join('');
  if(navStale(_seq))return;
  document.getElementById('app').innerHTML=`<div class="card"><div class="card-hd">🔑 Session <span class="muted-12 fw400">活跃 ${pool.active}/${pool.total}${pool.active_but_expired?` <span class="text-danger">⚠️${pool.active_but_expired}个已过期</span>`:''}</span><button class="btn btn-primary ml-auto px5-14 fs12" data-on-click="openAddSesModal">+ 添加 Session</button></div><div class="card-bd">
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
async function _saveSessionEdit(id,mid,p,m,lastCollect){
  const newP=document.getElementById('etP').value||p, newM=document.getElementById('etM').value||m;
  _removeById(mid);
  const warn=()=>confirmAct('把最后一个采集 Session 改为「发现」？', ()=>save(true), '⚠️', '确认修改', true,
    '<div class="confirm-desc">修改后将没有可用于采集的 Session，定时采集和手动采集都会失败，直到重新添加采集 Session。</div>');
  const save=async(force)=>{
    const r=await api(`/sessions/${id}`+(force?'?force=1':''),{method:'PATCH',body:JSON.stringify({purpose:newP,collect_mode:newM})});
    if(r.code===0){toast('已更新');sessions();return;}
    if(r.code===40901&&!force){warn();return;}
    toast(r.message||'失败','error');
  };
  if(lastCollect&&newP!=='collect')warn();
  else await save(false);
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
        <div class="mt10 muted-11 ta-center">获取方式：浏览器登录 update.nsfocus.com → F12 开发者工具 → Application → Cookies → 复制 PHPSESSID</div>
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
  if(collectBtn){collectBtn.classList.toggle('seg-btn-active',p==='collect');collectBtn.classList.toggle('tab-flat',p!=='collect');}
  if(discoverBtn){discoverBtn.classList.toggle('seg-btn-active',p==='discover');discoverBtn.classList.toggle('tab-flat',p!=='discover');}
  const m=window._sesMode;
  const stdBtn=document.getElementById('modalBtnStandard');
  const vmBtn=document.getElementById('modalBtnVm');
  if(stdBtn){stdBtn.classList.toggle('seg-btn-active',m==='standard');stdBtn.classList.toggle('tab-flat',m!=='standard');}
  if(vmBtn){vmBtn.classList.toggle('seg-btn-active',m==='vm');vmBtn.classList.toggle('tab-flat-off',m!=='vm');}
  // 发现模式不需要选择标品/虚拟化，隐藏格式选择
  const modeRow=document.getElementById('modalModeRow');
  if(modeRow)modeRow.classList.toggle('hidden', p==='discover');
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
async function delSes(id,last){
  const warn=!!last;
  const msg=warn?'删除最后一个可用的采集 Session？':'删除此Session？';
  const body=warn?'<div class="confirm-desc">删除后将没有可用于采集的 Session，定时采集和手动采集都会失败，直到重新添加。确定继续？</div>':'';
  await confirmAct(msg, async ()=>{
    const r=await api('/sessions/'+id+(warn?'?force=1':''),{method:'DELETE'});
    if(r.code===0){toast('已删除');sessions();return;}
    if(r.code===40901&&!warn){delSes(id,true);return;}
    toast(r.message||'删除失败','error');
  }, '🗑️', '删除', warn, body);
}
async function editSesType(id,curP,curM,lastCollect){
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
    <div class="flex gap10 items-center justify-center mb18">
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
      <button class="confirm-btn-ok" data-on-click="_saveSessionEdit" data-args='${_dataArgs(id, mid, p, m, !!lastCollect)}'>确认</button>
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
let rows=chList.map(c=>{let extra='';if(c.type==='email'&&c.config){extra=`<br><span class="muted-11">SMTP:${escHtml(c.config.smtp_host||'-')}:${escHtml(c.config.smtp_port||'-')} 发件人:${escHtml(c.config.smtp_user||'-')}</span>`;}
  // 5 个渠道(dingtalk/feishu/wecom/apprise/email)都支持「📋 日志」按钮 — 测试按钮触发后写入 /tmp/<type>_test_<id>.log,失败时常驻可见
  const testLogBtn=`<button class="btn btn-xs btn-outline" data-on-click="showTestLog" data-args='${_dataArgs(c.id, c.name)}'>📋 日志</button>`;
  return `<tr><td class="muted-12-w50">${c.id}</td><td>${escHtml(c.name)}${extra}</td><td>${chTypeChip(c.type)}</td><td><span class="badge badge-${c.is_active?'ok':'err'}">${c.is_active?'启用':'停用'}</span></td><td><button class="btn btn-xs btn-outline" data-on-click="openChModal" data-args='${_dataArgs(c.id)}'>编辑</button> <button class="btn btn-xs btn-success" data-on-click="testCh" data-args='${_dataArgs(c.id, c.type)}'>测试</button> ${testLogBtn} <button class="btn btn-xs btn-outline" data-on-click="delCh" data-args='${_dataArgs(c.id)}'>删除</button></td></tr>`;}).join('');
  if(navStale(_seq))return;
  document.getElementById('app').innerHTML=`<div class="card"><div class="card-hd">📡 通知渠道 <button class="btn btn-primary ml-auto px5-14 fs12" data-on-click="openChModal">+ 添加渠道</button></div><div class="card-bd"><table><tr><th class="w50">ID</th><th>名称</th><th>类型</th><th>状态</th><th>操作</th></tr>${rows||'<tr><td colspan="5" class="empty">暂无渠道</td></tr>'}</table></div></div>`;}
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
  const formHtml=`<div class="grid2"><div class="form-group"><label>名称</label><input id="chn"></div><div class="form-group"><label>类型</label><div class="pos-rel" id="chTypeDropdown"><div class="pointer min-h22" id="chTypeCurrent" data-on-click="toggleChTypeDropdown"></div><div id="chTypeMenu" class="ch-type-menu hidden">${typeOptionsHtml}</div><input type="hidden" id="cht" value="wecom"></div></div></div><div id="chcfg"></div>`;
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
  let c,eh=0,ed=0;
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
    let wu=document.getElementById('cw')?.value||'';
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
async function customers(){
  const _seq=_navSeq;
  const [r,p]=await Promise.all([
    api('/customers'),
    api('/customers/push-summary?days=30')
  ]);
  const list=r.data||[];
  _custPushStats=(p&&p.code===0&&p.data)?{}:null;
  if(_custPushStats)((p.data.items)||[]).forEach(it=>{_custPushStats[String(it.id)]=it;});

  let rows=list.map(c=>`<tr><td class="muted-12-w50">${c.id}</td><td>${esc(c.name)}</td><td>${esc(c.company||'')}</td><td>${esc(c.email||'')}</td><td class="fs11">${escHtml((c.owned_products||[]).map(_assetLabel).join(', '))}</td><td class="nowrap-11">${_custPushCell(c.id)}</td><td>${c.attachment_max_mb?'<span class="muted-11">附件≤'+c.attachment_max_mb+'MB</span>':''}</td><td><button class="btn btn-xs btn-outline" data-on-click="openSvcReport" data-args='${_dataArgs(c.id, c.name)}'>报告</button> <button class="btn btn-xs btn-outline" data-on-click="openCModal" data-args='${_dataArgs(c.id, c.name, c.company||'', c.email||'', c.phone||'', c.owned_products||[], c.attachment_max_mb||0)}'>编辑</button> <button class="btn btn-danger btn-xs" data-on-click="delC" data-args='${_dataArgs(c.id)}'>删除</button></td></tr>`).join('');
  if(navStale(_seq))return;
  document.getElementById('app').innerHTML=`<div class="card"><div class="card-hd">👥 客户 <button class="btn btn-primary ml-auto px5-14 fs12" data-on-click="openCModal">+ 添加客户</button></div><div class="card-bd"><table><tr><th class="w50">ID</th><th>名称</th><th>公司</th><th>邮箱</th><th>产品 / 版本</th><th>推送 / 成功率（近30天）</th><th>附件</th><th>操作</th></tr>${rows||'<tr><td colspan="8" class="empty">暂无</td></tr>'}</table></div></div>`;}

let _custPushStats=null;
function _custPushCell(cid){
  if(_custPushStats===null)return '<span class="disabled-11">—</span>';
  const p=_custPushStats[String(cid)];
  if(!p||!p.total)return '<span class="disabled-11">近30天无推送</span>';
  const total=p.total||0,success=p.success||0,failed=p.failed||0;
  const rate=Math.round(success/total*1000)/10;
  const cls=failed>0?(rate>=80?'text-warn':'text-danger'):'text-success-text';
  const tip=[`近30天推送 ${total} 次`,`成功 ${success} · 失败 ${failed}`,
    p.last_sent_at?`最近 ${fmtTZ(p.last_sent_at,true)}`:'',
    p.last_product?`最近产品 ${p.last_product}`:'',
    '点击查看推送历史'].filter(Boolean).join('\n');
  return `<span class="pointer ${cls}" title="${escHtml(tip)}" data-on-click="_gotoHistoryDays" data-args='${_dataArgs(30,cid)}'>${total} 次 · ${rate}%</span>`;
}

function _assetLabel(a){return a&&a.product?(a.version?`${a.product}@${a.version}`:a.product):'';}

let _svcCid=null,_svcMonth='';
function _cstMonth(){return new Date(Date.now()+8*3600*1000).toISOString().slice(0,7);}
function _svcShift(delta){
  if(!_svcMonth)return;
  const [y,m]=_svcMonth.split('-').map(Number);
  const d=new Date(Date.UTC(y,m-1+delta,1));
  _svcMonth=`${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}`;
  _svcLoad();
}
function openSvcReport(cid,name){
  _svcCid=cid;_svcMonth=_cstMonth();
  let modal=document.createElement('div');
  modal.id='svcReportWrap';
  modal.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.4);z-index:999;display:flex;align-items:center;justify-content:center';
  modal.onclick=function(e){if(e.target===this)this.remove();};
  modal.innerHTML=`<div class="card modal-card-scroll"><div class="card-hd">📄 服务报告${name?' · '+escHtml(name):''} <button data-on-click="_removeById" data-args='${_dataArgs('svcReportWrap')}' class="icon-btn">×</button></div><div class="card-bd p16" id="svcBody"><div class="muted-11">加载中…</div></div></div>`;
  document.body.appendChild(modal);
  _svcLoad();
}
async function _svcLoad(){
  const cid=_svcCid,month=_svcMonth;
  const r=await api(`/customers/${cid}/service-report?month=${month}`).catch(e=>({code:-1,message:String(e&&e.message||e)}));
  const box=document.getElementById('svcBody');
  if(!box||cid!==_svcCid||month!==_svcMonth)return;
  if(r.code!==0){box.innerHTML=`<div class="empty">加载失败：${escHtml(r.message||'未知错误')}</div>`;return;}
  const d=r.data||{},s=d.summary||{};
  const nextDisabled=month>=_cstMonth()?' disabled':'';
  const rate=s.success_rate==null?'—':s.success_rate+'%';
  const dlStatus={sent:['已发送','badge-ok'],failed:['失败','badge-err'],pending:['待发送','badge-warn']};
  const dlRows=(d.deliveries||[]).map(x=>{
    const [lb,cl]=dlStatus[x.status]||[x.status||'—','badge-grey'];
    return `<tr><td class="nowrap-11">${fmtTZ(x.sent_at,true)||'—'}</td><td>${escHtml(x.product_name)||'—'}</td><td class="fs11">${escHtml(x.version)||'—'}</td><td class="fs11">${escHtml(x.package_type)||'—'}</td><td><span class="badge ${cl}">${lb}</span></td></tr>`;
  }).join('');
  box.innerHTML=`<div class="items-center mb12"><button class="btn btn-xs btn-outline" data-on-click="_svcShift" data-args='${_dataArgs(-1)}'>‹ 上月</button><span class="fw600 ml8 mr8">${escHtml(d.month_label||month)}</span><button class="btn btn-xs btn-outline" data-on-click="_svcShift" data-args='${_dataArgs(1)}'${nextDisabled}>下月 ›</button><span class="muted-11 ml-auto">${escHtml(d.customer&&d.customer.company||'')}</span></div>
  <div class="mb12"><span class="badge badge-blue mr8">推送 ${s.total||0}</span><span class="badge badge-ok mr8">成功 ${s.success||0}</span><span class="badge badge-err mr8">失败 ${s.failed||0}</span><span class="badge badge-grey">成功率 ${rate}</span></div>
  <div class="items-center mb12"><span class="fw600">📨 本月推送明细</span>${d.truncated?`<span class="muted-11 ml-auto">共 ${s.total||0} 条，仅显示最近 ${(d.deliveries||[]).length} 条</span>`:''}</div>
  <table><tr><th>时间</th><th>产品</th><th>版本</th><th>类型</th><th>状态</th></tr>${dlRows||'<tr><td colspan="5" class="empty">本月无推送记录</td></tr>'}</table>`;
}

let _cvarRows=[];

// 客户资产：统一成 [{product,version}]，兼容旧 "产品" / "产品:版本" 字符串
// 同一产品可保留多个不同版本；仅当该产品有带版本条目时丢弃空版本条目
function _cvarNormalize(pr){
  const out=[],seen=new Set();
  (Array.isArray(pr)?pr:[pr]).forEach(a=>{
    if(!a)return;
    let p,v;
    if(typeof a==='string'){const [pp,vv]=a.split(':');p=pp||'';v=vv||'';}
    else{p=a.product||'';v=a.version||'';}
    p=String(p).trim();v=String(v).trim();
    if(!p)return;
    const key=p+'\u0000'+v;
    if(seen.has(key))return;
    seen.add(key);out.push({product:p,version:v});
  });
  const versioned=new Set(out.filter(a=>a.version).map(a=>a.product));
  return out.filter(a=>a.version||!versioned.has(a.product));
}

function renderCVars(){
  const box=document.getElementById('cvars');
  if(!box)return;
  if(!_cvarRows.length){box.innerHTML='<span class="muted-12">未添加产品路径</span>';return;}
  box.innerHTML=_cvarRows.map((a,i)=>{
    const label=a.version?`${a.product}@${a.version}`:a.product;
    const title=a.chain&&a.chain.length?a.chain.join(' › '):label;
    return `<span class="cvar-chip" title="${escHtml(title)}"><span class="cvar-label">${escHtml(label)}</span><button type="button" class="btn-x-plain-14" data-on-click="removeCVar" data-args='${_dataArgs(i)}'>×</button></span>`;
  }).join('');
}

function removeCVar(i){
  _cvarRows.splice(i,1);
  renderCVars();
}

// 与订阅规则同一套路径树：选中叶子即确定产品 + 当前版本
function openCVarPicker(){
  openChainSelectorModal([], {
    title:'选择客户产品',
    plain:true,
    onConfirm:chains=>{
      (chains||[]).forEach(c=>{
        const chain=(c&&c.chain)||[];
        const product=String(chain[0]||'').trim();
        if(!product)return;
        const version=cverVersionFromChain(chain);
        const row={product,version,chain:chain.slice()};
        const i=_cvarRows.findIndex(r=>r.product===product&&(r.version||'')===(version||''));
        if(i>=0)_cvarRows[i]=row;else _cvarRows.push(row);
      });
      renderCVars();
    }
  });
}

function openCModal(id,n,co,em,ph,pr,attMb){
  let modal=document.createElement('div');
  modal.id='cModalWrap';
  modal.style.cssText='position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.4);z-index:998;display:flex;align-items:center;justify-content:center';
  modal.onclick=function(e){if(e.target===this)this.remove();};
  modal.innerHTML=`<div class="card modal-card-760"><div class="card-hd">${id?'编辑客户':'添加客户'} <button data-on-click="_removeClosestId" class="icon-btn">×</button></div><div class="card-bd p16"><input type="hidden" id="cid"><div class="grid2"><div class="form-group"><label class="req">名称</label><input id="cnm" required></div><div class="form-group"><label class="req">公司</label><input id="ccm" required></div></div><div class="grid2"><div class="form-group"><label class="req">邮箱</label><input id="cem" required></div><div class="form-group"><label>电话</label><input id="cph"></div></div><div class="grid2"><div class="form-group"><label>附件大小限制 (MB) <span class="muted-11-400">0=使用全局设置</span></label><input id="camb" type="number" min="0" max="50" value="0"></div><div class="form-group"></div></div><div class="form-group"><label>持有产品 / 当前版本</label><div id="cvars" class="cvar-list"></div><button class="btn btn-xs btn-outline mt8" data-on-click="openCVarPicker">+ 添加产品</button></div><div class="modal-form-actions"><button class="btn btn-primary flex-1" data-on-click="saveC">保存</button><button class="btn btn-outline" data-on-click="_removeById" data-args='${_dataArgs('cModalWrap')}'>取消</button></div></div></div>`;
  document.body.appendChild(modal);
  document.getElementById('cid').value=id||'';
  document.getElementById('cnm').value=n||'';
  document.getElementById('ccm').value=co||'';
  document.getElementById('cem').value=em||'';
  document.getElementById('cph').value=ph||'';
  document.getElementById('camb').value=attMb||0;
  _cvarRows=_cvarNormalize(pr);
  renderCVars();
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
  const prods=_cvarRows.map(r=>({product:r.product,version:r.version||''}));
  const amb=parseInt(document.getElementById('camb').value)||0;
  const d={name:esc(nm),company:esc(cm),email:esc(em),phone:esc(ph),owned_products:prods,attachment_max_mb:amb};
  const r=id?await api('/customers/'+id,{method:'PUT',body:JSON.stringify(d)}):await api('/customers',{method:'POST',body:JSON.stringify(d)});
  if(r.code===0){toast('已保存');document.getElementById('cModalWrap')?.remove();customers();}else toast(r.message,'error');
}
async function delC(id){await confirmAct('删除此客户？', async ()=>{const r=await api('/customers/'+id,{method:'DELETE'});if(r.code===0){toast('已删除');customers();}else toast(r.message,'error');}, '🗑️', '删除');}

// ── 客户资产：从订阅路径树推导版本 ──
// 选中的路径节点标签就是当前版本；升级包/列表只是路径容器，不作为版本
// （WAF V6.0.9 / 网络入侵防护系统 5.6.11 / V2.0R00F01 …）
function cverVersionFromChain(chain){
  const segs=(chain||[]).slice(1).map(s=>String(s||'').trim()).filter(Boolean);
  const isContainer=s=>/升级包|升级|列表$|包$/.test(s);
  const re=/[Vv]?\d+(?:\.\d+)*(?:R\d+)?(?:F\d+)?/;
  for(let i=segs.length-1;i>=0;i--){if(!isContainer(segs[i])&&re.test(segs[i]))return segs[i];}
  for(let i=segs.length-1;i>=0;i--){if(!isContainer(segs[i]))return segs[i];}
  return '';
}

// 客户档案里的产品+版本 → 订阅路径链：新建订阅规则时默认沿用客户持有产品
async function customerAssetChains(c){
  const rows=(c&&Array.isArray(c.owned_products))?c.owned_products:[];
  if(!rows.length)return [];
  try{ if(!Object.keys(_dataAll||{}).length)await loadDataAll(); }catch(e){ return []; }
  const all=[];
  Object.entries(_dataAll||{}).forEach(([sid,src])=>{
    if(!src||!src.is_active)return;
    (src.package_type&&src.package_type.paths||[]).forEach(p=>{
      const chain=p.chain||[];
      if(chain.length<2)return;
      all.push(chain);
      if(chain[0]&&!_nameToSid[chain[0]])_nameToSid[chain[0]]=String(sid);
    });
  });
  const out=[];
  rows.forEach(a=>{
    let product='',version='';
    if(typeof a==='string'){
      const [p,v]=a.split(':');
      product=(p||'').trim();version=(v||'').trim();
    }else if(a){
      product=String(a.product||'').trim();version=String(a.version||'').trim();
    }
    if(!product)return;
    let paths=all.filter(chain=>chain[0]===product);
    if(!paths.length){
      // 旧客户档案可能存的是数据源名称而非路径根节点
      Object.entries(_dataAll||{}).forEach(([sid,src])=>{
        if(!src||!src.is_active)return;
        const nm=src.display_name||src.name||String(sid);
        if(nm!==product&&src.name!==product)return;
        (src.package_type&&src.package_type.paths||[]).forEach(p=>{
          const chain=p.chain||[];
          if(chain.length>=2)paths.push(chain);
        });
      });
    }
    if(!paths.length)return;
    let full=paths[0],chosen=full;
    if(version){
      chosen=null;
      for(const chain of paths){
        for(let i=chain.length-1;i>=1;i--){
          if(String(chain[i]).trim()===version){full=chain;chosen=chain.slice(0,i+1);break;}
        }
        if(chosen)break;
      }
      if(!chosen){
        for(const chain of paths){
          if(cverVersionFromChain(chain)===version){full=chain;chosen=chain.slice(0,-1);break;}
        }
      }
      if(!chosen){
        for(const chain of paths){
          for(let i=chain.length-1;i>=1;i--){
            if(String(chain[i]).includes(version)||version.includes(String(chain[i]))){full=chain;chosen=chain.slice(0,i+1);break;}
          }
          if(chosen)break;
        }
      }
      if(!chosen){full=paths[0];chosen=full;}
    }
    out.push({chain:chosen,match:chosen.length<full.length?'subtree':'leaf'});
  });
  // 同一产品多个版本各保留一条；相同路径只生成一次
  const seen=new Set();
  return out.filter(o=>{
    const key=JSON.stringify(o.chain);
    if(seen.has(key))return false;
    seen.add(key);
    return true;
  });
}

// 仅在新建规则且用户尚未手动改过路径时自动填入；编辑规则保留已保存条件
async function applyCustomerPaths(c){
  if(!c||!window._custAutoFill)return;
  const rall=document.getElementById('rall');
  if(rall&&rall.checked)return;                       // 用户显式选择「全部产品」
  const chains=window._condChains||[];
  if(chains.length&&!window._condChainsAuto)return;    // 已手动选择，保留现状
  const cid=String(c.id);
  const derived=await customerAssetChains(c);
  const sel=document.getElementById('rcust');
  if(!sel||String(sel.value)!==cid)return;             // 期间切换客户或关闭弹窗
  if(!derived.length){
    if(window._condChainsAuto){
      window._condChains=[];
      window._condChainsAuto=false;
      renderCondPills();
    }
    return;
  }
  window._condChains=derived;
  window._condChainsAuto=true;
  const wrap=document.getElementById('rcondsWrap');
  if(wrap)wrap.classList.remove('hidden');
  renderCondPills();
}

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
  return `<span class="d-inline-block nowrap"><span class="badge tpl-badge tpl-badge-${m.key.toLowerCase()}">${m.key}</span> ${m.name}</span>`;
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
  if(c && (c.email||'').trim()) return `${c.email.trim()}（客户档案邮箱，留空即用）`;
  return '输入邮箱后点 + 添加';
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
  const[rR,cR,oR,custR,pushR,schR]=await Promise.all([api('/subscriptions'),api('/channels'),api('/options/products'),api('/customers'),api('/subscriptions/push-summary?days=30'),api('/settings/scheduler').catch(()=>({}))]);
  const rules=rR.data||[],channels=cR.data||[],list=custR.data||[];
  // 首采护栏：完成一轮采集前建规则，首轮的全部存量包会被当作新增批量推送
  const sch=schR.data||{};
  _subsCollectRunning=!!sch.is_running;
  _subsNeedFirstCollect=sch.collection_baseline_ready===false;
  _rulePushStats=(pushR&&pushR.code===0&&pushR.data)?{}:null;
  if(_rulePushStats)((pushR.data.items)||[]).forEach(it=>{_rulePushStats[String(it.id)]=it;});
  PROD=(oR.data||[]);if(!PROD.length)PROD=['WEB应用防护系统(WAF)','网络入侵防护系统(IPS)','网络入侵检测系统(IDS)','远程安全评估系统(RSAS)','下一代防火墙(NF/SG)','绿盟综合威胁探针(UTS)'];
  let chOpts=channels.map(c=>`<option value="${c.id}">${escHtml(c.name)} (${escHtml(c.type)})</option>`).join('');

let rows=rules.map(r=>{let f=r.filter_conditions||{};let digestLabel={'weekly':'每周','monthly':'每月','quarterly':'每季'}[r.digest_mode]||'即时';let rb=r.notify_rollback!==0?'📢':'';let chains=f.chains||[];let isAll=!chains.length&&!(f.products||[]).length&&!(f.versions||[]).length&&!(f.package_types||[]).length;let condHtml=isAll?'<span class="text-primary fw600">📡 全部产品</span>':(chains.length>0?chains.map(c=>{let pathStr=c.chain.join(' › ');let isLeaf=c.match!=='subtree';let staleBadge=isChainStale(c.chain, c.match)?chainWarnBadge(c.chain, c.match):'';return `<span class="pill-tip" data-tip="${escHtml(pathStr)}\n匹配: ${isLeaf ? 'leaf (精确到该节点)' : 'subtree (含子节点)'}">${staleBadge}<span class="cond-pill tree-tag"><span class="tree-label">${escHtml(pathStr)}</span><span class="match-badge-sm ${isLeaf?'match-leaf':'match-sub'}">${isLeaf?'leaf':'sub'}</span></span></span>`;}).join(''):`<span class="text-muted">—</span>`);let cust=list.find(c=>c.id==r.customer_id);let custName=cust?escHtml(cust.name):'<span class="text-muted">—</span>';let custEmail=cust&&cust.email?'<span class="muted-11 d-block ellipsis" title="'+escHtml(cust.email)+'">'+escHtml(cust.email)+'</span>':'<span class="muted-11">—</span>';return `<tr><td class="muted-12-w50">${r.id}</td><td class="max-w140 ellipsis">${rb} ${escHtml(r.name)}</td><td class="max-w130 ellipsis fs11"><div class="ellipsis" title="${custName}">${custName}</div>${custEmail}</td><td class="max-w320 fs11 lh15">${(r.notification_groups&&r.notification_groups.length?r.notification_groups:[{type_label:"",recipients:[],customer_name:"",attachment_max_mb:0}]).map(g=>{const rc=(g.recipients&&g.recipients.length)?g.recipients.join(", "):"";const custTag=g.customer_name?` (${escHtml(g.customer_name)})`:"";const mb=g.attachment_max_mb>0?` <span class="text-muted">(${g.attachment_max_mb}MB)</span>`:"";const titleParts=[g.type_label, g.channel_name, ...(g.recipients||[]), g.customer_name].filter(Boolean).join(" | ");return `<div class="ellipsis" title="${escHtml(titleParts)}">${chTypeChip(g.channel_type||'').replace(/企业微信/g,'企微')}${rc?escHtml(rc):`<span class="text-muted">—</span>`}${custTag}${mb}</div>`;}).join("")||`<span class="text-muted">—</span>`}</td><td class="fs11 max-w200 overflow-visible">${condHtml}</td><td class="nowrap-11">${_rulePushCell(r.id)}</td><td class="nowrap-11">${digestLabel}</td><td class="nowrap-11">${renderRuleTemplateSummary(r,channels)}</td><td class="nowrap-11">${escHtml(r.valid_until||'—')}</td><td><span class="badge badge-${r.enabled?'ok':'err'} pointer" data-on-click="toggleRule" data-args='${_dataArgs(r.id)}'>${r.enabled?'启用':'停用'}</span></td><td><button class="btn btn-xs btn-outline" data-on-click="openRModal" data-args='${_dataArgs(r.id)}'>编辑</button> <button class="btn btn-danger btn-xs" data-on-click="delRule" data-args='${_dataArgs(r.id)}'>删除</button></td></tr>`;}).join('');
  if(navStale(_seq))return;
  const firstCollectBanner=_subsNeedFirstCollect
    ? `<div class="warn-banner"><span class="fs16">⚠️</span><div class="flex-1">${_subsCollectRunning
        ? '首次采集正在进行中：请等本轮采集完成后再创建订阅规则，否则会把全部存量包当作新增批量推送。'
        : '尚未完成首次采集：现在创建订阅规则，会把首轮采集到的全部存量包当作新增一次性推送。请先执行一次全量采集，等待完成后再创建规则。'}</div><button class="btn btn-xs btn-outline shrink0" data-on-click="showPage" data-args='${_dataArgs('data')}'>前往采集数据</button></div>`
    : '';
  document.getElementById('app').innerHTML=`${firstCollectBanner}<div class="card"><div class="card-hd">📋 订阅规则 <button class="btn btn-primary ml-auto px5-14 fs12" data-on-click="openRModal">+ 创建规则</button></div><div class="card-bd"><table class="table-fixed w-full"><tr><th class="w50">ID</th><th class="w150">名称</th><th class="w130">客户 / 收件人</th><th class="w320">通知对象</th><th class="w220">订阅条件</th><th class="w140">命中 / 最近（近30天）</th><th class="w70">推送</th><th class="w90">通知模板</th><th class="w100">有效期</th><th class="w60">状态</th><th class="w100">操作</th></tr>${rows||'<tr><td colspan="11" class="empty">暂无规则</td></tr>'}</table></div></div>`;

  window._custs=list;window._chOpts=chOpts;window._chData=channels;}

// 订阅规则命中统计：长期未命中通常意味着条件失效 / 客户已不再采购该产品
let _rulePushStats=null;
// 首采护栏状态（由 subscriptions() 从 /settings/scheduler 刷新）
let _subsNeedFirstCollect=false;
let _subsCollectRunning=false;
function _rulePushAge(t){
  const ms=_parseServerMs(t);
  if(ms===null)return '';
  const diff=Math.max(0,Date.now()-ms);
  const d=Math.floor(diff/86400000);
  if(d>0)return d+'天前';
  const h=Math.floor(diff/3600000);
  return h>0?h+'小时前':'刚刚';
}
function _rulePushCell(rid){
  if(_rulePushStats===null)return '<span class="disabled-11">—</span>';
  const p=_rulePushStats[String(rid)];
  if(!p||!p.packages)return '<span class="text-warn" title="近30天没有匹配到推送包，可检查订阅条件、客户产品或规则有效期">未命中</span>';
  const tip=[`近30天匹配 ${p.packages} 个包 / 投递 ${p.deliveries} 次`,
    `成功 ${p.sent||0} · 失败 ${p.failed||0}`,
    p.last_at?`最近命中 ${fmtTZ(p.last_at,true)}`:'',
    '点击查看推送历史'].filter(Boolean).join('\n');
  return `<span class="pointer" title="${escHtml(tip)}" data-on-click="_gotoRuleHistoryDays" data-args='${_dataArgs(30,rid)}'>${p.packages} 包 · ${_rulePushAge(p.last_at)}</span>`;
}
function _gotoRuleHistoryDays(days,rid){
  location.hash=`history?days=${days}&rule=${rid}`;
  setTimeout(()=>showPage('history'),0);
}

function onCustChange(){
  let cid=document.getElementById('rcust').value;
  let row=document.getElementById('custInfoRow');
  let emailSpan=document.getElementById('custEmailDisplay');
  let attachRow=document.getElementById('custAttachRow');
  let attachSpan=document.getElementById('custAttachDisplay');
  if(!cid||!window._custs){
    if(row)row.classList.add('hidden');
    if(attachRow)attachRow.classList.add('hidden');
    window._currentCustomer=null;
    if(window._condChainsAuto){
      window._condChains=[];
      window._condChainsAuto=false;
      renderCondPills();
    }
    try{ renderNotifyTargets(); }catch(e){}
    return;
  }
  let c=window._custs.find(x=>x.id==cid);
  window._currentCustomer=c||null;
  if(c&&emailSpan){
    emailSpan.textContent=c.email||'（未设置）';
    row.classList.remove('hidden');
  }else if(row){
    row.classList.add('hidden');
  }
  // Display the customer's attachment_max_mb (read-only — comes from customer).
  // 全局默认 10MB = 10485760 字节,见 src/notifiers/email.py ATTACHMENT_MAX_SIZE
  if(attachRow&&attachSpan){
    if(c && Number(c.attachment_max_mb)>0){
      attachSpan.textContent = c.attachment_max_mb + ' MB（来自客户档案）';
      attachSpan.classList.remove('text-muted');
    }else{
      attachSpan.textContent = '未配置(发送时默认 10 MB)';
      attachSpan.classList.add('text-muted');
    }
    attachRow.classList.remove('hidden');
  }
  // 已渲染的 email 渠道 tag 同步展示客户档案附件大小(仅展示,saveRule 仍写 0 让 router 自动 fallback)
  try{ renderNotifyTargets(); }catch(e){}
  if(c)applyCustomerPaths(c);
}

function toggleAllMode(){
  const on=document.getElementById('rall').checked;
  document.getElementById('rcondsWrap').classList.toggle('hidden', on);
  if(on){
    // 订阅全部模式：清空路径
    window._condChains=[];
    window._condChainsAuto=true;
    renderCondPills();
  }else if(!(window._condChains||[]).length&&window._currentCustomer){
    // 取消「全部产品」时回到客户档案默认产品
    applyCustomerPaths(window._currentCustomer);
  }
  updateRuleScopeWarning();
}

function _ruleScopeStats(){
  const isAll=!!document.getElementById('rall')?.checked;
  const products=new Set();
  (window._condChains||[]).forEach(c=>{
    const p=(c.chain||[])[0];
    if(p)products.add(p);
  });
  return {isAll,count:products.size};
}

function updateRuleScopeWarning(){
  const box=document.getElementById('rscopeWarn');
  if(!box)return;
  const {isAll,count}=_ruleScopeStats();
  let msg='';
  if(isAll){
    msg='⚠️ 已选择「全部产品」，该规则会匹配所有产品的新包，可能存在大量推送。保存时需要二次确认。';
  }else if(count>5){
    msg=`⚠️ 当前订阅覆盖 ${count} 个产品，可能存在大量推送。保存时需要二次确认。`;
  }
  box.textContent=msg;
  box.classList.toggle('hidden',!msg);
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

// 订阅「添加路径」：带上当前已选路径，取消时不影响已保存条件
function openRuleChainSelector(){
  const chains=(window._condChains||[]).map(c=>({chain:(c.chain||[]).slice(),match:c.match||'leaf'}));
  openChainSelectorModal(chains);
}

// 链选择器弹窗（树形选择 + match 模式切换）
// opts: { title, plain, onConfirm }
//   plain=true 为客户端产品路径选择：不显示 leaf/sub 与失效提示，确定后回调 onConfirm
async function openChainSelectorModal(existingChains, opts) {
  const opt = opts || {};
  window._chainSelConfirm = opt.onConfirm || null;
  window._chainSelPlain = !!opt.plain;
  // existingChains: 编辑时传入已有条件，供回显选中态
  // 弹窗内用独立的 _chainSelChains 工作副本，取消时不影响订阅条件
  window._chainSelChains = existingChains || [];
  const chains = window._chainSelChains;

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
    : `<div id="chainSelList">${renderChainSelList(chains, window._chainSelPlain)}</div>`;

  modal.innerHTML = `
  <div class="card modal-card-860-col" >
    <div class="card-hd">${escHtml(opt.title || '选择订阅路径')} <span class="muted-12 fw400 ml8">${window._chainSelPlain ? '可多选；选中路径即确定产品与当前版本，同一产品可添加多个版本' : '点击路径节点添加到右侧列表'}</span>
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
  const isLeafNode = nodeEl.getAttribute('data-is-leaf') === '1';
  const match = isLeafNode ? 'leaf' : 'subtree';
  const chainKey = JSON.stringify(chain);

  // 冲突检测：检查新 chain 与已选项是否有父子重叠
  for (const existing of window._chainSelChains) {
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

  const idx = window._chainSelChains.findIndex(c => JSON.stringify(c.chain) === chainKey);
  if (idx >= 0) {
    window._chainSelChains.splice(idx, 1);
    nodeEl.setAttribute('data-sel', '0');
    nodeEl.classList.remove('tree-node-selected');
  } else {
    window._chainSelChains.push({ chain, match });
    nodeEl.setAttribute('data-sel', '1');
    nodeEl.classList.add('tree-node-selected');
  }
  const rightDiv = document.getElementById('chainSelRight');
  if (rightDiv) {
    rightDiv.innerHTML = `<div class="subtitle">已选路径（${window._chainSelChains.length}）</div>` + renderChainSelList(window._chainSelChains, window._chainSelPlain);
  }
}

// 链选择器：节点折叠/展开
function csTreeToggleById(childDivId, arrowEl) {
  const child = document.getElementById(childDivId);
  if (!child) return;
  const isHidden = child.classList.contains('hidden');
  child.classList.toggle('hidden', !isHidden);
  if (arrowEl) arrowEl.innerHTML = isHidden ? '&#9660;' : '&#9658;';
}

// 渲染链选择树（复用 buildDataTreeHtml 的树结构逻辑，节点点击 toggle 选中）
// clickFn：节点点击动作，默认订阅的 csTreeNodeClick
function buildChainSelectTree(pt, snapshotsMap, MAP, verTypeMap, alias, sid, selectedSet, clickFn) {
  const nodeClick = clickFn || 'csTreeNodeClick';
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

  // 递增序号保证容器 id 唯一；用节点名转义会因中文压缩成下划线而撞 id
  let childUid = 0;
  function renderNode(node, name, depth) {
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
    const selClass = isSelected ? ' tree-node-selected' : '';
    const childDivId = hasChildren ? ('cst_' + sid + '_' + (childUid++)) : null;
    const arrow = hasChildren
      ? `<span class="tree-toggle" data-on-click="csTreeToggleById" data-stop data-args='${_dataArgs(childDivId, '$el')}'>&#9658;</span>`
      : `<span class="tree-indent"></span>`;

    const isLeafNode = !hasChildren;
    const leafAttr = ' data-is-leaf="' + (isLeafNode ? '1' : '0') + '"';
    let html = `<div class="tree-node tree-row tree-row-gap4 tree-row-click ti-${depth}"${leafAttr} data-chain="${chainJson}" data-url="${nodeUrl}" data-on-click="${nodeClick}" data-stop data-args='${_dataArgs('$el')}'${selClass}>`;
    html += arrow;
    html += `<span class="tree-name" title="${escHtml(label)}">${icon} ${escHtml(label)}</span>`;
    html += `<span class="tree-meta">${childCount}</span>`;
    html += `</div>`;

    if (hasChildren) {
      html += `<div id="${childDivId}" class="tree-children hidden" data-target="${childDivId}">`;
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
  html += `<div class="tree-sec-hd">${escHtml(alias || String(sid))}</div>`;
  // 始终从真实根节点开始渲染，保持完整的链层级深度
  rootChildren.forEach(([name, node]) => { html += renderNode(node, name, 0); });
  return html;
}

// 事件委托：链选择器树节点点击
document.addEventListener('click', function(e) {
  // 链选择器模态框内的树节点点击
  const csNode = e.target.closest('[data-action="csTreeNode"]');
  if (csNode) {
    const isSelected = csNode.getAttribute('data-sel') === '1';
    // Toggle: 选中→取消，取消→选中
    const newSel = isSelected ? '0' : '1';
    csNode.setAttribute('data-sel', newSel);
    csNode.classList.toggle('tree-node-selected', newSel === '1');
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
      const hidden = children.classList.contains('hidden');
      children.classList.toggle('hidden', !hidden);
      csToggle.innerHTML = hidden ? '&#9660;' : '&#9658;';
    }
    return;
  }
});

// 确认链选择：将选中节点转为条件条目；客户路径模式交给回调
function confirmChainSelection() {
  const chains = window._chainSelChains || [];
  window._chainSelChains = null;
  const cb = window._chainSelConfirm;
  window._chainSelConfirm = null;
  const modal = document.getElementById('chainSelModal');
  if (cb) cb(chains);
  else {
    window._condChains = chains;
    window._condChainsAuto = false;   // 用户确认过路径，不再跟随客户档案
    renderCondPills();
  }
  if (modal) modal.remove();
}

// 渲染已选路径列表（在链选择器右侧面板）
// plain=true：客户路径只展示路径本身，不标 leaf/sub，也不做失效检测
function renderChainSelList(chains, plain) {
  if (!chains || chains.length === 0) {
    return '<div class="muted-12 ta-center p20">未选择路径</div>';
  }
  return chains.map((c, i) => {
    const pathStr = c.chain.join(' › ');
    if (plain) {
      return `<div class="tree-row">
        <span class="flex-1 min-w0 text-gray-muted break-all">${escHtml(pathStr)}</span>
        <button class="btn-x-danger" data-on-click="removeChainCond" data-args='${_dataArgs(i)}'>×</button>
      </div>`;
    }
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

// 移除弹窗工作副本中的单个路径（确认后才写入订阅条件）
function removeChainCond(idx) {
  const chains = window._chainSelChains || [];
  const removed = chains.splice(idx, 1);
  if (removed.length) {
    const chainKey = JSON.stringify(removed[0].chain);
    // C2-1: 同步清理 _csSelectedSet，避免树节点状态残留
    if (window._csSelectedSet) window._csSelectedSet.delete(chainKey);
    // 取消树的该节点高亮
    const safeChainKey = chainKey.replace(/"/g, '&quot;');
    const node = document.querySelector('.tree-node[data-chain="' + safeChainKey + '"]');
    if (node) { node.setAttribute('data-sel', '0'); node.classList.remove('tree-node-selected'); }
  }
  // 同时更新右侧面板
  const rightDiv = document.getElementById('chainSelRight');
  if (rightDiv) {
    rightDiv.innerHTML = `<div class="subtitle">已选路径（${chains.length}）</div>` + renderChainSelList(chains, window._chainSelPlain);
  }
}

// 渲染订阅条件区的路径标签（在主表单的 rconds div 中）
function renderCondPills() {
  const container = document.getElementById('rconds');
  if (!container) return;
  const chains = window._condChains || [];
  const empty = document.getElementById('rcondsEmpty');
  if (chains.length === 0) {
    if (empty) empty.classList.remove('hidden');
    container.querySelectorAll('.cond-pill').forEach(el => el.remove());
    updateRuleScopeWarning();
    return;
  }
  if (empty) empty.classList.add('hidden');
  // 保留 empty div，删掉所有 pill
  container.querySelectorAll('.cond-pill').forEach(el => el.remove());
  chains.forEach((c, i) => {
    const pathStr = c.chain.join(' › ');
    const isLeaf = c.match !== 'subtree';
    const matchLabel = isLeaf ? 'leaf' : 'sub';
    const staleBadge = isChainStale(c.chain, c.match) ? chainWarnBadge(c.chain, c.match) : '';
    const pill = document.createElement('div');
    pill.className = 'cond-pill';
    pill.innerHTML = `${staleBadge}<span class="tree-label-blue" title="${escHtml(pathStr)}">${escHtml(pathStr)}</span>
      <span class="match-badge ${isLeaf?'match-leaf':'match-sub'}">${matchLabel}</span>
      <button class="btn-x-plain-14" data-on-click="removeCondPill" data-args='${_dataArgs(i)}'>×</button>`;
    container.appendChild(pill);
  });
  updateRuleScopeWarning();
}

// 移除主表单中的路径标签
function removeCondPill(idx) {
  window._condChains.splice(idx, 1);
  window._condChainsAuto = false;
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

// ── 通知对象：机器人渠道与邮箱收件人/发件渠道都用 + 添加 ───────────────────
const NS_EMAIL_DOMAINS=['nsfocus.com','nsfocus.net'];

function _isNsEmail(addr){
  const d=String(addr||'').split('@')[1]||'';
  return NS_EMAIL_DOMAINS.some(x=>d===x||d.endsWith('.'+x));
}
function _parseEmails(s){
  return [...new Set(String(s||'').split(/[\s,;]+/).map(x=>x.trim()).filter(x=>x&&x.includes('@')))];
}
function _customerEmail(){
  const c=window._currentCustomer;
  return c&&(c.email||'').trim()||'';
}
function _channelSenderEmail(ch){
  return String(ch&&ch.config&&ch.config.smtp_user||'').trim();
}
function _selectedEmailSenders(){
  const sel=window._selChans||[];
  return sel.map(getChannelById).filter(c=>c&&c.type==='email');
}
function _sortedEmailChannels(emails){
  return emails.slice().sort((a,b)=>{
    const p=(_isNsEmail(_channelSenderEmail(a))?0:1)-(_isNsEmail(_channelSenderEmail(b))?0:1);
    return p||String(a.name||'').localeCompare(String(b.name||''),'zh-Hans-CN');
  });
}

function _syncNotifyInputs(){
  const e=document.getElementById('remails');
  if(e)e.placeholder=emailPlaceholder();
  const a=document.getElementById('remailAttach');
  if(a){a.value=window._emailAttach||'';a.placeholder=attPlaceholder();}
}

function renderNotifyTargets(){
  const chData=window._chData||[];
  const sel=window._selChans||[];
  const robotEl=document.getElementById('rchansRobotTags');
  if(!robotEl)return;

  const robots=chData.filter(c=>c.type!=='email');
  const emails=_sortedEmailChannels(chData.filter(c=>c.type==='email'));
  const selEmails=_selectedEmailSenders();

  sel.forEach(cid=>ensureFeishuDefaultTemplate(cid));

  const selRobots=robots.filter(c=>sel.includes(c.id));
  robotEl.innerHTML=selRobots.length?selRobots.map(c=>`<div class="obj-row obj-row-on">
      <span class="obj-name" title="${escHtml(c.name)} #${c.id}">${escHtml(c.name)} <span class="obj-meta">(${escHtml(c.type)}) #${c.id}</span></span>
      <select class="sel-sm dd obj-tpl" data-cid="${c.id}" data-field="template" data-on-change="onChanFieldChange" data-args='${_dataArgs(c.id, 'template', '$value')}' title="机器人模板">${getTplOptsForChannel(c.type)}</select>
      <button type="button" class="email-chip-x" data-on-click="removeRobotChan" data-args='${_dataArgs(c.id)}' title="移除">×</button>
    </div>`).join(''):(robots.length?'<span class="muted-11">尚未添加机器人渠道</span>':'<div class="muted-12">还没有机器人渠道，请先在「通知渠道」页创建</div>');

  // 回显每个机器人渠道的模板
  const tpls=window._chanTpls||{};
  document.querySelectorAll('#rchansRobotTags select[data-cid][data-field="template"]').forEach(s=>{
    const cid=parseInt(s.dataset.cid);
    const ch=getChannelById(cid);
    let cur=tpls[cid]||getChanField(cid,'template')||'';
    if(ch&&ch.type==='feishu'&&cur!=='feishu_full'){
      ensureFeishuDefaultTemplate(cid);
      cur='feishu_full';
    }
    s.value=cur;
  });

  const robotMenu=document.getElementById('rchansRobotMenu');
  if(robotMenu){
    const picked=new Set(sel);
    const avail=robots.filter(c=>!picked.has(c.id));
    robotMenu.innerHTML=avail.length?avail.map(c=>`<button type="button" class="pick-item" data-on-click="addRobotChan" data-args='${_dataArgs(c.id)}'><span class="ellipsis">${escHtml(c.name)} <span class="obj-meta">(${escHtml(c.type)}) #${c.id}</span></span></button>`).join('')
      :`<div class="muted-11 p8">${robots.length?'机器人渠道已全部添加':'还没有机器人渠道，请先在「通知渠道」页创建'}</div>`;
  }

  const rc=document.getElementById('robotCount');
  if(rc)rc.textContent=sel.filter(id=>{const c=getChannelById(id);return c&&c.type!=='email';}).length
    ? `已选 ${sel.filter(id=>{const c=getChannelById(id);return c&&c.type!=='email';}).length} 个` : '';

  // 发件渠道 chip：按选择顺序主备，nsfocus 发件地址排前
  const chips=document.getElementById('remailSenderChips');
  if(chips){
    chips.innerHTML=selEmails.length?selEmails.map((c,i)=>{
      const addr=_channelSenderEmail(c);
      const ns=_isNsEmail(addr);
      return `<span class="sender-chip" title="${escHtml(c.name)}${addr?' · '+escHtml(addr):''}"><span class="sender-order">${i+1}</span><span class="ellipsis">${escHtml(c.name)}</span>${addr?`<span class="sender-addr${ns?' sender-addr-ns':''}">${escHtml(addr)}</span>`:''}<button type="button" class="email-chip-x" data-on-click="removeEmailSender" data-args='${_dataArgs(c.id)}'>×</button></span>`;
    }).join(''):'<span class="muted-11">尚未添加发件渠道</span>';
  }

  const menu=document.getElementById('remailSenderMenu');
  if(menu){
    const picked=new Set(selEmails.map(c=>c.id));
    const avail=emails.filter(c=>!picked.has(c.id));
    menu.innerHTML=avail.length?avail.map(c=>{
      const addr=_channelSenderEmail(c);
      const ns=_isNsEmail(addr);
      return `<button type="button" class="pick-item" data-on-click="addEmailSender" data-args='${_dataArgs(c.id)}'><span class="ellipsis">${escHtml(c.name)} <span class="obj-meta">#${c.id}</span></span>${addr?`<span class="pick-addr${ns?' pick-addr-ns':''}">${escHtml(addr)}</span>`:''}${ns?'<span class="pick-badge">优先</span>':''}</button>`;
    }).join(''):`<div class="muted-11 p8">${emails.length?'邮箱渠道已全部添加':'还没有邮箱渠道，请先在「通知渠道」页创建'}</div>`;
  }
  const sh=document.getElementById('remailSenderHint');
  if(sh){
    if(selEmails.length){
      const hasNs=selEmails.some(c=>_isNsEmail(_channelSenderEmail(c)));
      sh.innerHTML=`发送顺序：<b>${selEmails.map(c=>escHtml(c.name)).join('</b> → <b>')}</b>${selEmails.length>1?'（前一个失败自动用下一个）':''}`
        +(hasNs?'<div class="mt2">优先使用 nsfocus 邮箱渠道发送</div>':'<div class="text-amber-700 mt2">建议优先添加 @nsfocus.com / @nsfocus.net 发件渠道</div>');
    }else sh.innerHTML='';
  }

  // 建议邮箱：客户档案优先，nsfocus 域名排前
  const dl=document.getElementById('remailSuggest');
  if(dl){
    const all=[...new Set((window._custs||[]).map(c=>(c.email||'').trim()).filter(Boolean))];
    all.sort((a,b)=>(_isNsEmail(b)?1:0)-(_isNsEmail(a)?1:0));
    dl.innerHTML=all.map(e=>`<option value="${escHtml(e)}"></option>`).join('');
  }
  _syncNotifyInputs();
  renderEmailPreview();
}

function renderEmailPreview(){
  const el=document.getElementById('remailChips');
  if(!el)return;
  const list=_parseEmails(window._emailRecipients);
  const cust=_customerEmail();
  el.innerHTML=list.length?list.map(a=>{
    const ns=_isNsEmail(a);
    return `<span class="email-chip ${ns?'email-chip-ns':'email-chip-ext'}" title="${escHtml(a)}"><span class="ellipsis">${escHtml(a)}</span><button type="button" class="email-chip-x" data-on-click="removeEmailRecipient" data-args='${_dataArgs(a)}'>×</button></span>`;
  }).join(''):`<span class="muted-11">${cust?`未单独填写，将使用客户档案邮箱 ${escHtml(cust)}`:'未填写收件邮箱'}</span>`;
  const ext=list.filter(a=>!_isNsEmail(a)).length;
  const hint=document.getElementById('remailDomainHint');
  if(hint){
    hint.classList.toggle('hidden',!ext);
    hint.textContent=ext?`⚠️ ${ext} 个非 nsfocus 域名邮箱，仍会发送`:'';
  }
  const ec=document.getElementById('emailCount');
  if(ec)ec.textContent=list.length?`${list.length} 个收件邮箱`:(cust?'使用客户邮箱':'');
}

function toggleRobotPicker(){
  const p=document.getElementById('rchansRobotPicker');
  if(!p)return;
  if(p.classList.contains('hidden')){renderNotifyTargets();p.classList.remove('hidden');}
  else p.classList.add('hidden');
  document.getElementById('rrobotAdd')?.blur();
}
function addRobotChan(cid){
  window._selChans=window._selChans||[];
  if(!window._selChans.some(x=>String(x)===String(cid)))window._selChans.push(cid);
  document.getElementById('rchansRobotPicker')?.classList.add('hidden');
  renderNotifyTargets();
  _ruleRefreshSummary();
}
function removeRobotChan(cid){
  window._selChans=(window._selChans||[]).filter(x=>String(x)!==String(cid));
  renderNotifyTargets();
  _ruleRefreshSummary();
}
function toggleSenderPicker(){
  const p=document.getElementById('remailSenderPicker');
  if(!p)return;
  if(p.classList.contains('hidden')){renderNotifyTargets();p.classList.remove('hidden');}
  else p.classList.add('hidden');
}
function addEmailSender(cid){
  window._selChans=window._selChans||[];
  if(!window._selChans.some(x=>String(x)===String(cid))){
    const ns=_isNsEmail(_channelSenderEmail(getChannelById(cid)));
    let idx=-1;
    if(ns){
      idx=window._selChans.findIndex(x=>{
        const c=getChannelById(x);
        return c&&c.type==='email'&&!_isNsEmail(_channelSenderEmail(c));
      });
    }
    if(idx>=0)window._selChans.splice(idx,0,cid);else window._selChans.push(cid);
  }
  document.getElementById('remailSenderPicker')?.classList.add('hidden');
  renderNotifyTargets();
  _ruleRefreshSummary();
}
function removeEmailSender(cid){
  window._selChans=(window._selChans||[]).filter(x=>String(x)!==String(cid));
  renderNotifyTargets();
  _ruleRefreshSummary();
}
function addEmailRecipient(){
  const el=document.getElementById('remails');
  const raw=(el&&el.value||'').trim();
  if(!raw){toast('请输入邮箱地址','warn');return false;}
  const add=_parseEmails(raw);
  if(!add.length){toast('邮箱格式不正确','error');return false;}
  const list=_parseEmails(window._emailRecipients);
  add.forEach(a=>{if(!list.includes(a))list.push(a);});
  window._emailRecipients=list.join(', ');
  if(el)el.value='';
  renderEmailPreview();
  _ruleRefreshSummary();
  return true;
}
function onEmailDraftKeydown(el,ev){
  if(ev&&ev.key==='Enter'){ev.preventDefault();addEmailRecipient();}
}
function _commitEmailDraft(){
  const el=document.getElementById('remails');
  if(!el||!el.value.trim())return true;
  return addEmailRecipient();
}
function onEmailAttachChange(el){
  window._emailAttach=el.value;
}
function removeEmailRecipient(addr){
  const list=_parseEmails(window._emailRecipients).filter(x=>x!==addr);
  window._emailRecipients=list.join(', ');
  renderEmailPreview();
  _ruleRefreshSummary();
}
function useCustomerEmail(){
  const e=_customerEmail();
  if(!e){toast('客户档案未配置邮箱','warn');return;}
  const list=_parseEmails(window._emailRecipients);
  if(list.includes(e)){toast('客户邮箱已在收件人中');return;}
  list.unshift(e);
  window._emailRecipients=list.join(', ');
  renderEmailPreview();
  _ruleRefreshSummary();
}
function validateNotifyTargets(){
  const sel=window._selChans||[];
  const emailSenders=_selectedEmailSenders();
  const recips=_parseEmails(window._emailRecipients);
  if(!sel.length)return '请至少选择一个机器人渠道或邮箱渠道';
  if(recips.length&&!emailSenders.length)return '已填写收件邮箱，请选择至少一个邮箱发件渠道';
  if(!recips.length&&emailSenders.length&&!_customerEmail())return '请填写收件邮箱（客户档案未配置邮箱）';
  return '';
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
  if(!id&&_subsNeedFirstCollect){
    toast(_subsCollectRunning?'首次采集进行中，请等待本轮采集完成后再创建规则':'请先在「采集数据」页完成一次全量采集，再创建订阅规则','warn');
    return;
  }
  // 新建规则时订阅条件默认跟随客户档案产品；编辑时保留已保存条件
  window._custAutoFill=!id;
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
  modal.innerHTML=`<div class="card modal-card-760" ><div class="card-hd">${id?'编辑规则':'创建规则'} <span class="wizard-hint" id="rStepHint"></span><button data-on-click="_removeClosestId" class="icon-btn">×</button></div><div class="card-bd p16"><input type="hidden" id="rid">
    <div class="wizard-steps" id="rWizardSteps">
      <button type="button" class="wizard-step" data-step="1" data-on-click="goRuleStep" data-args='${_dataArgs(1)}'><span class="wizard-dot">1</span><span>基本信息</span></button>
      <span class="wizard-line"></span>
      <button type="button" class="wizard-step" data-step="2" data-on-click="goRuleStep" data-args='${_dataArgs(2)}'><span class="wizard-dot">2</span><span>订阅条件</span></button>
      <span class="wizard-line"></span>
      <button type="button" class="wizard-step" data-step="3" data-on-click="goRuleStep" data-args='${_dataArgs(3)}'><span class="wizard-dot">3</span><span>推送策略</span></button>
      <span class="wizard-line"></span>
      <button type="button" class="wizard-step" data-step="4" data-on-click="goRuleStep" data-args='${_dataArgs(4)}'><span class="wizard-dot">4</span><span>通知对象</span></button>
    </div>
    <div class="wizard-summary hidden" id="rSummary"></div>

    <div class="rstep" data-step="1"><div class="form-section">
    <div class="form-group"><label class="req">规则名称</label><input id="rnm" placeholder="例如：客户A · WAF 规则包订阅" required></div>
    <div class="form-group"><label class="req">关联客户</label><select class="dd" id="rcust" data-on-change="onCustChange" required><option value="">请选择客户</option>${custOpts}</select></div>
    <div id="custInfoRow" class="form-group cust-note hidden">📧 客户邮箱：<span id="custEmailDisplay"></span></div>
    <div id="custAttachRow" class="form-group cust-note hidden">📎 客户档案附件大小：<span id="custAttachDisplay"></span></div>
    <div class="form-group"><label>有效期至</label><input class="min-w140" id="rvu" type="date" data-on-click="_showPicker" placeholder="不限制"><span class="muted-11 ml8">留空表示长期有效</span></div>
    </div></div>

    <div class="rstep hidden" data-step="2"><div class="form-section">
    <label class="check-row"><input class="m0" type="checkbox" id="rall" data-on-change="toggleAllMode"> 📡 订阅全部产品与类型，不设筛选条件</label>
    <div id="rcondsWrap"><div class="min-h32" id="rconds"><div class="muted-12 mb6" id="rcondsEmpty">未选择路径</div></div><button class="btn btn-xs btn-outline mt8" data-on-click="openRuleChainSelector">+ 添加路径</button></div>
    <div id="rscopeWarn" class="warn-banner mt10 hidden"></div>
    <div class="muted-11 mt10">选择客户后会默认带出客户档案中的产品与版本，可继续手动增删。</div>
    </div></div>

    <div class="rstep hidden" data-step="3"><div class="form-section">
    <div class="form-group"><label>推送模式 <span class="help-icon ml4" id="rmodeTip" title=""></span></label><select class="dd" id="rdm" data-on-change="onDigestModeChange"><option value="">即时推送</option><option value="weekly">每周汇总</option><option value="monthly">每月汇总</option><option value="quarterly">每季汇总</option></select></div>
    <div id="dcfg" class="hidden info-soft-box"></div>
    <div id="delayWrap"><div class="form-group"><label>延迟(d) <span class="help-icon" title="检测到新版本后，观察 N 天确认无问题再推送（仅即时推送模式）">ℹ️</span></label><input class="w80" id="rdl" type="number" value="0" min="0"></div></div>
    <div id="winSection" class="hidden mt6"><label class="chip-click"><input type="checkbox" id="rwin" data-on-change="onWinToggle"> ⏰ 启用推送时间窗口</label></div>
    <div id="winCfg" class="hidden warn-soft-box">
      <div class="mb8" id="winDaysRow"><label class="mr8">周几：</label><label class="mr10"><input type="checkbox" class="win-day" value="1">周一</label><label class="mr10"><input type="checkbox" class="win-day" value="2">周二</label><label class="mr10"><input type="checkbox" class="win-day" value="3">周三</label><label class="mr10"><input type="checkbox" class="win-day" value="4">周四</label><label class="mr10"><input type="checkbox" class="win-day" value="5">周五</label><label class="mr10"><input type="checkbox" class="win-day" value="6">周六</label><label><input type="checkbox" class="win-day" value="0">周日</label></div>
      <div><label class="mr8">时间段：</label><input class="w100" id="winStart" type="time" value="00:00"> ～ <input class="w100" id="winEnd" type="time" value="23:59"></div>
    </div>
    <div class="mt10 pt8 border-top"><label class="chip-click nowrap"><input class="m0" type="checkbox" id="rnr"> 📢 包回退通知</label><div class="muted-11 mt2">升级包被撤回时也发送通知</div></div>
    </div></div>

    <div class="rstep hidden" data-step="4"><div class="form-section">
    <!-- 2026-07-11: 4 模板说明 (A=完整 / B=智能裁剪 / C=极简 / D=飞书全量) -->
    <!-- 仅钉钉/企微/飞书 走模板机制;邮箱是 HTML 全量单封,不走这 4 档 -->
    <details class="tpl-help">
      <summary>📝 模板说明（仅机器人渠道生效，邮箱为 HTML 全量单封）</summary>
      <div class="grid-kv2">
        <div><b>A · 完整</b> (默认)</div><div>完整标题+元信息+描述,超 4KB 自动切多段;钉钉/企微默认</div>
        <div><b>B · 智能裁剪</b></div><div>优先完整描述 → 超长自动剥英文段 → 仍超则只保留 attention 关键行 → 兜底只 head+链接</div>
        <div><b>C · 极简</b></div><div>只 head + 链接,描述完全省略,适合「通知到人即可,详情自己点」的内部群</div>
        <div><b>D · 飞书全量</b></div><div>飞书专属单段展示,放宽到 30KB;飞书默认</div>
      </div>
    </details>

    <div class="notify-sec">
      <div class="notify-sec-hd">🤖 机器人渠道 <span class="muted-11" id="robotCount"></span><span class="muted-11 fw400 ml-auto">添加后直接作为通知对象</span></div>
      <div class="email-input-row">
        <input id="rrobotAdd" class="pick-field" readonly placeholder="点 + 添加机器人渠道" data-on-click="toggleRobotPicker">
        <button type="button" class="btn btn-xs btn-primary shrink0" data-on-click="toggleRobotPicker" title="添加机器人渠道">+</button>
      </div>
      <div class="pick-menu hidden" id="rchansRobotPicker">
        <div id="rchansRobotMenu"></div>
      </div>
      <div class="col gap6 min-h28 mt8" id="rchansRobotTags"></div>
    </div>

    <div class="notify-sec">
      <div class="notify-sec-hd">📧 邮箱通知 <span class="muted-11 ml-auto" id="emailCount"></span></div>
      <div class="form-group">
        <label>收件邮箱 <span class="muted-11">逐个添加，支持多个</span></label>
        <div class="email-input-row">
          <input id="remails" list="remailSuggest" placeholder="输入邮箱后点 + 添加" data-on-keydown="onEmailDraftKeydown" data-args='${_dataArgs('$el', '$event')}'>
          <datalist id="remailSuggest"></datalist>
          <button type="button" class="btn btn-xs btn-primary shrink0" data-on-click="addEmailRecipient" title="添加收件邮箱">+</button>
          <button type="button" class="btn btn-xs btn-outline shrink0" data-on-click="useCustomerEmail">使用客户邮箱</button>
        </div>
        <div class="email-hint">留空则使用客户档案邮箱；收件人越多，建议优先选择 <span class="email-hint-ns">@nsfocus.com</span> / <span class="email-hint-ns">@nsfocus.net</span> 发件渠道</div>
        <div class="email-chips" id="remailChips"></div>
        <div class="email-hint email-warn hidden" id="remailDomainHint"></div>
      </div>
      <div class="grid2">
        <div class="form-group">
          <label>发件渠道 <span class="muted-11">逐个添加，按顺序主备</span></label>
          <div class="email-chips" id="remailSenderChips"></div>
          <div class="pick-add-wrap">
            <button type="button" class="btn btn-xs btn-outline mt6" data-on-click="toggleSenderPicker">+ 添加发件渠道</button>
            <div class="pick-menu hidden" id="remailSenderPicker">
              <div id="remailSenderMenu"></div>
            </div>
          </div>
          <div class="muted-11 mt4" id="remailSenderHint"></div>
        </div>
        <div class="form-group">
          <label>附件大小 (MB)</label>
          <input id="remailAttach" type="number" min="0" max="50" placeholder="" data-on-change="onEmailAttachChange" data-args='${_dataArgs('$el')}'>
          <div class="muted-11 mt4">留空或 0 = 使用客户档案 / 全局默认</div>
        </div>
      </div>
    </div>
    </div></div>

    <div class="drawer-foot wizard-foot"><button class="btn btn-outline" data-on-click="_removeById" data-args='${_dataArgs('rModalWrap')}'>取消</button><div class="ml-auto flex gap8"><button class="btn btn-outline hidden" id="rPrevBtn" data-on-click="rulePrevStep">上一步</button><button class="btn btn-primary" id="rNextBtn" data-on-click="ruleNextStep">下一步</button><button class="btn btn-soft-primary hidden" id="rSaveBtn" data-on-click="saveRule">保存规则</button></div></div></div></div>`;
  document.body.appendChild(modal);
  _ruleShowStep(1);
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
    // 邮箱收件人与附件大小：多邮箱渠道共用同一组收件人（后端按顺序主备发送）
    window._emailRecipients='';
    window._emailAttach='';
    (r.channels||[]).forEach(ch=>{
      if(typeof ch!=='object'||!ch)return;
      const info=getChannelById(ch.channel_id);
      if(!info||info.type!=='email')return;
      if(!window._emailRecipients&&(ch.customer_emails||'').trim())window._emailRecipients=ch.customer_emails;
      if(!window._emailAttach&&Number(ch.attachment_max_mb)>0)window._emailAttach=String(ch.attachment_max_mb);
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
    _syncNotifyInputs();
    document.getElementById('rcust').value=r.customer_id||'';
    window._condChainsAuto=false;
    onCustChange();
    // NOTE: subscription_rules.attachment_max_mb is deprecated. The customer's
    // value drives everything (onCustChange() above writes it to rattDisplay)
    // — we deliberately do NOT read r.attachment_max_mb here, as it would
    // mislead users into thinking the field still works.
    let f=r.filter_conditions||{};
    const isAll=!(f.chains||[]).length;
    document.getElementById('rall').checked=isAll;
    document.getElementById('rcondsWrap').classList.toggle('hidden', isAll);
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
    document.getElementById('rid').value='';document.getElementById('rnm').value='';document.getElementById('rcust').value='';document.getElementById('rdl').value='0';document.getElementById('rvu').value='';document.getElementById('rdm').value='';document.getElementById('rnr').checked=false;document.getElementById('rall').checked=false;document.getElementById('rcondsWrap').classList.remove('hidden');document.getElementById('dcfg').classList.add('hidden');document.getElementById('dcfg').innerHTML='';document.getElementById('rwin').checked=false;document.getElementById('winCfg').classList.add('hidden');document.querySelectorAll('.win-day').forEach(c=>c.checked=false);document.getElementById('winStart').value='00:00';document.getElementById('winEnd').value='23:59';window._condChains=[];window._condChainsAuto=true;window._selChans=[];window._chanTpls={};window._chanCfg={};window._emailRecipients='';window._emailAttach='';renderCondPills();renderNotifyTargets();onDigestModeChange();onCustChange();
  }
}

// ── 订阅规则向导 ───────────────────────────────────────────────────────────
const RULE_STEPS=['基本信息','订阅条件','推送策略','通知对象'];
window._ruleStep=1;

function _ruleShowStep(n){
  n=Math.max(1,Math.min(RULE_STEPS.length,parseInt(n)||1));
  window._ruleStep=n;
  document.querySelectorAll('#rModalWrap .rstep').forEach(el=>{
    el.classList.toggle('hidden',parseInt(el.dataset.step)!==n);
  });
  document.querySelectorAll('#rWizardSteps .wizard-step').forEach(el=>{
    const s=parseInt(el.dataset.step);
    el.classList.toggle('active',s===n);
    el.classList.toggle('done',s<n);
    const dot=el.querySelector('.wizard-dot');
    if(dot)dot.textContent=s<n?'✓':String(s);
  });
  const hint=document.getElementById('rStepHint');
  if(hint)hint.textContent=`第 ${n} / ${RULE_STEPS.length} 步 · ${RULE_STEPS[n-1]}`;
  const prev=document.getElementById('rPrevBtn'),next=document.getElementById('rNextBtn'),save=document.getElementById('rSaveBtn');
  if(prev)prev.classList.toggle('hidden',n===1);
  if(next)next.classList.toggle('hidden',n===RULE_STEPS.length);
  if(save)save.classList.toggle('hidden',n!==RULE_STEPS.length);
  const sum=document.getElementById('rSummary');
  if(sum){
    sum.classList.toggle('hidden',n!==RULE_STEPS.length);
    if(n===RULE_STEPS.length)sum.innerHTML=_ruleSummaryHtml();
  }
}

function _ruleValidateStep(n){
  if(n===1){
    if(!document.getElementById('rnm')?.value.trim())return '请输入规则名称';
    if(!document.getElementById('rcust').value)return '请选择关联客户';
  }else if(n===2){
    if(!document.getElementById('rall').checked&&!(window._condChains||[]).length)
      return '请选择订阅路径，或勾选「订阅全部产品」';
  }else if(n===3){
    const vc=validateRuleConf();
    if(vc.type==='block')return vc.message;
  }
  return '';
}

function goRuleStep(n){
  n=parseInt(n)||1;
  const cur=window._ruleStep||1;
  if(n>cur){
    for(let s=cur;s<n;s++){
      const err=_ruleValidateStep(s);
      if(err){toast(err,'error');_ruleShowStep(s);return;}
    }
  }
  _ruleShowStep(n);
}
function ruleNextStep(){goRuleStep((window._ruleStep||1)+1);}
function rulePrevStep(){_ruleShowStep((window._ruleStep||1)-1);}

function _ruleRefreshSummary(){
  const sum=document.getElementById('rSummary');
  if(sum&&!sum.classList.contains('hidden'))sum.innerHTML=_ruleSummaryHtml();
}

function _ruleSummaryHtml(){
  const cid=document.getElementById('rcust').value;
  const c=(window._custs||[]).find(x=>String(x.id)===String(cid));
  const all=document.getElementById('rall').checked;
  const cond=all?'全部产品':((window._condChains||[]).length?`${window._condChains.length} 条路径`:'未选择');
  const sel=window._selChans||[];
  const robots=sel.filter(id=>{const ch=getChannelById(id);return ch&&ch.type!=='email';}).length;
  const senders=_selectedEmailSenders().length;
  const recips=_parseEmails(window._emailRecipients);
  const mailTargets=recips.length||(senders&&_customerEmail()?1:0);
  const obj=[];
  if(robots)obj.push(`${robots} 机器人`);
  if(mailTargets)obj.push(`${mailTargets} 邮箱`);
  else if(senders)obj.push('邮箱');
  const dm=document.getElementById('rdm').value;
  const push={'':'即时推送',weekly:'每周汇总',monthly:'每月汇总',quarterly:'每季汇总'}[dm]||'即时推送';
  return `<span class="wz-sum-label">确认信息</span><span class="wz-sum-item"><span class="wz-sum-k">客户</span><b>${c?escHtml(c.name):'—'}</b></span><span class="wz-sum-item"><span class="wz-sum-k">订阅</span><b>${cond}</b></span><span class="wz-sum-item"><span class="wz-sum-k">对象</span><b>${obj.length?obj.join(' + '):'未选择'}</b></span><span class="wz-sum-item"><span class="wz-sum-k">推送</span><b>${push}</b></span>`;
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
  if(wdr)wdr.classList.add('hidden'); // 汇总模式：隐藏周几（只有时间段）
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
}async function saveRule(skipMassWarn=false){
  let id=document.getElementById('rid').value;
  const name=document.getElementById('rnm').value.trim();
  if(!name){toast('请输入规则名称','error');_ruleShowStep(1);return;}
  const vc=validateRuleConf();
  if(vc.type==='block'){toast(vc.message,'error');_ruleShowStep(3);return;}
  let chs=window._selChans||[];
  let cid=parseInt(document.getElementById('rcust').value)||0;
  if(!cid){toast('请选择关联客户','error');_ruleShowStep(1);return;}
  const isAll=document.getElementById('rall').checked;
  if(!isAll&&!(window._condChains||[]).length){
    toast('请选择订阅路径，或勾选「订阅全部产品」','error');_ruleShowStep(2);return;
  }
  const fc=isAll?{}:{chains:window._condChains||[]};
  const nrr=document.getElementById('rnr')?.checked?1:0;
  if(!_commitEmailDraft())return;
  const vt=validateNotifyTargets();
  if(vt){toast(vt,'error');_ruleShowStep(4);return;}
  const emailRecips=_parseEmails(window._emailRecipients).join(',');
  const emailAttach=parseInt(window._emailAttach)||0;
  // 2026-07-11: channels 改成对象 list,per-channel 字段走 rule_channels 新列
  //   兼容老数字 list:自动转成 {channel_id: ch}
  // 邮箱收件人/附件大小是规则级共享值,写入每个已选邮箱渠道(多邮箱渠道即主备)
  const chanCfg=window._chanCfg||{};
  const channels=chs.map(ch=>{
    const channelId=typeof ch==='object'&&ch?ch.channel_id:ch;
    const info=getChannelById(channelId);
    const isEmail=!!info&&info.type==='email';
    const cfg=chanCfg[channelId]||{};
    return {
      channel_id: channelId,
      customer_id: cid,
      customer_emails: isEmail?emailRecips:'',
      attachment_max_mb: isEmail?emailAttach:0,
      template: cfg.template||(window._chanTpls||{})[channelId]||feishuDefaultTplForChannelId(channelId),
      extras: cfg.extras||'{}'
    };
  });
  let d={name:name,filter_conditions:fc,delay_days:parseInt(document.getElementById('rdl').value)||0,digest_mode:document.getElementById('rdm').value,valid_until:document.getElementById('rvu').value,customer_id:cid,channels:channels,notify_rollback:nrr,template:window._chanTpls||{}};
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
  // 全部产品 / 超过 5 个产品：可能产生大量推送，保存前二次确认
  const scope=_ruleScopeStats();
  if(!skipMassWarn && (scope.isAll || scope.count>5)){
    const scopeLabel=scope.isAll?'全部产品':`${scope.count} 个产品`;
    confirmAct(`当前规则将订阅${scopeLabel}，可能存在大量推送。确认${id?'保存':'创建'}这条规则？`, ()=>saveRule(true), '⚠️', id?'确认保存':'确认创建', true);
    return;
  }
  let r=id?await api('/subscriptions/'+id,{method:'PUT',body:JSON.stringify(d)}):await api('/subscriptions',{method:'POST',body:JSON.stringify(d)});
  if(r.code===0){toast('已保存');document.getElementById('rModalWrap')?.remove();subscriptions();}else toast(r.message,'error');
}async function delRule(id){await confirmAct('删除此订阅规则？', async ()=>{const r=await api('/subscriptions/'+id,{method:'DELETE'});if(r.code===0){toast('已删除');subscriptions();}else toast(r.message,'error');}, '🗑️', '删除');}
