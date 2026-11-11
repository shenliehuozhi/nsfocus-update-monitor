// 经典脚本，无构建；按 index.html 中的顺序加载，跨文件共享全局作用域。
// ── Dashboard ──
// 全局浮窗: append 到 body, 避开所有祖先 overflow:hidden 裁切
let _tipEl=null;
function _showTip(target){
  if(!_tipEl){_tipEl=document.createElement('div');_tipEl.style.cssText='position:fixed;background:#1a2332;color:#fff;padding:8px 12px;border-radius:6px;font-size:12px;white-space:pre-wrap;max-width:720px;min-width:240px;z-index:99999;box-shadow:0 4px 12px rgba(0,0,0,.25);line-height:1.6;pointer-events:none;opacity:0;transition:opacity .15s';document.body.appendChild(_tipEl);}
  _tipEl.textContent=target.dataset.tip||'';
  const r=target.getBoundingClientRect();
  // 默认显示在行的下方 (top: r.bottom + 6), 如果下方空间不够则显示在上方
  let top=r.bottom+6;
  const tipH=80; // 估算浮窗高度
  if(top+tipH>window.innerHeight)top=r.top-tipH-6;
  let left=r.left;
  const tipW=280;
  if(left+tipW>window.innerWidth)left=window.innerWidth-tipW-8;
  if(left<8)left=8;
  _tipEl.style.top=top+'px';
  _tipEl.style.left=left+'px';
  _tipEl.style.opacity='1';
}
function _hideTip(){if(_tipEl)_tipEl.style.opacity='0';}
document.addEventListener('mouseover',e=>{const t=e.target.closest('.pill-tip');if(t)_showTip(t)});
document.addEventListener('mouseout',e=>{const t=e.target.closest('.pill-tip');if(t)_hideTip()});
// 2026-09-18: 三个卡的 7/30/90 各自独立, 每个卡独立 fetch 自己 range 的数据
let _dashRanges={cust:'7',tl:'7',pie:'7'};
let _dashData=null; // 缓存 dashboard 接口响应, 只刷新部分卡片时复用

async function setDashRange(card, range){
  _dashRanges[card]=String(range);
  // 更新按钮高亮
  const map={cust:'#custRange',tl:'#tlRange',pie:'#pieRange'};
  const btns=document.querySelectorAll(map[card]+' .btn');
  btns.forEach(b=>{
    const v=b.textContent.trim();
    b.className='btn btn-xs '+(v===range+'天'?'btn-primary':'btn-outline');
  });
  if(card==='cust'){
    // 客户推送独立 fetch
    const r=await api('/customers/push-summary?days='+range).catch(()=>({data:{items:[]}}));
    _renderCustPushTable(r.data?.items||[], range);
  }else if(card==='tl'){
    // 包发布趋势 fetch 整个 dashboard (timeline 用 range_tl)
    const r=await api('/dashboard?range_tl='+range);
    _renderDashFromData(r.data);
    _renderTimelineChart('chartTimeline', window._tlData, window._tlDays, window._tlVisible);
    _renderTlLegend(Object.keys(window._tlVisible), window._tlVisible);
  }else if(card==='pie'){
    // 产品发布分布 fetch 整个 dashboard (product_stats 用 range_pie)
    const r=await api('/dashboard?range_pie='+range);
    _renderDashFromData(r.data);
    _renderProdDistTable(r.data?.product_stats||[]);
  }
}

// 从 dashboard 响应数据中更新 timeline + product + legend 共享数据
function _renderDashFromData(d){
  if(!d)return;
  window._tlData=d.timeline_stats||[];
  window._tlDays=parseInt(_dashRanges.tl);
  if(!window._tlVisible)window._tlVisible={};
  const allProds=(d.product_stats||[]).map(p=>p.name);
  allProds.forEach(p=>{if(window._tlVisible[p]===undefined)window._tlVisible[p]=true;});
  window._productStats=d.product_stats||[];
}

function _confirmRestartService(){
  confirmAct('⚠️ 确认重启服务？服务将短暂中断后自动恢复。', async()=>{let r=await api('/system/restart',{method:'POST'});if(r.code===0)toast('服务正在重启…','success');else toast('失败: '+r.message,'error');},'🔄','确认重启',true);
}
function _ackEventAndReload(id){ackEvent(id);loadEventsCenterModal();}

