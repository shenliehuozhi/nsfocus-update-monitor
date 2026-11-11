"""前端拆分后的静态门禁。

背景：index.html 曾是 9899 行单文件（内联 CSS + 8600 行脚本），
2026-11-11 的表头垃圾文本就是「语法正确、运行才暴露」的典型。
拆分后这里守住几件容易在后续改动中悄悄退化的事：
- 不允许再把可执行脚本塞回 HTML；
- script 顺序固定（经典脚本，跨文件共享全局作用域）；
- 不许出现跨文件 const/let 重声明（浏览器会直接 SyntaxError，整页白屏）；
- 不许出现重复的顶层 function 声明（后加载的定义会静默覆盖前一份）；
- 模板里不许再写内联 on*（CSP），事件统一走 data-on-* 委托且动作函数必须存在；
- data-args 必须是 _dataArgs() 或 JSON 字面量（不能再用 escAttr 二次转义）；
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
    'core.js', 'dashboard.js', 'data.js', 'data_push.js', 'pages.js', 'history.js',
    'products.js', 'collection.js', 'observability.js', 'account.js',
    'bootstrap.js', 'tools.js',
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


def test_no_duplicate_top_level_functions():
    """顶层同名 function 会被后加载的定义静默覆盖，前一份成为死代码。

    只匹配行首 0-1 个空格的 function（本仓库顶层函数最多缩进 1 格，
    嵌套函数至少缩进 2 格），因此跨文件和单文件内的重复都能抓到。
    """
    seen = defaultdict(list)
    for path in script_paths():
        src = path.read_text(encoding='utf-8')
        for m in re.finditer(
                r'(?m)^ ?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(', src):
            line = src.count('\n', 0, m.start()) + 1
            seen[m.group(1)].append(f'{path.name}:{line}')
    dup = {k: v for k, v in seen.items() if len(v) > 1}
    assert not dup, f'重复的顶层函数声明: {dup}'


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


INLINE_HANDLER = re.compile(
    r'\bon(?:click|change|input|keydown|keyup|submit|blur|focus|'
    r'mouse\w+|dblclick|contextmenu|load|error)="')
DATA_ON = re.compile(r'data-on-([a-z]+)="([^"]*)"')
DATA_ARGS = re.compile(r"data-args='([^']*)'")
DISPATCH_TYPES = {'click', 'change', 'input', 'keydown'}


def test_no_inline_event_handlers():
    """CSP script-src 'self' 会拦截内联属性；属性既可能在 HTML，也可能在 JS 模板里。"""
    hits = []
    for path in script_paths() + [INDEX_HTML]:
        for m in INLINE_HANDLER.finditer(path.read_text(encoding='utf-8')):
            line = path.read_text(encoding='utf-8').count('\n', 0, m.start()) + 1
            hits.append(f'{path.name}:{line}')
    assert not hits, f'仍有内联事件属性，请改用 data-on-*: {hits}'


def _data_on_names(source: str) -> tuple[set[str], set[str]]:
    names, types = set(), set()
    for m in DATA_ON.finditer(source):
        types.add(m.group(1))
        value = m.group(2)
        if value.startswith('${'):
            names |= set(re.findall(r"'([A-Za-z_$][\w$]*)'", value))
        else:
            names.add(value)
    return names, types


def test_data_on_handlers_are_defined():
    source = js_source() + '\n' + INDEX_HTML.read_text(encoding='utf-8')
    names, types = _data_on_names(source)
    assert len(names) > 150, f'只扫到 {len(names)} 个 data-on 处理器，正则可能失效了'
    assert types <= DISPATCH_TYPES, f'委托未监听的类型: {types - DISPATCH_TYPES}'
    defined = _defined_names()
    missing = sorted(n for n in names if n not in defined)
    assert not missing, f'data-on 动作找不到定义: {missing}'


def test_data_args_are_escaped_once():
    source = js_source()
    assert 'escAttr(JSON.stringify' not in source, \
        'data-args 的 JSON 必须用 _dataArgs()/escHtml，escAttr 会破坏 JSON.parse'
    assert 'function _dataArgs(' in source, '缺少 _dataArgs() 辅助函数'
    bad = []
    for m in DATA_ARGS.finditer(source):
        value = m.group(1)
        if value.startswith('${_dataArgs(') or value.startswith('['):
            continue
        bad.append(f'{source.count(chr(10), 0, m.start()) + 1}: {value[:60]}')
    assert not bad, f'data-args 不是 _dataArgs()/JSON 字面量: {bad}'


# 占位符只有写成字符串才会被 _actArg() 解析；裸标识符在模板求值时就抛 ReferenceError
BARE_ARG_PLACEHOLDER = re.compile(r'[\s(,]\$(?:el|value|checked|event)\b')


def test_data_args_placeholders_are_strings():
    """裸写 $el/$value/$checked 会让页面函数在写 #app 前抛错，表现为导航点击无反应。"""
    bad = []
    for path in script_paths():
        src = path.read_text(encoding='utf-8')
        for m in BARE_ARG_PLACEHOLDER.finditer(src):
            bad.append(f'{path.name}:{src.count(chr(10), 0, m.start()) + 1}: {m.group(0).strip()}')
    assert not bad, f'data-args 占位符必须写成字符串（如 "$el"）: {bad}'


