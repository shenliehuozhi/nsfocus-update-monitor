// 经典脚本，无构建；仪表盘页面（/api/metrics 的可视化层）
let _obsHours = 24;

async function observability(){
  document.getElementById('app').innerHTML = `
    <div class="card">
      <div class="card-hd items-center">
        <span>📈 仪表盘</span>
        <span class="fw400 fs115 text-muted" id="obsMeta"></span>
        <span class="ml-auto flex items-center gap8">
          <select id="obsWindow" class="dd sel-sm" data-on-change="obsSetWindow" data-args='${_dataArgs('$value')}'>
            <option value="6">最近 6 小时</option>
            <option value="24">最近 24 小时</option>
            <option value="168">最近 7 天</option>
            <option value="720">最近 30 天</option>
          </select>
          <button class="btn btn-outline btn-sm" id="obsRefreshBtn" data-on-click="obsRefresh" data-args='${_dataArgs(true)}'>↻ 刷新</button>
          <button class="btn btn-outline btn-sm danger-outline-text" data-on-click="obsRestartService">🔄 重启服务</button>
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

function _obsWindowDays(hours){
  return Math.max(1, Math.round((Number(hours) || 24) / 24));
}

async function obsRestartService(){
  await confirmAct('⚠️ 确认重启服务？服务将短暂中断后自动恢复。', async () => {
    const r = await api('/system/restart', { method: 'POST' });
    toast(r.code === 0 ? '服务正在重启…' : '重启失败: ' + (r.message || '未知错误'), r.code === 0 ? 'success' : 'error');
  }, '🔄', '确认重启', true);
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
  _applyPctBars(root);
  _renderCustPushTable((r.data.customers || {}).items || [], _obsWindowDays(_obsHours), _obsWindowLabel());
  const meta = document.getElementById('obsMeta');
  if (meta) meta.textContent = '更新于 ' + _obsTime(r.data.generated_at) + ' · 30 秒自动刷新';
  if (manual) toast('观测数据已刷新');
}

function _obsRender(d){
  const del = d.delivery || {}, queues = d.queues || {};
  const dq = queues.delayed || {}, dg = queues.digest || {};
  const col = d.collection || {}, ev = d.events || {};
  const st = d.storage || {}, rules = d.rules || {};
  const td = del;
  const updates = d.updates || {};
  const sess = d.sessions || {}, proc = d.process || {};
  const unacked = ev.unacked || {};
  const unackedTotal = (unacked.CRITICAL || 0) + (unacked.ERROR || 0) + (unacked.WARNING || 0);
  const rate = td.success_rate;
  const rateText = (rate === null || rate === undefined) ? '—' : Math.round(rate * 100) + '%';
  const rateTint = (rate === null || rate === undefined) ? 'neutral'
    : (rate >= 0.95 ? 'success' : (rate >= 0.8 ? 'warn' : 'danger'));
  const queueRisk = (dq.overdue || 0) + (dq.retrying || 0);
  const unackedTint = ((unacked.CRITICAL || 0) + (unacked.ERROR || 0)) > 0 ? 'danger'
    : ((unacked.WARNING || 0) > 0 ? 'warn' : 'success');
  const sessionTint = !sess.collect_total ? 'danger'
    : (sess.collect_active > 0 ? (sess.collect_polluted ? 'warn' : 'success') : 'warn');
  const schedOff = col.enabled === false;
  const schedUnknown = col.enabled === null || col.enabled === undefined;
  const schedValue = schedUnknown ? '—' : (schedOff ? '已关闭' : (col.is_running ? '采集中' : '已启用'));
  const schedSub = schedOff ? '采集与自动投递已停止'
    : (schedUnknown ? '状态不可用' : '上次 ' + _obsTime(col.last_run));
  const schedTint = schedOff ? 'danger' : (schedUnknown ? 'neutral' : (col.is_running ? 'primary' : 'success'));
  const mm = d.maintenance || {};
  const mmOn = mm.enabled === true;
  const site = d.site || {};
  const siteDown = site.status === 'down';

  return `
    ${schedOff ? '<div class="obs-alert danger">⚠️ 调度器已关闭：采集与自动投递已停止，请在「系统配置 → 采集设置」中重新启用。</div>' : ''}
    ${mmOn ? '<div class="obs-alert warn">🔧 维护模式已开启：所有订阅推送已被抑制，采集仍会正常记录；恢复推送请到「系统配置」关闭维护模式。</div>' : ''}
    ${siteDown ? `<div class="obs-alert danger">🌐 update.nsfocus.com 不可用：${escHtml(site.error || '连接失败')}（连续 ${site.consecutive_failures || 0} 次）</div>` : ''}
    <div class="obs-kpis">
      ${_obsKpi('调度器', schedValue, schedSub, schedTint)}
      ${_obsKpi('未确认告警', unackedTotal,
        `严重 ${unacked.CRITICAL || 0} · 错误 ${unacked.ERROR || 0} · 警告 ${unacked.WARNING || 0}`,
        unackedTint)}
      ${_obsKpi('推送', td.total || 0,
        `<b class="${td.failed ? 'text-danger' : 'text-success-text'}">${td.sent || 0} 成功</b> · <b class="${td.failed ? 'text-danger' : 'text-muted'}">${td.failed || 0} 失败</b>`,
        td.failed ? 'warn' : (td.total ? 'success' : 'neutral'))}
      ${_obsKpi('成功率', rateText, `${_obsWindowLabel()} · 成功 / 已结束投递`, rateTint)}
      ${_obsKpi('产品更新', updates.total || 0,
        `${(updates.items || []).length} 个产品 · ${_obsWindowLabel()}`,
        (updates.total || 0) ? 'primary' : 'neutral')}
      ${_obsKpi('待投递队列', dq.pending || 0,
        `逾期 ${dq.overdue || 0} · 重试中 ${dq.retrying || 0}`,
        queueRisk ? 'warn' : 'success')}
      ${_obsKpi('活跃 Session', sess.collect_active || 0,
        `采集 ${sess.collect_active || 0} · 发现 ${sess.discover_active || 0}`,
        sessionTint)}
    </div>
    <div class="obs-grid2">
      ${_obsSchedulerCard(d.scheduler_jobs || [])}
      ${_obsSiteCard(site)}
    </div>
    <div class="obs-grid2">
      ${_obsProductsCard(updates)}
      ${_obsSessionCard(sess)}
    </div>
    <div class="obs-grid2">
      ${_obsDeliveryCard(del)}
      ${_obsQueueCard(dq, dg)}
    </div>
    <div class="obs-grid2">
      ${_obsCollectionCard(col, rules, proc, mmOn)}
      ${_obsCustomersCard()}
    </div>
    <div class="obs-grid2">
      ${_obsEventsCard(ev)}
      ${_obsStorageCard(st)}
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

function _obsProductsCard(updates){
  const items = updates.items || [];
  const rows = items.map(p => `<tr>
    <td>${escHtml(p.name)}</td>
    <td class="ta-right"><b>${p.count || 0}</b></td>
    <td class="muted-11">${_obsTime(p.last_published_at)}</td>
  </tr>`).join('');
  return `<div class="card">
    <div class="card-hd">📦 产品更新
      <span class="obs-muted ml-auto fs115 fw400">${_obsWindowLabel()} · ${items.length} 个产品</span>
    </div>
    <div class="card-bd pt0">
      <div class="obs-scroll obs-products-scroll"><table>
        <thead><tr><th>产品大类</th><th class="ta-right">更新包</th><th>最近更新</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="3" class="empty">${_obsWindowLabel()}暂无产品更新</td></tr>`}</tbody>
      </table></div>
    </div>
  </div>`;
}

function _obsSessionCard(sess){
  return `<div class="card">
    <div class="card-hd">🔑 Session 状态
      <span class="obs-muted ml-auto fs115 fw400">${sess.active || 0} / ${sess.total || 0} 活跃</span>
    </div>
    <div class="card-bd">
      <div class="obs-kv">
        <span>可用采集 Session</span><b class="${sess.collect_active ? 'text-success-text' : 'text-danger'}">${sess.collect_active || 0} / ${sess.collect_total || 0}</b>
        <span>可用发现 Session</span><b>${sess.discover_active || 0}</b>
        <span>采集 Session 污染</span><b class="${sess.collect_polluted ? 'text-warn' : ''}">${sess.collect_polluted || 0}</b>
        <span>已过期仍标记活跃</span><b class="${sess.active_but_expired ? 'text-danger' : ''}">${sess.active_but_expired || 0}</b>
        <span>过期 / 未知</span><b>${sess.expired || 0} / ${sess.unknown || 0}</b>
      </div>
      ${sess.collect_active ? '' : '<div class="obs-foot text-danger">⚠️ 没有可用的采集 Session，请到「Session」页处理。</div>'}
    </div>
  </div>`;
}

function _obsCustomersCard(){
  return `<div class="card">
    <div class="card-hd">
      <span>📨 订阅规则执行 · 客户推送</span>
      <span class="obs-muted ml-auto fs115 fw400">${_obsWindowLabel()} · 跟随右上角时间</span>
    </div>
    <div class="card-bd pane-clip">
      <div class="pane-list" id="custPushTable"></div>
      <div class="obs-foot">点击客户行查看推送历史</div>
    </div>
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
        <div><b class="${dq.overdue ? 'text-warn' : 'text-body'}">${dq.overdue || 0}</b><span>已逾期</span></div>
        <div><b class="${dq.retrying ? 'text-warn' : 'text-body'}">${dq.retrying || 0}</b><span>重试中</span></div>
        <div><b>${_obsAge(dq.oldest_age_minutes)}</b><span>最久等待</span></div>
      </div>
      <div class="obs-section-title mt14">汇总队列（按规则）</div>
      <div class="obs-scroll"><table>
        <thead><tr><th>规则</th><th>待发</th><th>最久等待</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="3" class="empty">暂无积压</td></tr>'}</tbody>
      </table></div>
    </div>
  </div>`;
}

function _obsCollectionCard(col, rules, proc, maintenanceOn){
  const stale = col.sources_stale || 0;
  return `<div class="card">
    <div class="card-hd">🔄 采集健康</div>
    <div class="card-bd">
      <div class="obs-kv">
        <span>调度器</span><b class="${col.enabled === false ? 'text-danger' : (col.enabled ? 'text-success-text' : '')}">${col.enabled === null || col.enabled === undefined ? '—' : (col.enabled ? '已启用' : '已关闭')}</b>
        <span>维护模式</span><b class="${maintenanceOn ? 'text-warn' : ''}">${maintenanceOn ? '已开启（推送静默）' : '已关闭'}</b>
        <span>当前状态</span><b>${col.is_running ? '采集中' : (col.current && col.current.phase ? escHtml(col.current.phase) : '空闲')}</b>
        <span>已运行时间</span><b>${_obsUptime((proc || {}).uptime_seconds)}</b>
        <span>进程启动</span><b>${_obsTime((proc || {}).started_at)}</b>
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
  const hours = _obsHours;
  const windowNum = (s) => {
    const n = by[s] || 0;
    return n
      ? `<span class="pointer text-primary fw600 underline" title="查看窗口内 ${s} 告警" data-on-click="openEventsCenterModal" data-args='${_dataArgs(s, hours, false)}'>${n}</span>`
      : '0';
  };
  const unackedNum = (s) => {
    if (s === 'INFO') return '—';
    const n = un[s] || 0;
    return n
      ? `<span class="pointer text-danger fw600 underline" title="查看全部未确认 ${s} 告警" data-on-click="openEventsCenterModal" data-args='${_dataArgs(s, 0, true)}'>${n}</span>`
      : '0';
  };
  const rows = ['CRITICAL', 'ERROR', 'WARNING', 'INFO'].map(s => `<tr>
    <td>${_obsSevBadge(s)}</td>
    <td>${windowNum(s)}</td>
    <td>${unackedNum(s)}</td>
  </tr>`).join('');
  return `<div class="card">
    <div class="card-hd">🔔 事件与告警
      <span class="obs-muted ml-auto fs115 fw400">窗口内 ${ev.window_total || 0} 条</span>
      <button class="btn btn-xs btn-soft-primary ml8 shrink0" data-on-click="openEventsCenterModal" data-args='${_dataArgs('', hours, false)}'>查看全部 ↗</button>
    </div>
    <div class="card-bd pt0">
      <div class="obs-scroll"><table>
        <thead><tr><th>级别</th><th>窗口内</th><th>未确认</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <div class="obs-foot">点击数字查看告警；未确认统计全部时间。</div>
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
    </div>
  </div>`;
}

