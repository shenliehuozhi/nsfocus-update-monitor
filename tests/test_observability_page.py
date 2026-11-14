"""仪表盘页面上线门禁。

页面必须能从主导航进入、在 bootstrap 之前加载（bootstrap 立即调用 showPage），
并从 /api/metrics 渲染投递、队列、采集、事件与调度等观测分区。
"""
import re
import shutil
import subprocess

import pytest

from tests.frontend_source import INDEX_HTML, WEB, js_source


def test_nav_and_page_registered():
    html = INDEX_HTML.read_text(encoding='utf-8')
    assert 'data-page="observability"' in html
    assert '>仪表盘</a>' in html
    assert 'data-page="dashboard"' not in html

    source = js_source()
    assert re.search(r'async function observability\(\)', source)
    assert 'logs,observability,tools:toolsPage' in source


def test_observability_script_loads_before_bootstrap():
    html = INDEX_HTML.read_text(encoding='utf-8')
    assert html.index('/static/js/observability.js') < html.index('/static/js/bootstrap.js')


def test_page_renders_observability_sections():
    source = (WEB / 'static/js/observability.js').read_text(encoding='utf-8')
    for marker in ('/metrics?hours=', '投递成功率', '队列积压', '采集健康',
                   '事件与告警', '存储与数据量', '调度任务',
                   'scheduler_jobs', 'format=prometheus', 'MONITOR_METRICS_TOKEN',
                   'updates', 'customers', 'sessions', 'process',
                   '已运行时间', '产品更新', '调度器', 'obs-alert', 'obsRestartService',
                   '运行中', '已停止', '未启用',
                   '站点可用性', 'nsfocus_site_check', '维护模式', 'maintenance',
                   'site-spark', '延时曲线', 'history_summary', '_obsSiteChart',
                   'openEventsCenterModal', '_obsWindowLabel',
                   '_obsDatabaseCard', '数据库写', '_obsMs', '写入耗时',
                   "'lock_wait', 'Python 队列'", "'sqlite', 'SQLite 执行'", "'total', '合计'",
                   'python_lock_timeouts', 'sqlite_busy_errors', 'timeout_rate',
                   'wait_budget_seconds', 'sample_limit'):
        assert marker in source, f'观测页缺少 {marker}'
    assert '<th>状态</th>' not in source
    assert 'obsToday' not in source
    assert '今日概览' not in source
    assert 'undated_failed' in source
    assert 'sources_stale' in source
    # 规则名/渠道名来自数据库，渲染时必须转义
    assert '${escHtml(r.rule_name' in source
    assert '${escHtml(_obsChannel(ch.channel_type))}' in source
    # 数据库写卡片与外部观测接入同排，半宽下复制按钮不能被压缩
    assert re.search(r'_obsDatabaseCard\(d\.database \|\| \{\}\)\}\s*\$\{_obsPromCard\(\)\}', source)
    assert re.search(r'btn-xs shrink0" data-on-click="obsCopyProm"', source)


def test_observability_grid_contains_wide_tables():
    css = (WEB / 'static/css/app.css').read_text(encoding='utf-8')
    columns = re.findall(r'\.obs-grid2\s*\{[^}]*grid-template-columns:([^;}]+)', css)
    assert columns == ['repeat(2,minmax(0,1fr))', 'minmax(0,1fr)']
    assert re.search(r'\.obs-grid2>\.card\s*\{[^}]*min-width:0', css)
    assert re.search(r'\.obs-scroll\s*\{[^}]*overflow-x:auto', css)


def test_auto_refresh_stops_when_leaving_page():
    page = (WEB / 'static/js/observability.js').read_text(encoding='utf-8')
    core = (WEB / 'static/js/core.js').read_text(encoding='utf-8')

    assert 'setInterval' in page and 'clearInterval' in page
    # 切页时统一清理页面级定时器，观测页只是其中一项
    assert '_clearPageTimers(_currentPage)' in core
    assert "page==='observability'" in core
    assert 'window._obsTimer' in core
    # 退出登录同样要停表
    assert '_clearAllTimers();_currentPage=null;' in core
    assert "closeEventsCenterModal()" in core


