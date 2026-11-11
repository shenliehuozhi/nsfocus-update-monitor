// 经典脚本，无构建；可观测性页面（/api/metrics 的可视化层）
let _obsHours = 24;

async function observability(){
  document.getElementById('app').innerHTML = `
    <div class="card">
      <div class="card-hd items-center">
        <span>📈 可观测性</span>
        <span id="obsMeta" data-css="font-weight:400;color:var(--muted);font-size:11.5px"></span>
        <span class="ml-auto flex items-center gap8">
          <select id="obsWindow" class="dd sel-sm" data-on-change="obsSetWindow" data-args='${_dataArgs('$value')}'>
            <option value="6">最近 6 小时</option>
            <option value="24">最近 24 小时</option>
            <option value="168">最近 7 天</option>
            <option value="720">最近 30 天</option>
          </select>
          <button class="btn btn-outline btn-sm" id="obsRefreshBtn" data-on-click="obsRefresh" data-args='${_dataArgs(true)}'>↻ 刷新</button>
        </span>
      </div>
      <div class="card-bd" id="obs-root"><div class="empty">加载中…</div></div>
    </div>`;
  document.getElementById('obsWindow').value = String(_obsHours);
  if (window._obsTimer) clearInterval(window._obsTimer);
  window._obsTimer = setInterval(() => {
    if (document.getElementById('obs-root')) obsRefresh(false);
    else { clearInterval(window._obsTimer); window._obsTimer = null; }
  }, 30000);
  await obsRefresh(false);
}

function obsSetWindow(value){
  _obsHours = parseInt(value, 10) || 24;
  obsRefresh(false);
}

async function obsRefresh(manual){
  const btn = document.getElementById('obsRefreshBtn');
  if (btn) { btn.disabled = true; btn.textContent = '刷新中…'; }
  const r = await api('/metrics?hours=' + _obsHours);
  if (btn) { btn.disabled = false; btn.textContent = '↻ 刷新'; }
  const root = document.getElementById('obs-root');
  if (!root) return;
  if (r.code !== 0 || !r.data) {
    root.innerHTML = `<div class="empty">加载失败：${escHtml(r.message || '未知错误')}</div>`;
    return;
  }
  root.innerHTML = _obsRender(r.data);
  const meta = document.getElementById('obsMeta');
  if (meta) meta.textContent = '更新于 ' + _obsTime(r.data.generated_at) + ' · 30 秒自动刷新';
  if (manual) toast('观测数据已刷新');
}

function _obsRender(d){
  const del = d.delivery || {}, queues = d.queues || {};
  const dq = queues.delayed || {}, dg = queues.digest || {};
  const col = d.collection || {}, ev = d.events || {};
  const st = d.storage || {}, rules = d.rules || {};
  const unacked = ev.unacked || {};
  const unackedTotal = (unacked.CRITICAL || 0) + (unacked.ERROR || 0) + (unacked.WARNING || 0);
  const rate = del.success_rate;
  const rateText = (rate === null || rate === undefined) ? '—' : Math.round(rate * 100) + '%';
  const rateTint = (rate === null || rate === undefined) ? 'neutral'
    : (rate >= 0.95 ? 'success' : (rate >= 0.8 ? 'warn' : 'danger'));
  const queueRisk = (dq.overdue || 0) + (dq.retrying || 0);
  const unackedTint = ((unacked.CRITICAL || 0) + (unacked.ERROR || 0)) > 0 ? 'danger'
    : ((unacked.WARNING || 0) > 0 ? 'warn' : 'success');
  const stale = col.sources_stale || 0;
  const colKnown = col.enabled !== null && col.enabled !== undefined;
  const colState = !colKnown ? '—' : (col.is_running ? '运行中' : (col.enabled ? '就绪' : '已关闭'));
  const colTint = !colKnown ? 'neutral'
    : (!col.enabled ? 'danger' : (col.is_running ? 'primary' : (stale ? 'warn' : 'success')));

  return `
    <div class="obs-kpis">
      ${_obsKpi('投递成功率', rateText,
        `${del.total || 0} 条 · ${del.sent || 0} 成功 · <b data-css="color:${del.failed ? 'var(--danger)' : 'var(--muted)'}">${del.failed || 0} 失败</b>`,
        rateTint)}
      ${_obsKpi('待投递队列', dq.pending || 0,
        `逾期 ${dq.overdue || 0} · 重试中 ${dq.retrying || 0}`,
        queueRisk ? 'warn' : 'success')}
      ${_obsKpi('汇总积压', dg.pending || 0,
        `重试中 ${dg.retrying || 0}`,
        dg.pending ? 'warn' : 'success')}
      ${_obsKpi('未确认告警', unackedTotal,
        `严重 ${unacked.CRITICAL || 0} · 错误 ${unacked.ERROR || 0} · 警告 ${unacked.WARNING || 0}`,
        unackedTint)}
      ${_obsKpi('采集状态', colState,
        `上次 ${_obsTime(col.last_run)} · 下次 ${_obsTime(col.next_run)}`,
        colTint)}
      ${_obsKpi('数据库体积', _obsSize(st.db_size_bytes),
        `${st.delivery_log_rows || 0} 条投递日志 · ${st.system_event_log_rows || 0} 条事件`,
        'info')}
    </div>
    <div class="obs-grid2">
      ${_obsDeliveryCard(del)}
      ${_obsQueueCard(dq, dg)}
    </div>
    <div class="obs-grid2">
      ${_obsCollectionCard(col, rules)}
      ${_obsEventsCard(ev)}
    </div>
    <div class="obs-grid2">
      ${_obsStorageCard(st)}
      ${_obsSchedulerCard(d.scheduler_jobs || [])}
    </div>
    ${_obsPromCard()}`;
}

