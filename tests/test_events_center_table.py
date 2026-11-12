"""事件中心表格渲染回归。

背景（2026-11-11）：renderEventsCenterTable / renderEventsTable 的 `const head = \\`...\\``
模板里混入了旧的字符串拼接片段（`'<thead ...>'+\n '<tr ...>'+ ...`），
反引号内被当成纯文本输出，仪表盘卡片头部渲染成 "+ "+ "+ "+ ... 垃圾文本。
仪表盘已删除，事件中心弹窗的 renderEventsTable 仍需守住同类回归。
"""
import pytest

from tests.frontend_source import js_source


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