function _obsSchedulerCard(jobs){
  const rows = (jobs || []).map(j => {
    const st = j.status === 'running'
      ? ['sched-ready', '运行中']
      : (j.status === 'stopped'
        ? ['sched-off', '已停止']
        : ['sched-idle', '未启用']);
    const next = j.next_run ? _obsTime(j.next_run) : '—';
    const countdown = j.seconds_until === null || j.seconds_until === undefined
      ? '—' : _obsCountdown(j.seconds_until);
    return `<tr>
      <td><span class="sched-dot ${st[0]} mr4" title="${st[1]}" aria-label="${st[1]}"></span>${escHtml(_obsJobLabel(j.id))}<span class="obs-muted fs105"> ${escHtml(j.id)}</span></td>
      <td>${next}</td>
      <td>${countdown}</td>
    </tr>`;
  }).join('');
  return `<div class="card">
    <div class="card-hd">⏱️ 调度任务
      <span class="obs-muted ml-auto fs115 fw400">绿点运行 · 红点停止 · 灰点未启用</span>
    </div>
    <div class="card-bd pt0">
      <div class="obs-scroll"><table>
        <thead><tr><th>任务</th><th>下次运行</th><th>倒计时</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="3" class="empty">调度器未启动</td></tr>'}</tbody>
      </table></div>
    </div>
  </div>`;
}

