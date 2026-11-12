// 经典脚本，无构建；按 index.html 中的顺序加载，跨文件共享全局作用域。
// ── Single-product collection banner (mirrors _showDiscBanner pattern) ──────
// Persistent purple banner at bottom showing phase + app.log tail lines for
// the running single-product collection. Polls every 2s; stays visible until
// user clicks × (does not auto-hide on completion).
let _singleCollTimer = null;
let _singleLogTailSince = null;  // ISO timestamp cursor for incremental log poll
function showSingleCollectBanner(sid, name){
  // Reset tail cursor when showing new banner
  _singleLogTailSince = null;
  // Probe current status first; only show banner when active.
  api('/system/products/0/collect-status').then(r => {
    if(r && r.data && r.data.active){
      // Decorate active state with caller-provided name (in case state lost it)
      const s = Object.assign({}, r.data, {source_name: r.data.source_name || name, source_id: r.data.source_id || sid});
      _renderSingleBanner(s);
      _startSingleLogTail(sid);
    } else {
      // Not active yet — start polling; banner will appear once active=true arrives.
      _startSingleLogTail(sid);
    }
  });
}
function _renderSingleBanner(state){
  // Idempotent: if banner already exists (whether or not _initialized), update it
  // in-place. Only do a full innerHTML rebuild on the very first render.
  const existing=document.getElementById('single-collect-banner');
  if(existing){
    _updateSingleBannerInPlace(state);
    return;
  }
  const phaseOrder = ['starting','collecting','detecting','notifying','done'];
  // Phase-only progress (single-product has no real % — just show phase progression).
  const phasePct = state.phase==='done'?100 :
              state.phase==='error'?0 :
              state.phase==='idle'?10 :
              Math.round((phaseOrder.indexOf(state.phase)+1)/phaseOrder.length*100);
  const pct = phasePct;
  const banner=document.createElement('div');
  banner.id='single-collect-banner';
  banner.className='single-collect-banner';
  const phaseIcon = {starting:'🚀',collecting:'📦',detecting:'🔍',notifying:'📤',done:'✅',error:'❌',skipped:'⏭️',idle:'⏳'}[state.phase]||'⏳';
  const phaseLabel = {starting:'启动中',collecting:'采集中',detecting:'检测中',notifying:'推送中',done:'采集完成',error:'失败',skipped:'已跳过',idle:'等待中'}[state.phase]||state.phase;
  // Latest log line: prefer scheduler summary (state.log_lines) over raw app.log tail.
  const tailLines = state._tail_lines || [];
  const logLines = state.log_lines || [];
  const src = logLines.length > 0 ? logLines[logLines.length - 1] : (tailLines.length > 0 ? tailLines[tailLines.length - 1] : '');
  const latestClean = src.replace(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} /, '');
  const latestShort = latestClean.length > 120 ? latestClean.slice(0, 120) + '…' : latestClean;
  banner.innerHTML=`
    <div class="flex-center-10">
      <div class="fs15" data-banner-role="phase-icon">${phaseIcon}</div>
      <div class="flex-1">
        <div class="fw600" data-banner-role="title">🔄 单产品采集 · ${escHtml(state.source_name||('sid='+state.source_id))}</div>
        <div class="opacity-90 fs12" data-banner-role="sub-title">${phaseLabel} · 耗时 ${state.duration_s||0}s</div>
      </div>
      <div class="banner-chip pointer" id="single-banner-log-btn">📋 日志</div>
      <div class="banner-close" id="single-banner-close" title="关闭">×</div>
    </div>
    <div class="banner-bar">
      <div id="single-banner-bar" class="banner-bar-fill" data-pct="${pct}"></div>
    </div>
    <div id="single-banner-latest-wrap" class="single-banner-latest${latestShort?' ':' hidden'}" title="${escHtml(latestClean)}">▶ ${escHtml(latestShort)}</div>
    <div id="single-banner-log" class="single-banner-log hidden"></div>
  `;
  _applyPctBars(banner);
  // Toggle log viewer
  banner.querySelector('#single-banner-log-btn').onclick=(ev)=>{
    ev.stopPropagation();
    const log=document.getElementById('single-banner-log');
    const opening = log.classList.contains('hidden');
    log.classList.toggle('hidden', !opening);
    if(opening){
      // Prefer scheduler summary (log_lines) — it's already populated by collect-status.
      // Fall back to a fresh app.log tail fetch if state.log_lines is empty.
      const logLines = state.log_lines || [];
      if(logLines.length > 0){
        _renderSingleLog(state);
        return;
      }
      log.innerHTML = '<div class="opacity-60">⏳ 加载日志...</div>';
      const sid = state.source_id;
      const params = new URLSearchParams({file:'app.log', lines:2000});
      if(sid) params.set('source_id', String(sid));
      api('/system/logs?' + params.toString()).then(r => {
        const fresh = (r && r.data && r.data.lines) || [];
        state._tail_lines = fresh.slice().reverse();
        _renderSingleLog(state);
      }).catch(()=>{
        _renderSingleLog(state);
      });
    }
  };
  banner.querySelector('#single-banner-close').onclick=(ev)=>{
    ev.stopPropagation();
    _hideSingleBanner();
  };
  document.body.appendChild(banner);
  banner._initialized = true;  // marker: prevent _renderSingleBanner from re-rendering this banner
}
function _renderSingleLog(state){
  const log=document.getElementById('single-banner-log');
  if(!log) return;
  // Prefer scheduler summary (state.log_lines) — clean structured milestones like
  // "✓ 采集完成: 144 个文件". Only fall back to raw app.log tail (state._tail_lines)
  // if no summary lines exist yet.
  const logLines = state.log_lines || [];
  const tailLines = state._tail_lines || [];
  const lines = logLines.length > 0 ? logLines : tailLines;
  log.innerHTML = lines.map(l=>`<div class="mb2 break-all">${escHtml(l)}</div>`).join('');
  log.scrollTop = log.scrollHeight;
}

