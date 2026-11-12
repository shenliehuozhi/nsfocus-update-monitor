// 经典脚本，无构建；事件中心弹窗（仪表盘「查看全部」入口）
// 数据源 /api/system/events,message 字段前端 JSON.parse 后渲染
const _EVENTS_PER_PAGE = 50;
// 事件列表有两个挂载面：仪表盘弹窗 + 「事件与日志」页签
const EVENTS_SCOPE_MODAL = 'eventsCenterModal';
const EVENTS_SCOPE_PAGE = 'eventsPage';

function _eventsState(scope){
  const key = scope === EVENTS_SCOPE_PAGE ? 'eventsPageState' : 'eventsCenterState';
  if(!window[key]) window[key] = { offset: 0, total: 0, loadedOnce: false };
  return window[key];
}

function _evtEl(scope, suffix){
  return document.getElementById(scope + suffix);
}

function _eventsToolbarHtml(scope){
  return `<div class="evt-tools-bar">
    <span class="text-muted">严重度</span>
    <select id="${scope}Sev" class="sel-sm dd" data-on-change="eventsGoPage" data-args='${_dataArgs(scope, 0)}'>
      <option value="">全部</option>
      <option value="CRITICAL">🔴 CRITICAL</option>
      <option value="ERROR">🟠 ERROR</option>
      <option value="WARNING">⚠️ WARNING</option>
      <option value="INFO">ℹ️ INFO</option>
    </select>
    <span class="text-muted">时间范围</span>
    <select id="${scope}Hours" class="sel-sm dd" data-on-change="eventsGoPage" data-args='${_dataArgs(scope, 0)}'>
      <option value="6">近 6 小时</option>
      <option value="24">近 24 小时</option>
      <option value="168" selected>近 7 天</option>
      <option value="720">近 30 天</option>
      <option value="2160">近 90 天</option>
      <option value="0">全部时间</option>
    </select>
    <label class="flex items-center gap4 text-muted pointer">
      <input type="checkbox" id="${scope}Unacked" data-on-change="eventsGoPage" data-args='${_dataArgs(scope, 0)}'>
      仅未确认
    </label>
    <button class="btn btn-xs btn-outline ml-auto" data-on-click="eventsAckCurrent" data-args='${_dataArgs(scope)}' title="按当前严重度和时间范围，把未确认事件批量标记为已读">批量确认</button>
    <button class="btn btn-xs btn-outline" data-on-click="eventsReload" data-args='${_dataArgs(scope)}'>🔄 刷新</button>
  </div>`;
}

function _eventsFooterHtml(scope){
  return `<div class="card-bd evt-foot">
    <span class="text-muted" id="${scope}PagerInfo"></span>
    <div class="ml-auto flex items-center gap6">
      <button id="${scope}PrevBtn" class="btn btn-xs btn-outline" data-on-click="eventsPrevPage" data-args='${_dataArgs(scope)}'>← 上一页</button>
      <span class="muted-11 min-w60 ta-center" id="${scope}PageInfo"></span>
      <button id="${scope}NextBtn" class="btn btn-xs btn-outline" data-on-click="eventsNextPage" data-args='${_dataArgs(scope)}'>下一页 →</button>
    </div>
  </div>`;
}

function eventsPageHtml(){
  return `<div class="card">
    <div class="card-hd">🔔 事件与告警
      <span class="ml8 muted-11-400" id="eventsPageCount"></span>
      <span class="ml-auto muted-12 fw400">未读口径：CRITICAL / ERROR / WARNING</span>
    </div>
    ${_eventsToolbarHtml(EVENTS_SCOPE_PAGE)}
    <div class="card-bd p0" id="eventsPageBody">
      <div class="p40 ta-center muted-13">加载中…</div>
    </div>
    ${_eventsFooterHtml(EVENTS_SCOPE_PAGE)}
  </div>`;
}

function _ackEventAndReload(id, scope){
  api('/system/events/' + id + '/ack', { method: 'POST' })
    .then(() => _loadEventsPanel(scope || EVENTS_SCOPE_MODAL))
    .catch(() => toast('标记失败', 'error'));
}