async function dashboard(range){
  const _seq=_navSeq;
  // range 兼容旧的全局参数 (默认用 _dashRanges.pie)
  if(range && !window._dashInit){
    _dashRanges.cust=String(range);
    _dashRanges.tl=String(range);
    _dashRanges.pie=String(range);
    window._dashInit=true;
  }
  const custRange=_dashRanges.cust;
  const tlRange=_dashRanges.tl;
  const pieRange=_dashRanges.pie;
  const[dR,hR,cR]=await Promise.all([
    api('/dashboard?range_recent='+custRange+'&range_tl='+tlRange+'&range_pie='+pieRange),
    api('/system/health').catch(()=>({data:{}})),
    api('/customers/push-summary?days='+custRange).catch(()=>({data:{items:[]}}))
  ]);
  _dashData=dR.data||{};
  // 调度器状态直接复用 /system/health 返回的那份,省一次 /settings/scheduler 请求
  const d=dR.data||{},h=hR.data||{},sch=h.scheduler||{},custs=cR.data?.items||[];
  // 运维视角: 大字看成功率, 失败数单独标红
  const pt=d.push_today||{};
  const ptRate=(pt.total>0)?Math.round((pt.success||0)/pt.total*100):null;

  // 2026-09-02: 服务已运行时间 — process_start 是 UTC ISO,前端每秒重算
  if(d.process_start){ _processStartMs=new Date(d.process_start).getTime(); _tickUptime(); if(!_uptimeTimer){ _uptimeTimer=setInterval(_tickUptime,1000); } }

  // Session status line
  let sesLine='';
  if(d.session_detail&&d.session_detail.length){
    sesLine=d.session_detail.map(s=>{
      let hbIcon=s.heartbeat_status==='正常'?'💚':s.heartbeat_status==='过期'?'❤️‍🔥':s.heartbeat_status==='污染'?'🟠':'⚠️';
      let hbTime=s.last_heartbeat_at?fmtTZ(s.last_heartbeat_at,true):'—';
      return `${hbIcon} #${s.id} ${hbTime}(${s.heartbeat_count||0}次)`;
    }).join(' · ');
  }
  let sesWarn='';
  const _ss=d.session_status||{};
  const _cTotal=_ss.collect_total||0;
  // 2026-08-25: 顶部告警只在"无可用采集 session"时显示
  // 其他情况(部分污染/过期)由 Session 菜单自己的卡片提示,不再在仪表盘顶部打扰
  if(_cTotal===0){
    sesWarn=`<div class="info-bar" data-css="background:var(--danger-light);border:1px solid var(--danger-border);color:var(--danger-text);margin-bottom:14px;padding:10px 14px;border-radius:8px;font-size:13px">🚨 <strong>无可用采集 Session</strong> — 请前往 <a href="#" data-on-click="showPage" data-args='${_dataArgs('sessions')}'>Session 管理</a> 添加 PHPSESSID 才能采集<br><span class="muted-12">提示:如果之前已添加过 session,请检查 Session 菜单里 session 是否被标记为过期或污染,需要重新添加</span></div>`;
  }
  // 采集进度条改用 _showCollectBanner() (2026-08-21),仿 _showDiscBanner 风格:底部悬浮卡片 + 进度条
  // 这里不再单独控制 dashRunningBar,改由 dataPage 内 _collectPollTimer 调度。

  const mode_label={'quick':'⚡Quick','full':'🔍Full','':''};
  // 加载事件中心头部统计 + badge（无论面板是否展开都要显示）
  fetchEventsHeader();
  // Collect status
  let nextCol='—';
  let lastCol='—';
  if(sch.is_running && !sch.last_run){
    lastCol='采集中…';
    let now=new Date();now.setUTCHours(now.getUTCHours()+sch.interval_hours);
    nextCol=fmtTZ(now.toISOString().substring(0,19),true);
  } else if(sch.last_run){
    let d2=new Date(sch.last_run+'Z');d2.setUTCHours(d2.getUTCHours()+sch.interval_hours);
    nextCol=fmtTZ(d2.toISOString().substring(0,19),true);
    lastCol=fmtTZ(sch.last_run,true);
  }


  if(navStale(_seq))return;
  document.getElementById('app').innerHTML=
    sesWarn +
    `<div class="grid3">
      <div class="stat tint-info">
        <div class="num">${d.session_status?.collect_active||0}<span class="muted-14 fw500 mx4">/</span><span data-css="color:#5b21b6">${d.session_status?.discover_active||0}</span></div>
        <div class="label">活跃 Session <span data-css="color:var(--muted);font-weight:400">(采集 / 发现)</span></div>
      </div>
      <div class="stat tint-neutral"><div class="num">${d.total_snapshots||0}</div><div class="label">监控快照</div></div>
      <div class="stat ${sch.is_running?'tint-primary':(sch.enabled?'tint-success':'tint-danger')}" data-css="display:flex;flex-direction:column;justify-content:center;gap:8px;padding:14px 12px">
        <div data-css="display:flex;align-items:center;justify-content:center;gap:10px">
          <span data-css="display:inline-block;width:10px;height:10px;border-radius:50%;background:${sch.is_running?'var(--primary)':(sch.enabled?'var(--success)':'var(--danger)')};box-shadow:0 0 0 4px ${sch.is_running?'rgba(67,97,238,0.2)':(sch.enabled?'rgba(12,166,120,0.2)':'rgba(229,72,77,0.2)')};flex-shrink:0"></span>
          <span data-css="font-size:14px;font-weight:600;color:var(--text)">${sch.is_running?'采集中':(sch.enabled?'就绪':'已关闭')}</span>
        </div>
        <div data-css="font-size:11px;color:var(--muted);line-height:1.4">采集器状态</div>
      </div>
    </div>
    <div class="grid3 mt14">
      <div class="stat tint-primary">
        <div class="num" id="dashUptime">—</div>
        <div class="label">已运行时间</div>
      </div>
      <div class="stat ${d.product_total>0?(d.product_active===d.product_total?'tint-success':(d.product_active>0?'tint-warn':'tint-danger')):'tint-neutral'}">
        <div class="num">${d.product_active||0}<span class="muted-14 fw500 mx4">/</span><span class="text-muted">${d.product_total||0}</span></div>
        <div class="label">已启用产品 / 总数</div>
        ${d.product_total>0 && d.product_active!==d.product_total?`<div class="muted-11 mt6">未启用 <b data-css="color:var(--warn)">${d.product_total-d.product_active}</b></div>`:''}
      </div>
      <div class="stat ${pt.total>0?(pt.failed>0?'tint-warn':'tint-success'):'tint-neutral'}">
        <div class="num">${ptRate===null?'—':ptRate+'%'}</div>
        <div class="label">${pt.total>0?`今日推送 ${pt.total} 条`:'今日推送'}</div>
        ${pt.failed>0?`<div class="muted-11 mt6"><b class="text-danger">${pt.failed}</b> 失败</div>`:''}
      </div>
    </div>
    <div data-css="display:flex;flex-direction:column;gap:12px;margin-top:12px">
      <div class="info-bar info-bar-stat">
        <span class="info-seg info-seg-time">⏱️ 上次采集: <b>${lastCol}</b>${sch.last_mode?' <span class="info-tag">'+mode_label[sch.last_mode]+'</span>':''}<span id="dashProgress" data-css="display:none;margin-left:8px;font-size:12px;color:var(--primary);font-weight:600"></span></span>
        <span class="info-seg info-seg-next">⏭️ 下次采集: <b>${nextCol}</b></span>
        <span class="info-seg info-seg-week">📊 本周: <b class="info-num">${d.push_week?.total||0}</b>次 <b class="info-ok">${d.push_week?.success||0}✓</b> ${d.push_week?.failed>0?'<b class="info-err">'+d.push_week.failed+'✗</b>':('<b class="info-muted">0✗</b>')}</span>
      </div>
    </div>

    <div class="dash-grid" data-css="display:grid;grid-template-columns:1fr 1.5fr 1fr;grid-template-rows:auto 430px;gap:12px;margin-top:12px;align-items:stretch">
    <!-- Row 1 Col 1: 运维健康 -->
    <div class="card" data-css="grid-column:1;grid-row:1;display:flex;flex-direction:column;min-width:0">
      <div data-css="display:flex;align-items:center;gap:8px;padding:10px 16px;border-bottom:1px solid var(--border);background:rgba(67,97,238,0.04);border-radius:10px 10px 0 0;flex-shrink:0">
        <span data-css="color:var(--primary);font-size:13px">🩺 运维健康</span>
        <span data-css="margin-left:auto;display:flex;gap:6px;align-items:center;position:relative;z-index:1">
          <button class="btn btn-soft-primary h30-pill" data-on-click="triggerCollect">⏎ 手动采集</button>
          <button class="btn btn-soft-danger h30-pill" data-on-click="_confirmRestartService">🔄 重启服务</button>
        </span>
      </div>
      <div class="card-bd" data-css="flex:1;padding:8px 10px;display:flex;flex-wrap:wrap;gap:10px;align-content:flex-start;overflow-y:auto">
        ${h.product_health&&h.product_health.length>0?`<div data-css="min-width:160px;flex:1">
          <div class="metric-label">采集</div>
          <div data-css="display:flex;flex-direction:column;gap:2px">
            ${h.product_health.slice(0,12).map(p=>{
              const statusIcon=(p.health_status==='ok'||p.health_status==='正常')?'💚':p.health_status&&p.health_status!=='未知'&&p.health_status!=='ok'?'❌':'⚪';
              const last=p.last_collected_at?fmtTZ(p.last_collected_at,true).slice(11,16):'—';
              return `<div data-css="display:flex;align-items:center;gap:5px;font-size:11px;color:var(--gray-disabled)">
                <span class="shrink0">${statusIcon}</span>
                <span data-css="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(p.name||'未知')}</span>
                <span data-css="color:var(--gray-disabled);font-size:10px;flex-shrink:0">${last}</span>
              </div>`}).join('')}
          </div>
        </div>`:''}
        ${h.push_today?`<div data-css="min-width:110px">
          <div class="metric-label">推送</div>
          <div data-css="display:flex;gap:4px;flex-wrap:wrap">
            <span class="badge ${h.push_success_rate>=80?'badge-ok':h.push_success_rate>=50?'badge-warn':'badge-err'}">${h.push_success_rate||0}%</span>
            <span class="badge badge-grey">${h.push_today.total||0}条</span>
          </div>
          <div data-css="font-size:10px;color:var(--gray-disabled);margin-top:3px">成功 ${h.push_today.success||0} / 失败 ${h.push_today.failed||0}</div>
        </div>`:''}
        ${h.active_snapshots!==undefined&&h.active_snapshots!==null?`<div data-css="min-width:80px">
          <div class="metric-label">快照</div>
          <span class="badge badge-blue">${h.active_snapshots} 个</span>
        </div>`:''}
        ${h.异常日志&&h.异常日志.length>0?`<div class="min-w140">
          <div class="metric-label">异常 (2h内)</div>
          <div class="list-compact">
            ${h.异常日志.slice(0,3).map(l=>`<div data-css="font-size:10px;color:${l.level==='ERROR'?'var(--danger)':'var(--warn)'};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px" title="${escHtml(l.msg)}">${l.level==='ERROR'?'❌':'⚠️'} ${l.msg.slice(-50)}</div>`).join('')}
          </div>
        </div>`:''}
        ${h.queue?.overdue>0?`<div data-css="min-width:100px">
          <div class="metric-label">队列</div>
          <span class="badge badge-warn">⏰ 超时 ${h.queue.overdue} 条</span>
        </div>`:''}
        ${h.rate_limits?.bans&&h.rate_limits.bans.length>0?`<div data-css="min-width:130px">
          <div class="metric-label">限流</div>
          <div class="list-compact">
            ${h.rate_limits.bans.slice(0,3).map(b=>`<span class="badge badge-err badge-xs">🔒 ${b.key} (${b.remaining_seconds}s)</span>`).join('')}
          </div>
        </div>`:''}
      </div>
    </div>
    <!-- Row 1 Col 2: 事件中心 -->
    <div class="card" data-css="grid-column:2;grid-row:2;display:flex;flex-direction:column;min-width:0">
      <div class="card-hd" data-css="background:rgba(239,68,68,0.04);border-radius:10px 10px 0 0">
        <span class="text-danger">🔔 事件中心</span>
        <span id="eventsCenterCounts" data-css="margin-left:auto;display:flex;gap:4px;align-items:center;font-size:11px"></span>
        <button id="eventsCenterExpandBtn" class="btn btn-xs btn-soft-primary" data-on-click="openEventsCenterModal" data-css="margin-left:6px;flex-shrink:0">查看更多 ↗</button>
      </div>
      <div class="card-bd pane-scroll" id="eventsCenterBody">
        <div data-css="text-align:center;color:var(--muted);font-size:12px;padding:20px 0">—</div>
      </div>
    </div>
    <!-- Row 1 Col 3: 最近更新 -->
    <div class="card" data-css="grid-column:3;grid-row:1;display:flex;flex-direction:column;min-width:0">
      <div class="card-hd muted-11-shrink">📋 最近更新</div>
      <div class="card-bd pane-scroll">
        ${_recentPackageGroups(d.recent_deliveries||[]).map(g=>{
          const bad=g.failed>0, icon=bad?'✗':(g.sent>0?'✓':'·');
          const color=bad?'var(--danger)':(g.sent>0?'var(--success)':'var(--muted)');
          const tip=`推送 ${g.total} 次 · ${g.sent}✓ ${g.failed}✗\n${[...g.customers].join('、')||'—'}`;
          return `<div class="pill-tip" data-tip="${escHtml(tip)}" data-css="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12px;border-bottom:1px solid var(--gray-border);white-space:nowrap">
            <span data-css="color:${color};flex-shrink:0;font-size:13px">${icon}</span>
            <span data-css="flex:1;overflow:hidden;text-overflow:ellipsis">${escHtml(g.product||'—')} ${escHtml(g.ver||'')}${g.file?' ('+escHtml(g.file.slice(0,22))+')':''}</span>
            <span class="muted-11-shrink">${g.customers.size}客户</span>
            <span class="muted-11-shrink">${fmtTZ(g.sent_at,true)}</span>
          </div>`;
        }).join('')||'<div data-css="text-align:center;color:var(--muted);font-size:12px;padding:18px 0">暂无推送</div>'}
      </div>
    </div>

    <!-- Row 2 Col 1: 客户推送分布 -->
    <div class="card" data-css="grid-column:1;grid-row:2;display:flex;flex-direction:column;min-width:0;height:430px">
      <div class="card-hd status-row">
        <span>📨 客户推送分布</span>
        <div class="flex gap4 ml8" id="custRange">
          <button class="btn btn-xs btn-primary" data-on-click="setDashRange" data-args='${_dataArgs('cust', '7')}'>7天</button>
          <button class="btn btn-xs btn-outline" data-on-click="setDashRange" data-args='${_dataArgs('cust', '30')}'>30天</button>
          <button class="btn btn-xs btn-outline" data-on-click="setDashRange" data-args='${_dataArgs('cust', '90')}'>90天</button>
        </div>
      </div>
      <div class="card-bd pane-clip">
        <div class="pane-list" id="custPushTable"></div>
      </div>
    </div>

    <!-- Row 2 Col 2: 包发布趋势 (中间,与事件中心对齐) -->
    <div class="card" data-css="grid-column:2;grid-row:1;display:flex;flex-direction:column;min-width:0;height:430px">
      <div class="card-hd status-row">
        <span>📈 包发布趋势</span>
        <div class="flex gap4 ml8" id="tlRange">
          <button class="btn btn-xs btn-primary" data-on-click="setDashRange" data-args='${_dataArgs('tl', '7')}'>7天</button>
          <button class="btn btn-xs btn-outline" data-on-click="setDashRange" data-args='${_dataArgs('tl', '30')}'>30天</button>
          <button class="btn btn-xs btn-outline" data-on-click="setDashRange" data-args='${_dataArgs('tl', '90')}'>90天</button>
        </div>
      </div>
      <div class="card-bd" data-css="flex:1;padding:12px;box-sizing:border-box;display:flex;flex-direction:column;overflow:hidden">
        <canvas id="chartTimeline" width="700" height="200" data-css="width:100%;height:200px;display:block;flex-shrink:0"></canvas>
        <div id="tlLegend" data-css="display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;line-height:1.4;flex:1;overflow-y:auto;overflow-x:hidden;padding-right:4px"></div>
      </div>
    </div>

    <!-- Row 2 Col 3: 产品发布分布 -->
    <div class="card" data-css="grid-column:3;grid-row:2;display:flex;flex-direction:column;min-width:0;height:430px">
      <div class="card-hd status-row">
        <span>📊 产品发布分布</span>
        <div class="flex gap4 ml8" id="pieRange">
          <button class="btn btn-xs btn-primary" data-on-click="setDashRange" data-args='${_dataArgs('pie', '7')}'>7天</button>
          <button class="btn btn-xs btn-outline" data-on-click="setDashRange" data-args='${_dataArgs('pie', '30')}'>30天</button>
          <button class="btn btn-xs btn-outline" data-on-click="setDashRange" data-args='${_dataArgs('pie', '90')}'>90天</button>
        </div>
      </div>
      <div class="card-bd pane-clip">
        <div class="pane-list" id="prodDistTable"></div>
      </div>
    </div>
    </div>
    `;
    // 渲染图表
    window._tlData = d.timeline_stats || [];
    window._tlDays = parseInt(tlRange);
    // 从 product_stats 取所有产品列表（确保全0产品也被初始化）
    const allProds = (d.product_stats || []).map(p => p.name);
    if(!window._tlVisible) window._tlVisible = {};
    allProds.forEach(p => { if(window._tlVisible[p]===undefined) window._tlVisible[p]=true; });
    _renderProdDistTable(d.product_stats || []);
    // feat/customer-push-card 2026-09-02: 客户推送聚合中间卡片
    _renderCustPushTable(custs, custRange);
    _renderTimelineChart('chartTimeline', window._tlData, window._tlDays, window._tlVisible);
    _renderTlLegend(allProds, window._tlVisible);
    // 2026-08-26: 事件中心卡片数据已在 d.recent_events 里(dashboard 接口一次性返回)
    // 此时 #eventsCenterBody 已存在于 DOM 中,可以直接渲染
    if (typeof renderEventsCenterFromData === 'function') {
      renderEventsCenterFromData(d.recent_events);
    }
    // 高亮当前选中范围按钮
    document.querySelectorAll('#pieRange .btn, #tlRange .btn').forEach(b => {
      const v = b.textContent.trim();
      const rng = b.closest('#tlRange') ? _dashRanges.tl : _dashRanges.pie;
      b.className = 'btn btn-xs ' + (v === rng + '天' ? 'btn-primary' : 'btn-outline');
    });

  // Start/stop progress poll (must be in JS, not innerHTML script tag)
  if(sch.is_running){
    if(!window._dashPollTimer){pollDashProgress();window._dashPollTimer=setInterval(pollDashProgress,3000);}
  }else{
    if(window._dashPollTimer){clearInterval(window._dashPollTimer);window._dashPollTimer=null;}
  }

  // 启动事件中心头部轮询(每 30 秒,即使面板折叠也运行更新 badge)
  if(!window._eventsHeaderTimer){
    fetchEventsHeader();
    window._eventsHeaderTimer = setInterval(fetchEventsHeader, 30000);
  }
  if(!window._eventsRefreshTimer && document.getElementById('eventsPanel') && document.getElementById('eventsPanel').style.display !== 'none'){
    loadEvents();
    window._eventsRefreshTimer = setInterval(loadEvents, 15000);
  }
}