STYLE_PROP_ASSIGN = re.compile(r"\.style\s*=\s*['\"]")


def test_no_style_property_assignment():
    """el.style = '...' 静默无效（style 是只读对象），必须写 el.style.cssText。"""
    bad = []
    for path in script_paths():
        src = path.read_text(encoding='utf-8')
        for m in STYLE_PROP_ASSIGN.finditer(src):
            bad.append(f'{path.name}:{src.count(chr(10), 0, m.start()) + 1}')
    assert not bad, f'请改用 .style.cssText = ...: {bad}'


# 属性名后紧跟 = 与引号；排除 el.style=... 的 CSSOM 赋值（前面有 .）
INLINE_STYLE = re.compile(r'''(?<![.\w-])style\s*=\s*["']''')
DATA_CSS = re.compile(r'''\bdata-css\s*=\s*["']''')
DATA_CSS_MAX = 672  # 只减不增：新代码必须写 CSS 类


def test_no_inline_style_attributes():
    """CSP style-src 'self' 会拦截内联 style 属性；历史样式暂存 data-css，由 core.js 应用。"""
    hits = []
    for path in script_paths() + [INDEX_HTML]:
        text = path.read_text(encoding='utf-8')
        for m in INLINE_STYLE.finditer(text):
            hits.append(f'{path.name}:{text.count(chr(10), 0, m.start()) + 1}')
    assert not hits, f'仍有内联 style 属性，请改用 CSS 类: {hits}'


def test_data_css_usage_only_shrinks():
    source = js_source() + '\n' + INDEX_HTML.read_text(encoding='utf-8')
    n = len(DATA_CSS.findall(source))
    assert n <= DATA_CSS_MAX, (
        f'data-css 从 {DATA_CSS_MAX} 涨到 {n}：新代码请写 CSS 类，'
        'data-css 只用于历史模板且只减不增'
    )


def test_data_css_applier_present():
    source = js_source()
    assert 'function _applyDataCss(' in source, '缺少 data-css 应用函数'
    assert "querySelectorAll('[data-css]')" in source
    assert 'MutationObserver' in source, 'data-css 需要监听动态插入的节点'


def test_no_runtime_style_injection():
    source = js_source()
    for bad in (
        "createElement('style')", 'createElement("style")',
        "setAttribute('style'", 'setAttribute("style"',
        '.insertRule(', 'adoptedStyleSheets',
    ):
        assert bad not in source, f'CSP style-src 禁止运行时注入样式: {bad}'


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
