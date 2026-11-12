"""仪表盘页面上线门禁。

页面必须能从主导航进入、在 bootstrap 之前加载（bootstrap 立即调用 showPage），
并从 /api/metrics 渲染投递、队列、采集、事件与调度等观测分区。
"""
import re

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
                   'openEventsCenterModal', '_obsWindowLabel'):
        assert marker in source, f'观测页缺少 {marker}'
    assert '<th>状态</th>' not in source
    assert 'undated_failed' in source
    assert 'sources_stale' in source
    # 规则名/渠道名来自数据库，渲染时必须转义
    assert '${escHtml(r.rule_name' in source
    assert '${escHtml(_obsChannel(ch.channel_type))}' in source


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
