"""内联 on* 事件属性的转义防线。

背景(拆分前 index.html 是 9761 行单文件 SPA, 277 个内联 on* 属性):
HTML 解析器先在属性值里做**实体解码**, 再把结果交给 JS 编译器。所以
``escHtml`` 把 ``'`` 转成 ``&#39;`` 对 JS 字符串字面量毫无保护 —— 解码回来
就是个裸单引号, 照样把 handler 截断。2026-11-10 实测 13 个敌意样本, 旧写法
6 个出问题: 撇号/CR LF 直接语法错误, 反斜杠和 JSON 串被**静默改坏**。

正确写法是 escAttr: 反斜杠 + ``\'`` + ``\\n``, 只有这些能活到 JS 层。
本文件钉住两件事 —— 规则不被写回, 以及 escAttr 本身性质成立。
"""
import html
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests.frontend_source import INDEX_HTML, js_source

HTML_SOURCE = INDEX_HTML.read_text(encoding='utf-8')
SOURCE = js_source()
# 内联 on* 属性既可能写在 HTML 骨架里，也可能在 JS 模板字符串里生成
ATTR_SOURCE = HTML_SOURCE + '\n' + SOURCE

ATTR_RE = re.compile(r'\bon[a-z]+="([^"]*)"')

# 敌意样本: 撇号截断、反斜杠静默丢失、CRLF 截断、JSON 载荷
HOSTILE = [
    "WAF V6.0.8 规则升级包",
    "It's a test",
    'say "hi"',
    "back\\slash",
    '["a\\"b","c"]',
    "'; alert(1); //",
    "line1\nline2",
    "\r\n CRLF",
    "",
    "'''",
    '"""',
    "a&b<c>d",
    "tab\there",
]


def _find_js_string_arg_sites(source: str):
    """返回内联 on* 属性里, 处在 JS 单引号字符串字面量位置的插值点。"""
    sites = []
    for lineno, line in enumerate(source.split('\n'), 1):
        for m in ATTR_RE.finditer(line):
            body = m.group(1)
            if re.search(r"'\$\{esc(Html)?\(", body):
                sites.append((lineno, "esc/escHtml 用在 JS 字符串实参位, 应改 escAttr"))
            if re.search(r"'\$\{[^}]*\.replace\(/'/g", body):
                sites.append((lineno, "手写 ' → \\' 转义, 应统一走 escAttr"))
    return sites


def test_no_esc_helper_in_js_string_argument_position():
    """属性里的 JS 字符串实参只能使用 escAttr()。

    回归来源: commit fbdfc2c 修了 4 处, 复查发现还剩 8 处同类
    (渠道名 showTestLog、entry_url togglePkgDetail 等) —— 没有这条测试,
    同一类 bug 会一直漏。
    """
    bad = _find_js_string_arg_sites(ATTR_SOURCE)
    assert not bad, '内联属性 JS 字符串实参位转义写错:\n' + '\n'.join(
        f'  index.html:{ln}: {why}' for ln, why in bad)


def test_escattr_defined_exactly_once():
    """escAttr 只能有一份定义。

    这个文件已有 6 组重名顶层函数(后定义覆盖前定义, 例如 escHtml 在
    8046 和 9720 各一份, 前一份是死代码), 新 helper 不该再加一组。
    """
    defs = re.findall(r'^\s*function\s+escAttr\s*\(', SOURCE, flags=re.M)
    assert len(defs) == 1, f'escAttr 定义了 {len(defs)} 次, 应只有 1 次'


def _node_binary():
    """GitHub Runner 的 node 在 PATH 上; 开发机可能装在 ~/.local/bin。"""
    found = shutil.which('node')
    if found:
        return found
    for cand in (Path.home() / '.local' / 'bin' / 'node',
                 Path('/usr/local/bin/node'), Path('/usr/bin/node')):
        if cand.exists():
            return str(cand)
    return None


def _require_node():
    node = _node_binary()
    if not node:
        pytest.skip('找不到 node, 跳过 JS 侧往返验证')
    return node