// In-place banner update — keeps DOM intact so expanded log viewer stays open.
// Updates phase icon+label, progress bar width, sub-title, latest log line.
// Does NOT touch #single-banner-log div (preserves user's open/close state).
function _updateSingleBannerInPlace(state){
  const banner = document.getElementById('single-collect-banner');
  if(!banner) return;
  const phaseOrder = ['starting','collecting','detecting','notifying','done'];
  const phasePct = state.phase==='done'?100 :
              state.phase==='error'?0 :
              state.phase==='idle'?10 :
              Math.round((phaseOrder.indexOf(state.phase)+1)/phaseOrder.length*100);
  const phaseIcon = {starting:'🚀',collecting:'📦',detecting:'🔍',notifying:'📤',done:'✅',error:'❌',skipped:'⏭️',idle:'⏳'}[state.phase]||'⏳';
  const phaseLabel = {starting:'启动中',collecting:'采集中',detecting:'检测中',notifying:'推送中',done:'采集完成',error:'失败',skipped:'已跳过',idle:'等待中'}[state.phase]||state.phase;
  // Find rows by data attribute (more robust than positional selectors)
  const iconEl = banner.querySelector('[data-banner-role="phase-icon"]');
  if(iconEl) iconEl.textContent = phaseIcon;
  const subTitleEl = banner.querySelector('[data-banner-role="sub-title"]');
  if(subTitleEl) subTitleEl.textContent = `${phaseLabel} · 耗时 ${state.duration_s||0}s`;
  const titleEl = banner.querySelector('[data-banner-role="title"]');
  if(titleEl) titleEl.textContent = `🔄 单产品采集 · ${state.source_name||('sid='+state.source_id)}`;
  // Update progress bar width
  const bar = document.getElementById('single-banner-bar');
  if(bar) bar.style.width = phasePct + '%';
  // Update latest log line: prefer scheduler summary (log_lines) over raw app.log noise
  const logEl = document.getElementById('single-banner-log');
  const logOpen = logEl && !logEl.classList.contains('hidden');
  const latestWrap = document.getElementById('single-banner-latest-wrap');
  const logLines = state.log_lines || [];
  const tailLines = state._tail_lines || [];
  if(!logOpen){
    // Closed: show latest line (scheduler summary wins; fall back to app.log tail)
    const src = logLines.length > 0 ? logLines[logLines.length - 1] : (tailLines.length > 0 ? tailLines[tailLines.length - 1] : '');
    const latestClean = src.replace(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} /, '');
    const latestShort = latestClean.length > 120 ? latestClean.slice(0, 120) + '…' : latestClean;
    if(latestWrap){
      latestWrap.textContent = latestShort ? '▶ ' + latestShort : '';
      latestWrap.title = latestClean;
      latestWrap.classList.toggle('hidden', !latestShort);
    }
  } else {
    // Open: hide latest line (avoid duplication with log viewer)
    if(latestWrap) latestWrap.classList.add('hidden');
  }
  // If log viewer is open, refresh its content
  if(logOpen){
    const wasScrollBottom = (logEl.scrollTop + logEl.clientHeight) >= logEl.scrollHeight - 5;
    // Prefer scheduler summary (log_lines); fall back to app.log tail
    const lines = logLines.length > 0 ? logLines : tailLines;
    logEl.innerHTML = lines.map(l=>`<div class="mb2 break-all">${escHtml(l)}</div>`).join('');
    if(wasScrollBottom) logEl.scrollTop = logEl.scrollHeight;
  }
}
// Poll app.log for new lines related to this source_id
function _startSingleLogTail(sid){
  if(_singleCollTimer){clearInterval(_singleCollTimer);_singleCollTimer=null;}
  const tick = async () => {
    try {
      // 1) Update phase from collect-status
      const r = await api('/system/products/0/collect-status');
      if(!r || !r.data) return;
      const s = r.data;
      // 2) Get tail lines from app.log
      const params = new URLSearchParams({file:'app.log', lines:2000, source_id:String(sid)});
      if(_singleLogTailSince) params.set('since_ts', _singleLogTailSince);
      const logR = await api('/system/logs?' + params.toString());
      let tailLines = (logR && logR.data && logR.data.lines) || [];
      // Bump cursor to newest line's timestamp (we want future polls to fetch
      // strictly newer lines; backend tail returns newest-first so [0] is newest).
      if(tailLines.length > 0){
        const newestLine = tailLines[0];
        const m = newestLine.match(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})/);
        if(m) _singleLogTailSince = m[1];
      }
      // Reverse for display: banner wants oldest-first ordering.
      s._tail_lines = tailLines.slice().reverse();
      // Decorate with caller-provided sid if state lost it
      if(!s.source_name) s.source_name = `sid=${s.source_id||sid}`;
      if(!s.source_id) s.source_id = sid;
      // Only render/update banner if active OR banner already exists (so user can close it).
      // This prevents showing a "sid=null" idle banner before collection actually starts.
      const existing = document.getElementById('single-collect-banner');
      if(s.active || existing){
        // _renderSingleBanner is now idempotent — first render builds DOM, subsequent
        // calls update in-place. Both paths preserve #single-banner-log open state.
        _renderSingleBanner(s);
      }
      // If not active and no banner exists, do nothing — banner stays hidden until active arrives.
      // Stop polling after 30s of inactivity to avoid leaking timer
      if(!s.active && s.phase === 'done'){
        // Keep banner visible — user must click × to close
      }
    } catch(e) { /* swallow transient errors */ }
  };
  // Initial fetch + every 2s
  tick();
  _singleCollTimer = setInterval(tick, 2000);
}
function _hideSingleBanner(){
  if(_singleCollTimer){clearInterval(_singleCollTimer);_singleCollTimer=null;}
  const b=document.getElementById('single-collect-banner'); if(b) b.remove();
}

