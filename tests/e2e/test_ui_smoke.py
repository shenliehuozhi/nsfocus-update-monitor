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
    ('dashboard', '仪表盘'),
    ('data', '采集数据'),
    ('history', '推送历史'),
    ('products', '产品管理'),
    ('observability', '可观测'),
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


def test_no_data_css_and_dynamic_widths_applied(browser_session):
    page, errors, failed = browser_session
    sampled = False

    for route in ('dashboard', 'data', 'observability'):
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
            assert bar['inline'] == f"{bar['pct']}%", f'{route}: width not applied for {bar}'

        visible = [bar for bar in result['bars'] if bar['visible']]
        if not visible:
            continue
        sampled = True
        assert any(bar['pct'] > 0 and bar['px'] > 0 for bar in visible), (
            f'{route}: visible progress bars have no width: {visible}'
        )

    if not sampled:
        pytest.skip('no visible progress bars on dashboard/data/observability')
