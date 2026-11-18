"""产品页：包类型刷新日志展示 + 启用后包类型为空自动刷新。"""

from tests.frontend_source import WEB, js_source

CSS = (WEB / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')


def test_float_log_body_keeps_single_class_attr():
    """重复 class 属性会让 .log-body 丢失，refresh-pkg 日志永远写不进去。"""
    src = js_source()
    assert '<div class="json-pre" ${bodyAttrs}></div>' not in src
    assert "const cls = `json-pre${bodyClass ? ' ' + bodyClass : ''}`;" in src
    assert '<div id="${logId}-body" class="${cls}"></div>' in src
    assert "win.querySelector('.log-body')" in src


def test_float_log_wraps_long_lines():
    assert '.float-log .json-pre{white-space:pre-wrap' in CSS


def test_enabling_product_without_package_type_triggers_refresh():
    src = js_source()
    assert 'const needPkg=active&&prod&&!fmtPkgTypes(prod.package_type);' in src
    assert 'if(needPkg)refreshPkgType(id);' in src
    assert '包类型为空，正在自动刷新' in src


def test_batch_enable_warns_about_empty_package_types():
    assert '个包类型为空，请点 🔄 刷新' in js_source()