def test_escattr_roundtrips_hostile_values_into_js():
    """完整流水线: escAttr(v) → 写进属性 → HTML 实体解码 → 当 JS 编译执行,
    被调函数必须收到**逐字节相同**的 v。

    需要 node(GitHub Runner 自带); 本机没有则跳过。
    """
    node = _require_node()

    m = re.search(r'^function escAttr\(s\)\{.*$', SOURCE, re.M)
    assert m, 'escAttr 定义没找到(签名变了?本测试的正则需要同步)'
    esc_attr_src = m.group(0)

    # 在 node 里跑 escAttr, 输出未解码的属性文本
    probe = (
        esc_attr_src + '\n'
        + 'const v=' + json.dumps(HOSTILE) + ';\n'
        + 'process.stdout.write(JSON.stringify(v.map(x=>`fn(\'${escAttr(x)}\')`)));\n'
    )
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
        f.write(probe)
        probe_path = f.name
    try:
        emitted = json.loads(subprocess.run(
            [node, probe_path], capture_output=True, text=True, check=True).stdout)
    finally:
        Path(probe_path).unlink(missing_ok=True)

    assert len(emitted) == len(HOSTILE)
    # HTML 属性实体会被解析器解码, 这一步 Python 标准库和浏览器行为一致
    decoded = [html.unescape(a) for a in emitted]

    runner = (
        'const attrs=' + json.dumps(decoded) + ', orig=' + json.dumps(HOSTILE) + ';\n'
        'const out=[];\n'
        'for(let i=0;i<attrs.length;i++){\n'
        '  let got="<未调用>", err=null;\n'
        '  try{ new Function("fn", attrs[i])(x=>{got=x;}); }catch(e){ err=e.message; }\n'
        '  out.push({i, err, got, ok: err===null && got===orig[i]});\n'
        '}\n'
        'process.stdout.write(JSON.stringify(out));\n'
    )
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
        f.write(runner)
        runner_path = f.name
    try:
        res = json.loads(subprocess.run(
            [node, runner_path], capture_output=True, text=True, check=True).stdout)
    finally:
        Path(runner_path).unlink(missing_ok=True)

    broken = [r for r in res if not r['ok']]
    assert not broken, 'escAttr 往返失败:\n' + '\n'.join(
        f"  #{r['i']} {HOSTILE[r['i']]!r} → err={r['err']} got={r['got']!r}" for r in broken)


def test_eschtml_would_break_where_escattr_does_not():
    """反向证明: 同样这批数据交给 escHtml, 撇号/CRLF 必须确实写坏。

    如果哪天有人把 escAttr 换回 escHtml, 这条给出的是"为什么不许"的证据,
    而不是空口规矩。
    """
    node = _require_node()

    # escHtml 只有一份(static/js/core.js), 用它证明实体解码后撇号/CRLF 仍会写坏
    hm = list(re.finditer(r'^function escHtml\(s\)\{(?:.|\n)*?^\}', SOURCE, re.M))
    assert len(hm) == 1, f'escHtml 应只有 1 份定义, 实际 {len(hm)} 份'
    esc_html_src = hm[0].group(0)

    probe = (
        esc_html_src + '\n'
        + 'const v=' + json.dumps(HOSTILE) + ';\n'
        + 'process.stdout.write(JSON.stringify(v.map(x=>`fn(\'${escHtml(x)}\')`)));\n'
    )
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
        f.write(probe)
        probe_path = f.name
    try:
        emitted = json.loads(subprocess.run(
            [node, probe_path], capture_output=True, text=True, check=True).stdout)
    finally:
        Path(probe_path).unlink(missing_ok=True)

    decoded = [html.unescape(a) for a in emitted]
    runner = (
        'const attrs=' + json.dumps(decoded) + ', orig=' + json.dumps(HOSTILE) + ';\n'
        'let bad=0;\n'
        'for(let i=0;i<attrs.length;i++){\n'
        '  let got="<未调用>", err=null;\n'
        '  try{ new Function("fn", attrs[i])(x=>{got=x;}); }catch(e){ err=e.message; }\n'
        '  if(err!==null || got!==orig[i]) bad++;\n'
        '}\n'
        'process.stdout.write(String(bad));\n'
    )
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as f:
        f.write(runner)
        runner_path = f.name
    try:
        bad = int(subprocess.run(
            [node, runner_path], capture_output=True, text=True, check=True).stdout)
    finally:
        Path(runner_path).unlink(missing_ok=True)

    assert bad >= 3, (
        f'escHtml 只写坏了 {bad} 个样本 —— 与实测(13 个里 6 个)不符, '
        '说明实体解码环节的认知变了, 请重新评估 escAttr 的必要性')
