"""Real-browser smoke test for the build-free SPA.

Run from a machine that has Playwright + Chromium, against a running instance:

    MONITOR_E2E_BASE_URL=http://10.10.10.109:29999/ \
    MONITOR_E2E_USER=admin \
    MONITOR_E2E_PASSWORD=*** \
    python -m pytest tests/e2e/test_ui_smoke.py -q

The suite is read-only: it logs in and navigates, but never triggers collection,
restart, push, save or delete actions. It is skipped when the environment
variables or Playwright are absent, so the normal `pytest` gate is unaffected.
"""

import json
import os

import pytest

pytest.importorskip('playwright.sync_api')

BASE_URL = os.environ.get('MONITOR_E2E_BASE_URL', '').rstrip('/')
USER = os.environ.get('MONITOR_E2E_USER', '')
PASSWORD = os.environ.get('MONITOR_E2E_PASSWORD', '')

pytestmark = pytest.mark.skipif(
    not (BASE_URL and USER and PASSWORD),
    reason='set MONITOR_E2E_BASE_URL / MONITOR_E2E_USER / MONITOR_E2E_PASSWORD to run browser smoke',
)

PAGES = [
    ('observability', '仪表盘'),
    ('data', '采集数据'),
    ('history', '推送历史'),
    ('customers', '客户'),
    ('products', '产品管理'),
    ('settings', '系统配置'),
]

IGNORED_CONSOLE = ('favicon.ico',)


def _ignored(text):
    return any(marker in text for marker in IGNORED_CONSOLE)


@pytest.fixture(scope='module')
def browser_session():
    from playwright.sync_api import sync_playwright

    errors = []
    failed = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 900})
        page = context.new_page()
        page.on(
            'console',
            lambda msg: errors.append(f'console[{msg.type}] {msg.text}')
            if msg.type == 'error' and not _ignored(msg.text)
            else None,
        )
        page.on('pageerror', lambda exc: errors.append(f'pageerror {exc}'))
        page.on(
            'requestfailed',
            lambda req: failed.append(f'{req.method} {req.url} {req.failure}')
            if not _ignored(req.url) and 'ERR_ABORTED' not in (req.failure or '')
            else None,
        )

        page.goto(BASE_URL + '/', wait_until='domcontentloaded')
        page.wait_for_selector('button[data-on-click="showLoginForm"]', timeout=15000)
        page.click('button[data-on-click="showLoginForm"]')
        page.wait_for_selector('#lu', timeout=15000)
        page.fill('#lu', USER)
        page.fill('#lp', PASSWORD)
        page.click('button[data-on-click="doLogin"]')
        page.wait_for_selector('#header:not(.hidden)', timeout=15000)
        page.wait_for_function("document.querySelector('#app') && document.querySelector('#app').children.length > 0")

        yield page, errors, failed

        browser.close()


def _assert_clean(errors, failed):
    assert not errors, 'browser console/page errors:\n' + '\n'.join(errors)
    assert not failed, 'failed network requests:\n' + '\n'.join(failed)


@pytest.mark.parametrize('route,label', PAGES)
def test_page_renders_without_errors(browser_session, route, label):
    page, errors, failed = browser_session
    errors.clear()
    failed.clear()

    page.click(f'nav a[data-page="{route}"]')
    page.wait_for_function(
        "document.querySelector('#app') && document.querySelector('#app').children.length > 0"
    )
    page.wait_for_timeout(1500)

    _assert_clean(errors, failed)
    assert page.locator('#header').is_visible(), f'{label}: header disappeared'


def test_instant_auth_response_still_renders(browser_session):
    """/auth/me 立即返回时，bootstrap 的 showPage 不能读到尚未加载的页面函数。"""
    page, errors, failed = browser_session
    errors.clear()
    failed.clear()
    me = page.evaluate("fetch('/api/auth/me').then(r => r.json())")
    assert me['code'] == 0

    def instant(route):
        route.fulfill(status=200, content_type='application/json', body=json.dumps(me))

    page.route('**/api/auth/me', instant)
    try:
        page.reload(wait_until='domcontentloaded')
        page.wait_for_selector('#header:not(.hidden)', timeout=15000)
        page.wait_for_function(
            "document.querySelector('#app') && document.querySelector('#app').children.length > 0",
            timeout=15000,
        )
        page.wait_for_timeout(1000)
        _assert_clean(errors, failed)
        assert page.locator('#obs-root').count() == 1
    finally:
        page.unroute('**/api/auth/me', instant)


