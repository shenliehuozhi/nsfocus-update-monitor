// 经典脚本，无构建；事件中心弹窗（仪表盘「查看全部」入口）
// 数据源 /api/system/events,message 字段前端 JSON.parse 后渲染
const _EVENTS_PER_PAGE = 50;

function _ackEventAndReload(id){
  api('/system/events/' + id + '/ack', { method: 'POST' })
    .then(() => loadEventsCenterModal())
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
          <div class="evt-tools-bar">
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
              <option value="6">近 6 小时</option>
              <option value="24">近 24 小时</option>
              <option value="168" selected>近 7 天</option>
              <option value="720">近 30 天</option>
              <option value="2160">近 90 天</option>
              <option value="0">全部时间</option>
            </select>
            <label class="flex items-center gap4 text-muted pointer">
              <input type="checkbox" id="eventsCenterModalUnacked" data-on-change="eventsCenterGoPage" data-args='${_dataArgs(0)}'>
              仅未确认
            </label>
            <button class="btn btn-xs btn-outline ml-auto" data-on-click="eventsCenterAckCurrent" title="按当前过滤器批量标记为已读;无过滤时标全部 4 个 severity(含 INFO)">一键已读</button>
            <button class="btn btn-xs btn-outline" data-on-click="loadEventsCenterModal">🔄 刷新</button>
          </div>
          <div class="card-bd flex-1 o-auto p0" id="eventsCenterModalBody">
            <div class="p40 ta-center muted-13">加载中…</div>
          </div>
          <div class="card-bd evt-foot">
            <span class="text-muted" id="eventsCenterModalPagerInfo"></span>
            <div class="ml-auto flex items-center gap6">
              <button id="eventsCenterModalPrevBtn" class="btn btn-xs btn-outline" data-on-click="eventsCenterPrevPage">← 上一页</button>
              <span class="muted-11 min-w60 ta-center" id="eventsCenterModalPageInfo"></span>
              <button id="eventsCenterModalNextBtn" class="btn btn-xs btn-outline" data-on-click="eventsCenterNextPage">下一页 →</button>
            </div>
          </div>
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
  eventsCenterState.offset = 0;
  loadEventsCenterModal();
  if(!window._eventsCenterModalTimer) window._eventsCenterModalTimer = setInterval(()=>{ if(!document.getElementById('eventsCenterModal').classList.contains('hidden')) loadEventsCenterModal(); }, 30000);
}

// 分页状态(全局,避免每次重置)
window.eventsCenterState = window.eventsCenterState || { offset: 0, total: 0, loadedOnce: false };

function closeEventsCenterModal(){
  const m = document.getElementById('eventsCenterModal');
  if(m) m.classList.add('hidden');
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
  const unackedEl = document.getElementById('eventsCenterModalUnacked');
  const offset = eventsCenterState.offset || 0;
  const params = new URLSearchParams({
    limit: String(_EVENTS_PER_PAGE),
    offset: String(offset),
    since_hours: hrs
  });
  if(sev) params.set('severity', sev);
  if(unackedEl && unackedEl.checked) params.set('unacked', '1');
  // 第一次加载才显示"加载中",后续刷新不闪
  if(!eventsCenterState.loadedOnce){
    body.innerHTML = '<div class="p40 ta-center muted-13">加载中…</div>';
  }
  const r = await api('/system/events?' + params.toString());
  // DOM 守卫:modal 可能已关闭(避免写已卸载元素)
  if(document.getElementById('eventsCenterModalBody') !== body) return;
  if(r.code !== 0){
    body.innerHTML = '<div class="p40 ta-center danger-13">加载失败: '+escHtml(r.message||'未知错误')+'</div>';
    return;
  }
  const data = r.data || {};
  const events = data.events || [];
  const total = data.total || 0;
  eventsCenterState.total = total;
  if(!events.length){
    body.innerHTML = '<div class="empty-success-lg">✅ 当前条件下无事件</div>';
    if(countEl) countEl.textContent = '(共 0 条)';
    if(pagerInfo) pagerInfo.textContent = '';
    if(pageInfo) pageInfo.textContent = '0 / 0';
    if(prevBtn) prevBtn.disabled = true;
    if(nextBtn) nextBtn.disabled = true;
    eventsCenterState.loadedOnce = true;
    return;
  }
  body.innerHTML = renderEventsTable(events);
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
        ? `<button class="btn btn-xs btn-soft-success chip-xs" data-on-click="_ackEventAndReload" data-stop data-args='${_dataArgs(e.id)}'>已读</button>`
        : '<span class="tag-purple-xs">已读</span>'}</td>
  </tr>${detail}`;
}

// 表格行的展开/收起 — 用 table-row/none(display:block 对 tr 不合法)
function toggleEventsTableRow(eventId){
  const el = document.getElementById('eventTableDetail-' + eventId);
  if (!el) return;
  el.classList.toggle('hidden');
}

// 一键已读 — 按当前过滤器(severity)批量标,无过滤时标全部 4 个 severity
async function eventsCenterAckCurrent(){
  const sev = document.getElementById('eventsCenterModalSev').value;
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
      }catch(e){
        toast('网络错误', 'error');
      }
    },
    ' ',
    '标记为已读',
    false,
    `<div class="info-purple-wide">
       <span class="fs20 lh1">✓</span>
       <span class="text-purple fs12 lh15">将标记当前过滤条件下 <b>${targets.length}</b> 个 severity 类别(${targets.join(' / ')})的全部未读事件</span>
     </div>`
  );
}