def _run_js(body):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for runtime checks')
    source = (WEB / 'static/js/observability.js').read_text(encoding='utf-8')
    script = """
const assert = require('node:assert/strict');
const elements = {};
const document = {getElementById: id => elements[id] || null};
const window = {};
let _navSeq = 1;
const navStale = seq => seq !== _navSeq;
const escHtml = value => String(value || '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const notices = [];
const toast = (...args) => notices.push(args);
const _applyPctBars = () => {};
const _renderCustPushTable = () => {};
""" + source + '\n(async () => {\n' + body + '\n})().catch(e => {console.error(e); process.exitCode = 1;});'
    result = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_product_card_escapes_names_and_displays_cst():
    _run_js("""
const html = _obsProductsCard({items:[{name:'<img src=x onerror=alert(1)>', count:2, last_published_at:'2026-11-11T16:00:00Z'}]});
assert.ok(html.includes('产品更新') && html.includes('最近 24 小时'));
assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
assert.ok(!html.includes('<img'));
assert.ok(html.includes('2026-11-12 00:00'));
assert.ok(_obsProductsCard({items:[]}).includes('暂无产品更新'));
assert.ok(_obsRateCell(null).includes('—'));
assert.ok(_obsRateCell(0).includes('0%'));
assert.equal(_obsUptime(90061), '1 天 1 小时 1 分');
""")


def test_database_card_shows_lock_wait_split_and_flags_slow_writes():
    _run_js("""
const stats = {operations:120, errors:3, timeouts:2, python_lock_timeouts:1, sqlite_busy_errors:1,
  sample_count:120, sample_limit:1024, wait_budget_seconds:10, timeout_rate:0.5,
  lock_wait_ms:{p50:1.5, p95:12, p99:30}, sqlite_ms:{p50:2.5, p95:6, p99:null},
  total_ms:{p50:4, p95:1800, p99:4000}};
const html = _obsDatabaseCard(stats);
assert.ok(html.includes('数据库写'));
assert.ok(html.includes('预算 10 秒'));
assert.ok(html.includes('超时率 <b class="text-danger">50%</b>'));
assert.ok(html.includes('队列超时') && html.includes('SQLite 超时'));
assert.ok(html.includes('最近 120 次（上限 1024）'));
assert.ok(html.includes('<th>p50</th><th>p95</th><th>p99</th>'));
assert.ok(html.includes('1.80 秒') && html.includes('4.00 秒'));
assert.ok(html.includes('text-danger fw600'));
assert.ok(html.includes('<td>—</td>'));
assert.ok(_obsDatabaseCard({...stats, timeout_rate:0.001}).includes('&lt;1%'));
const quiet = _obsDatabaseCard({...stats, errors:0, timeouts:0, python_lock_timeouts:0,
  sqlite_busy_errors:0, timeout_rate:0, total_ms:{p50:4, p95:18, p99:40}});
assert.ok(!quiet.includes('text-danger'));
assert.ok(quiet.includes('超时率 <b class="">0%</b>'));
assert.ok(quiet.includes('18 ms'));
const empty = _obsDatabaseCard({});
assert.ok(empty.includes('预算 —'));
assert.ok(empty.includes('超时率 <b class="">—</b>'));
assert.ok(empty.includes('最近 0 次（上限 0）'));
assert.ok(!empty.includes('text-danger'));
assert.equal((empty.match(/<td[^>]*>—<\\/td>/g) || []).length, 9);
assert.equal(_obsMs(null), '—');
assert.equal(_obsMs(undefined), '—');
assert.equal(_obsMs(0), '0.0 ms');
assert.equal(_obsMs(9.96), '10.0 ms');
assert.equal(_obsMs(12.34), '12 ms');
assert.equal(_obsMs(999), '999 ms');
assert.equal(_obsMs(1000), '1.00 秒');
assert.equal(_obsMs(12000), '12.0 秒');
""")