function _obsSiteCard(site){
  const status = site.status || 'unknown';
  const up = status === 'up';
  const down = status === 'down';
  const tone = up ? 'sched-ready' : (down ? 'sched-off' : 'sched-idle');
  const label = up ? '正常' : (down ? '不可用' : '未检测');
  const url = site.url || 'https://update.nsfocus.com/';
  const latency = site.latency_ms === null || site.latency_ms === undefined
    ? '—' : site.latency_ms + ' ms';
  return `<div class="card">
    <div class="card-hd">🌐 站点可用性
      <span class="ml-auto flex items-center gap6 fs115 fw400">
        <span class="sched-dot ${tone}" title="${label}" aria-label="${label}"></span>${label}
      </span>
    </div>
    <div class="card-bd">
      <div class="obs-kv">
        <span>监控地址</span><b><a class="text-primary" href="${escHtml(url)}" target="_blank" rel="noopener noreferrer">${escHtml(url)}</a></b>
        <span>HTTP 状态</span><b class="${down ? 'text-danger' : ''}">${site.http_status || '—'}</b>
        <span>响应耗时</span><b>${latency}</b>
        <span>上次检测</span><b>${_obsTime(site.checked_at)}</b>
        <span>连续失败</span><b class="${site.consecutive_failures ? 'text-danger' : ''}">${site.consecutive_failures || 0} 次</b>
        <span>最近可用</span><b>${_obsTime(site.last_ok_at)}</b>
      </div>
      ${_obsSiteChart(site)}
      ${down ? `<div class="obs-foot text-danger">${escHtml(site.error || '连接失败')}</div>` : ''}
      ${status === 'unknown' ? '<div class="obs-foot">等待首次检测，调度器每 5 分钟探测一次。</div>' : ''}
    </div>
  </div>`;
}

