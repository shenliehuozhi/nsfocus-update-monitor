"""可观测性页面上线门禁。

页面必须能从主导航进入、在 bootstrap 之前加载（bootstrap 立即调用 showPage），
并从 /api/metrics 渲染投递、队列、采集、事件与调度等观测分区。
"""
import re

from tests.frontend_source import INDEX_HTML, WEB, js_source


def test_nav_and_page_registered():
    html = INDEX_HTML.read_text(encoding='utf-8')
    assert 'data-page="observability"' in html
    assert '>可观测</a>' in html

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
                   'scheduler_jobs', 'format=prometheus', 'MONITOR_METRICS_TOKEN'):
        assert marker in source, f'观测页缺少 {marker}'
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