def test_refresh_ignores_out_of_order_navigation_and_old_roots():
    _run_js("""
const requests = [];
api = path => new Promise(resolve => requests.push({path, resolve}));
_obsRender = data => data.marker;
elements['obs-root'] = {innerHTML:'initial'};
elements.obsRefreshBtn = {disabled:false};
const old = obsRefresh(false);
_obsHours = 720;
const latest = obsRefresh(false);
assert.equal(requests[1].path, '/metrics?hours=720');
requests[1].resolve({code:0, data:{marker:'latest'}});
await latest;
requests[0].resolve({code:0, data:{marker:'old'}});
await old;
assert.equal(elements['obs-root'].innerHTML, 'latest');
const leaving = obsRefresh(true);
_navSeq++;
requests[2].resolve({code:0, data:{marker:'stale navigation'}});
await leaving;
assert.equal(elements['obs-root'].innerHTML, 'latest');
assert.equal(notices.length, 0);
const replaced = obsRefresh(false);
elements['obs-root'] = {innerHTML:'new root'};
requests[3].resolve({code:0, data:{marker:'stale root'}});
await replaced;
assert.equal(elements['obs-root'].innerHTML, 'new root');
const error = obsRefresh(false);
requests[4].resolve({code:-1, message:'<bad>'});
await error;
assert.ok(elements['obs-root'].innerHTML.includes('&lt;bad&gt;'));
assert.equal(elements.obsRefreshBtn.disabled, false);
delete elements['obs-root'];
await obsRefresh(false);
assert.equal(requests.length, 5);
""")


def test_restart_requires_confirmation_and_handles_failure():
    _run_js("""
const calls = [];
let confirmCallback;
confirmAct = (message, callback) => {confirmCallback = callback;};
api = async (path, opts) => {calls.push({path, opts}); return {code:-1, message:'not managed'};};
await obsRestartService();
assert.equal(calls.length, 0);
confirmCallback = null;
assert.equal(calls.length, 0);
await obsRestartService();
await confirmCallback();
assert.deepEqual(calls, [{path:'/system/restart', opts:{method:'POST'}}]);
assert.equal(notices[0][1], 'error');
""")


def test_event_panels_ignore_old_filters_and_closed_modals():
    events = (WEB / 'static/js/events_center.js').read_text(encoding='utf-8')
    _run_js(events + """
const requests = [];
api = path => new Promise(resolve => requests.push({path, resolve}));
renderEventsTable = rows => rows[0].marker;
elements.eventsPageBody = {innerHTML:'initial'};
elements.eventsPageSev = {value:'ERROR'};
const older = loadEventsPage();
elements.eventsPageSev.value = 'WARNING';
const newer = loadEventsPage();
requests[1].resolve({code:0, data:{events:[{marker:'warning'}], total:1}});
await newer;
requests[0].resolve({code:0, data:{events:[{marker:'error'}], total:1}});
await older;
assert.equal(elements.eventsPageBody.innerHTML, 'warning');
let hidden = false;
elements.eventsCenterModal = {classList:{contains:() => hidden, add:() => {hidden = true;}}};
elements.eventsCenterModalBody = {innerHTML:'modal'};
_eventsState(EVENTS_SCOPE_MODAL).loadedOnce = true;
const modalRequest = loadEventsCenterModal();
closeEventsCenterModal();
requests[2].resolve({code:0, data:{events:[{marker:'late'}], total:1}});
await modalRequest;
assert.equal(elements.eventsCenterModalBody.innerHTML, 'modal');
await loadEventsCenterModal();
assert.equal(requests.length, 3);
""")


def test_event_ack_failure_is_visible_and_success_refreshes_counts():
    events = (WEB / 'static/js/events_center.js').read_text(encoding='utf-8')
    _run_js(events + """
let reloaded = 0, refreshed = 0;
_loadEventsPanel = () => {reloaded++;};
obsRefresh = () => {refreshed++;};
elements['obs-root'] = {};
api = async () => ({code:-1, message:'ack failed'});
await _ackEventAndReload(1, EVENTS_SCOPE_MODAL);
assert.equal(reloaded, 0);
assert.equal(refreshed, 0);
assert.deepEqual(notices[0], ['ack failed', 'error']);
api = async () => ({code:0});
await _ackEventAndReload(1, EVENTS_SCOPE_MODAL);
assert.equal(reloaded, 1);
assert.equal(refreshed, 1);
""")


def test_collection_entry_and_completion_use_navigation():
    source = (WEB / 'static/js/observability.js').read_text(encoding='utf-8')
    collection = (WEB / 'static/js/collection.js').read_text(encoding='utf-8')
    assert 'data-on-click="triggerCollect"' in source
    assert "closeCollectModal();showPage('observability')" in collection
    assert '本轮已运行时间' in source
