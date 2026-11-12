"""事件中心表格渲染回归。

背景（2026-11-11）：renderEventsCenterTable / renderEventsTable 的 `const head = \\`...\\``
模板里混入了旧的字符串拼接片段（`'<thead ...>'+\n '<tr ...>'+ ...`），
反引号内被当成纯文本输出，仪表盘卡片头部渲染成 "+ "+ "+ "+ ... 垃圾文本。
仪表盘已删除，事件中心弹窗的 renderEventsTable 仍需守住同类回归。
"""
import pytest

from tests.frontend_source import INDEX_HTML, js_source


def _head_template(func_name: str) -> str:
    src = js_source()
    fstart = src.index(f'function {func_name}(')
    start = src.index('const head = `', fstart) + len('const head = `')
    end = src.index('`;', start)
    return src[start:end]


@pytest.mark.parametrize('func_name', ['renderEventsTable'])
def test_head_template_has_no_leaked_concatenation(func_name):
    head = _head_template(func_name)
    assert "'+" not in head.replace(' ', ''), '模板字符串里残留字符串拼接片段'
    assert "'" not in head, '模板字符串里不应出现单引号片段'
    assert '<table' in head and '</colgroup>' in head
    assert '<thead' in head and '</tr>' in head and head.rstrip().endswith('<tbody>')


@pytest.mark.parametrize('func_name', ['renderEventsTable'])
def test_head_template_has_all_columns(func_name):
    head = _head_template(func_name)
    for col in ('时间', '严重度', '类型', '标题', '产品 / URL', '渠道', '状态'):
        assert f'>{col}</th>' in head, f'缺少表头列: {col}'


def test_batch_ack_uses_current_severity_and_time_filters():
    source = js_source()
    assert '批量确认' in source
    assert '一键已读' not in source
    assert 'function eventsAckCurrent(scope)' in source
    assert "_evtEl(scope, 'Hours')" in source
    assert "severity: sev || null" in source
    assert 'since_hours: Number(hrs)' in source
    assert 'for(const s of targets)' not in source
    assert '筛选范围外的事件不受影响' in source


def test_logs_menu_renamed_with_events_and_logs_tabs():
    html = INDEX_HTML.read_text(encoding='utf-8')
    assert 'data-page="logs">事件与日志<' in html
    assert 'data-page="logs">系统日志<' not in html
    source = js_source()
    assert 'function eventsPageHtml()' in source
    assert 'function logsSwitchTab(tab)' in source
    assert 'id="logsPaneEvents"' in source
    assert 'id="logsPaneSys"' in source
    assert "loadEventsPage()" in source


def test_events_page_and_dashboard_modal_share_filters():
    source = js_source()
    assert "EVENTS_SCOPE_MODAL = 'eventsCenterModal'" in source
    assert "EVENTS_SCOPE_PAGE = 'eventsPage'" in source
    assert 'function _loadEventsPanel(scope)' in source
    assert 'data-on-change="eventsGoPage"' in source
    assert 'data-on-click="eventsAckCurrent"' in source
    assert 'renderEventsTable(events, scope)' in source