async function handleSchedulerToggle(cb){
  if(!cb.checked){
    // 关闭调度器，先检查是否正在采集
    const sr=await api('/settings/scheduler');
    if(sr.data?.scheduler?.is_running){
      const ok=confirm('调度器正在采集中，现在关闭将中断本次采集。\n\n确定要关闭吗？');
      if(!ok){cb.checked=true;return;}
    }
  }
  // 保存并实时生效
  const d={scheduler_enabled:cb.checked?'1':'0'};
  const r=await api('/settings/config',{method:'PUT',body:JSON.stringify(d)});
  if(r.code===0){
    toast(cb.checked?'调度器已启用':'调度器已关闭', cb.checked?'success':'warn');
  }else{
    toast('保存失败','error');
    cb.checked=!cb.checked; // 还原
  }
}

async function forceStopAndSave(){
  if(!confirm('采集将被迫停止，是否继续？')) return;
  const d={scheduler_enabled:document.getElementById('cfg-scheduler-enabled').checked?'1':'0',collect_interval:document.getElementById('cfg-interval').value,rollback_confirm:document.getElementById('cfg-rollback').value,collect_delay_min:document.getElementById('cfg-delay-min').value,collect_delay_max:document.getElementById('cfg-delay-max').value,attachment_max_mb:document.getElementById('cfg-attach').value,quiet_hours_enabled:document.getElementById('cfg-quiet-enabled').checked?'1':'0',quiet_hours_start:document.getElementById('cfg-quiet-start').value,quiet_hours_end:document.getElementById('cfg-quiet-end').value,heartbeat_enabled:document.getElementById('cfg-heartbeat-enabled').checked?'1':'0',heartbeat_interval:document.getElementById('cfg-heartbeat').value,heartbeat_url:document.getElementById('cfg-heartbeat-url').value.trim()||'/update/listBvsV6/v/bvssys',channel_health_enabled:document.getElementById('cfg-channel-health').checked?'1':'0',
    pkg_track_limit:document.getElementById('cfg-pkg-track').value,
    snapshots_per_group:document.getElementById('cfg-snapshots-per-group').value,
    email_global_hourly_limit:document.getElementById('cfg-email-global-h').value,
    email_global_daily_limit:document.getElementById('cfg-email-global-d').value,
    email_customer_hourly_limit:document.getElementById('cfg-email-cust-h').value,
    email_customer_daily_limit:document.getElementById('cfg-email-cust-d').value,
    ops_contact_name:document.getElementById('cfg-ops-name').value.trim(),
    ops_contact_email:document.getElementById('cfg-ops-email').value.trim(),
    ops_contact_phone:document.getElementById('cfg-ops-phone').value.trim()};
  const r=await api('/settings/force-stop',{method:'POST',body:JSON.stringify(d)});
  await saveEventConfig();
  toast(r.code===0?'采集已停止，配置已保存':'保存失败',r.code===0?'success':'error');
  if(r.code===0) settings();
}

