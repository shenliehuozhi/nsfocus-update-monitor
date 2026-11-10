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
  banner.style.cssText='position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:linear-gradient(135deg,#7c3aed,#4f46e5);color:#fff;border-radius:10px;padding:12px 20px;box-shadow:0 8px 32px rgba(124,58,237,0.4);z-index:3000;min-width:600px;max-width:90vw;font-size:13px;display:flex;flex-direction:column;gap:8px';
  const phaseIcon = {starting:'🚀',collecting:'📦',detecting:'🔍',notifying:'📤',done:'✅',error:'❌',skipped:'⏭️',idle:'⏳'}[state.phase]||'⏳';
  const phaseLabel = {starting:'启动中',collecting:'采集中',detecting:'检测中',notifying:'推送中',done:'采集完成',error:'失败',skipped:'已跳过',idle:'等待中'}[state.phase]||state.phase;
  // Latest log line: prefer scheduler summary (state.log_lines) over raw app.log tail.
  const tailLines = state._tail_lines || [];
  const logLines = state.log_lines || [];
  const src = logLines.length > 0 ? logLines[logLines.length - 1] : (tailLines.length > 0 ? tailLines[tailLines.length - 1] : '');
  const latestClean = src.replace(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} /, '');
  const latestShort = latestClean.length > 120 ? latestClean.slice(0, 120) + '…' : latestClean;
  banner.innerHTML=`
    <div style="display:flex;align-items:center;gap:10px">
      <div data-banner-role="phase-icon" style="font-size:15px">${phaseIcon}</div>
      <div style="flex:1">
        <div data-banner-role="title" style="font-weight:600">🔄 单产品采集 · ${escHtml(state.source_name||('sid='+state.source_id))}</div>
        <div data-banner-role="sub-title" style="opacity:0.9;font-size:12px">${phaseLabel} · 耗时 ${state.duration_s||0}s</div>
      </div>
      <div id="single-banner-log-btn" style="background:rgba(255,255,255,0.2);border-radius:6px;padding:4px 10px;font-size:12px;white-space:nowrap;cursor:pointer">📋 日志</div>
      <div id="single-banner-close" style="background:rgba(255,255,255,0.15);border-radius:6px;padding:4px 8px;font-size:14px;color:rgba(255,255,255,0.7);cursor:pointer;line-height:1;margin-left:4px" title="关闭">×</div>
    </div>
    <div style="height:6px;background:rgba(255,255,255,0.2);border-radius:3px;overflow:hidden">
      <div id="single-banner-bar" style="height:100%;background:rgba(255,255,255,0.85);border-radius:3px;transition:width 0.4s;width:${pct}%"></div>
    </div>
    <div id="single-banner-latest-wrap" style="display:${latestShort ? 'block' : 'none'};font-family:monospace;font-size:11.5px;background:rgba(0,0,0,0.18);border-radius:4px;padding:4px 8px;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(latestClean)}">▶ ${escHtml(latestShort)}</div>
    <div id="single-banner-log" style="display:none;max-height:500px;overflow-y:auto;background:rgba(0,0,0,0.2);border-radius:6px;padding:6px 10px;font-family:monospace;font-size:11px;line-height:1.5"></div>
  `;
  // Toggle log viewer
  banner.querySelector('#single-banner-log-btn').onclick=(ev)=>{
    ev.stopPropagation();
    const log=document.getElementById('single-banner-log');
    const opening = log.style.display==='none';
    log.style.display = opening?'block':'none';
    if(opening){
      // Prefer scheduler summary (log_lines) — it's already populated by collect-status.
      // Fall back to a fresh app.log tail fetch if state.log_lines is empty.
      const logLines = state.log_lines || [];
      if(logLines.length > 0){
        _renderSingleLog(state);
        return;
      }
      log.innerHTML = '<div style="opacity:0.6">⏳ 加载日志...</div>';
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
  log.innerHTML = lines.map(l=>`<div style="margin-bottom:2px;word-break:break-all">${escHtml(l)}</div>`).join('');
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
  const logOpen = logEl && logEl.style.display !== 'none';
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
      latestWrap.style.display = latestShort ? 'block' : 'none';
    }
  } else {
    // Open: hide latest line (avoid duplication with log viewer)
    if(latestWrap) latestWrap.style.display = 'none';
  }
  // If log viewer is open, refresh its content
  if(logOpen){
    const wasScrollBottom = (logEl.scrollTop + logEl.clientHeight) >= logEl.scrollHeight - 5;
    // Prefer scheduler summary (log_lines); fall back to app.log tail
    const lines = logLines.length > 0 ? logLines : tailLines;
    logEl.innerHTML = lines.map(l=>`<div style="margin-bottom:2px;word-break:break-all">${escHtml(l)}</div>`).join('');
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
      inp.style.background='#fff';
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
  inp.style.background='var(--gray-bg)';
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
    <div id="collectModal" style="position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.45);z-index:999;display:flex;align-items:center;justify-content:center" onclick="if(event.target===this)closeCollectModal()">
      <div class="card" style="width:420px;max-height:90vh;overflow-y:auto" onclick="event.stopPropagation()">
        <div class="card-hd">🔄 采集进度 <button class="btn btn-xs btn-outline" style="margin-left:auto" onclick="closeCollectModal()">✕ 关闭</button></div>
        <div class="card-bd" id="collectModalBody">
          <div style="text-align:center;padding:12px 0"><div style="font-size:32px;margin-bottom:8px">⏳</div><p style="font-size:13px">加载进度...</p></div>
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
  const runBar=document.getElementById('dashRunningBar');if(runBar)runBar.style.display='none';
  document.body.classList.remove('warning-open');
  if(_collectPollTimer){clearInterval(_collectPollTimer);_collectPollTimer=null;}
}
function pollProgress(){
  const body=document.getElementById('collectModalBody');
  if(!body)return; // Modal closed, stop polling
  // Guard: if no collection is running on backend, clear timer and skip request
  if(!_collectRunning){if(_collectPollTimer){clearInterval(_collectPollTimer);_collectPollTimer=null;}return;}
  api('/system/collect/progress').then(pr=>{
    if(!pr.data){if(body)body.innerHTML='<p style="text-align:center;color:var(--muted)">无法获取进度</p>';return;}
    const p=pr.data.progress||{};
    // 2026-08-21: 同时调 _showCollectBanner,让底部悬浮卡片在仪表盘也可见
    if(p.phase){_showCollectBanner(p);}
    if(!p.active&&!p.phase){
      body.innerHTML='<p style="text-align:center;color:var(--muted)">当前无采集任务</p><button class="btn btn-sm btn-outline" style="width:100%;margin-top:12px" onclick="closeCollectModal()">关闭</button>';
      _collectRunning=false;
      _hideCollectBanner();
      if(_collectPollTimer){clearInterval(_collectPollTimer);_collectPollTimer=null;}
      return;
    }
    if(p.phase==='done'){_collectRunning=false;_hideCollectBanner();if(_collectPollTimer){clearInterval(_collectPollTimer);_collectPollTimer=null;}}
    let html='';
    if(p.phase==='collecting'){
      const pct=p.products_total?Math.round(p.products_done/p.products_total*100):0;
      html+=`<div style="margin-bottom:12px"><div style="background:var(--gray-border);border-radius:6px;height:8px;overflow:hidden"><div style="background:var(--primary);height:100%;width:${pct}%;transition:width .3s;border-radius:6px"></div></div><div style="font-size:11px;color:var(--muted);margin-top:4px;text-align:center">${p.products_done}/${p.products_total} 产品 (${pct}%)</div></div>`;
      html+=`<div style="font-size:13px;text-align:center;margin-bottom:4px"><strong>📦 ${p.current_product||'...'}</strong></div>`;
      if(p.current_version)html+=`<div style="font-size:11px;color:var(--muted);text-align:center">${p.current_version}</div>`;
      html+=`<div style="font-size:10px;color:var(--muted);text-align:center;margin-top:4px">已采集 ${p.items_collected||0} 项</div>`;
      html+=`<button class="btn btn-xs btn-outline" style="margin-top:10px;width:100%" onclick="closeCollectModal()">后台运行</button>`;
    }else if(p.phase==='detecting'){
      html+=`<div style="text-align:center;font-size:32px;margin-bottom:8px">🔍</div><p style="text-align:center;font-size:13px">正在检测变化...</p>`;
    }else if(p.phase==='notifying'){
      html+=`<div style="text-align:center;font-size:32px;margin-bottom:8px">📨</div><p style="text-align:center;font-size:13px">正在推送通知...</p>`;
    }else if(p.phase==='done'){
      const runBar=document.getElementById('dashRunningBar');if(runBar)runBar.style.display='none';
      const ok=p.errors&&p.errors.length>0?'warn':'ok';
      html+=`<div style="text-align:center;font-size:32px;margin-bottom:8px">${ok==='ok'?'✅':'⚠️'}</div>`;
      html+=`<p style="text-align:center;font-size:14px;font-weight:600">采集完成</p>`;
      html+=`<div class="grid2" style="margin-top:10px"><div class="stat green"><div class="num">${p.total_new||0}</div><div class="label">新增</div></div><div class="stat red"><div class="num">${p.total_rollback||0}</div><div class="label">回退</div></div></div>`;
      html+=`<p style="font-size:11px;color:var(--muted);text-align:center;margin-top:6px">耗时 ${p.duration_s||0}秒 · ${p.items_collected||0}项</p>`;
      if(p.errors&&p.errors.length)html+=`<div style="margin-top:8px;font-size:11px;color:var(--danger)">${p.errors.map(e=>'⚠️ '+e).join('<br>')}</div>`;
      html+=`<button class="btn btn-sm btn-primary" style="width:100%;margin-top:12px" onclick="closeCollectModal();dashboard()">刷新仪表盘</button>`;
      _collectRunning=false;
      if(_collectPollTimer){clearInterval(_collectPollTimer);_collectPollTimer=null;}
    }
    if(html)body.innerHTML=html;
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

// ── Dashboard Progress Poll ──
// 只更新 dashboard 自己的 dashProgress DOM 元素;banner 由 pollCollectBannerGlobal 维护
async function pollDashProgress(){
  try{
    const pr=await api('/system/collect/progress');
    if(!pr.data)return;
    const p=pr.data.progress||{};
    const el=document.getElementById('dashProgress');
    const lc=document.getElementById('lastColText');
    if(!el)return;
    if(p.phase){_showCollectBanner(p);}
    if(!p.active||p.phase==='done'){
      el.style.display='none';
      if(p.phase==='done'){
        if(lc)lc.textContent='已完成';
        setTimeout(()=>_hideCollectBanner(), 8000);  // 2026-08-25: 延长到 8 秒让用户看清完成状态
        if(window._dashPollTimer){clearInterval(window._dashPollTimer);window._dashPollTimer=null;}
      } else {
        _hideCollectBanner();
      }
      return;
    }
    el.style.display='inline';
    if(p.phase==='collecting'){
      const pct=p.products_total?Math.round(p.products_done/p.products_total*100):0;
      el.innerHTML=`<span style="background:var(--primary);color:#fff;border-radius:4px;padding:1px 6px;font-size:11px">${pct}%</span> ${p.current_product||''} ${p.products_done}/${p.products_total}`;
    }else if(p.phase==='detecting'){
      el.innerHTML='<span style="background:#f59e0b;color:#fff;border-radius:4px;padding:1px 6px;font-size:11px">检测中</span>';
    }else if(p.phase==='notifying'){
      el.innerHTML='<span style="background:var(--success);color:#fff;border-radius:4px;padding:1px 6px;font-size:11px">推送中</span>';
    }
  }catch(e){}
}

// ── Debug Toggle ──
async function toggleDebug(){
  const lr=await api('/system/log-level');
  const cur=lr.data?.level||'INFO';
  const next=cur==='DEBUG'?'INFO':'DEBUG';
  const r=await api('/system/log-level',{method:'POST',body:JSON.stringify({level:next,auto_restore_minutes:30})});
  if(r.code===0)toast(r.message,'success');else toast(r.message,'error');
}

// ── System Logs ──
let _logTimer=null;
async function logs(){
  if(_logTimer){clearInterval(_logTimer);_logTimer=null;}

  // Get log files and current level
  const[fR,lR]=await Promise.all([
    api('/system/log-files').catch(()=>({data:{files:[]}})),
    api('/system/log-level').catch(()=>({data:{level:'INFO'}}))
  ]);
  const files=fR.data?.files||[],curLevel=lR.data?.level||'INFO';

  let fOpts=files.map(f=>`<option value="${f.name}">${f.name} (${f.size_human||''})</option>`).join('');
  if(!fOpts)fOpts='<option value="app.log">app.log</option><option value="access.log">access.log</option>';

  document.getElementById('app').innerHTML=`
    <div class="card"><div class="card-hd">📋 系统日志
      <span style="margin-left:auto;font-size:12px;font-weight:400;color:var(--muted)">
        当前级别: <span class="badge badge-${curLevel==='DEBUG'?'warn':'ok'}">${curLevel}</span>
        <button class="btn btn-xs ${curLevel==='DEBUG'?'btn-danger':'btn-outline'}" onclick="toggleDebug()">${curLevel==='DEBUG'?'关闭调试':'开启调试'}</button>
      </span>
    </div><div class="card-bd">
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:end;margin-bottom:12px">
        <div class="form-group" style="margin-bottom:0;min-width:180px"><label>日志文件</label><select id="logFile" class="dd">${fOpts}</select></div>
        <div class="form-group" style="margin-bottom:0;min-width:120px"><label>级别</label><select id="logLevel" class="dd"><option value="">全部</option><option value="DEBUG">DEBUG</option><option value="INFO" selected>INFO</option><option value="WARNING">WARNING</option><option value="ERROR">ERROR</option></select></div>
        <div class="form-group" style="margin-bottom:0;min-width:80px"><label>行数</label><select id="logLines" class="dd"><option value="100">100</option><option value="200" selected>200</option><option value="500">500</option><option value="1000">1000</option></select></div>
        <div class="form-group" style="margin-bottom:0"><label>&nbsp;</label><div style="display:flex;gap:6px"><button class="btn btn-sm btn-primary" onclick="loadLogs()">查询</button><button class="btn btn-sm btn-outline" id="logAutoBtn" onclick="toggleAutoRefresh()">自动刷新</button></div></div>
      </div>
      <div id="logContent" style="background:#0d1117;color:#c9d1d9;border-radius:8px;padding:14px;font:12px/1.6 'SF Mono','Fira Code','Consolas',monospace;max-height:600px;overflow-y:auto;white-space:pre-wrap;word-break:break-all">
        点击「查询」加载日志...
      </div>
    </div></div>`;

  // Load logs immediately
  loadLogs();
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
        let cls='var(--gray-muted)';
        if(l.includes('[ERROR]'))cls='#f85149';
        else if(l.includes('[WARNING]'))cls='#d2991d';
        else if(l.includes('[INFO]'))cls='#7ee787';
        else if(l.includes('[DEBUG]'))cls='var(--gray-muted)';
        return `<span style="color:${cls}">${l.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}</span>`;
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
