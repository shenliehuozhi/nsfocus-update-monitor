"""采集数据页左侧产品卡片回归。

产品名可能很长（如「WEB应用防护系统(WAF)」），卡片头 flex-wrap 会让
徽标和「采」按钮被挤到第二行。产品名改为单行省略并保留完整 title 提示。
"""
from tests.frontend_source import WEB, js_source

CSS = (WEB / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')


def test_product_card_title_is_single_line_ellipsis():
    source = js_source()
    assert 'class="flex-1 min-w0 ellipsis" title="${escHtml(prodName)}"' in source
    assert 'const prodName = src.display_name || src.name || String(sid);' in source


def test_ellipsis_utilities_are_defined():
    assert '.ellipsis{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' in CSS
    assert '.min-w0{min-width:0}' in CSS
    assert '.flex-1{flex:1}' in CSS


def test_tree_path_labels_truncate_single_line():
    """长路径名（如「V6.0R04F04及以前（RSASNX3-X/...）」）不能把树行折成两行。"""
    source = js_source()
    assert source.count('class="tree-name" title="${escHtml(label)}"') >= 3
    assert '<span class="tree-meta">' in source
    assert 'gray-muted-13-shrink' not in source
    assert '.tree-row-click{cursor:pointer;flex-wrap:nowrap}' in CSS
    assert '.tree-name{' in CSS and 'text-overflow:ellipsis' in CSS
    assert '.tree-meta{' in CSS and 'flex-shrink:0' in CSS