async function saveEventConfig(){
  // 共享事件配置保存逻辑(force-stop 和正常 saveSettings 都用)
  const eventEnabled=document.getElementById('cfg-event-enabled').checked;
  const eventChannel=document.getElementById('cfg-event-channel').value;
  const eventTypes=Array.from(document.querySelectorAll('.cfg-event-type:checked')).map(cb=>cb.value);
  await api('/settings/event-config',{method:'PUT',body:JSON.stringify({
    enabled:eventEnabled,
    channel_id:eventChannel?parseInt(eventChannel):null,
    event_types:eventTypes
  })});
}

function editHeartbeatUrl(){
  const inp=document.getElementById('cfg-heartbeat-url');
  const btn=document.getElementById('cfg-heartbeat-url-edit');
  if(!inp){console.warn('[editHeartbeatUrl] input#cfg-heartbeat-url not found');return;}
  if(!btn){console.warn('[editHeartbeatUrl] button#cfg-heartbeat-url-edit not found');return;}
  if(!inp.readOnly){return;} // 已在编辑模式,无操作
  confirmAct(
    '⚠️ 修改预检 URL 会影响 Session 状态判断\n\n该 URL 用于采集前/主动心跳快速探测 Session 状态:\n• 错配会导致"过期/污染"漏报或误报\n• 默认值 /update/listBvsV6/v/bvssys 是 NSFOCUS 设备验证过的端点\n• 必须是返回 v2 升级包列表的轻量 GET 接口\n\n确认要编辑吗?',
    async()=>{
      inp.readOnly=false;
      inp.classList.remove('bg-subtle');
      inp.focus();inp.select();
      btn.textContent='🔒 锁定';
      btn.onclick=lockHeartbeatUrl;
      toast('已进入编辑模式,改完点"保存配置"提交','info');
    },
    '✏️',
    '确认编辑',
    true
  );
}
function lockHeartbeatUrl(){
  const inp=document.getElementById('cfg-heartbeat-url');
  const btn=document.getElementById('cfg-heartbeat-url-edit');
  inp.readOnly=true;
  inp.value=inp.defaultValue; // 还原成进入编辑前的值,避免误存
  inp.classList.add('bg-subtle');
  btn.textContent='✏️ 编辑';
  btn.onclick=editHeartbeatUrl;
  toast('已锁定,改动已撤销','info');
}

