"""系统配置页分区卡片网格回归。

配置页与仪表盘统一使用 .cfg-grid 双列卡片：保存按钮只在页头，
各分区标题右侧保留生效时机提示，避免改回单列长表单。
"""
from tests.frontend_source import WEB, js_source

CSS = (WEB / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')


def test_settings_page_uses_section_card_grid():
    source = js_source()
    assert '<div class="cfg-grid">' in source
    # 8 个分区：开关/采集/存储/静默/高级/邮件限流/联系信息/系统事件
    assert source.count('<div class="cfg-section">') == 8
    assert '.cfg-grid{display:grid;grid-template-columns:1fr 1fr' in CSS
    assert '@media (max-width:900px){.cfg-grid{grid-template-columns:1fr}}' in CSS


def test_settings_save_button_only_in_header():
    source = js_source()
    assert "data-on-click=\"${isCollecting?'forceStopAndSave':'saveSettings'}\"" in source
    # 页头之外不再保留整行宽的底部保存按钮
    assert '<button class="btn btn-primary" data-on-click="${isCollecting?' not in source


def test_settings_section_titles_keep_effect_hints():
    source = js_source()
    for hint in ('即时生效', '下一轮采集生效', '保存后生效', '三项全填才展示'):
        assert f'>{hint}</span>' in source


def test_settings_tooltip_uses_body_overlay():
    source = js_source()
    # 长文案提示改走 core.js 追加到 body 的固定浮窗，避免 CSS 伪元素撑宽文档
    assert "closest('.pill-tip,.tp')" in source
    assert '.tp::after' not in CSS
