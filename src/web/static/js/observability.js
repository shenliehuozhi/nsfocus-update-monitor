// 经典脚本，无构建；仪表盘页面（/api/metrics 的可视化层）
let _obsHours = 24;
let _obsRequestSeq = 0;
let _cveSummary = null;
let _cveModalSeq = 0;
const _cveFilter = { severity: '', product: '', package_type: '', q: '', page: 1 };

async function observability(){
  document.getElementById('app').innerHTML = `
    <div class="card">
      <div class="card-hd items-center">
        <span>📈 仪表盘</span>
        <span class="fw400 fs115 text-muted" id="obsMeta"></span>
        <span class="ml-auto flex items-center gap8 wrap">
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
  await confirmAct('确认重启服务？服务将短暂中断，仅支持由 systemd 管理的实例。', async () => {
    const r = await api('/system/restart', { method: 'POST' });
    toast(r.code === 0 ? (r.message || '重启请求已接受') : '重启失败: ' + (r.message || '未知错误'), r.code === 0 ? 'success' : 'error');
  }, '🔄', '确认重启', true);
}

async function obsRefresh(manual){
  const root = document.getElementById('obs-root');
  if (!root) return;
  const seq = ++_obsRequestSeq;
  const navSeq = _navSeq;
  const btn = document.getElementById('obsRefreshBtn');
  if (btn) { btn.disabled = true; btn.textContent = '刷新中…'; }
  const [r, cveR] = await Promise.all([
    api('/metrics?hours=' + _obsHours),
    api('/cve/summary?days=' + _obsWindowDays(_obsHours)),
  ]);
  if (seq !== _obsRequestSeq || navStale(navSeq) || root !== document.getElementById('obs-root')) return;
  if (btn) { btn.disabled = false; btn.textContent = '↻ 刷新'; }
  if (r.code !== 0 || !r.data) {
    root.innerHTML = `<div class="empty">加载失败：${escHtml(r.message || '未知错误')}</div>`;
    return;
  }
  _cveSummary = (cveR && cveR.code === 0 && cveR.data) ? cveR.data : null;
  root.innerHTML = _obsRender(r.data, _cveSummary);
  _applyPctBars(root);
  _applyCveBars(root);
  _renderCustPushTable((r.data.customers || {}).items || [], _obsWindowDays(_obsHours), _obsWindowLabel());
  const meta = document.getElementById('obsMeta');
  if (meta) meta.textContent = '更新于 ' + _obsTime(r.data.generated_at) + ' · 30 秒自动刷新';
  if (manual) toast('观测数据已刷新');
}

function _obsRender(d, cve){
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
      ${_obsCveCard(cve)}
      ${_obsProductsCard(updates)}
    </div>
    <div class="obs-grid2">
      ${_obsSessionCard(sess)}
      ${_obsCollectionCard(col, rules, proc, mmOn)}
    </div>
    <div class="obs-grid2">
      ${_obsDeliveryCard(del)}
      ${_obsQueueCard(dq, dg)}
    </div>
    <div class="obs-grid2">
      ${_obsCustomersCard()}
    </div>
    <div class="obs-grid2">
      ${_obsEventsCard(ev)}
      ${_obsStorageCard(st)}
    </div>
    <div class="obs-grid2">
      ${_obsDatabaseCard(d.database || {})}
      ${_obsPromCard()}
    </div>`;
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
        <span>心跳异常但仍标记活跃</span><b class="${sess.active_but_expired ? 'text-danger' : ''}">${sess.active_but_expired || 0}</b>
        <span>过期 / 未知</span><b>${sess.expired || 0} / ${sess.unknown || 0}</b>
      </div>
      ${sess.collect_active ? '' : '<div class="obs-foot text-danger">⚠️ 没有可用的采集 Session，请到「Session」页处理。</div>'}
    </div>
  </div>`;
}

function _obsCustomersCard(){
  return `<div class="card obs-span2">
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

function _obsCveCard(cve){
  const win = _cveWindowLabel();
  const head = `<div class="card-hd">🛡️ CVE 情报
      <span class="obs-muted ml8 fs115 fw400">${win} · 按升级包发布日期统计</span>
      <button class="btn btn-xs btn-soft-primary ml-auto shrink0" data-on-click="openCveModal">查看全部 ↗</button>
    </div>`;
  if (!cve){
    return `<div class="card">${head}
      <div class="card-bd"><div class="empty">CVE 数据暂不可用，随仪表盘自动重试</div></div>
    </div>`;
  }
  const t = cve.totals || {}, sev = cve.by_severity || {};
  const total = t.distinct_cves || 0;
  const scored = t.scored_cves || 0;
  const unscored = t.unscored_cves || 0;
  const cnvd = t.cnvd_cves || 0;
  const coverage = total ? Math.round(scored / total * 100) : 0;
  const stats = [
    ['严重', sev.critical || 0, 'critical'],
    ['高危', sev.high || 0, 'high'],
    ['中危', sev.medium || 0, 'medium'],
    ['未评分', unscored, 'unknown'],
    ['评分覆盖', coverage + '%', 'coverage'],
  ].map(item => `<div class="cve-stat cve-stat-${item[2]}">
      <b>${item[1]}</b><span>${item[0]}</span>
    </div>`).join('');

  const trend = Array.isArray(cve.trend) ? cve.trend : [];
  const dayTotal = p => (p.critical || 0) + (p.high || 0) + (p.medium || 0) + (p.low || 0) + (p.unknown || 0);
  const peak = Math.max(1, ...trend.map(dayTotal));
  const bars = trend.map(p => {
    const n = dayTotal(p);
    const tone = p.critical ? 'critical' : (p.high ? 'high' : (p.medium ? 'medium' : (p.low ? 'low' : 'unknown')));
    const h = n ? Math.max(6, Math.round(n / peak * 100)) : 2;
    const tip = p.date + ' · 严重 ' + (p.critical || 0) + ' · 高危 ' + (p.high || 0)
      + ' · 中危 ' + (p.medium || 0) + ' · 低危 ' + (p.low || 0) + ' · 未评分 ' + (p.unknown || 0);
    return `<span class="cve-spark-bar cve-spark-${tone}" data-h="${h}" title="${escHtml(tip)}"></span>`;
  }).join('');

  const top = (cve.top_cves || []).slice(0, 3).map(c => {
    const kinds = (c.pkg_kinds || []).map(k => k === 'rule' ? '规则包' : '系统包').join(' + ');
    const name = _cveName(c);
    return `<div class="cve-top-row">
      <div class="cve-top-line">
        ${_cveSevBadge(c.severity)}
        <span class="cve-top-id" data-on-click="cveOpenFor" data-args='${_dataArgs(c.cve_id)}' title="在弹窗中筛选 ${escHtml(c.cve_id)}">${escHtml(c.cve_id)}</span>
        <span class="cve-top-score">${_cveScore(c.cvss_score)}</span>
        <span class="cve-top-links">${c.links || 0} 条</span>
      </div>
      <div class="cve-top-sub">
        ${_cveCnvdLink(c)}
        <span class="cve-top-name${name ? '' : ' pending'}" title="${escHtml(_cveNameHint(c) || '名称待同步')}">${escHtml(name || '名称待同步')}</span>
        <span class="cve-top-meta">${c.device_count || 0} 个产品${kinds ? ' · ' + escHtml(kinds) : ''} · 最近 ${_obsTime(c.latest_at)}</span>
      </div>
    </div>`;
  }).join('');

  return `<div class="card">
    ${head}
    <div class="card-bd">
      <div class="cve-card-body">
        <div class="cve-stats">${stats}</div>
        <div class="cve-spark-wrap">
          <div class="cve-spark">${bars || '<div class="cve-spark-empty">窗口内暂无关联 CVE</div>'}</div>
          <div class="cve-spark-ft"><span>${escHtml((trend[0] || {}).date || '')}</span><span>${escHtml((trend[trend.length - 1] || {}).date || '')}</span></div>
        </div>
      </div>
      <div class="cve-top">${top || '<div class="empty">窗口内暂无关联 CVE</div>'}</div>
      <div class="obs-foot">「关联 CVE」来自升级包描述与规则包历史覆盖，不等同于本次修复；评分由 NVD / CNA 补齐${unscored ? `，尚有 ${unscored} 个待评分` : ''}${cnvd ? `；已关联 CNVD ${cnvd} 个` : ''}。</div>
    </div>
  </div>`;
}

function _applyCveBars(root){
  if (!root || !root.querySelectorAll) return;
  root.querySelectorAll('[data-h]').forEach(el => {
    el.style.height = el.getAttribute('data-h') + '%';
  });
}

function _cveWindowLabel(){
  const days = _obsWindowDays(_obsHours);
  return days === 1 ? '最近 1 天' : '最近 ' + days + ' 天';
}

function _cveName(item){
  if (!item) return '';
  return _cveClean(item.name_zh) || _cveClean(item.description_zh) || _cveClean(item.description);
}

function _cveClean(value){
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function _cveNameHint(item){
  const zh = _cveClean(item && item.name_zh);
  const en = _cveClean(item && item.description);
  const hint = (zh && en && zh !== en) ? zh + ' · ' + en : (zh || en);
  return hint.slice(0, 300);
}

function _cveCnvdLink(item){
  const id = _cveClean(item && item.cnvd_id).toUpperCase();
  if (!/^CNVD-\d{4}-\d+$/.test(id)) return '';
  const url = 'https://www.cnvd.org.cn/flaw/show/' + id;
  return `<a class="cve-cnvd" href="${escHtml(url)}" target="_blank" rel="noopener noreferrer" title="在 CNVD 查看 ${escHtml(id)}">${escHtml(id)}</a>`;
}

function _cveSevBadge(sev){
  const map = {
    critical: ['严重', 'critical'],
    high: ['高危', 'high'],
    medium: ['中危', 'medium'],
    low: ['低危', 'low'],
    unknown: ['未评分', 'unknown'],
  };
  const item = map[sev] || [sev || '未知', 'unknown'];
  return `<span class="cve-sev cve-sev-${item[1]}">${escHtml(item[0])}</span>`;
}

function _cveScore(score){
  if (score === null || score === undefined || isNaN(Number(score))) {
    return '<span class="obs-muted">—</span>';
  }
  const v = Number(score);
  const tone = v >= 9 ? 'critical' : (v >= 7 ? 'high' : (v >= 4 ? 'medium' : 'low'));
  return `<b class="cve-score cve-score-${tone}">${v.toFixed(1)}</b>`;
}

function openCveModal(){
  _cveFilter.severity = '';
  _cveFilter.product = '';
  _cveFilter.package_type = '';
  _cveFilter.q = '';
  _cveFilter.page = 1;
  return _showCveModal();
}

function _showCveModal(){
  let m = document.getElementById('cveModal');
  if (!m){
    m = document.createElement('div');
    m.id = 'cveModal';
    m.innerHTML = `
      <div class="modal-overlay" data-on-click="closeCveModal" data-self>
        <div class="card modal-card-cve">
          <div class="card-hd flex items-center">
            <span>🛡️ CVE 情报</span>
            <span class="ml8 muted-11-400" id="cveModalCount"></span>
            <span class="ml-auto pointer text-gray-muted fs18 lh1 px4" data-on-click="closeCveModal">×</span>
          </div>
          <div class="cve-toolbar">
            <div class="cve-filter-chips">
              <button class="cve-filter-chip" data-cve-sev="" data-on-click="cveSetSeverity" data-args='${_dataArgs('')}'>全部</button>
              <button class="cve-filter-chip" data-cve-sev="critical" data-on-click="cveSetSeverity" data-args='${_dataArgs('critical')}'>严重</button>
              <button class="cve-filter-chip" data-cve-sev="high" data-on-click="cveSetSeverity" data-args='${_dataArgs('high')}'>高危</button>
              <button class="cve-filter-chip" data-cve-sev="medium" data-on-click="cveSetSeverity" data-args='${_dataArgs('medium')}'>中危</button>
              <button class="cve-filter-chip" data-cve-sev="low" data-on-click="cveSetSeverity" data-args='${_dataArgs('low')}'>低危</button>
              <button class="cve-filter-chip" data-cve-sev="unknown" data-on-click="cveSetSeverity" data-args='${_dataArgs('unknown')}'>未评分</button>
            </div>
            <select id="cveProduct" class="dd sel-sm" data-on-change="cveFilterChange" data-args='${_dataArgs('product', '$value')}'></select>
            <select id="cvePackage" class="dd sel-sm" data-on-change="cveFilterChange" data-args='${_dataArgs('package_type', '$value')}'></select>
            <input id="cveSearch" class="input-sm cve-search" placeholder="搜索 CVE / CNVD / 名称 / 文件 / 版本" data-on-keydown="cveSearchEnter" data-key="Enter">
            <button class="btn btn-outline btn-sm shrink0" data-on-click="cveSearchEnter">搜索</button>
            <button class="btn btn-outline btn-sm shrink0" data-on-click="cveResetFilters">重置</button>
          </div>
          <div class="card-bd flex-1 o-auto p0" id="cveModalBody"><div class="p40 ta-center muted-13">加载中…</div></div>
          <div class="card-bd cve-modal-ft">
            <span class="muted-11-400" id="cveModalPageInfo"></span>
            <span class="ml-auto flex gap8 items-center">
              <button class="btn btn-outline btn-sm" id="cvePrevBtn" data-on-click="cveGoPage" data-args='${_dataArgs(-1)}'>上一页</button>
              <button class="btn btn-outline btn-sm" id="cveNextBtn" data-on-click="cveGoPage" data-args='${_dataArgs(1)}'>下一页</button>
            </span>
          </div>
          <div class="card-bd evt-foot-end">
            <button class="btn btn-outline" data-on-click="closeCveModal">关闭</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(m);
  }
  m.classList.remove('hidden');
  _cveSyncControls();
  return loadCveModal();
}

function closeCveModal(){
  _cveModalSeq++;
  const m = document.getElementById('cveModal');
  if (m) m.classList.add('hidden');
}

function cveOpenFor(cveId){
  _cveFilter.severity = '';
  _cveFilter.product = '';
  _cveFilter.package_type = '';
  _cveFilter.q = cveId || '';
  _cveFilter.page = 1;
  return _showCveModal();
}

function _cveFillSelect(el, placeholder, items, current){
  if (!el) return;
  const values = (items || []).slice();
  const opts = [`<option value="">${placeholder}</option>`];
  values.forEach(v => opts.push(`<option value="${escHtml(v)}">${escHtml(v)}</option>`));
  if (current && values.indexOf(current) < 0){
    opts.push(`<option value="${escHtml(current)}">${escHtml(current)}</option>`);
  }
  el.innerHTML = opts.join('');
  el.value = current || '';
}

function _cveSyncControls(){
  document.querySelectorAll('#cveModal [data-cve-sev]').forEach(el => {
    el.classList.toggle('active', el.getAttribute('data-cve-sev') === _cveFilter.severity);
  });
  const search = document.getElementById('cveSearch');
  if (search) search.value = _cveFilter.q;
  const devices = ((_cveSummary || {}).by_device || []).map(d => d.product_name);
  const pkgs = ((_cveSummary || {}).by_package_type || []).map(p => p.package_type);
  _cveFillSelect(document.getElementById('cveProduct'), '全部产品线', devices, _cveFilter.product);
  _cveFillSelect(document.getElementById('cvePackage'), '全部升级包类型', pkgs, _cveFilter.package_type);
}

function cveSetSeverity(value){
  _cveFilter.severity = value || '';
  _cveFilter.page = 1;
  _cveSyncControls();
  return loadCveModal();
}

function cveFilterChange(key, value){
  if (key !== 'product' && key !== 'package_type') return;
  _cveFilter[key] = value || '';
  _cveFilter.page = 1;
  return loadCveModal();
}

function cveSearchEnter(){
  const el = document.getElementById('cveSearch');
  const q = el ? el.value.trim() : '';
  if (q === _cveFilter.q) return;
  _cveFilter.q = q;
  _cveFilter.page = 1;
  return loadCveModal();
}

function cveResetFilters(){
  _cveFilter.severity = '';
  _cveFilter.product = '';
  _cveFilter.package_type = '';
  _cveFilter.q = '';
  _cveFilter.page = 1;
  _cveSyncControls();
  return loadCveModal();
}

function cveGoPage(delta){
  const step = Number(delta) || 0;
  const target = _cveFilter.page + step;
  if (target < 1) return;
  const btn = document.getElementById(step < 0 ? 'cvePrevBtn' : 'cveNextBtn');
  if (btn && btn.disabled) return;
  _cveFilter.page = target;
  return loadCveModal();
}

function _cveUpdatePager(total, limit){
  const size = Math.max(1, limit || 50);
  const pages = Math.max(1, Math.ceil(total / size));
  const page = Math.min(Math.max(1, _cveFilter.page), pages);
  _cveFilter.page = page;
  const info = document.getElementById('cveModalPageInfo');
  if (info) info.textContent = total ? `共 ${total} 条 · 第 ${page} / ${pages} 页` : '暂无关联记录';
  const prev = document.getElementById('cvePrevBtn');
  const next = document.getElementById('cveNextBtn');
  if (prev) prev.disabled = page <= 1;
  if (next) next.disabled = !total || page >= pages;
}

function _cveSourceUrl(url){
  if (!url) return '';
  if (/^https?:\/\//i.test(url)) return url;
  return 'https://update.nsfocus.com' + (url.charAt(0) === '/' ? '' : '/') + url;
}

function _cveTableHtml(items){
  const rows = items.map(it => {
    const kind = it.pkg_kind === 'rule' ? '规则包' : '系统包';
    const meta = it.cvss_version ? 'CVSS ' + escHtml(it.cvss_version)
      : (it.meta_status === 'pending' ? '待补评分' : '');
    const confidence = Math.round((Number(it.confidence) || 0) * 100);
    const name = _cveName(it);
    const sourceUrl = _cveSourceUrl(it.source_url);
    const file = it.file_name
      ? (sourceUrl
        ? `<a class="text-primary" href="${escHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">${escHtml(it.file_name)}</a>`
        : escHtml(it.file_name))
      : '—';
    const version = escHtml(it.version_branch || it.package_version || '—');
    return `<tr>
      <td>
        <div class="cve-id">${escHtml(it.cve_id)}</div>
        <div class="cve-name-row">
          <div class="cve-name${name ? '' : ' pending'}" title="${escHtml(_cveNameHint(it) || '名称待同步')}">${escHtml(name || '名称待同步')}</div>
          ${_cveCnvdLink(it)}
        </div>
        <div class="cve-evidence" title="${escHtml(it.evidence || '')}">${escHtml(it.evidence || '')}</div>
      </td>
      <td class="cve-cell-score">${_cveScore(it.cvss_score)}
        <div class="cve-cell-sub">${_cveSevBadge(it.severity)}${meta ? ' <span>' + meta + '</span>' : ''}</div>
      </td>
      <td>${escHtml(it.product_name || '—')}<div class="cve-cell-sub">${kind}</div></td>
      <td>${escHtml(it.package_type || '—')}</td>
      <td><div>${version}</div><div class="cve-cell-sub">${file}</div><div class="cve-cell-sub">${_obsTime(it.released_at)}</div></td>
      <td class="ta-right"><span class="cve-conf${confidence < 60 ? ' low' : ''}">${confidence}%</span></td>
    </tr>`;
  }).join('');
  return `<div class="obs-scroll"><table class="cve-table">
    <thead><tr><th>CVE</th><th>评分 / 严重度</th><th>设备（产品线）</th><th>升级包类型</th><th>版本 / 发布</th><th class="ta-right">置信度</th></tr></thead>
    <tbody>${rows || '<tr><td colspan="6" class="empty">当前筛选条件下没有关联 CVE</td></tr>'}</tbody>
  </table></div>`;
}

async function loadCveModal(){
  const body = document.getElementById('cveModalBody');
  if (!body) return;
  const seq = ++_cveModalSeq;
  const navSeq = _navSeq;
  const params = new URLSearchParams();
  params.set('days', String(_obsWindowDays(_obsHours)));
  if (_cveFilter.severity) params.set('severity', _cveFilter.severity);
  if (_cveFilter.product) params.set('product', _cveFilter.product);
  if (_cveFilter.package_type) params.set('package_type', _cveFilter.package_type);
  if (_cveFilter.q) params.set('q', _cveFilter.q);
  params.set('page', String(_cveFilter.page));
  body.innerHTML = '<div class="p40 ta-center muted-13">加载中…</div>';
  const r = await api('/cve/list?' + params.toString());
  if (seq !== _cveModalSeq || navStale(navSeq) || body !== document.getElementById('cveModalBody')) return;
  if (r.code !== 0 || !r.data){
    body.innerHTML = `<div class="empty">加载失败：${escHtml(r.message || '未知错误')}</div>`;
    _cveUpdatePager(0, 0);
    return;
  }
  const d = r.data;
  body.innerHTML = _cveTableHtml(d.items || []);
  _cveUpdatePager(d.total || 0, d.limit || 50);
  const count = document.getElementById('cveModalCount');
  if (count) count.textContent = '关联 CVE · ' + _cveWindowLabel();
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
    <div class="card-hd">🔄 采集健康
      <button class="btn btn-outline btn-xs ml-auto" data-on-click="triggerCollect" ${col.is_running ? 'disabled' : ''}>${col.is_running ? '采集中' : '立即采集'}</button>
    </div>
    <div class="card-bd">
      <div class="obs-kv">
        <span>调度器</span><b class="${col.enabled === false ? 'text-danger' : (col.enabled ? 'text-success-text' : '')}">${col.enabled === null || col.enabled === undefined ? '—' : (col.enabled ? '已启用' : '已关闭')}</b>
        <span>维护模式</span><b class="${maintenanceOn ? 'text-warn' : ''}">${maintenanceOn ? '已开启（推送静默）' : '已关闭'}</b>
        <span>当前状态</span><b>${col.is_running ? '采集中' : (col.current && col.current.phase ? escHtml(col.current.phase) : '空闲')}</b>
        <span>服务已运行时间</span><b>${_obsUptime((proc || {}).uptime_seconds)}</b>
        ${col.is_running && col.current ? `<span>本轮已运行时间</span><b>${_obsDur(col.current.duration_s)}</b>` : ''}
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

function _obsDatabaseCard(db){
  const errors = db.errors || 0;
  const queueTimeouts = db.python_lock_timeouts || 0;
  const busyTimeouts = db.sqlite_busy_errors || 0;
  const rate = db.timeout_rate;
  const rateText = (rate === null || rate === undefined) ? '—'
    : (rate > 0 && rate < 0.01 ? '&lt;1%' : Math.round(rate * 100) + '%');
  const budget = (db.wait_budget_seconds === null || db.wait_budget_seconds === undefined)
    ? '—' : db.wait_budget_seconds + ' 秒';
  const rows = [['lock_wait', 'Python 队列'], ['sqlite', 'SQLite 执行'], ['total', '合计']]
    .map(([key, label]) => {
      const p = db[key + '_ms'] || {};
      // 与后端 db_write 告警同口径：单次写超过 1 秒即视为慢写
      const slow = key === 'total' && Number(p.p95) >= 1000;
      return `<tr>
      <td>${label}</td>
      <td>${_obsMs(p.p50)}</td>
      <td class="${slow ? 'text-danger fw600' : ''}">${_obsMs(p.p95)}</td>
      <td>${_obsMs(p.p99)}</td>
    </tr>`;
    }).join('');
  return `<div class="card">
    <div class="card-hd">🗄️ 数据库写
      <span class="obs-muted ml-auto fs115 fw400">预算 ${budget} · 超时率 <b class="${rate ? 'text-danger' : ''}">${rateText}</b></span>
    </div>
    <div class="card-bd">
      <div class="obs-mini">
        <div><b>${db.operations || 0}</b><span>写操作</span></div>
        <div><b class="${errors ? 'text-danger' : 'text-body'}">${errors}</b><span>失败</span></div>
        <div><b class="${queueTimeouts ? 'text-danger' : 'text-body'}">${queueTimeouts}</b><span>队列超时</span></div>
        <div><b class="${busyTimeouts ? 'text-danger' : 'text-body'}">${busyTimeouts}</b><span>SQLite 超时</span></div>
      </div>
      <div class="obs-section-title mt14">写入耗时 · 最近 ${db.sample_count || 0} 次（上限 ${db.sample_limit || 0}）</div>
      <div class="obs-scroll"><table>
        <thead><tr><th>阶段</th><th>p50</th><th>p95</th><th>p99</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <div class="obs-foot">队列与 SQLite 共用同一等待预算；计数自本次启动累计，分位数只覆盖最近样本。</div>
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
        <button class="btn btn-outline btn-xs shrink0" data-on-click="obsCopyProm">复制地址</button>
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
    nsfocus_db_backup: '数据库备份',
    nsfocus_db_health: '数据库健康',
    nsfocus_cve_extract: 'CVE 提取',
    nsfocus_cve_extract_full: 'CVE 全量重扫',
    nsfocus_cve_meta: 'CVE 评分同步',
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

function _obsMs(value){
  if (value === null || value === undefined || isNaN(Number(value))) return '—';
  const v = Math.max(0, Number(value));
  if (v >= 1000) return (v / 1000).toFixed(v < 10000 ? 2 : 1) + ' 秒';
  return (v < 10 ? v.toFixed(1) : String(Math.round(v))) + ' ms';
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