function _obsKpi(label, value, sub, tint){
  return `<div class="stat tint-${tint || 'neutral'}">
    <div class="num">${value}</div>
    <div class="label">${label}</div>
    <div class="obs-sub">${sub || ''}</div>
  </div>`;
}

function _obsDeliveryCard(del){
  const rows = (del.by_channel || []).map(ch => `<tr>
    <td>${escHtml(_obsChannel(ch.channel_type))}</td>
    <td>${ch.total || 0}</td>
    <td>${ch.sent || 0}</td>
    <td>${ch.failed ? `<b class="text-danger">${ch.failed}</b>` : '0'}</td>
    <td>${ch.pending || 0}</td>
    <td>${_obsRateCell(ch.success_rate)}</td>
  </tr>`).join('');
  const undated = del.undated_failed || 0;
  return `<div class="card">
    <div class="card-hd">📤 投递成功率
      <span class="obs-muted ml-auto fs115 fw400">${escHtml(_obsWindowLabel())}</span>
    </div>
    <div class="card-bd pt0">
      <div class="obs-scroll"><table>
        <thead><tr><th>渠道</th><th>总数</th><th>成功</th><th>失败</th><th>待定</th><th>成功率</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="6" class="empty">窗口内暂无投递记录</td></tr>'}</tbody>
      </table></div>
      ${undated ? `<div class="obs-foot">另有 ${undated} 条历史失败记录没有时间戳（created_at 上线前写入），未计入当前窗口。</div>` : ''}
    </div>
  </div>`;
}

function _obsQueueCard(dq, dg){
  const rows = (dg.by_rule || []).map(r => `<tr>
    <td>${escHtml(r.rule_name || ('规则 #' + r.rule_id))}</td>
    <td>${r.pending || 0}</td>
    <td>${_obsAge(r.oldest_age_minutes)}</td>
  </tr>`).join('');
  return `<div class="card">
    <div class="card-hd">🚚 队列积压</div>
    <div class="card-bd">
      <div class="obs-section-title">即时投递队列</div>
      <div class="obs-mini">
        <div><b>${dq.pending || 0}</b><span>待处理</span></div>
        <div><b data-css="color:${dq.overdue ? 'var(--warn)' : 'var(--text)'}">${dq.overdue || 0}</b><span>已逾期</span></div>
        <div><b data-css="color:${dq.retrying ? 'var(--warn)' : 'var(--text)'}">${dq.retrying || 0}</b><span>重试中</span></div>
        <div><b>${_obsAge(dq.oldest_age_minutes)}</b><span>最久等待</span></div>
      </div>
      <div class="obs-section-title mt14">汇总队列（按规则）</div>
      <div class="obs-scroll"><table>
        <thead><tr><th>规则</th><th>待发</th><th>最久等待</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="3" class="empty">暂无积压</td></tr>'}</tbody>
      </table></div>
      <div class="obs-foot">逾期指已到 push_after 仍在等待，静默时段、时间窗或渠道异常时会自然出现；重试中表示已有失败、等待下次退避。</div>
    </div>
  </div>`;
}