async function saveSettings(){
  const d={scheduler_enabled:document.getElementById('cfg-scheduler-enabled').checked?'1':'0',collect_interval:document.getElementById('cfg-interval').value,rollback_confirm:document.getElementById('cfg-rollback').value,collect_delay_min:document.getElementById('cfg-delay-min').value,collect_delay_max:document.getElementById('cfg-delay-max').value,attachment_max_mb:document.getElementById('cfg-attach').value,quiet_hours_enabled:document.getElementById('cfg-quiet-enabled').checked?'1':'0',quiet_hours_start:document.getElementById('cfg-quiet-start').value,quiet_hours_end:document.getElementById('cfg-quiet-end').value,heartbeat_enabled:document.getElementById('cfg-heartbeat-enabled').checked?'1':'0',heartbeat_interval:document.getElementById('cfg-heartbeat').value,heartbeat_url:document.getElementById('cfg-heartbeat-url').value.trim()||'/update/listBvsV6/v/bvssys',channel_health_enabled:document.getElementById('cfg-channel-health').checked?'1':'0',
    pkg_track_limit:document.getElementById('cfg-pkg-track').value,
    snapshots_per_group:document.getElementById('cfg-snapshots-per-group').value,
    email_global_hourly_limit:document.getElementById('cfg-email-global-h').value,
    email_global_daily_limit:document.getElementById('cfg-email-global-d').value,
    email_customer_hourly_limit:document.getElementById('cfg-email-cust-h').value,
    email_customer_daily_limit:document.getElementById('cfg-email-cust-d').value,
    ops_contact_name:document.getElementById('cfg-ops-name').value.trim(),
    ops_contact_email:document.getElementById('cfg-ops-email').value.trim(),
    ops_contact_phone:document.getElementById('cfg-ops-phone').value.trim()};
  const r=await api('/settings/config',{method:'PUT',body:JSON.stringify(d)});

  await saveEventConfig();

  toast(r.code===0?'配置已保存':'保存失败',r.code===0?'success':'error');
}

