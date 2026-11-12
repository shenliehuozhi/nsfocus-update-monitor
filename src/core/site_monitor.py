"""update.nsfocus.com 可用性探测。

每 5 分钟由调度器调用一次，记录 HTTP 状态、响应耗时与连续失败次数；
状态写入 system_settings，进程重启后仍可展示最近一次结果。
只有状态发生变化（可用 ↔ 不可用）时才写系统事件，避免每轮探测刷屏。
"""

import json
import time
from datetime import datetime, timezone

import requests

from src.core.logger import get_logger

logger = get_logger('site_monitor')

SITE_URL = 'https://update.nsfocus.com/'
STATUS_KEY = 'site_status'
PROBE_TIMEOUT = 10

_UNKNOWN = {
    'url': SITE_URL,
    'status': 'unknown',
    'http_status': None,
    'latency_ms': None,
    'checked_at': None,
    'error': '',
    'consecutive_failures': 0,
    'last_ok_at': None,
    'last_fail_at': None,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def get_status() -> dict:
    """Return the last persisted probe result (all keys always present)."""
    state = dict(_UNKNOWN)
    try:
        from src.models.database import query
        rows = query("SELECT value FROM system_settings WHERE key = ?", (STATUS_KEY,))
        if rows and rows[0]['value']:
            saved = json.loads(rows[0]['value'])
            if isinstance(saved, dict):
                state.update({k: saved[k] for k in state if k in saved})
    except Exception as e:
        logger.debug(f'get_status unavailable: {e}')
    return state


def _save_status(state: dict):
    from src.models.database import execute
    execute(
        "INSERT OR REPLACE INTO system_settings (key, value, updated_at) "
        "VALUES (?, ?, datetime('now'))",
        (STATUS_KEY, json.dumps(state, ensure_ascii=False)),
    )


def probe(url: str = SITE_URL, timeout: int = PROBE_TIMEOUT) -> dict:
    """Issue one HTTP request; 5xx/timeout/connection errors count as down."""
    started = time.perf_counter()
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            stream=True,
            headers={'User-Agent': 'nsfocus-monitor/1.0 availability-check'},
        )
        try:
            code = resp.status_code
        finally:
            resp.close()
        latency = int(round((time.perf_counter() - started) * 1000))
        up = code < 500
        return {
            'status': 'up' if up else 'down',
            'http_status': code,
            'latency_ms': latency,
            'error': '' if up else f'HTTP {code}',
        }
    except Exception as e:
        latency = int(round((time.perf_counter() - started) * 1000))
        return {
            'status': 'down',
            'http_status': None,
            'latency_ms': latency,
            'error': str(e)[:200] or e.__class__.__name__,
        }


def _emit_transition(previous: str, state: dict):
    try:
        from src.models.event_log import log_event
        payload = {
            'url': state['url'],
            'http_status': state['http_status'],
            'latency_ms': state['latency_ms'],
            'error': state['error'],
            'checked_at': state['checked_at'],
        }
        if state['status'] == 'down':
            payload['title'] = f"站点不可用: {state['error'] or '连接失败'}"
            log_event('site_unavailable', 'ERROR',
                      product_name='update.nsfocus.com',
                      source_url=state['url'], message=payload)
        elif previous == 'down':
            payload['title'] = '站点已恢复'
            log_event('site_recovered', 'INFO',
                      product_name='update.nsfocus.com',
                      source_url=state['url'], message=payload)
    except Exception as e:
        logger.error(f'emit transition failed: {e}')


def run_check(url: str = SITE_URL) -> dict:
    """Probe once, persist the result and emit an event on state transitions."""
    previous = get_status()
    result = probe(url)
    now = _now_iso()
    failures = 0 if result['status'] == 'up' else (previous.get('consecutive_failures') or 0) + 1
    state = {
        'url': url,
        'status': result['status'],
        'http_status': result['http_status'],
        'latency_ms': result['latency_ms'],
        'checked_at': now,
        'error': result['error'],
        'consecutive_failures': failures,
        'last_ok_at': now if result['status'] == 'up' else previous.get('last_ok_at'),
        'last_fail_at': now if result['status'] == 'down' else previous.get('last_fail_at'),
    }
    try:
        _save_status(state)
    except Exception as e:
        logger.error(f'persist status failed: {e}')
    try:
        from src.models.site_probe import record
        record(state)
    except Exception as e:
        logger.warning(f'persist probe history failed: {e}')
    previous_status = previous.get('status') or 'unknown'
    if state['status'] == 'down' and previous_status != 'down':
        _emit_transition(previous_status, state)
    elif state['status'] == 'up' and previous_status == 'down':
        _emit_transition(previous_status, state)
    return state