function _obsCollectionCard(col, rules){
  const stale = col.sources_stale || 0;
  return `<div class="card">
    <div class="card-hd">🔄 采集健康</div>
    <div class="card-bd">
      <div class="obs-kv">
        <span>调度器</span><b>${col.enabled === null || col.enabled === undefined ? '—' : (col.enabled ? '已启用' : '已关闭')}</b>
        <span>当前状态</span><b>${col.is_running ? '采集中' : (col.current && col.current.phase ? escHtml(col.current.phase) : '空闲')}</b>
        <span>上次采集</span><b>${_obsTime(col.last_run)}</b>
        <span>下次采集</span><b>${_obsTime(col.next_run)}</b>
        <span>上次耗时</span><b>${_obsDur(col.last_duration_s)}</b>
        <span>近 20 轮均值</span><b>${_obsDur(col.avg_duration_s)}</b>
        <span>数据源</span><b>${col.sources_total || 0} 个${stale ? ` · <span class="text-danger">${stale} 个超过 ${col.stale_after_hours || 24}h 未更新</span>` : ' · 全部新鲜'}</b>
        <span>订阅规则</span><b>${rules.enabled || 0} / ${rules.total || 0} 启用 · ${rules.digest_enabled || 0} 汇总模式</b>
      </div>
      ${col.is_running && col.current ? `<div class="obs-foot">当前：${escHtml(col.current.mode || '')} ${escHtml(col.current.phase || '')}${col.current.items_collected != null ? ' · 已采集 ' + col.current.items_collected + ' 项' : ''}</div>` : ''}
    </div>
  </div>`;
}

function _obsEventsCard(ev){
  const by = ev.by_severity || {}, un = ev.unacked || {};
  const rows = ['CRITICAL', 'ERROR', 'WARNING', 'INFO'].map(s => `<tr>
    <td>${_obsSevBadge(s)}</td>
    <td>${by[s] || 0}</td>
    <td>${un[s] ? `<b class="text-danger">${un[s]}</b>` : (s === 'INFO' ? '—' : '0')}</td>
  </tr>`).join('');
  return `<div class="card">
    <div class="card-hd">🔔 事件与告警
      <span class="obs-muted ml-auto fs115 fw400">窗口内 ${ev.window_total || 0} 条</span>
    </div>
    <div class="card-bd pt0">
      <div class="obs-scroll"><table>
        <thead><tr><th>级别</th><th>窗口内</th><th>未确认</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <div class="obs-foot">未确认只统计 CRITICAL / ERROR / WARNING，可在仪表盘事件中心逐条确认。</div>
    </div>
  </div>`;
}

function _obsStorageCard(st){
  return `<div class="card">
    <div class="card-hd">💾 存储与数据量</div>
    <div class="card-bd">
      <div class="obs-kv">
        <span>数据库文件</span><b>${_obsSize(st.db_size_bytes)}</b>
        <span>快照</span><b>${st.snapshots_active || 0} 活跃 / ${st.snapshots_total || 0} 总数</b>
        <span>投递日志</span><b>${st.delivery_log_rows || 0} 行</b>
        <span>汇总队列</span><b>${st.digest_queue_rows || 0} 行</b>
        <span>系统事件</span><b>${st.system_event_log_rows || 0} 行</b>
      </div>
      <div class="obs-foot">失败投递记录保留 180 天，其余历史按系统配置的保留条数清理。</div>
    </div>
  </div>`;
}

function _obsSchedulerCard(jobs){
  const rows = (jobs || []).map(j => `<tr>
    <td>${escHtml(_obsJobLabel(j.id))}<span class="obs-muted fs105"> ${escHtml(j.id)}</span></td>
    <td>${_obsTime(j.next_run)}</td>
    <td>${_obsCountdown(j.seconds_until)}</td>
  </tr>`).join('');
  return `<div class="card">
    <div class="card-hd">⏱️ 调度任务</div>
    <div class="card-bd pt0">
      <div class="obs-scroll"><table>
        <thead><tr><th>任务</th><th>下次运行</th><th>倒计时</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="3" class="empty">调度器未运行（服务未启动或已关闭）</td></tr>'}</tbody>
      </table></div>
    </div>
  </div>`;
}

function _obsPromCard(){
  return `<div class="card">
    <div class="card-hd">🔌 外部观测接入</div>
    <div class="card-bd">
      <div class="obs-foot mb6-flush">同一份指标同时提供 JSON 与 Prometheus 文本格式，可接入 Prometheus / Grafana。</div>
      <div class="obs-code">
        <code id="obsPromUrl">${escHtml(obsPrometheusUrl())}</code>
        <button class="btn btn-outline btn-xs" data-on-click="obsCopyProm">复制地址</button>
      </div>
      <div class="obs-foot">抓取鉴权：服务端设置 <code>MONITOR_METRICS_TOKEN</code> 后，请求头带 <code>X-Metrics-Token</code> 或 <code>Authorization: Bearer &lt;token&gt;</code>；未设置时静态令牌路径关闭，仅接受登录会话（HttpOnly Cookie）。页面不会保存或展示该令牌。</div>
    </div>
  </div>`;
}