def test_no_data_css_and_dynamic_widths_applied(browser_session):
    page, errors, failed = browser_session
    sampled = False

    for route in ('observability', 'data'):
        errors.clear()
        failed.clear()
        page.click(f'nav a[data-page="{route}"]')
        page.wait_for_timeout(2000)
        _assert_clean(errors, failed)

        result = page.evaluate(
            """() => ({
                dataCss: document.querySelectorAll('[data-css]').length,
                applyFn: typeof window._applyPctBars,
                bars: [...document.querySelectorAll('[data-pct]')].map((el) => ({
                    pct: Number(el.getAttribute('data-pct')),
                    inline: el.style.width,
                    px: parseFloat(getComputedStyle(el).width) || 0,
                    visible: el.getClientRects().length > 0,
                })),
            })"""
        )

        assert result['dataCss'] == 0, f'{route}: [data-css] elements still present'
        assert result['applyFn'] == 'function', f'{route}: _applyPctBars is missing'

        for bar in result['bars']:
            assert bar['inline'].endswith('%'), f'{route}: width not applied for {bar}'
            assert float(bar['inline'][:-1]) == pytest.approx(bar['pct']), bar

        visible = [bar for bar in result['bars'] if bar['visible']]
        if not visible:
            continue
        sampled = True
        assert any(bar['pct'] > 0 and bar['px'] > 0 for bar in visible), (
            f'{route}: visible progress bars have no width: {visible}'
        )

    if not sampled:
        pytest.skip('no visible progress bars on observability/data')


def test_window_updates_without_daily_overview(browser_session):
    page, errors, failed = browser_session
    errors.clear()
    failed.clear()
    page.click('nav a[data-page="observability"]')
    page.wait_for_selector('#obs-root .obs-kpis')
    for hours in ('6', '720'):
        with page.expect_response(lambda r: '/api/metrics?hours=' + hours in r.url):
            page.select_option('#obsWindow', hours)
        page.wait_for_function("!document.querySelector('#obsRefreshBtn').disabled")
        assert page.locator('#obsToday').count() == 0
        assert '今日概览' not in page.locator('#obs-root').inner_text()
        assert page.locator('#custPushTable').count() == 1
    assert page.locator('[data-on-click="triggerCollect"]').count() == 1
    _assert_clean(errors, failed)


def test_events_modal_closes_when_leaving_page(browser_session):
    """切页必须关闭事件弹窗并停掉它的 30 秒刷新，旧弹窗不能继续请求。"""
    page, errors, failed = browser_session
    errors.clear()
    failed.clear()
    page.click('nav a[data-page="observability"]')
    page.wait_for_selector('#obs-root .obs-kpis')
    page.locator('#obs-root [data-on-click="openEventsCenterModal"]').first.click()
    page.wait_for_selector('#eventsCenterModal:not(.hidden)')
    page.wait_for_function("window._eventsCenterModalTimer")
    page.evaluate("showPage('settings')")
    page.wait_for_function(
        "document.getElementById('eventsCenterModal').classList.contains('hidden')",
        timeout=5000,
    )
    assert page.evaluate("window._eventsCenterModalTimer") is None
    page.wait_for_selector('#app .card')
    _assert_clean(errors, failed)


@pytest.mark.parametrize('width', [1440, 820, 390])
def test_observability_layout_stays_within_viewport(browser_session, width):
    page, errors, failed = browser_session
    errors.clear()
    failed.clear()
    page.click('nav a[data-page="observability"]')
    page.wait_for_selector('#obs-root .obs-kpis')
    db_card = page.locator('#obs-root .card', has_text='数据库写')
    assert db_card.count() == 1
    assert db_card.locator('thead th').all_inner_texts() == ['阶段', 'p50', 'p95', 'p99']
    try:
        page.set_viewport_size({'width': width, 'height': 900})
        dimensions = page.evaluate("""() => ({
            viewport: document.documentElement.clientWidth,
            scroll: document.documentElement.scrollWidth,
            grids: [...document.querySelectorAll('.obs-grid2')].map(grid => ({
                right: grid.getBoundingClientRect().right,
                cards: [...grid.children].map(card => card.getBoundingClientRect().right),
            })),
        })""")
        assert dimensions['viewport'] <= width
        assert dimensions['scroll'] <= dimensions['viewport'] + 1, dimensions
        assert dimensions['grids']
        for grid in dimensions['grids']:
            assert all(right <= grid['right'] + 1 for right in grid['cards']), grid
        _assert_clean(errors, failed)
    finally:
        page.set_viewport_size({'width': 1440, 'height': 900})


def test_restart_cancel_and_mocked_confirmation(browser_session):
    page, errors, failed = browser_session
    errors.clear()
    failed.clear()
    page.click('nav a[data-page="observability"]')
    page.wait_for_selector('#obs-root .obs-kpis')
    intercepted = []

    def fake_restart(route):
        intercepted.append(route.request.method)
        route.fulfill(status=200, content_type='application/json',
                      body='{"code":0,"message":"mock restart accepted"}')

    page.route('**/api/system/restart', fake_restart)
    try:
        page.click('[data-on-click="obsRestartService"]')
        page.click('#_confirmBox .confirm-btn-cancel')
        assert intercepted == []
        page.click('[data-on-click="obsRestartService"]')
        page.click('#_confirmBox .confirm-btn-ok')
        page.wait_for_selector('.toast:has-text("mock restart accepted")')
        assert intercepted == ['POST']
        _assert_clean(errors, failed)
    finally:
        page.unroute('**/api/system/restart', fake_restart)