// ── Manual Collect ──
let _collectPollTimer=null;
let _collectRunning=false;
async function triggerCollect(){
  if(_collectRunning){toast('⚠️ 采集正在进行中，请等待完成后再操作','warn');return;}
  try{
    const sR=await api('/settings/scheduler');
    if(sR.data?.is_running){_collectRunning=true;toast('⚠️ 采集正在进行中，请等待完成后再操作','warn');return;}
  }catch(e){}
  confirmAct('确认立即触发一次增量采集？', startCollect, '🔄', '确认采集', false);
}
async function startCollect(){
  // 2026-08-21: 改用底部悬浮 banner, 不用 dashRunningBar (已删除 DOM)
  // 立即显示 "launching" 状态, 后续由 pollProgress 持续更新
  _showCollectBanner({phase: 'init', products_done: 0, products_total: 0, started_at: new Date().toISOString().slice(0, 19)});
  const r=await api('/system/collect',{method:'POST',body:JSON.stringify({mode:'quick'})});
  if(r.code===409){toast('采集正在进行中','warn');location.reload();return;}
  if(r.code!==0){toast(r.message,'error');location.reload();return;}
  _collectRunning=true;
  // 2026-08-25: 改用全局独立轮询,不管用户切到哪个页面 banner 都持续更新
  // (之前用 pollProgress 依赖 modal body,切走就停了)
  startCollectBannerPoll();
}
function showCollectProgress(){
  // Remove existing modal if any
  const old=document.getElementById('collectModal');if(old)old.remove();
  document.getElementById('app').insertAdjacentHTML('beforeend',`
    <div class="overlay-999" id="collectModal" data-on-click="closeCollectModal" data-self>
      <div class="card modal-card-420" >
        <div class="card-hd">🔄 采集进度 <button class="btn btn-xs btn-outline ml-auto" data-on-click="closeCollectModal">✕ 关闭</button></div>
        <div class="card-bd" id="collectModalBody">
          <div class="ta-center py12"><div class="fs32 mb8">⏳</div><p class="fs13">加载进度...</p></div>
        </div>
      </div>
    </div>
  `);
  // Trigger a poll immediately
  pollProgress();
  // Ensure timer is running
  if(!_collectPollTimer)_collectPollTimer=setInterval(pollProgress,2000);
}
function closeCollectModal(){
  const m=document.getElementById('collectModal');if(m)m.remove();
  const runBar=document.getElementById('dashRunningBar');if(runBar)runBar.classList.add('hidden');
  document.body.classList.remove('warning-open');
  if(_collectPollTimer){clearInterval(_collectPollTimer);_collectPollTimer=null;}
}
function _closeCollectAndObserve(){closeCollectModal();observability();}
function pollProgress(){
  const body=document.getElementById('collectModalBody');
  if(!body)return; // Modal closed, stop polling
  // Guard: if no collection is running on backend, clear timer and skip request
  if(!_collectRunning){if(_collectPollTimer){clearInterval(_collectPollTimer);_collectPollTimer=null;}return;}
  api('/system/collect/progress').then(pr=>{
    if(!pr.data){if(body)body.innerHTML='<p class="ta-center text-muted">无法获取进度</p>';return;}
    const p=pr.data.progress||{};
    // 2026-08-21: 同时调 _showCollectBanner,让底部悬浮卡片在仪表盘也可见
    if(p.phase){_showCollectBanner(p);}
    if(!p.active&&!p.phase){
      body.innerHTML='<p class="ta-center text-muted">当前无采集任务</p><button class="btn btn-sm btn-outline w-full mt12" data-on-click="closeCollectModal">关闭</button>';
      _collectRunning=false;
      _hideCollectBanner();
      if(_collectPollTimer){clearInterval(_collectPollTimer);_collectPollTimer=null;}
      return;
    }
    if(p.phase==='done'){_collectRunning=false;_hideCollectBanner();if(_collectPollTimer){clearInterval(_collectPollTimer);_collectPollTimer=null;}}
    let html='';
    if(p.phase==='collecting'){
      const pct=p.products_total?Math.round(p.products_done/p.products_total*100):0;
      html+=`<div class="mb12"><div class="progress-8"><div class="progress-8-fill" data-pct="${pct}"></div></div><div class="muted-11 mt4 ta-center">${p.products_done}/${p.products_total} 产品 (${pct}%)</div></div>`;
      html+=`<div class="fs13 ta-center mb4"><strong>📦 ${p.current_product||'...'}</strong></div>`;
      if(p.current_version)html+=`<div class="muted-11 ta-center">${p.current_version}</div>`;
      html+=`<div class="muted-10 ta-center mt4">已采集 ${p.items_collected||0} 项</div>`;
      html+=`<button class="btn btn-xs btn-outline mt10 w-full" data-on-click="closeCollectModal">后台运行</button>`;
    }else if(p.phase==='detecting'){
      html+=`<div class="empty-icon">🔍</div><p class="empty-text">正在检测变化...</p>`;
    }else if(p.phase==='notifying'){
      html+=`<div class="empty-icon">📨</div><p class="empty-text">正在推送通知...</p>`;
    }else if(p.phase==='done'){
      const runBar=document.getElementById('dashRunningBar');if(runBar)runBar.classList.add('hidden');
      const ok=p.errors&&p.errors.length>0?'warn':'ok';
      html+=`<div class="empty-icon">${ok==='ok'?'✅':'⚠️'}</div>`;
      html+=`<p class="ta-center fs14 fw600">采集完成</p>`;
      html+=`<div class="grid2 mt10"><div class="stat green"><div class="num">${p.total_new||0}</div><div class="label">新增</div></div><div class="stat red"><div class="num">${p.total_rollback||0}</div><div class="label">回退</div></div></div>`;
      html+=`<p class="muted-11 mt6 ta-center">耗时 ${p.duration_s||0}秒 · ${p.items_collected||0}项</p>`;
      if(p.errors&&p.errors.length)html+=`<div class="mt8 fs11 text-danger">${p.errors.map(e=>'⚠️ '+e).join('<br>')}</div>`;
      html+=`<button class="btn btn-sm btn-primary w-full mt12" data-on-click="_closeCollectAndObserve">刷新仪表盘</button>`;
      _collectRunning=false;
      if(_collectPollTimer){clearInterval(_collectPollTimer);_collectPollTimer=null;}
    }
    if(html){body.innerHTML=html;_applyPctBars(body);}
  }).catch(()=>{});
}
// ── 全局独立轮询 ──
// 不依赖任何页面 DOM:只更新底部 banner 浮动卡片。
// 启动一次后,无论用户切到哪个页面 banner 都持续更新;采集完成自动停止。
// 用于仪表盘"立即采集"按钮 (banner 是全站可见的浮动卡片,轮询也应该是全局的)。
let _collectBannerPollTimer = null;
let _collectBannerPollRunning = false;
async function pollCollectBannerGlobal(){
  if (_collectBannerPollRunning) return; // 防重叠
  _collectBannerPollRunning = true;
  try{
    const pr = await api('/system/collect/progress');
    if (!pr.data) return;
    const p = pr.data.progress || {};
    if (!p.phase) {
      _hideCollectBanner();
      if (_collectBannerPollTimer) { clearInterval(_collectBannerPollTimer); _collectBannerPollTimer = null; }
      return;
    }
    _showCollectBanner(p);
    if (p.phase === 'done' || p.phase === 'error' || p.phase === 'cancelled') {
      setTimeout(() => {
        _hideCollectBanner();
        if (_collectBannerPollTimer) { clearInterval(_collectBannerPollTimer); _collectBannerPollTimer = null; }
      }, 8000);  // 2026-08-25: 延长到 8 秒让用户看清完成状态
    }
  } catch(e) {}
  finally { _collectBannerPollRunning = false; }
}
function startCollectBannerPoll(){
  if (_collectBannerPollTimer) return;
  pollCollectBannerGlobal();
  _collectBannerPollTimer = setInterval(pollCollectBannerGlobal, 2000);
}
function stopCollectBannerPoll(){
  if (_collectBannerPollTimer) { clearInterval(_collectBannerPollTimer); _collectBannerPollTimer = null; }
  _hideCollectBanner();
}