function _obsSevBadge(sev){
  const map = {
    CRITICAL: ['严重', 'badge-err'],
    ERROR: ['错误', 'badge-err'],
    WARNING: ['警告', 'badge-warn'],
    INFO: ['信息', 'badge-blue'],
  };
  const item = map[sev] || [sev, 'badge-grey'];
  return `<span class="badge ${item[1]}">${escHtml(item[0])}</span>`;
}

function _obsRateCell(rate){
  if (rate === null || rate === undefined) return '<span class="obs-muted">—</span>';
  const pct = Math.max(0, Math.min(100, Math.round(Number(rate) * 100)));
  const cls = pct >= 95 ? '' : (pct >= 80 ? 'warn' : 'bad');
  return `<div class="obs-rate"><span>${pct}%</span><div class="obs-bar ${cls}"><i data-css="width:${Math.max(2, pct)}%"></i></div></div>`;
}

function _obsChannel(type){
  const map = {
    wecom: '企业微信', dingtalk: '钉钉', feishu: '飞书',
    email: '邮件', apprise: 'Apprise', webhook: 'Webhook',
  };
  return map[type] || type || '未知';
}

function _obsJobLabel(id){
  const map = {
    nsfocus_collect: '升级包采集',
    nsfocus_heartbeat: 'Session 心跳',
    nsfocus_digest: '汇总检查',
    nsfocus_delivery_queue: '投递队列',
    nsfocus_db_cleanup: '数据清理',
    nsfocus_channel_health: '渠道体检',
  };
  return map[id] || id || '未命名任务';
}

function _obsWindowLabel(){
  if (_obsHours === 24) return '最近 24 小时';
  if (_obsHours === 168) return '最近 7 天';
  if (_obsHours === 720) return '最近 30 天';
  return '最近 ' + _obsHours + ' 小时';
}

// 服务端返回的时间大多是不带时区的 UTC，generated_at 自带偏移；统一显示为 CST
function _obsTime(value){
  if (!value) return '—';
  const text = String(value);
  let ms;
  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(text)) ms = Date.parse(text);
  else ms = Date.parse(text.replace(' ', 'T') + 'Z');
  if (!isFinite(ms)) return escHtml(text.substring(0, 16));
  const dt = new Date(ms + 8 * 3600 * 1000);
  const p = n => String(n).padStart(2, '0');
  return dt.getUTCFullYear() + '-' + p(dt.getUTCMonth() + 1) + '-' + p(dt.getUTCDate()) +
    ' ' + p(dt.getUTCHours()) + ':' + p(dt.getUTCMinutes());
}

function _obsAge(minutes){
  if (minutes === null || minutes === undefined || isNaN(Number(minutes))) return '—';
  const v = Math.max(0, Number(minutes));
  if (v < 1) return '&lt;1 分钟';
  if (v < 60) return Math.round(v) + ' 分钟';
  if (v < 1440) {
    const h = v / 60;
    return (h < 10 ? h.toFixed(1) : String(Math.round(h))) + ' 小时';
  }
  return (v / 1440).toFixed(1) + ' 天';
}

function _obsDur(seconds){
  if (seconds === null || seconds === undefined || isNaN(Number(seconds))) return '—';
  const v = Math.max(0, Number(seconds));
  if (v < 60) return v.toFixed(v < 10 ? 1 : 0) + ' 秒';
  if (v < 3600) return Math.floor(v / 60) + ' 分 ' + Math.round(v % 60) + ' 秒';
  return (v / 3600).toFixed(1) + ' 小时';
}

function _obsSize(bytes){
  if (bytes === null || bytes === undefined || isNaN(Number(bytes))) return '—';
  let v = Math.max(0, Number(bytes));
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (i === 0 ? Math.round(v) : v.toFixed(v < 10 ? 1 : 0)) + ' ' + units[i];
}

function _obsCountdown(seconds){
  if (seconds === null || seconds === undefined || isNaN(Number(seconds))) return '—';
  const v = Number(seconds);
  if (v <= 0) return '即将运行';
  if (v < 60) return v + ' 秒';
  if (v < 3600) return Math.floor(v / 60) + ' 分 ' + (v % 60) + ' 秒';
  return Math.floor(v / 3600) + ' 小时 ' + Math.floor((v % 3600) / 60) + ' 分';
}

function obsPrometheusUrl(){
  return location.origin + '/api/metrics?format=prometheus&hours=' + _obsHours;
}

function obsCopyProm(){
  const text = obsPrometheusUrl();
  const done = () => toast('Prometheus 地址已复制');
  const fallback = () => {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); done(); }
    catch (e) { toast('复制失败，请手动选择地址', 'error'); }
    ta.remove();
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(fallback);
  } else fallback();
}
