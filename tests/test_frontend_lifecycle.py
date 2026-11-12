"""前端生命周期/网络门禁：401、定时器、导航竞态、裸 fetch、CSS 自引用。

2026-11-11 审计发现四类“不会报错但会静默变坏”的问题：
- token 过期后 api() 只回 {code:401}，页面空白且不跳登录；
- 登出/切页只停了 3 个定时器，事件头 30s 轮询等继续打 401；
- 慢请求返回后直接覆盖 #app，用户已切走的页面被写成旧页；
- 11 处裸 fetch 绕开统一错误处理。

这里把它们钉成静态门禁，避免改动时回退。
"""
import re

from tests.frontend_source import WEB, js_source, script_paths

CORE = (WEB / 'static/js/core.js').read_text(encoding='utf-8')


def test_api_handles_401_but_not_login_failure():
    assert 'r.status===401' in CORE
    # 登录接口的 401 是“密码错误”，不能触发“登录已过期”并清 token
    assert "path!=='/auth/login'" in CORE
    assert 'function _handleUnauthorized' in CORE
    handle = CORE[CORE.index('function _handleUnauthorized'):CORE.index('function api(')]
    assert '_clearAllTimers()' in handle
    assert 'localStorage.removeItem' in handle
    assert 'renderLogin()' in handle
    # 并发请求只提示一次
    assert '_authExpired' in handle


def test_logout_and_navigation_clear_timers():
    logout = CORE[CORE.index('function logout()'):]
    assert '_clearAllTimers();_currentPage=null;' in logout

    clear_all = CORE[CORE.index('function _clearAllTimers'):CORE.index('function logout()')]
    for marker in ('window._eventsCenterModalTimer',
                   '_collectPollTimer', '_collectBannerPollTimer', '_singleCollTimer',
                   'window._discTimer', '_banTimer', 'stopProductsPolls', '_stopBgRotation'):
        assert marker in clear_all, f'登出未清理 {marker}'

    page_timers = CORE[CORE.index('function _clearPageTimers'):CORE.index('function _clearAllTimers')]
    for marker in ('_logTimer', 'window._obsTimer'):
        assert marker in page_timers, f'切页未清理 {marker}'

    show = CORE[CORE.index('function showPage('):]
    assert '_clearPageTimers(_currentPage)' in show
    assert '_navSeq++' in show
    # 页面渲染异常不能静默：async 页面函数的 rejection 要转成用户可见提示
    assert 'function _pageRenderError(' in CORE
    assert '_pageRenderError(n,' in show


def test_products_polling_has_cancel_hook():
    products = (WEB / 'static/js/products.js').read_text(encoding='utf-8')
    assert 'function stopProductsPolls' in products
    # 发现/刷新/确认三处轮询都要能取消
    assert products.count('_productsPollCleanup=()=>') == 3
    assert 'pollSSEAbort.abort()' in products


def test_async_pages_check_navigation_generation():
    pages = {
        'history.js': ['async function history', 'async function settings', 'async function products'],
        'data.js': ['async function dataPage'],
        'collection.js': ['async function logs'],
        'pages.js': ['async function sessions', 'async function channels',
                     'async function customers', 'async function subscriptions'],
    }
    for filename, funcs in pages.items():
        text = (WEB / 'static/js' / filename).read_text(encoding='utf-8')
        for fn in funcs:
            start = text.index(fn)
            body = text[start:]
            # 函数体到下一个顶层 function/async function 为止
            nxt = re.search(r'\n(?:async )?function ', body[1:])
            body = body[:nxt.start() + 1] if nxt else body
            assert 'const _seq=_navSeq;' in body, f'{filename} {fn} 未记录导航代际'
            assert 'navStale(_seq)' in body, f'{filename} {fn} 写入 #app 前未校验代际'


def test_raw_fetch_is_allowlisted():
    # 只允许：api() 自身、SSE 流、导出文本/blob 响应
    allowed = {'core.js': 1, 'products.js': 1, 'tools.js': 2}
    for path in script_paths():
        count = len(re.findall(r'\bfetch\(', path.read_text(encoding='utf-8')))
        assert count == allowed.get(path.name, 0), (
            f'{path.name} 有 {count} 处裸 fetch，未走 api()；'
            '新增特殊响应请同步更新本测试的 allowlist'
        )


def test_auth_uses_httponly_cookie_and_csrf():
    # 登录凭证不再写入 localStorage（唯一保留读取是升级前旧标签页的迁移副本）
    assert "localStorage.setItem('token'" not in js_source()
    assert "X-CSRF-Token" in CORE and 'nsm_csrf' in CORE
    assert "credentials:'same-origin'" in CORE

    # 登出要通知服务端清 Cookie；刷新后先问 /auth/me（凭证在 Cookie 里，T 为 null）
    logout = CORE[CORE.index('function logout()'):]
    assert "api('/auth/logout',{method:'POST'})" in logout
    bootstrap = (WEB / 'static/js/bootstrap.js').read_text(encoding='utf-8')
    assert "api('/auth/me')" in bootstrap

    # 例外里的裸 fetch 也不能再手拼 Bearer；CSRF 头由 _authHeaders() 统一提供
    for name in ('products.js', 'tools.js'):
        text = (WEB / 'static/js' / name).read_text(encoding='utf-8')
        assert 'Bearer ' not in text, f'{name} 仍在手工拼 Bearer'


def test_css_has_no_self_referential_custom_properties():
    css = (WEB / 'static/css/app.css').read_text(encoding='utf-8')
    root = css[:css.index('}')]
    bad = re.findall(r'(--[\w-]+)\s*:\s*var\(\1\)', root)
    assert not bad, f'CSS 变量自引用（解析为无效值）: {bad}'