// ── 事件中心 ───────────────────────────────────────────
let _processStartMs=null, _uptimeTimer=null;
function _fmtUptime(ms){
  if(ms<0) ms=0;
  const s=Math.floor(ms/1000);
  const d=Math.floor(s/86400), h=Math.floor((s%86400)/3600), m=Math.floor((s%3600)/60), sec=s%60;
  if(d>0) return `${d}天${h}时${m}分`;
  if(h>0) return `${h}时${m}分${sec}秒`;
  return `${m}分${sec}秒`;
}
function _tickUptime(){
  const el=document.getElementById('dashUptime');
  if(!el || !_processStartMs) return;
  el.textContent = _fmtUptime(Date.now()-_processStartMs);
}

async function fetchEventsHeader(){
  try{
    const r = await api('/system/events?limit=1');
    if(r.code !== 0) return;
    const c = r.data?.unread_counts || {CRITICAL:0,ERROR:0,WARNING:0,unread_total:0};
    const badge = document.getElementById('eventsUnreadBadge');
    const counts = document.getElementById('eventsCounts');
    if(badge){
      if(c.unread_total > 0){
        badge.textContent = c.unread_total;
        badge.style.display = 'inline-block';
      } else {
        badge.style.display = 'none';
      }
    }
    if(counts){
      const parts = [];
      if(c.CRITICAL) parts.push(`🔴 ${c.CRITICAL}`);
      if(c.ERROR) parts.push(`🟠 ${c.ERROR}`);
      if(c.WARNING) parts.push(`⚠️ ${c.WARNING}`);
      counts.textContent = parts.length ? parts.join(' · ') + ' 未读' : '✅ 全部已读';
    }
    // 2026-08-26: 同步刷新仪表盘事件中心卡片的"未读徽章"
    const centerCounts = document.getElementById('eventsCenterCounts');
    if (centerCounts) {
      const parts2 = [];
      if(c.CRITICAL) parts2.push(`<span data-css="background:var(--danger-light);color:var(--danger-text);padding:1px 6px;border-radius:8px;font-weight:600">🔴${c.CRITICAL}</span>`);
      if(c.ERROR) parts2.push(`<span class="chip-warn">🟠${c.ERROR}</span>`);
      if(c.WARNING) parts2.push(`<span class="chip-warn">⚠️${c.WARNING}</span>`);
      centerCounts.innerHTML = parts2.length ? parts2.join(' ') : '<span class="text-success">✅</span>';
    }
  }catch(e){}
}

