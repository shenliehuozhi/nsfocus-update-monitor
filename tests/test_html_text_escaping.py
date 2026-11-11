"""HTML 文本上下文的转义门禁。

背景（2026-11-11 审计）：``test_inline_attr_escaping`` 只守住了内联 on* 属性
里的 JS 字符串实参，但页面渲染里还有大量 ``<td>${c.name}</td>``、
``<option>${ch.name}</option>`` 这类**文本上下文**直接拼接。渠道名 / 客户名 /
规则名 / 解析出来的文件名、版本号、描述都可能在 HTML 里解释执行。

这里用启发式扫描：只检查同行同时含 ``<`` 和 ``>`` 的模板行（即 HTML 片段），
命中用户/外部字段且没有被 escHtml / escAttr / esc / _opt 包裹的插值即失败。
条件表达式（``===`` / ``!==``）和 ``.length`` 只输出常量或数字，跳过。
"""
import re

from tests.frontend_source import script_paths

FIELDS = (
    'name', 'email', 'company', 'product_name', 'file_name',
    'customer_name', 'channel_name', 'display_name', 'package_version',
    'description_raw', 'min_sys_version', 'valid_until', 'size_human',
    'last_product', 'urgency', 'type',
)
# 常见的数据变量名：c/ch 渠道·客户，s/snap 快照，h 历史项，src 数据源，
# r 规则，p 产品，f 文件，d 交付，x/g 临时项。
EXPR_RE = re.compile(
    r'\$\{[^{}]*\b(?:c|ch|s|h|snap|src|p|f|r|d|x|g)\.(?:'
    + '|'.join(FIELDS)
    + r')\b[^{}]*\}'
)
SAFE_WRAPPERS = (
    'escHtml(', 'escAttr(', 'encodeURIComponent(', 'JSON.stringify(',
    '_opt(', 'esc(', 'fmtTZ(', 'typeCN(', 'chTypeChip(',
    '_dataArgs(',  # escHtml(JSON.stringify(args))，用于 data-args 属性
)
SAFE_DERIVED = ('===', '!==', '.length')


def _html_lines():
    for path in script_paths():
        text = path.read_text(encoding='utf-8')
        for lineno, line in enumerate(text.split('\n'), 1):
            if '<' in line and '>' in line:
                yield path, lineno, line


def _violations():
    out = []
    for path, lineno, line in _html_lines():
        for m in EXPR_RE.finditer(line):
            expr = m.group(0)
            if any(w in expr for w in SAFE_WRAPPERS):
                continue
            if any(w in expr for w in SAFE_DERIVED):
                continue
            out.append(f'{path.name}:{lineno}: {expr}')
    return out


def test_html_text_interpolations_are_escaped():
    bad = _violations()
    assert not bad, (
        'HTML 文本/属性上下文里出现未转义的动态字段，请包 escHtml()：\n'
        + '\n'.join('  ' + b for b in bad)
    )


def test_esc_html_covers_text_and_attribute_contexts():
    """escHtml 必须同时处理文本（&<>）和属性（"\'）上下文。"""
    from tests.frontend_source import js_source

    source = js_source()
    m = re.search(r'^function escHtml\(s\)\{(.*?)^\}', source, re.M | re.S)
    assert m, 'escHtml 定义没找到'
    body = m.group(1)
    for ch in ('&amp;', '&lt;', '&gt;', '&quot;', '&#39;'):
        assert ch in body, f'escHtml 缺少 {ch} 映射'