function _obsSiteChart(site){
  const points = Array.isArray(site.history) ? site.history : [];
  const sum = site.history_summary || {};
  const head = `<div class="obs-site-chart-hd"><span>延时曲线 · ${_obsWindowLabel()}</span><span>${_obsSiteMeta(sum)}</span></div>`;
  if (!points.length){
    return `<div class="obs-site-chart">${head}<div class="obs-site-empty">暂无历史采样，等待下一轮探测…</div></div>`;
  }

  const W = 600, H = 90, P = 6;
  const f = n => Math.round(n * 10) / 10;
  const times = points.map(p => _obsParseTime(p.t));
  const known = times.filter(t => t !== null);
  const first = known.length ? known[0] : null;
  const last = known.length ? known[known.length - 1] : null;
  const byIndex = first === null || last === null || last - first < 1000;
  const span = byIndex ? 0 : last - first;
  const xAt = (t, i) => {
    if (byIndex) return points.length === 1 ? W / 2 : P + (i / (points.length - 1)) * (W - 2 * P);
    return P + ((t - first) / span) * (W - 2 * P);
  };

  const ups = points.filter(p => p.s !== 'down' && p.ms !== null && p.ms !== undefined && isFinite(Number(p.ms)));
  const peak = Math.max(1, ...ups.map(p => Number(p.ms)));
  const top = Math.max(10, Math.ceil(peak / 10) * 10);
  const yAt = ms => H - P - (Math.max(0, Math.min(top, Number(ms))) / top) * (H - 2 * P);

  const marks = [];
  points.forEach((p, i) => {
    if (p.s !== 'down') return;
    const x = xAt(times[i], i);
    marks.push(`<line class="site-spark-fail" x1="${f(x)}" x2="${f(x)}" y1="${P}" y2="${H - P}"><title>${escHtml('不可用 · ' + _obsTime(p.t))}</title></line>`);
  });

  const shapes = [];
  let seg = [];
  let latest = null;
  const flush = () => {
    if (!seg.length) return;
    if (seg.length === 1){
      shapes.push(`<circle class="site-spark-dot" cx="${f(seg[0].x)}" cy="${f(seg[0].y)}" r="2.2"></circle>`);
    } else {
      const pts = seg.map(s => f(s.x) + ',' + f(s.y)).join(' ');
      const area = f(seg[0].x) + ',' + (H - P) + ' ' + pts + ' ' + f(seg[seg.length - 1].x) + ',' + (H - P);
      shapes.push(`<polygon class="site-spark-area" points="${area}"></polygon>`);
      shapes.push(`<polyline class="site-spark-line" points="${pts}"></polyline>`);
    }
    seg = [];
  };
  points.forEach((p, i) => {
    const bad = p.s === 'down' || p.ms === null || p.ms === undefined || !isFinite(Number(p.ms));
    if (bad){ flush(); return; }
    const pt = {x: xAt(times[i], i), y: yAt(p.ms)};
    seg.push(pt);
    latest = pt;
  });
  flush();
  if (latest) shapes.push(`<circle class="site-spark-dot" cx="${f(latest.x)}" cy="${f(latest.y)}" r="2.8"></circle>`);

  return `<div class="obs-site-chart">
    ${head}
    <svg class="site-spark" viewBox="0 0 ${W} ${H}" role="img" aria-label="站点延时曲线">
      <line class="site-spark-base" x1="${P}" y1="${H - P}" x2="${W - P}" y2="${H - P}"></line>
      ${marks.join('')}${shapes.join('')}
    </svg>
    <div class="obs-site-chart-ft"><span>${_obsTime(points[0].t)}</span><span>${_obsTime(points[points.length - 1].t)}</span></div>
  </div>`;
}