// 仪表盘事件中心卡片 — 同步渲染(数据从 /api/dashboard 拿,不另外 ajax)
// 之前走 ajax /api/system/events 太慢,现在跟 dashboard 一起返回

// 2026-09-01: 弹窗表格点已读后,同步刷新仪表盘事件中心卡片(去掉对应行 NEW)
// 只在当前是仪表盘页面 + eventsCenterBody 存在时才走,避免无谓请求
// 加时间戳避免浏览器 GET 缓存返回老数据(浏览器对 /dashboard?range=7 默认会缓存)
async function refreshEventsCenterCardIfVisible(){
  const body = document.getElementById('eventsCenterBody');
  if(!body) return;  // 当前不在仪表盘,跳过
  try{
    // 只查事件表,不再为了刷新事件卡把 7/30/90 天统计全部重算一遍
    const r = await api('/dashboard?events_only=1&_t=' + Date.now());
    if(r.code !== 0) return;
    const events = r.data && r.data.recent_events;
    if(events && typeof renderEventsCenterFromData === 'function'){
      renderEventsCenterFromData(events);
    }
  }catch(e){}
}

function renderEventsCenterFromData(events){
  const body = document.getElementById('eventsCenterBody');
  if (!body) return;
  if (!events || !events.length) {
    body.innerHTML = '<div class="empty-success">✅ 系统正常,无事件</div>';
    return;
  }
  // 2026-09-01: 仪表盘卡片现在宽度足够,直接按弹窗表格格式渲染
  // 弹窗表格已有事件中心全部字段(时间/严重度/类型/标题/产品/渠道/状态)
  // 这里去掉 modalOnly 的"已读"按钮列,直接显示 NEW/已读
  body.style.padding = '0';
  body.innerHTML = renderEventsCenterTable(events);
}

// 仪表盘卡片专用表格(去掉"已读"按钮,仅显示 NEW/已读状态标签;列宽按卡片适配)
function renderEventsCenterTable(events){
  const head = `<table class="table-base table-fixed">
    <colgroup>
      <col class="w160">
      <col class="w100">
      <col class="w130">
      <col>
      <col class="w140">
      <col class="w100">
      <col class="w60">
    </colgroup>
    <thead class="evt-thead">
      <tr class="evt-head-row">
        <th class="evt-th">时间</th>
        <th class="evt-th">严重度</th>
        <th class="evt-th">类型</th>
        <th class="evt-th">标题</th>
        <th class="evt-th">产品 / URL</th>
        <th class="evt-th">渠道</th>
        <th class="evt-th ta-center">状态</th>
      </tr>
    </thead>
    <tbody>`;
  const rows = events.map(renderEventsCenterTableRow).join('');
  return head + rows + '</tbody></table>';
}

// 仪表盘卡片行 — 与弹窗 renderEventsTableRow 类似但用不同 detail ID 前缀(eventCardDetail-)
// 避免和弹窗的 eventTableDetail- 冲突;状态列只显示 NEW/已读 不显示按钮
function _deliveryErrorItemsHtml(m){
  const items = ((m && m.items) || []).filter(it => it && it.queue_id);
  if(!items.length) return '';
  return '<div class="mb8">'+items.map(it=>{
    const owner = [it.customer_name, it.rule_name].filter(Boolean).join(' / ') || ('规则 '+it.rule_id);
    const prod = [it.product_name, it.version].filter(Boolean).join(' ');
    return `<div data-css="display:flex;align-items:center;gap:8px;margin-top:4px;flex-wrap:wrap">
      <span class="text-danger">✗</span>
      <span>队列 #${it.queue_id} · ${escHtml(owner)}${prod?' '+escHtml(prod):''}</span>
      <span class="text-muted">${escHtml(String(it.reason||''))} ${escHtml(String(it.error||'').slice(0,80))}</span>
      <button class="btn btn-xs btn-soft-primary" data-on-click="retryDelivery" data-stop data-args='${_dataArgs(Number(it.queue_id), '$el')}'>重新入队</button>
    </div>`;
  }).join('')+'</div>';
}

async function retryDelivery(queueId, btn){
  if(btn){btn.disabled=true;btn.textContent='提交中...';}
  const r=await api('/history/queue/'+queueId+'/retry',{method:'POST'});
  if(r.code===0){
    toast(r.message||'已重新入队');
    if(btn)btn.textContent='已入队';
    if(document.getElementById('eventsCenterModal'))renderEventsCenter();
    else loadEvents();
  }else{
    toast(r.message||'重新入队失败','error');
    if(btn){btn.disabled=false;btn.textContent='重新入队';}
  }
}