// ── Debug Toggle ──
async function toggleDebug(){
  const lr=await api('/system/log-level');
  const cur=lr.data?.level||'INFO';
  const next=cur==='DEBUG'?'INFO':'DEBUG';
  const r=await api('/system/log-level',{method:'POST',body:JSON.stringify({level:next,auto_restore_minutes:30})});
  if(r.code===0)toast(r.message,'success');else toast(r.message,'error');
}

// ── 事件与日志（事件与告警 + 系统日志）──
let _logTimer=null;
async function logs(){
  const _seq=_navSeq;
  _stopLogTimer();

  // Get log files and current level
  const[fR,lR]=await Promise.all([
    api('/system/log-files').catch(()=>({data:{files:[]}})),
    api('/system/log-level').catch(()=>({data:{level:'INFO'}}))
  ]);
  const files=fR.data?.files||[],curLevel=lR.data?.level||'INFO';

  let fOpts=files.map(f=>`<option value="${escHtml(f.name)}">${escHtml(f.name)} (${escHtml(f.size_human||'')})</option>`).join('');
  if(!fOpts)fOpts='<option value="app.log">app.log</option><option value="access.log">access.log</option>';

  if(navStale(_seq))return;
  _eventsState('eventsPage').offset=0;
  document.getElementById('app').innerHTML=`
    <div class="push-tabs">
      <div class="push-tab-active" id="logsTabEvents" data-on-click="logsSwitchTab" data-args='${_dataArgs("events")}'>🔔 事件与告警</div>
      <div class="push-tab" id="logsTabSys" data-on-click="logsSwitchTab" data-args='${_dataArgs("syslog")}'>📋 系统日志</div>
    </div>
    <div id="logsPaneEvents">${eventsPageHtml()}</div>
    <div id="logsPaneSys" class="hidden">
      <div class="card"><div class="card-hd">📋 系统日志
        <span class="ml-auto muted-12 fw400">
          当前级别: <span class="badge badge-${curLevel==='DEBUG'?'warn':'ok'}">${curLevel}</span>
          <button class="btn btn-xs ${curLevel==='DEBUG'?'btn-danger':'btn-outline'}" data-on-click="toggleDebug">${curLevel==='DEBUG'?'关闭调试':'开启调试'}</button>
        </span>
      </div><div class="card-bd">
        <div class="flex wrap items-end gap8 mb12">
          <div class="form-group mb0 min-w180"><label>日志文件</label><select id="logFile" class="dd">${fOpts}</select></div>
          <div class="form-group mb0 min-w120"><label>级别</label><select id="logLevel" class="dd"><option value="">全部</option><option value="DEBUG">DEBUG</option><option value="INFO" selected>INFO</option><option value="WARNING">WARNING</option><option value="ERROR">ERROR</option></select></div>
          <div class="form-group mb0 min-w80"><label>行数</label><select id="logLines" class="dd"><option value="100">100</option><option value="200" selected>200</option><option value="500">500</option><option value="1000">1000</option></select></div>
          <div class="form-group mb0"><label>&nbsp;</label><div class="flex gap6"><button class="btn btn-sm btn-primary" data-on-click="loadLogs">查询</button><button class="btn btn-sm btn-outline" id="logAutoBtn" data-on-click="toggleAutoRefresh">自动刷新</button></div></div>
        </div>
        <div class="log-dark" id="logContent">
          点击「查询」加载日志...
        </div>
      </div></div>
    </div>`;

  loadEventsPage();
}