function _obsSiteMeta(sum){
  const parts = [];
  if (sum.max_ms !== null && sum.max_ms !== undefined) parts.push('峰值 ' + sum.max_ms + ' ms');
  if (sum.avg_ms !== null && sum.avg_ms !== undefined) parts.push('均值 ' + sum.avg_ms + ' ms');
  if (sum.failures) parts.push('<span class="text-danger">' + sum.failures + ' 次探测失败</span>');
  else if (sum.samples) parts.push('无失败');
  return parts.join(' · ') || '暂无采样';
}

function _obsPromCard(){
  return `<div class="card">
    <div class="card-hd">🔌 外部观测接入</div>
    <div class="card-bd">
      <div class="obs-code">
        <code id="obsPromUrl">${escHtml(obsPrometheusUrl())}</code>
        <button class="btn btn-outline btn-xs" data-on-click="obsCopyProm">复制地址</button>
      </div>
      <div class="obs-foot">抓取鉴权：设置 <code>MONITOR_METRICS_TOKEN</code> 后使用 <code>X-Metrics-Token</code> 或 <code>Authorization: Bearer &lt;token&gt;</code>。</div>
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
  return `<div class="obs-rate"><span>${pct}%</span><div class="obs-bar ${cls}"><i data-pct="${Math.max(2, pct)}"></i></div></div>`;
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
    nsfocus_site_check: '站点可用性',
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
function _obsParseTime(value){
  if (!value) return null;
  const text = String(value);
  const ms = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(text)
    ? Date.parse(text)
    : Date.parse(text.replace(' ', 'T') + 'Z');
  return isFinite(ms) ? ms : null;
}

function _obsTime(value){
  if (!value) return '—';
  const ms = _obsParseTime(value);
  if (ms === null) return escHtml(String(value).substring(0, 16));
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

function _obsUptime(seconds){
  if (seconds === null || seconds === undefined || isNaN(Number(seconds))) return '—';
  const v = Math.max(0, Math.floor(Number(seconds)));
  const d = Math.floor(v / 86400);
  const h = Math.floor((v % 86400) / 3600);
  const m = Math.floor((v % 3600) / 60);
  if (d > 0) return d + ' 天 ' + h + ' 小时 ' + m + ' 分';
  if (h > 0) return h + ' 小时 ' + m + ' 分';
  return m + ' 分';
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