function renderEventsCenterTableRow(e){
  let m = {};
  if(typeof e.message === 'string'){
    try { m = JSON.parse(e.message); } catch(_) { m = {}; }
  } else if(typeof e.message === 'object' && e.message){
    m = e.message;
  }
  const t = e.event_type || '';
  const sevIcon = {CRITICAL:'🔴',ERROR:'🟠',WARNING:'⚠️',INFO:'ℹ️'}[e.severity] || '⚪';
  const sevColor = {CRITICAL:'#dc2626',ERROR:'#ea580c',WARNING:'#ca8a04',INFO:'#6b7280'}[e.severity] || '#6b7280';
  // 标题提取 — 与弹窗表格保持完全一致
  let title = '';
  if(m.title) title = String(m.title);
  else if(t === 'session_error') title = (m.username?`Session 污染 (${m.username})`:'Session 污染')+': '+(m.reason||'未知');
  else if(t === 'session_expired') title = (m.session_id?`Session ${m.session_id} 失效`:'Session 失效')+': '+(m.reason||'');
  else if(t === 'log_error') title = (m.error_type||'日志异常')+(m.keyword?` / ${m.keyword}`:'');
  else if(t === 'collection_summary') title = m.ok===false ? '⚠️ 采集异常' : `采集完成 新增 ${m.total_new||0} / 回滚 ${m.total_rollback||0}`;
  else if(t === 'push_summary') title = m.success>0 ? `推送 ${m.success} 成功` : (m.total?`推送 ${m.total} 条`:'本次无推送');
  else if(t === 'push_success') title = (m.product_name||'')+(m.file_name?` ${m.file_name}`:'')+(m.success===false?' 推送失败':' 推送成功');
  else if(t === 'login_bruteforce') title = `IP ${m.ip||'?'} 失败 ${m.count||0} 次`;
  else if(t === 'channel_error') title = '渠道体检失败: '+(m.failures||[]).length+' 个'+((m.failures&&m.failures[0])?` (${m.failures[0].channel_name||m.failures[0].channel_type||''})`:'');
  else if(typeof e.message === 'string') title = e.message.split('\n')[0].slice(0, 100);
  else title = JSON.stringify(m).slice(0, 80);
  const isUnread = (e.severity==='CRITICAL'||e.severity==='ERROR'||e.severity==='WARNING') && !e.acked_at;
  const productCell = e.product_name
    ? `<div>${escHtml(e.product_name)}</div>`
    + (e.source_url ? `<div class="muted-10 mt2 ellipsis" title="${escHtml(e.source_url)}">${escHtml(e.source_url)}</div>` : '')
    : (e.source_url ? `<div class="muted-10 ellipsis" title="${escHtml(e.source_url)}">${escHtml(e.source_url)}</div>` : '<span class="text-muted">—</span>');
  const channelCell = e.channel_type
    ? `<div>${escHtml(e.channel_type)}</div>`
    + (e.channel_id ? `<div class="muted-10 mt2">#${e.channel_id}</div>` : '')
    + (e.customer_id ? `<div class="muted-10">客户 ${e.customer_id}</div>` : '')
    : '<span class="text-muted">—</span>';
  // 详情面板 — 用 eventCardDetail- 前缀避免跟弹窗 eventTableDetail- 撞 ID
  const detailId = 'eventCardDetail-' + e.id;
  const detailJson = escHtml(JSON.stringify(m, null, 2));
  const detail = `<tr id="${detailId}" data-css="display:none;background:var(--gray-bg)"><td colspan="7" data-css="padding:10px 12px;font-size:11px;line-height:1.6;border-top:1px solid var(--border)">
    <div class="flex gap24 wrap mb8">
      <span><b>event_type:</b> ${escHtml(t)}</span>
      <span><b>severity:</b> <span data-css="color:${sevColor}">${escHtml(e.severity)}</span></span>
      ${e.is_rollback?'<span><b>回滚事件</b></span>':''}
      ${e.rule_id?`<span><b>rule_id:</b> ${e.rule_id}</span>`:''}
      ${m.username?`<span><b>用户名:</b> ${escHtml(m.username)}</span>`:''}
      ${m.reason?`<span><b>原因:</b> ${escHtml(String(m.reason))}</span>`:''}
      ${m.source?`<span><b>来源:</b> ${escHtml(m.source)}</span>`:''}
      ${m.ip?`<span><b>IP:</b> ${escHtml(m.ip)}</span>`:''}
      ${m.count?`<span><b>失败次数:</b> ${m.count}</span>`:''}
    </div>
    ${t==='delivery_error'?_deliveryErrorItemsHtml(m):''}
    <details>
      <summary class="pointer text-muted no-select">📦 原始 message JSON</summary>
      <pre data-css="margin:6px 0 0;padding:8px;background:var(--card);border-radius:4px;font-family:monospace;font-size:11px;line-height:1.5;white-space:pre-wrap;word-break:break-all;color:var(--muted);max-height:200px;overflow-y:auto">${detailJson}</pre>
    </details>
  </td></tr>`;
  let rowStyle = 'border-bottom:1px solid var(--gray-border);cursor:pointer';
  if(isUnread){
    if(e.severity==='CRITICAL'){
      rowStyle += ';background:#fef2f2;border-left:4px solid #dc2626';
    } else if(e.severity==='ERROR'){
      rowStyle += ';background:#fff7ed;border-left:3px solid #ea580c';
    } else if(e.severity==='WARNING'){
      rowStyle += ';border-left:2px solid #ca8a04';
    }
  }
  return `<tr data-css="${rowStyle}" data-on-click="toggleEventsCenterCardRow" data-args='${_dataArgs(e.id)}' title="点击展开详情">
    <td data-css="padding:6px 8px;color:var(--muted);font-family:monospace;font-size:11px;white-space:nowrap">${escHtml(serverTime(e.created_at, true))}</td>
    <td data-css="padding:6px"><span data-css="display:inline-block;padding:2px 6px;border-radius:4px;background:${sevColor}1a;color:${sevColor};font-weight:600;font-size:11px;white-space:nowrap">${sevIcon} ${escHtml(e.severity)}</span></td>
    <td data-css="padding:6px;color:var(--muted);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(t)}">${escHtml(t)}</td>
    <td data-css="padding:6px;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(title)}">${escHtml(title)}</td>
    <td class="p6 text-body fs11">${productCell}</td>
    <td class="p6 text-body fs11">${channelCell}</td>
    <td data-css="padding:6px;text-align:center;font-size:10px">
      ${isUnread
        ? '<span data-css="background:var(--danger);color:#fff;padding:1px 6px;border-radius:4px">NEW</span>'
        : '<span data-css="background:#f3e8ff;color:#7c3aed;padding:1px 6px;border-radius:4px">已读</span>'}</td>
  </tr>${detail}`;
}

// 仪表盘卡片行的展开/收起
function toggleEventsCenterCardRow(eventId){
  const el = document.getElementById('eventCardDetail-' + eventId);
  if (!el) return;
  el.style.display = (el.style.display === 'none' || !el.style.display) ? 'table-row' : 'none';
}

// 旧版本异步拉(保留兼容,但 dashboard 不再调用)

// ═══════════════════════════════════════════════════════════════
// 事件中心弹窗 — 表格 + 分页 + 过滤器
// 2026-09-01: 改成表格形式,字段作为列;按 limit/offset 分页
// 数据源 /api/system/events,message 字段前端 JSON.parse 后渲染
// ═══════════════════════════════════════════════════════════════
const _EVENTS_PER_PAGE = 50;

