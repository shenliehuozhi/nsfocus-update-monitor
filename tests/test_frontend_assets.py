"""前端拆分后的静态门禁。

背景：index.html 曾是 9899 行单文件（内联 CSS + 8600 行脚本），
2026-11-11 的表头垃圾文本就是「语法正确、运行才暴露」的典型。
拆分后这里守住几件容易在后续改动中悄悄退化的事：
- 不允许再把可执行脚本塞回 HTML；
- script 顺序固定（经典脚本，跨文件共享全局作用域）；
- 不许出现跨文件 const/let 重声明（浏览器会直接 SyntaxError，整页白屏）；
- HTML / JS 模板里 163 个内联 on* 处理器必须都能找到定义；
- 每个文件都能被解析（本机 esprima，CI 用 node --check）。
"""
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import pytest

from tests.frontend_source import INDEX_HTML, WEB, js_source, script_paths

EXPECTED_ORDER = [
    'core.js', 'dashboard.js', 'data.js', 'pages.js', 'history.js',
    'products.js', 'collection.js', 'account.js', 'bootstrap.js', 'tools.js',
]
MAX_HTML_LINES = 80
MAX_JS_LINES = 3000

JS_KEYWORDS = {
    'if', 'for', 'while', 'switch', 'return', 'typeof', 'new', 'function',
    'catch', 'async', 'await', 'var', 'let', 'const', 'do', 'else', 'delete',
    'void', 'in', 'of', 'instanceof', 'throw', 'try', 'yield',
}
BROWSER_GLOBALS = {
    'this', 'event', 'alert', 'confirm', 'prompt', 'parseInt', 'parseFloat',
    'Number', 'String', 'Boolean', 'JSON', 'Object', 'Array', 'Math', 'Date',
    'encodeURIComponent', 'decodeURIComponent', 'setTimeout', 'setInterval',
    'clearInterval', 'clearTimeout', 'isNaN', 'isFinite', 'fetch', 'URL',
    'Blob', 'FileReader', 'Image', 'RegExp', 'Promise', 'Error', 'Map', 'Set',
    'Intl', 'structuredClone', 'requestAnimationFrame', 'getComputedStyle',
}


def test_index_has_no_inline_executable_script():
    html = INDEX_HTML.read_text(encoding='utf-8')
    inline = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)
    assert not inline, '可执行脚本必须放在 static/js，index.html 只保留 <script src>'


def test_css_is_external():
    html = INDEX_HTML.read_text(encoding='utf-8')
    assert '<style' not in html
    assert '<link rel="stylesheet" href="/static/css/app.css">' in html
    assert (WEB / 'static/css/app.css').stat().st_size > 0


def test_script_assets_exist_and_keep_load_order():
    paths = script_paths()
    assert [p.name for p in paths] == EXPECTED_ORDER
    for path in paths:
        assert path.stat().st_size > 0, f'{path.name} 为空'


def test_bundles_stay_split():
    html_lines = len(INDEX_HTML.read_text(encoding='utf-8').splitlines())
    assert html_lines < MAX_HTML_LINES, f'index.html 又长回 {html_lines} 行'
    for path in script_paths():
        n = len(path.read_text(encoding='utf-8').splitlines())
        assert n < MAX_JS_LINES, f'{path.name} 已 {n} 行，请拆到 3000 行以内'


def test_no_cross_file_lexical_redeclaration():
    """const/let 在经典脚本间共享词法环境，重声明会让整页脚本停止执行。"""
    seen = defaultdict(list)
    for path in script_paths():
        src = path.read_text(encoding='utf-8')
        for m in re.finditer(r'^(?:const|let|var)\s+([A-Za-z_$][\w$]*)', src, re.M):
            seen[m.group(1)].append(path.name)
    dup = {k: v for k, v in seen.items() if len(set(v)) > 1}
    assert not dup, f'跨文件重复声明: {dup}'


def _defined_names() -> set[str]:
    names = set()
    for path in script_paths():
        src = path.read_text(encoding='utf-8')
        names |= set(re.findall(
            r'(?<![\w$.])(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(', src))
        names |= set(re.findall(
            r'(?<![\w$.])(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=', src))
        names |= set(re.findall(r'\bwindow\.([A-Za-z_$][\w$]*)\s*=', src))
    return names


def _inline_handler_calls(source: str) -> set[str]:
    calls = set()
    for attr in re.finditer(r'\bon[a-z]+="([^"]*)"', source):
        for call in re.finditer(r'(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(', attr.group(1)):
            calls.add(call.group(1))
    return calls


def test_inline_handlers_are_all_defined():
    """内联属性既在 HTML 骨架里，也在 JS 模板字符串里生成。"""
    source = INDEX_HTML.read_text(encoding='utf-8') + '\n' + js_source()
    calls = _inline_handler_calls(source)
    assert len(calls) > 100, f'只扫到 {len(calls)} 个处理器，正则可能失效了'
    missing = sorted(c for c in calls
                     if c not in _defined_names()
                     and c not in JS_KEYWORDS | BROWSER_GLOBALS)
    assert not missing, f'内联处理器找不到定义: {missing}'


def test_bootstrap_dependencies_defined_before_it_runs():
    """bootstrap.js 里的 init 会立即执行，调用的函数必须在更早的文件里。"""
    paths = script_paths()
    # tools.js 是页面底部独立块，只被点击后调用；bootstrap 之前必须全部就绪
    assert paths[-2].name == 'bootstrap.js'
    idx = paths.index(paths[-2])
    earlier = set()
    for path in paths[:idx]:
        src = path.read_text(encoding='utf-8')
        earlier |= set(re.findall(
            r'(?<![\w$.])(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(', src))
        earlier |= set(re.findall(
            r'(?<![\w$.])(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=', src))
    calls = set(re.findall(r'(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(',
                           paths[idx].read_text(encoding='utf-8')))
    missing = sorted(c for c in calls
                     if c not in earlier and c not in JS_KEYWORDS | BROWSER_GLOBALS)
    assert not missing, f'bootstrap 立即调用了未加载的函数: {missing}'


def _es2017_compatible(src: str) -> str:
    """esprima 4 只到 ES2017；仅用于语法解析，不改运行代码。"""
    src = src.replace('?.', '.').replace('??', '||')
    return re.sub(r'catch\s*\{', 'catch(e){', src)


def test_js_parses_with_esprima():
    esprima = pytest.importorskip('esprima')
    for path in script_paths():
        try:
            esprima.parseScript(_es2017_compatible(path.read_text(encoding='utf-8')))
        except Exception as e:
            pytest.fail(f'{path.name} 语法错误: {e}')


def _node_binary():
    found = shutil.which('node')
    if found:
        return found
    for cand in ('/usr/local/bin/node', '/usr/bin/node'):
        if Path(cand).exists():
            return cand
    return None


def test_js_parses_with_node():
    node = _node_binary()
    if not node:
        pytest.skip('找不到 node，CI 上会由 node --check 覆盖')
    for path in script_paths():
        r = subprocess.run([node, '--check', str(path)],
                           capture_output=True, text=True)
        assert r.returncode == 0, f'{path.name} 语法错误:\n{r.stderr}'