function _deliveryErrorItemsHtml(m){
  const items = ((m && m.items) || []).filter(it => it && it.queue_id);
  if(!items.length) return '';
  return '<div class="mb8">'+items.map(it=>{
    const owner = [it.customer_name, it.rule_name].filter(Boolean).join(' / ') || ('规则 '+it.rule_id);
    const prod = [it.product_name, it.version].filter(Boolean).join(' ');
    return `<div class="flex items-center gap8 mt4 wrap">
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
    loadEventsCenterModal();
  }else{
    toast(r.message||'重新入队失败','error');
    if(btn){btn.disabled=false;btn.textContent='重新入队';}
  }
}

function _sevTone(sev){return {CRITICAL:'critical',ERROR:'error',WARNING:'warning',INFO:'info'}[sev]||'info';}

function _obsDefaultHours(){
  return typeof _obsHours === 'number' ? _obsHours : 168;
}

function openEventsCenterModal(severity, hours, unacked){
  let m = document.getElementById('eventsCenterModal');
  if(!m){
    m = document.createElement('div');
    m.id = 'eventsCenterModal';
    m.innerHTML = `
      <div class="modal-overlay" data-on-click="closeEventsCenterModal" data-self>
        <div class="card modal-card-960-col">
          <div class="card-hd flex items-center">
            <span class="text-danger">🔔 事件中心</span>
            <span class="ml8 muted-11-400" id="eventsCenterModalCount"></span>
            <span class="ml-auto pointer text-gray-muted fs18 lh1 px4" data-on-click="closeEventsCenterModal">×</span>
          </div>
          ${_eventsToolbarHtml(EVENTS_SCOPE_MODAL)}
          <div class="card-bd flex-1 o-auto p0" id="eventsCenterModalBody">
            <div class="p40 ta-center muted-13">加载中…</div>
          </div>
          ${_eventsFooterHtml(EVENTS_SCOPE_MODAL)}
          <div class="card-bd evt-foot-end">
            <button class="btn btn-outline" data-on-click="closeEventsCenterModal">关闭</button>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(m);
  }
  m.classList.remove('hidden');
  const sevEl = document.getElementById('eventsCenterModalSev');
  const hoursEl = document.getElementById('eventsCenterModalHours');
  const unackedEl = document.getElementById('eventsCenterModalUnacked');
  const targetHours = (hours === undefined || hours === null) ? String(_obsDefaultHours()) : String(hours);
  sevEl.value = severity || '';
  if(!Array.from(hoursEl.options).some(opt => opt.value === targetHours)){
    const opt = document.createElement('option');
    opt.value = targetHours;
    opt.textContent = '近 ' + targetHours + ' 小时';
    hoursEl.appendChild(opt);
  }
  hoursEl.value = targetHours;
  unackedEl.checked = !!unacked;
  _eventsState(EVENTS_SCOPE_MODAL).offset = 0;
  loadEventsCenterModal();
  if(!window._eventsCenterModalTimer) window._eventsCenterModalTimer = setInterval(()=>{ if(!document.getElementById('eventsCenterModal').classList.contains('hidden')) loadEventsCenterModal(); }, 30000);
}

function closeEventsCenterModal(){
  const m = document.getElementById('eventsCenterModal');
  if(m) m.classList.add('hidden');
  if(window._eventsCenterModalTimer){clearInterval(window._eventsCenterModalTimer); window._eventsCenterModalTimer=null;}
}

function eventsGoPage(scope, offset){
  _eventsState(scope).offset = Math.max(0, Number(offset) || 0);
  _loadEventsPanel(scope);
}

function eventsPrevPage(scope){
  eventsGoPage(scope, _eventsState(scope).offset - _EVENTS_PER_PAGE);
}

function eventsNextPage(scope){
  const st = _eventsState(scope);
  if(st.offset + _EVENTS_PER_PAGE < st.total){
    eventsGoPage(scope, st.offset + _EVENTS_PER_PAGE);
  }
}

function eventsReload(scope){
  _loadEventsPanel(scope);
}

function loadEventsCenterModal(){ return _loadEventsPanel(EVENTS_SCOPE_MODAL); }
function loadEventsPage(){ return _loadEventsPanel(EVENTS_SCOPE_PAGE); }

async function _loadEventsPanel(scope){
  const state = _eventsState(scope);
  const body = _evtEl(scope, 'Body');
  const countEl = _evtEl(scope, 'Count');
  const pagerInfo = _evtEl(scope, 'PagerInfo');
  const pageInfo = _evtEl(scope, 'PageInfo');
  const prevBtn = _evtEl(scope, 'PrevBtn');
  const nextBtn = _evtEl(scope, 'NextBtn');
  if(!body) return;
  const sevEl = _evtEl(scope, 'Sev');
  const hrsEl = _evtEl(scope, 'Hours');
  const unackedEl = _evtEl(scope, 'Unacked');
  const sev = sevEl ? sevEl.value : '';
  const hrs = (hrsEl && hrsEl.value) || '168';
  const offset = state.offset || 0;
  const params = new URLSearchParams({
    limit: String(_EVENTS_PER_PAGE),
    offset: String(offset),
    since_hours: hrs
  });
  if(sev) params.set('severity', sev);
  if(unackedEl && unackedEl.checked) params.set('unacked', '1');
  // 第一次加载才显示"加载中",后续刷新不闪
  if(!state.loadedOnce){
    body.innerHTML = '<div class="p40 ta-center muted-13">加载中…</div>';
  }
  const r = await api('/system/events?' + params.toString());
  // DOM 守卫:挂载面可能已切走(避免写已卸载元素)
  if(_evtEl(scope, 'Body') !== body) return;
  if(r.code !== 0){
    body.innerHTML = '<div class="p40 ta-center danger-13">加载失败: '+escHtml(r.message||'未知错误')+'</div>';
    return;
  }
  const data = r.data || {};
  const events = data.events || [];
  const total = data.total || 0;
  state.total = total;
  if(!events.length){
    body.innerHTML = '<div class="empty-success-lg">✅ 当前条件下无事件</div>';
    if(countEl) countEl.textContent = '(共 0 条)';
    if(pagerInfo) pagerInfo.textContent = '';
    if(pageInfo) pageInfo.textContent = '0 / 0';
    if(prevBtn) prevBtn.disabled = true;
    if(nextBtn) nextBtn.disabled = true;
    state.loadedOnce = true;
    return;
  }
  body.innerHTML = renderEventsTable(events, scope);
  const start = offset + 1;
  const end = Math.min(offset + events.length, total);
  if(countEl) countEl.textContent = '(共 ' + total + ' 条)';
  if(pagerInfo) pagerInfo.textContent = '第 ' + start + '-' + end + ' 条 / 共 ' + total + ' 条';
  const totalPages = Math.max(1, Math.ceil(total / _EVENTS_PER_PAGE));
  const curPage = Math.floor(offset / _EVENTS_PER_PAGE) + 1;
  if(pageInfo) pageInfo.textContent = curPage + ' / ' + totalPages;
  if(prevBtn) prevBtn.disabled = (offset <= 0);
  if(nextBtn) nextBtn.disabled = (offset + _EVENTS_PER_PAGE >= total);
  state.loadedOnce = true;
}

// 表格渲染 — 字段作为表头
function renderEventsTable(events, scope){
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
  const rows = events.map(e => renderEventsTableRow(e, scope)).join('');
  return head + rows + '</tbody></table>';
}

function renderEventsTableRow(e, scope){
  let m = {};
  if(typeof e.message === 'string'){
    try { m = JSON.parse(e.message); } catch(_) { m = {}; }
  } else if(typeof e.message === 'object' && e.message){
    m = e.message;
  }
  const t = e.event_type || '';
  const sevIcon = {CRITICAL:'🔴',ERROR:'🟠',WARNING:'⚠️',INFO:'ℹ️'}[e.severity] || '⚪';
  const sevTone = _sevTone(e.severity);
  let title;
  if(m.title) title = String(m.title);
  else if(t === 'session_error') title = (m.username?`Session 污染 (${m.username})`:'Session 污染')+': '+(m.reason||'未知');
  else if(t === 'session_expired') title = (m.session_id?`Session ${m.session_id} 失效`:'Session 失效')+': '+(m.reason||'');
  else if(t === 'log_error') title = (m.error_type||'日志异常')+(m.keyword?` / ${m.keyword}`:'');
  else if(t === 'collection_summary') title = m.ok===false ? '⚠️ 采集异常' : `采集完成 新增 ${m.total_new||0} / 回滚 ${m.total_rollback||0}`;
  else if(t === 'push_summary') title = m.success>0 ? `推送 ${m.success} 成功` : (m.total?`推送 ${m.total} 条`:'本次无推送');
  else if(t === 'push_success') title = (m.product_name||'')+(m.file_name?` ${m.file_name}`:'')+(m.success===false?' 推送失败':' 推送成功');
  else if(t === 'login_bruteforce') title = `IP ${m.ip||'?'} 失败 ${m.count||0} 次`;
  else if(t === 'channel_error') title = '渠道体检失败: '+(m.failures||[]).length+' 个'+((m.failures&&m.failures[0])?` (${m.failures[0].channel_name||m.failures[0].channel_type||''})`:'');
  else if(t === 'site_unavailable') title = '站点不可用: '+(m.error||m.url||'update.nsfocus.com');
  else if(t === 'site_recovered') title = '站点已恢复: '+(m.url||'update.nsfocus.com');
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
  const detailId = 'eventTableDetail-' + e.id;
  const detailJson = escHtml(JSON.stringify(m, null, 2));
  const detail = `<tr id="${detailId}" class="hidden detail-row"><td class="detail-cell-lg" colspan="7">
    <div class="flex gap24 wrap mb8">
      <span><b>event_type:</b> ${escHtml(t)}</span>
      <span><b>severity:</b> <span class="sev-text sev-text-${sevTone}">${escHtml(e.severity)}</span></span>
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
      <pre class="log-panel-tall">${detailJson}</pre>
    </details>
  </td></tr>`;
  return `<tr class="evt-row${isUnread?' evt-row-'+sevTone:''}" data-on-click="toggleEventsTableRow" data-args='${_dataArgs(e.id)}' title="点击展开详情">
    <td class="log-ts-lg">${escHtml(serverTime(e.created_at, true))}</td>
    <td class="p8"><span class="sev-pill sev-${sevTone}">${sevIcon} ${escHtml(e.severity)}</span></td>
    <td class="p8 muted-11">${escHtml(t)}</td>
    <td class="p8 text-body txt-clip">${escHtml(title)}</td>
    <td class="p8 text-body fs11">${productCell}</td>
    <td class="p8 text-body fs11">${channelCell}</td>
    <td class="p8 ta-center">
      ${isUnread
        ? `<button class="btn btn-xs btn-soft-success chip-xs" data-on-click="_ackEventAndReload" data-stop data-args='${_dataArgs(e.id, scope)}'>已读</button>`
        : '<span class="tag-purple-xs">已读</span>'}</td>
  </tr>${detail}`;
}

// 表格行的展开/收起 — 用 table-row/none(display:block 对 tr 不合法)
function toggleEventsTableRow(eventId){
  const el = document.getElementById('eventTableDetail-' + eventId);
  if (!el) return;
  el.classList.toggle('hidden');
}

// 批量确认 — 严格按当前严重度 + 时间范围标记未确认事件（与列表筛选一致）
async function eventsAckCurrent(scope){
  const sevEl = _evtEl(scope, 'Sev');
  const hrsEl = _evtEl(scope, 'Hours');
  const sev = sevEl ? sevEl.value : '';
  const hrs = (hrsEl && hrsEl.value) || '168';
  const hrsText = (hrsEl && hrsEl.selectedIndex >= 0) ? hrsEl.options[hrsEl.selectedIndex].text : `近 ${hrs} 小时`;
  const scopeText = `${hrsText} · ${sev || '全部严重度'}`;
  const total = _eventsState(scope).total || 0;
  await confirmAct(
    `批量确认${scopeText}的未确认事件?`,
    async () => {
      const r = await api('/system/events/ack-all', {
        method: 'POST',
        body: JSON.stringify({severity: sev || null, since_hours: Number(hrs)})
      });
      if(r.code !== 0){
        toast('标记失败: ' + (r.message || '未知错误'), 'error');
        return;
      }
      toast('已标记当前筛选条件下的未确认事件', 'success');
      _eventsState(scope).offset = 0;
      _loadEventsPanel(scope);
    },
    '✅',
    '批量确认',
    false,
    `<div class="info-purple-wide">
       <span class="fs20 lh1">✓</span>
       <span class="text-purple fs12 lh15">当前筛选共 <b>${total}</b> 条事件，只会把其中<b>未确认</b>的事件标记为已读；已读事件和筛选范围外的事件不受影响。</span>
     </div>`
  );
}