function openEventsCenterModal(){
  let m = document.getElementById('eventsCenterModal');
  if(!m){
    m = document.createElement('div');
    m.id = 'eventsCenterModal';
    m.innerHTML = `
      <div class="modal-overlay" data-on-click="closeEventsCenterModal" data-self>
        <div class="card" data-css="width:960px;max-width:96vw;max-height:90vh;display:flex;flex-direction:column">
          <div class="card-hd" data-css="display:flex;align-items:center">
            <span class="text-danger">🔔 事件中心</span>
            <span id="eventsCenterModalCount" data-css="margin-left:8px;color:var(--muted);font-size:11px;font-weight:400"></span>
            <span data-on-click="closeEventsCenterModal" data-css="margin-left:auto;cursor:pointer;color:var(--gray-muted);font-size:18px;line-height:1;padding:0 4px">×</span>
          </div>
          <div data-css="display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:10px 16px;border-bottom:1px solid var(--gray-border);background:var(--gray-bg);font-size:12px">
            <span class="text-muted">严重度</span>
            <select id="eventsCenterModalSev" class="sel-sm dd" data-on-change="eventsCenterGoPage" data-args='${_dataArgs(0)}'>
              <option value="">全部</option>
              <option value="CRITICAL">🔴 CRITICAL</option>
              <option value="ERROR">🟠 ERROR</option>
              <option value="WARNING">⚠️ WARNING</option>
              <option value="INFO">ℹ️ INFO</option>
            </select>
            <span class="text-muted">时间范围</span>
            <select id="eventsCenterModalHours" class="sel-sm dd" data-on-change="eventsCenterGoPage" data-args='${_dataArgs(0)}'>
              <option value="24">近 24 小时</option>
              <option value="168" selected>近 7 天</option>
              <option value="720">近 30 天</option>
              <option value="2160">近 90 天</option>
            </select>
            <button class="btn btn-xs btn-outline ml-auto" data-on-click="eventsCenterAckCurrent" title="按当前过滤器批量标记为已读;无过滤时标全部 4 个 severity(含 INFO)">一键已读</button>
            <button class="btn btn-xs btn-outline" data-on-click="loadEventsCenterModal">🔄 刷新</button>
          </div>
          <div class="card-bd" id="eventsCenterModalBody" data-css="flex:1;overflow:auto;padding:0">
            <div class="p40 ta-center muted-13">加载中…</div>
          </div>
          <div class="card-bd" data-css="border-top:1px solid var(--gray-border);padding:8px 16px;display:flex;align-items:center;gap:12px;font-size:12px">
            <span class="text-muted" id="eventsCenterModalPagerInfo"></span>
            <div data-css="margin-left:auto;display:flex;gap:6px;align-items:center">
              <button id="eventsCenterModalPrevBtn" class="btn btn-xs btn-outline" data-on-click="eventsCenterPrevPage">← 上一页</button>
              <span id="eventsCenterModalPageInfo" data-css="color:var(--muted);font-size:11px;min-width:60px;text-align:center"></span>
              <button id="eventsCenterModalNextBtn" class="btn btn-xs btn-outline" data-on-click="eventsCenterNextPage">下一页 →</button>
            </div>
          </div>
          <div class="card-bd" data-css="border-top:1px solid var(--gray-border);padding:8px 16px;display:flex;justify-content:flex-end">
            <button class="btn btn-outline" data-on-click="closeEventsCenterModal">关闭</button>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(m);
  }
  m.style.display = 'block';
  eventsCenterState.offset = 0;
  loadEventsCenterModal();
  if(!window._eventsCenterModalTimer) window._eventsCenterModalTimer = setInterval(()=>{ if(document.getElementById('eventsCenterModal').style.display!=='none') loadEventsCenterModal(); }, 30000);
}

// 分页状态(全局,避免每次重置)
window.eventsCenterState = window.eventsCenterState || { offset: 0, total: 0, loadedOnce: false };

function closeEventsCenterModal(){
  const m = document.getElementById('eventsCenterModal');
  if(m) m.style.display = 'none';
  if(window._eventsCenterModalTimer){clearInterval(window._eventsCenterModalTimer); window._eventsCenterModalTimer=null;}
}

function eventsCenterGoPage(offset){
  eventsCenterState.offset = Math.max(0, offset);
  loadEventsCenterModal();
}

function eventsCenterPrevPage(){
  eventsCenterGoPage(eventsCenterState.offset - _EVENTS_PER_PAGE);
}

function eventsCenterNextPage(){
  if(eventsCenterState.offset + _EVENTS_PER_PAGE < eventsCenterState.total){
    eventsCenterGoPage(eventsCenterState.offset + _EVENTS_PER_PAGE);
  }
}

async function loadEventsCenterModal(){
  const body = document.getElementById('eventsCenterModalBody');
  const countEl = document.getElementById('eventsCenterModalCount');
  const pagerInfo = document.getElementById('eventsCenterModalPagerInfo');
  const pageInfo = document.getElementById('eventsCenterModalPageInfo');
  const prevBtn = document.getElementById('eventsCenterModalPrevBtn');
  const nextBtn = document.getElementById('eventsCenterModalNextBtn');
  if(!body) return;
  const sev = document.getElementById('eventsCenterModalSev').value;
  const hrs = document.getElementById('eventsCenterModalHours').value || '168';
  const offset = eventsCenterState.offset || 0;
  const params = new URLSearchParams({
    limit: String(_EVENTS_PER_PAGE),
    offset: String(offset),
    since_hours: hrs
  });
  if(sev) params.set('severity', sev);
  // 第一次加载才显示"加载中",后续刷新不闪(避免 AP-127)
  if(!eventsCenterState.loadedOnce){
    body.innerHTML = '<div class="p40 ta-center muted-13">加载中…</div>';
  }
  const r = await api('/system/events?' + params.toString());
  // DOM 守卫:modal 可能已关闭(避免写已卸载元素)
  if(document.getElementById('eventsCenterModalBody') !== body) return;
  if(r.code !== 0){
    body.innerHTML = '<div data-css="padding:40px;text-align:center;color:var(--danger);font-size:13px">加载失败: '+escHtml(r.message||'未知错误')+'</div>';
    return;
  }
  const data = r.data || {};
  const events = data.events || [];
  const total = data.total || 0;
  eventsCenterState.total = total;
  if(!events.length){
    body.innerHTML = '<div data-css="padding:60px 20px;text-align:center;color:var(--success);font-size:14px">✅ 当前条件下无事件</div>';
    if(countEl) countEl.textContent = '(共 0 条)';
    if(pagerInfo) pagerInfo.textContent = '';
    if(pageInfo) pageInfo.textContent = '0 / 0';
    if(prevBtn) prevBtn.disabled = true;
    if(nextBtn) nextBtn.disabled = true;
    eventsCenterState.loadedOnce = true;
    return;
  }
  body.innerHTML = renderEventsTable(events);
  // 计数 + 分页信息
  const start = offset + 1;
  const end = Math.min(offset + events.length, total);
  if(countEl) countEl.textContent = '(共 ' + total + ' 条)';
  if(pagerInfo) pagerInfo.textContent = '第 ' + start + '-' + end + ' 条 / 共 ' + total + ' 条';
  const totalPages = Math.max(1, Math.ceil(total / _EVENTS_PER_PAGE));
  const curPage = Math.floor(offset / _EVENTS_PER_PAGE) + 1;
  if(pageInfo) pageInfo.textContent = curPage + ' / ' + totalPages;
  if(prevBtn) prevBtn.disabled = (offset <= 0);
  if(nextBtn) nextBtn.disabled = (offset + _EVENTS_PER_PAGE >= total);
  eventsCenterState.loadedOnce = true;
}

// 表格渲染 — 字段作为表头
function renderEventsTable(events){
  const head = `<table class="table-base table-fixed">
    <colgroup>
      <col class="w140">
      <col class="w80">
      <col class="w130">
      <col>
      <col class="w140">
      <col class="w110">
      <col class="w70">
    </colgroup>
    <thead class="evt-thead">
      <tr class="evt-head-row">
        <th class="evt-th">时间</th>
        <th class="evt-th">严重度</th>
        <th class="evt-th">类型</th>
        <th class="evt-th">标题</th>
        <th class="evt-th">产品 / URL</th>
        <th class="evt-th">渠道</th>
        <th class="evt-th ta-center">状态</th>
      </tr>
    </thead>
    <tbody>`;
  const rows = events.map(renderEventsTableRow).join('');
  return head + rows + '</tbody></table>';
}

function renderEventsTableRow(e){
  // message parse(JSON 字符串 → dict)
  let m = {};
  if(typeof e.message === 'string'){
    try { m = JSON.parse(e.message); } catch(_) { m = {}; }
  } else if(typeof e.message === 'object' && e.message){
    m = e.message;
  }
  const t = e.event_type || '';
  const sevIcon = {CRITICAL:'🔴',ERROR:'🟠',WARNING:'⚠️',INFO:'ℹ️'}[e.severity] || '⚪';
  const sevColor = {CRITICAL:'#dc2626',ERROR:'#ea580c',WARNING:'#ca8a04',INFO:'#6b7280'}[e.severity] || '#6b7280';
  // 标题提取
  let title = '';
  if(m.title) title = String(m.title);
  else if(t === 'session_error') title = (m.username?`Session 污染 (${m.username})`:'Session 污染')+': '+(m.reason||'未知');
  else if(t === 'session_expired') title = (m.session_id?`Session ${m.session_id} 失效`:'Session 失效')+': '+(m.reason||'');
  else if(t === 'log_error') title = (m.error_type||'日志异常')+(m.keyword?` / ${m.keyword}`:'');
  else if(t === 'collection_summary') title = m.ok===false ? '⚠️ 采集异常' : `采集完成 新增 ${m.total_new||0} / 回滚 ${m.total_rollback||0}`;
  else if(t === 'push_summary') title = m.success>0 ? `推送 ${m.success} 成功` : (m.total?`推送 ${m.total} 条`:'本次无推送');
  else if(t === 'push_success') title = (m.product_name||'')+(m.file_name?` ${m.file_name}`:'')+(m.success===false?' 推送失败':' 推送成功');
  else if(t === 'login_bruteforce') title = `IP ${m.ip||'?'} 失败 ${m.count||0} 次`;
  else if(t === 'channel_error') title = '渠道体检失败: '+(m.failures||[]).length+' 个'+((m.failures&&m.failures[0])?` (${m.failures[0].channel_name||m.failures[0].channel_type||''})`:'');
  else if(typeof e.message === 'string') title = e.message.split('\n')[0].slice(0, 100);
  else title = JSON.stringify(m).slice(0, 80);
  const isUnread = (e.severity==='CRITICAL'||e.severity==='ERROR'||e.severity==='WARNING') && !e.acked_at;
  const productCell = e.product_name
    ? `<div>${escHtml(e.product_name)}</div>`
    + (e.source_url ? `<div class="muted-10 mt2 ellipsis" title="${escHtml(e.source_url)}">${escHtml(e.source_url)}</div>` : '')
    : (e.source_url ? `<div class="muted-10 ellipsis" title="${escHtml(e.source_url)}">${escHtml(e.source_url)}</div>` : '<span class="text-muted">—</span>');
  const channelCell = e.channel_type
    ? `<div>${escHtml(e.channel_type)}</div>`
    + (e.channel_id ? `<div class="muted-10 mt2">#${e.channel_id}</div>` : '')
    + (e.customer_id ? `<div class="muted-10">客户 ${e.customer_id}</div>` : '')
    : '<span class="text-muted">—</span>';
  // 详情面板 — 用 eventTableDetail- 前缀避免跟 dashboard 卡片的 eventDetail- 撞 ID(AP-133)
  const detailId = 'eventTableDetail-' + e.id;
  const detailJson = escHtml(JSON.stringify(m, null, 2));
  const detail = `<tr id="${detailId}" data-css="display:none;background:var(--gray-bg)"><td colspan="7" data-css="padding:12px 16px;font-size:11px;line-height:1.6;border-top:1px solid var(--border)">
    <div class="flex gap24 wrap mb8">
      <span><b>event_type:</b> ${escHtml(t)}</span>
      <span><b>severity:</b> <span data-css="color:${sevColor}">${escHtml(e.severity)}</span></span>
      ${e.is_rollback?'<span><b>回滚事件</b></span>':''}
      ${e.rule_id?`<span><b>rule_id:</b> ${e.rule_id}</span>`:''}
      ${m.username?`<span><b>用户名:</b> ${escHtml(m.username)}</span>`:''}
      ${m.reason?`<span><b>原因:</b> ${escHtml(String(m.reason))}</span>`:''}
      ${m.source?`<span><b>来源:</b> ${escHtml(m.source)}</span>`:''}
      ${m.ip?`<span><b>IP:</b> ${escHtml(m.ip)}</span>`:''}
      ${m.count?`<span><b>失败次数:</b> ${m.count}</span>`:''}
    </div>
    ${t==='delivery_error'?_deliveryErrorItemsHtml(m):''}
    <details>
      <summary class="pointer text-muted no-select">📦 原始 message JSON</summary>
      <pre data-css="margin:6px 0 0;padding:8px;background:var(--card);border-radius:4px;font-family:monospace;font-size:11px;line-height:1.5;white-space:pre-wrap;word-break:break-all;color:var(--muted);max-height:240px;overflow-y:auto">${detailJson}</pre>
    </details>
  </td></tr>`;
  // 重要事件视觉强化:CRITICAL 未读加左侧粗红条 + 浅红背景;ERROR 橙条;INFO/WARNING 普通
  let rowStyle = 'border-bottom:1px solid var(--gray-border);cursor:pointer';
  if(isUnread){
    if(e.severity==='CRITICAL'){
      rowStyle += ';background:#fef2f2;border-left:4px solid #dc2626';
    } else if(e.severity==='ERROR'){
      rowStyle += ';background:#fff7ed;border-left:3px solid #ea580c';
    } else if(e.severity==='WARNING'){
      rowStyle += ';border-left:2px solid #ca8a04';
    }
  }
  return `<tr data-css="${rowStyle}" data-on-click="toggleEventsTableRow" data-args='${_dataArgs(e.id)}' title="点击展开详情">
    <td data-css="padding:8px 12px;color:var(--muted);font-family:monospace;font-size:11px;white-space:nowrap">${escHtml(serverTime(e.created_at, true))}</td>
    <td class="p8"><span data-css="display:inline-block;padding:2px 6px;border-radius:4px;background:${sevColor}1a;color:${sevColor};font-weight:600;font-size:11px;white-space:nowrap">${sevIcon} ${escHtml(e.severity)}</span></td>
    <td data-css="padding:8px;color:var(--muted);font-size:11px">${escHtml(t)}</td>
    <td data-css="padding:8px;color:var(--text);overflow:hidden;text-overflow:ellipsis">${escHtml(title)}</td>
    <td class="p8 text-body fs11">${productCell}</td>
    <td class="p8 text-body fs11">${channelCell}</td>
    <td data-css="padding:8px;text-align:center">
      ${isUnread
        ? `<button class="btn btn-xs btn-soft-success" data-on-click="_ackEventAndReload" data-stop data-args='${_dataArgs(e.id)}' data-css="font-size:10px;padding:1px 6px">已读</button>`
        : '<span data-css="background:#f3e8ff;color:#7c3aed;font-size:10px;padding:2px 8px;border-radius:4px">已读</span>'}</td>
  </tr>${detail}`;
}

// 表格行的展开/收起 — 用 table-row/none(display:block 对 tr 不合法)
function toggleEventsTableRow(eventId){
  const el = document.getElementById('eventTableDetail-' + eventId);
  if (!el) return;
  el.style.display = (el.style.display === 'none' || !el.style.display) ? 'table-row' : 'none';
}

// 一键已读 — 按当前过滤器(severity)批量标,无过滤时标全部 4 个 severity
// 用 confirmAct 弹项目级确认框(图标+标题+按钮,比原生 confirm/alert 好看),不阻塞;反馈用 toast
async function eventsCenterAckCurrent(){
  const sev = document.getElementById('eventsCenterModalSev').value;
  // 没选 severity 时默认标全部 4 个 severity(含 INFO,彻底清未读)
  const targets = sev ? [sev] : ['CRITICAL', 'ERROR', 'WARNING', 'INFO'];
  const label = sev ? `当前过滤(${sev})` : '全部未读事件(含 INFO)';
  await confirmAct(
    `标记${label}全部为已读?`,
    async () => {
      let ackCount = 0;
      try{
        for(const s of targets){
          const r = await api('/system/events/ack-all', {method:'POST', body: JSON.stringify({severity: s})});
          if(r.code !== 0){
            toast('标记失败 (' + s + '): ' + (r.message || '未知错误'), 'error');
            return;
          }
          ackCount++;
        }
        toast(`已标记 ${ackCount} 个 severity 类别为已读`, 'success');
        loadEventsCenterModal();
        fetchEventsHeader();
        refreshEventsCenterCardIfVisible();  // 同步刷新仪表盘事件中心卡片
      }catch(e){
        toast('网络错误', 'error');
      }
    },
    ' ',
    '标记为已读',
    false,  // 含 INFO 也是正常清理,不再警告
    `<div data-css="display:flex;align-items:center;gap:10px;padding:10px 14px;margin:12px 0;background:#f3e8ff;border:1px solid #e9d5ff;border-radius:8px">
       <span data-css="font-size:20px;line-height:1">✓</span>
       <span data-css="font-size:12px;color:#7c3aed;line-height:1.5">将标记当前过滤条件下 <b>${targets.length}</b> 个 severity 类别(${targets.join(' / ')})的全部未读事件</span>
     </div>`
  );
}

async function renderEventsCenter(){
  const body = document.getElementById('eventsCenterBody');
  if (!body) return;
  try{
    const r = await api('/system/events?limit=10');
    if (document.getElementById('eventsCenterBody') !== body) return;
    if (r.code !== 0) {
      body.innerHTML = `<div data-css="text-align:center;color:var(--muted);font-size:12px;padding:16px 0">加载失败,点击 <a href="#" data-on-click="renderEventsCenter" data-prevent>重试</a></div>`;
      return;
    }
    const events = r.data?.events || [];
    if (!events.length) {
      body.innerHTML = '<div class="empty-success">✅ 系统正常,无事件</div>';
      return;
    }
    renderEventsCenterFromData(events);
  }catch(e){
    if (document.getElementById('eventsCenterBody') === body) {
      body.innerHTML = `<div data-css="text-align:center;color:var(--danger);font-size:12px;padding:16px 0">网络错误</div>`;
    }
  }
}

async function loadEvents(){
  if(!document.getElementById('eventsList')) return;
  const sev = document.getElementById('eventsSevFilter').value;
  const typ = document.getElementById('eventsTypeFilter').value;
  const hrs = document.getElementById('eventsHoursFilter').value;
  const params = new URLSearchParams({limit:'100', since_hours:hrs});
  if(sev) params.set('severity', sev);
  if(typ) params.set('event_type', typ);
  try{
    const r = await api('/system/events?' + params.toString());
    const list = document.getElementById('eventsList');
    if(r.code !== 0){list.innerHTML='<div data-css="padding:20px;text-align:center;color:var(--muted);font-size:12px">加载失败: '+escHtml(r.message||'未知错误')+'</div>';console.error('[events] API error', r);return;}
    const events = r.data?.events || [];
    if(!events.length){
      list.innerHTML='<div data-css="padding:24px;text-align:center;color:var(--muted);font-size:13px">✅ 当前条件下无事件</div>';
      return;
    }
    list.innerHTML = events.map(renderEvent).join('');
  }catch(e){
    document.getElementById('eventsList').innerHTML='<div data-css="padding:20px;text-align:center;color:var(--danger);font-size:12px">网络错误</div>';
  }
}

function renderEvent(e){
  const sevIcon = {CRITICAL:'🔴',ERROR:'🟠',WARNING:'⚠️',INFO:'ℹ️'}[e.severity] || '⚪';
  const sevColor = {CRITICAL:'#dc2626',ERROR:'#ea580c',WARNING:'#ca8a04',INFO:'#6b7280'}[e.severity] || '#6b7280';
  const sevBg = {CRITICAL:'#fef2f2',ERROR:'#fff7ed',WARNING:'#fefce8',INFO:'#f9fafb'}[e.severity] || '#f9fafb';
  const isUnread = (e.severity === 'CRITICAL' || e.severity === 'ERROR' || e.severity === 'WARNING') && !e.acked_at;
  const ackBtn = isUnread ? `<button class="btn btn-xs btn-soft-success ml8" data-on-click="ackEvent" data-stop data-args='${_dataArgs(e.id)}'>✓ 已读</button>` : '';
  return `<div data-css="display:flex;align-items:flex-start;gap:10px;padding:10px 14px;border-bottom:1px solid var(--gray-bg);background:${isUnread?sevBg:'transparent'};${isUnread?'border-left:3px solid '+sevColor:''}cursor:pointer" data-on-click="toggleEventDetail" data-args='${_dataArgs(e.id)}'>
    <span data-css="font-size:14px;flex-shrink:0;margin-top:1px">${sevIcon}</span>
    <div class="flex1">
      <div data-css="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span data-css="font-weight:600;color:${sevColor};font-size:12px">${escHtml(e.severity)}</span>
        <span class="muted-11">${escHtml(e.event_type)}</span>
        ${e.product_name ? `<span class="text-primary fs11">${escHtml(e.product_name)}</span>` : ''}
        <span data-css="color:var(--muted);font-size:11px;margin-left:auto">${escHtml(serverTime(e.created_at, true))}</span>
      </div>
      <div data-css="font-size:12px;color:var(--text);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(e.message || '')}</div>
      <div id="eventDetail${e.id}" data-css="display:none;margin-top:8px;padding:8px;background:var(--gray-bg);border-radius:4px;font-size:11px;color:var(--muted)">
        ${e.source_url ? `<div><b>URL:</b> ${escHtml(e.source_url)}</div>` : ''}
        ${e.rule_id ? `<div><b>规则 ID:</b> ${e.rule_id}</div>` : ''}
        ${e.channel_id ? `<div><b>渠道 ID:</b> ${e.channel_id}</div>` : ''}
        ${e.customer_id ? `<div><b>客户 ID:</b> ${e.customer_id}</div>` : ''}
        ${e.is_rollback ? '<div><b>回滚事件</b></div>' : ''}
        <div><b>事件 ID:</b> ${e.id}</div>
        ${e.acked_at ? `<div><b>已确认:</b> ${escHtml(serverTime(e.acked_at, true))}</div>` : ''}
      </div>
    </div>
    ${ackBtn}
  </div>`;
}

function toggleEventDetail(id){
  const el = document.getElementById('eventDetail'+id);
  if(el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

function toggleEventsPanel(){
  const panel = document.getElementById('eventsPanel');
  const btn = document.getElementById('eventsExpandBtn');
  if(!panel || !btn) return;
  const showing = panel.style.display !== 'none';
  panel.style.display = showing ? 'none' : 'block';
  btn.textContent = showing ? '展开' : '收起';
  if(!showing) loadEvents();
  // 折叠时停轮询,展开时启动
  if(showing){
    if(window._eventsRefreshTimer){clearInterval(window._eventsRefreshTimer);window._eventsRefreshTimer=null;}
  } else {
    if(!window._eventsRefreshTimer) window._eventsRefreshTimer = setInterval(loadEvents, 15000);
  }
}

async function ackEvent(id){
  try{
    await api('/system/events/'+id+'/ack', {method:'POST'});
    fetchEventsHeader();
    loadEvents();
    refreshEventsCenterCardIfVisible();  // 同步刷新仪表盘事件中心卡片(去掉 NEW)
  }catch(e){}
}

async function ackAllEvents(){
  if(!confirm('标记所有未读事件为已读?')) return;
  try{
    await api('/system/events/ack-all', {method:'POST', body: JSON.stringify({severity:null})});
    fetchEventsHeader();
    loadEvents();
  }catch(e){}
}