function logsSwitchTab(tab){
  const evBtn=document.getElementById('logsTabEvents'),sysBtn=document.getElementById('logsTabSys');
  const evPane=document.getElementById('logsPaneEvents'),sysPane=document.getElementById('logsPaneSys');
  if(!evBtn||!sysBtn||!evPane||!sysPane)return;
  const showEvents=tab!=='syslog';
  evBtn.className=showEvents?'push-tab-active':'push-tab';
  sysBtn.className=showEvents?'push-tab':'push-tab-active';
  evPane.classList.toggle('hidden',!showEvents);
  sysPane.classList.toggle('hidden',showEvents);
  if(showEvents){_stopLogTimer();loadEventsPage();}
  else loadLogs();
}

function _stopLogTimer(){
  if(_logTimer){clearInterval(_logTimer);_logTimer=null;}
  const btn=document.getElementById('logAutoBtn');
  if(btn){btn.textContent='自动刷新';btn.classList.remove('btn-success');btn.classList.add('btn-outline');}
}

async function loadLogs(){
  const f=document.getElementById('logFile')?.value||'app.log';
  const l=document.getElementById('logLevel')?.value||'';
  const n=document.getElementById('logLines')?.value||'200';
  const box=document.getElementById('logContent');
  if(!box)return;
  box.textContent='加载中...';
  try{
    const r=await api(`/system/logs?file=${encodeURIComponent(f)}&lines=${n}${l?'&level='+l:''}`);
    if(r.code===0){
      const lines=r.data.lines||[];
      box.innerHTML=lines.map(l=>{
        let cls='log-lv-debug';
        if(l.includes('[ERROR]'))cls='log-lv-error';
        else if(l.includes('[WARNING]'))cls='log-lv-warn';
        else if(l.includes('[INFO]'))cls='log-lv-info';
        return `<span class="${cls}">${l.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}</span>`;
      }).join('\n');
      if(!lines.length)box.textContent='(无匹配日志)';
    }else{
      box.textContent=r.message||'加载失败';
    }
  }catch(e){box.textContent='加载失败: '+e.message;}
}

function toggleAutoRefresh(){
  const btn=document.getElementById('logAutoBtn');
  if(_logTimer){
    clearInterval(_logTimer);_logTimer=null;
    btn.textContent='自动刷新';btn.classList.remove('btn-success');btn.classList.add('btn-outline');
  }else{
    _logTimer=setInterval(loadLogs,5000);
    btn.textContent='停止刷新';btn.classList.remove('btn-outline');btn.classList.add('btn-success');
  }
}
