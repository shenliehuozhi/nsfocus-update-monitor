"""Scheduler — periodic collection + detection + notification pipeline.

Supports two modes:
  'delta' (fast) — only check list pages, ~20s, for frequent runs
  'full'  (slow) — traverse all detail pages, ~15-20min, for weekly deep scan
"""

import os
import random
import time
import threading
import atexit
from datetime import datetime, timedelta
from typing import Optional

from src.core.logger import get_logger
from src.collectors.nsfocus import NsfocusCollector, SessionExpiredError, _get_products as _collector_products
from src.detector.change import run_detection, get_new_for_subscription
from src.notifiers.router import route_notifications, process_delayed_queue
from src.models.user_session import (
    get_active_sessions,
    get_active_collect_sessions,
    update_status, update_heartbeat, log_heartbeat,
)
from src.models.snapshot import (
    get_source_by_name, update_source_health,
    list_sources as list_content_sources,
    create_source as create_content_source,
    get_active_snapshots,
    upsert_source,
)
from src.models.subscription import get_enabled_rules

logger = get_logger('scheduler')

# ── URL / path_id → Chain 缓存（用于订阅规则链路径匹配）─────────────
# 结构: _url_chain_cache[source_id] = {
#   'by_url':      {norm_url: chain_list},         # legacy fallback
#   'by_path_id':  {path_id: chain_list},          # 精确匹配(优先)
# }
# 在 start_scheduler 时通过 _build_url_chain_cache() 构建。
#
# path_id 算法与 _collect_quick / _extract_table_items 完全一致:
#   MD5(BASE_URL + url + json.dumps(chain, ensure_ascii=False))[:12]
# content_sources.package_type.paths[].path_id 字段当前未存(discover 时未写),
# 因此 cache 重建时实时计算,确保与 snapshots.path_id 字符串完全一致。
_url_chain_cache: dict[int, dict[str, dict]] = {}
_chain_cache_loaded = False

_BASE_URL = 'https://update.nsfocus.com'


def _compute_path_id(source_url: str, chain: list = None) -> str:
    """Compute path_id from source_url only.

    Chain argument is accepted but ignored — it was previously used in the
    hash to disambiguate chains sharing a URL, but that caused snapshots to
    be re-keyed (and re-emitted) whenever paths.chain text drifted (e.g.
    sec_c_b_tit block names changed). Identity should be stable across
    chain-text changes: a file is the same file regardless of which
    subscription branch discovered it. See plan discussion 2026-07-16.

    `chain` is kept in the signature for backward compatibility with callers
    that still pass it.
    """
    import hashlib as _hl
    url = source_url or ''
    if not url.startswith('http'):
        url = _BASE_URL + url
    return _hl.md5(url.encode()).hexdigest()[:12]


def _build_url_chain_cache():
    """从 DB 加载所有 source 的 package_type.paths，构建 chain 反查缓存。

    snapshot 通过 source_url + path_id 在这里反查 chain，用于订阅规则链路径匹配。
    每次 scheduler 启动时重建（产品管理修改路径后下次调度自动刷新）。
    """
    global _url_chain_cache, _chain_cache_loaded
    import json as _json
    from src.models.database import query

    _url_chain_cache = {}
    rows = query("SELECT id, package_type FROM content_sources WHERE is_active=1")
    for row in rows:
        try:
            pt = _json.loads(row['package_type'] or '{}')
        except Exception:
            pt = {}
        url_map = {}
        path_id_map = {}
        for p in pt.get('paths', []):
            url = p.get('url')
            chain = p.get('chain')
            if not (url and chain and isinstance(chain, list)):
                continue
            # 标准化 URL 格式：去除尾部斜线，确保前导斜线
            norm = '/' + url.lstrip('/').rstrip('/')
            url_map[norm] = chain
            # 实时算 path_id (content_sources.package_type.paths[].path_id 字段未存)
            try:
                pid = _compute_path_id(norm, chain)
                path_id_map[pid] = chain
            except Exception:
                pass
        _url_chain_cache[row['id']] = {
            'by_url': url_map,
            'by_path_id': path_id_map,
        }

    _chain_cache_loaded = True
    logger.debug(f'URL/path_id→Chain cache built: {len(_url_chain_cache)} sources')


def _scan_network_errors_from_log(started_at: str, finished_at: str) -> list:
    """扫描 app.log 中指定时间窗口内的网络错误，返回错误列表（不写数据库）。

    匹配关键词：ConnectTimeoutError / ConnectError / ConnectionError /
    NewConnectionError / Max retries exceeded / DNS / Network is unreachable
    """
    import re

    try:
        from src.core.logger import get_log_dir
        log_path = os.path.join(get_log_dir(), 'app.log')
        if not os.path.exists(log_path):
            return []
    except Exception:
        log_path = '/tmp/app.log'
        if not os.path.exists(log_path):
            return []

    # 解析时间窗口
    try:
        start_dt = datetime.fromisoformat(started_at)
        end_dt = datetime.fromisoformat(finished_at)
    except Exception:
        return []

    errors = []
    # 网络错误关键词模式（从 nsfocus.py 的检测逻辑保持一致）
    NET_ERROR_PATTERNS = (
        'ConnectTimeoutError', 'ConnectError', 'ConnectionError',
        'NewConnectionError', 'Max retries exceeded',
        'Connection to', 'timed out', 'DNS', 'Network is unreachable',
    )

    # 日志格式：2026-06-12 14:30:00 [INFO] scheduler: Quick WAF: /update/xxx ...
    TS_FMT = '%Y-%m-%d %H:%M:%S'
    log_ts_re = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')

    try:
        with open(log_path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line_dt = None  # init for lines without timestamp prefix
                m = log_ts_re.match(line)
                if m:
                    try:
                        line_dt = datetime.strptime(m.group(1), TS_FMT)
                    except ValueError:
                        continue
                    if not (start_dt <= line_dt <= end_dt):
                        continue

                # 时间窗口内，检测网络错误关键词
                if any(pat in line for pat in NET_ERROR_PATTERNS):
                    # 日志格式：... Quick{product}:{url}: HTTPSConnectionPool...
                    # 产品名可能紧跟Quick无空格（如中文名），URL可能为 //host/path 或 /update/...
                    # 找 HTTPSConnectionPool 作为错误起始，解析其前后的 product 和 url
                    err_start = line.find('HTTPSConnectionPool')
                    err_msg = line[err_start:err_start+150] if err_start != -1 else line.strip()[-100:]

                    q = line.find('Quick')  # 无空格，因为中文产品名紧跟Quick
                    if q != -1:
                        after_quick = line[q+5:]  # 跳过 'Quick'
                        err_marker = re.search(r':\s*HTTPSConnectionPool', after_quick)
                        before_err = after_quick[:err_marker.start()] if err_marker else after_quick
                        url_m = re.search(r'(//[^\s]+|/\S*)', before_err)
                        url = url_m.group(1) if url_m else ''
                        if url_m:
                            product = before_err[:url_m.start()].strip().rstrip(':')
                        else:
                            product = before_err.strip()
                    else:
                        product = 'unknown'
                        url = ''

                    errors.append({
                        'product_name': product,
                        'url': url,
                        'error_msg': err_msg[:150],
                        'error_time': line_dt.isoformat() if line_dt else '',  # CST 实际发生时间,避免告警延迟 8h 后看不出真凶
                    })
    except Exception as e:
        logger.debug(f'_scan_network_errors_from_log: {e}')

    return errors


def _get_chain(source_id: int, source_url: str, path_id: str = None) -> list:
    """从缓存反查 snapshot 的完整 chain 路径。

    优先 path_id 精确匹配（解决同 URL 多 chain 共享场景），
    fallback URL 匹配（兼容 legacy snap 无 path_id 的旧行）。

    返回 chain 数组，如找不到返回空列表。
    """
    if not _chain_cache_loaded:
        _build_url_chain_cache()
    cache = _url_chain_cache.get(source_id, {})
    if not isinstance(cache, dict):
        # 防御:旧缓存结构意外残留 → 回退 URL 匹配
        cache = {'by_url': cache, 'by_path_id': {}}
    # 1) path_id 精确匹配（推荐路径，同 URL 多 chain 时唯一区分）
    if path_id:
        by_path = cache.get('by_path_id') or {}
        ch = by_path.get(path_id)
        if ch:
            return ch
    # 2) URL fallback（兼容老 snap 无 path_id 或 path_id 算错的极端情况）
    by_url = cache.get('by_url') or {}
    import re as _re
    m = _re.match(r'^https?://[^/]+(.*)', source_url or '')
    rel_path = '/' + m.group(1).lstrip('/').rstrip('/') if m else ('/' + source_url.lstrip('/').rstrip('/') if source_url else '/')
    return by_url.get(rel_path, [])


def _get_chain_derived(source_id: int, source_url: str, path_id: str = None) -> dict:
    """Reverse-lookup chain from snapshot identity, then derive the legacy
    snapshot fields (product_name / version_branch / package_type) from
    chain text.

    With the v3 URL-based path_id change, snapshots no longer carry these
    chain-derived fields — they're computed at read time. Chain structure
    (paths[].chain[]):
      - chain[0]   = product name (e.g. "网络入侵防护系统(IPS)")
      - chain[-2]  = version (e.g. "网络入侵防护系统 5.6.10")
      - chain[-1]  = package type (e.g. "引擎升级包")

    Returns dict with keys: product_name, version_branch, package_type,
    and full chain list. Empty dict if no chain found.
    """
    chain = _get_chain(source_id, source_url, path_id)
    if not chain:
        return {
            'product_name': '',
            'version_branch': '',
            'package_type': '',
            'chain': [],
        }
    return {
        'product_name': chain[0] if len(chain) >= 1 else '',
        'version_branch': chain[-2] if len(chain) >= 2 else '',
        'package_type': chain[-1] if len(chain) >= 1 else '',
        'chain': chain,
    }


def invalidate_chain_cache():
    """供外部调用的缓存失效接口（产品管理修改路径后调用）。"""
    global _chain_cache_loaded
    _chain_cache_loaded = False
    _build_url_chain_cache()


def _get_setting(key: str, default: str) -> str:
    """Read setting from DB, fallback to env, fallback to default."""
    try:
        from src.models.database import query
        rows = query("SELECT value FROM system_settings WHERE key = ?", (key,))
        if rows:
            return rows[0]['value']
    except Exception:
        pass
    return os.getenv(f'MONITOR_{key.upper()}', default)


def _set_collection_running(mode: str):
    """Persist collection running state to DB. Non-blocking: gives up immediately on lock contention so the collector thread is never blocked."""
    import json
    from src.models.database import execute
    try:
        execute(
            "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('collection_running', ?, datetime('now'))",
            (json.dumps({"status": "1", "started_at": datetime.utcnow().isoformat(), "mode": mode}),)
        )
    except Exception as e:
        if 'locked' in str(e):
            logger.debug(f'_set_collection_running: skipped (DB locked), collection will proceed without DB flag')
        else:
            logger.warning(f'_set_collection_running: {e}')


def _clear_collection_running():
    """Clear collection running state from DB. Non-blocking: gives up immediately on lock contention."""
    import json
    from src.models.database import execute
    try:
        execute(
            "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('collection_running', ?, datetime('now'))",
            (json.dumps({"status": "0", "started_at": "", "mode": ""}),)
        )
    except Exception as e:
        if 'locked' in str(e):
            logger.debug(f'_clear_collection_running: skipped (DB locked)')
        else:
            logger.warning(f'_clear_collection_running: {e}')


# Record process start time for stale detection
_process_start_time = datetime.utcnow()


def _check_concurrent_stale() -> bool:
    """
    Check if a previous collection is still running (or crashed while running).
    Returns True if we should skip this trigger, False if safe to start.

    Persists state to DB so it survives process restarts.
    """
    from src.models.database import query
    import json
    rows = query("SELECT value FROM system_settings WHERE key = 'collection_running'")
    if not rows or not rows[0]['value']:
        return False

    try:
        data = json.loads(rows[0]['value'])
    except Exception:
        return False

    # data must be a dict with 'status' field; int/str/None is invalid
    if not isinstance(data, dict):
        return False

    if data.get('status') == '0':
        return False  # Idle, safe to start

    # status='1' means previous collection did not end normally
    started_str = data.get('started_at', '')
    if not started_str:
        return False

    try:
        started = datetime.fromisoformat(started_str)
    except Exception:
        return False

    # Defense-in-depth: if collection_running was set BEFORE current process started,
    # it is a leftover from a crashed predecessor — treat as stale regardless of elapsed time
    if started < _process_start_time:
        logger.warning(f'Previous collection started {started} '
                       f'before this process ({_process_start_time}), '
                       f'treating as stale, clearing and allowing this trigger')
        _clear_collection_running()
        return False

    elapsed_hours = (datetime.utcnow() - started).total_seconds() / 3600
    threshold = COLLECT_INTERVAL * 2  # 2x collection interval

    if elapsed_hours > threshold:
        logger.warning(f'Previous collection (mode={data["mode"]}) '
                       f'started {elapsed_hours:.1f}h ago (> {threshold}h threshold), '
                       f'treating as stale, clearing and allowing this trigger')
        _clear_collection_running()
        return False

    logger.info(f'Previous collection (mode={data["mode"]}) still running '
                f'({elapsed_hours:.1f}h < {threshold}h threshold), skipping trigger')
    return True  # Previous still running, skip


COLLECT_INTERVAL = int(_get_setting('collect_interval', '4'))
ROLLBACK_CONFIRM = int(_get_setting('rollback_confirm', '2'))
FULL_SCAN_INTERVAL = int(_get_setting('full_scan_interval', '24'))  # hours, default 1 day
HEALTH_URL = _get_setting('heartbeat_url', '/update/listBvsV6/v/bvssys')

_collector = NsfocusCollector()
_last_full_run: Optional[datetime] = None
_is_running = False
_startup_complete = False  # Set True after startup finishes to prevent premature job execution

# ── Progress state (for async collection) ────────────────────────

_progress = {
    'active': False,
    'mode': '',
    'phase': '',        # 'init' | 'collecting' | 'detecting' | 'notifying' | 'done'
    'current_product': '',
    'current_version': '',
    'products_done': 0,
    'products_total': 0,   # updated dynamically at run time from DB
    'items_collected': 0,
    'total_new': 0,
    'total_rollback': 0,
    'errors': [],
    'started_at': None,
    'finished_at': None,
    'duration_s': 0,
}
_progress_lock = threading.Lock()

_scheduler = None  # APScheduler instance, for runtime rescheduling
_last_heartbeat_run = None    # datetime of last heartbeat invocation
_last_heartbeat_success = None  # datetime of last heartbeat with ≥1 healthy session
_last_health_alert_at = None   # datetime of last health alert sent (avoid spam)
_start_time = None            # datetime of process start (for startup grace period)

# ── Package-type refresh progress (per-source) ──────────────────
_pkg_refresh_state = {
    'active': False,
    'source_id': None,
    'source_name': '',
    'phase': '',      # 'idle' | 'fetching_home' | 'fetching_ver' | 'done' | 'error'
    'log_lines': [],  # list of strings
    'started_at': None,
    'finished_at': None,
}
_pkg_refresh_lock = threading.Lock()


def get_pkg_refresh_progress() -> dict:
    with _pkg_refresh_lock:
        return dict(_pkg_refresh_state)


def get_progress() -> dict:
    """Return current collection progress (thread-safe)."""
    with _progress_lock:
        return dict(_progress)


def get_status() -> dict:
    # Read intervals dynamically from DB (user may have changed via settings page)
    interval_hours = int(_get_setting('collect_interval', '4'))
    full_interval_hours = int(_get_setting('full_scan_interval', '24'))

    # Compute next mode
    next_mode = 'full' if is_full_scan_due() else 'quick'
    # Compute next full scan time
    next_full = None
    if _last_full_run:
        next_full = (_last_full_run + timedelta(hours=full_interval_hours)).isoformat()

    return {
        'last_full_run': _last_full_run.isoformat() if _last_full_run else None,
        'is_running': _is_running and _progress.get('active', False),  # True only when collection is actually in progress
        'current_mode': _progress.get('mode', ''),
        'interval_hours': interval_hours,
        'full_scan_interval_hours': full_interval_hours,
        'next_mode': next_mode,
        'next_full_scan': next_full,
        'enabled': _get_setting('scheduler_enabled', '1') == '1',
    }


def run_now(mode: str = 'delta', progress_callback=None) -> dict:
    """Execute a collection+detection+notification cycle.

    Args:
        mode: 'delta' (fast, list pages only) or 'full' (deep traversal)
        progress_callback: optional callable(phase, detail) for progress updates
    """
    global _last_full_run, _is_running, _progress

    # Prevent concurrent collection across process restarts
    if mode in ('quick', 'delta', 'full'):
        if _check_concurrent_stale():
            return {'status': 'skipped', 'reason': 'Previous collection still running'}

    if _is_running:
        return {'status': 'skipped', 'reason': 'Already running (memory)'}

    _is_running = True
    logger.info(f'Collection starting: mode={mode}')
    start = time.time()

    # Initialize progress
    with _progress_lock:
        _progress.update({
            'active': True, 'mode': mode, 'phase': 'init',
            'current_product': '', 'current_version': '',
            'products_done': 0, 'products_total': len(_collector_products()),
            'zero_items': [],  # 0 items 产品诊断列表: [{name, changed_urls, stable_urls}]
            'items_collected': 0, 'total_new': 0, 'total_rollback': 0,
            'errors': [], 'started_at': datetime.utcnow().isoformat(),
            'finished_at': None, 'duration_s': 0,
        })

    def _emit(phase: str, product: str = '', version: str = '', items: int = 0):
        with _progress_lock:
            _progress['phase'] = phase
            if product:
                _progress['current_product'] = product
            if version:
                _progress['current_version'] = version
            if items:
                _progress['items_collected'] += items
        if progress_callback:
            try:
                progress_callback(phase, {
                    'product': product, 'version': version,
                    'items': items, 'products_done': _progress['products_done'],
                    'products_total': len(_collector_products()),
                })
            except Exception:
                pass

    summary = {
        'status': 'ok', 'mode': mode,
        'started_at': datetime.utcnow().isoformat(),
        'finished_at': None,  # Set on every completion path (L519, L640, L654) and used by L521/L641/L663
        'products': {},
        'products_total': len(_collector_products()),  # 实际访问过的产品数 (=active 总数)
        'zero_items': [],  # 0 items 产品诊断列表: [{name, changed_urls, stable_count}]
        'total_new': 0, 'total_rollback': 0, 'total_notified': 0,
        'errors': [],
    }

    try:
        # 1. Pre-flight-validate ALL active sessions (both discover and collect).
        # All sessions get their heartbeat updated regardless of purpose.
        # Collection MUST use a collect-purpose session.
        all_sessions = get_active_sessions()
        collect_sessions = get_active_collect_sessions()
        if not all_sessions:
            summary['status'] = 'error'
            summary['errors'].append('No active sessions')
            logger.warning('Collection aborted: no active sessions')
            with _progress_lock:
                _progress['active'] = False
                _progress['phase'] = 'done'
            return summary

        valid_session = None
        for sess in all_sessions:
            _collector._set_cookie(sess['cookie_value'])
            is_collect = sess['purpose'] == 'collect'
            purpose_tag = 'collect' if is_collect else 'discover'
            if _collector.verify_session(HEALTH_URL):
                update_status(sess['id'], 'active')
                update_heartbeat(sess['id'], '正常')
                log_heartbeat(sess['id'], '正常', error_msg=f'pre-flight OK ({purpose_tag})', purpose=sess['purpose'], collect_mode=sess.get('collect_mode', ''))
                if is_collect and valid_session is None:
                    valid_session = sess
                    logger.info(f'Pre-flight passed: session {sess["id"]} (collect/{sess.get("collect_mode","standard")})')
            else:
                logger.warning(f'Session {sess["id"]} ({purpose_tag}) pre-flight failed — marking expired')
                update_status(sess['id'], 'expired')
                update_heartbeat(sess['id'], '过期')
                log_heartbeat(sess['id'], '过期', error_msg=f'pre-flight failed ({purpose_tag})', purpose=sess['purpose'], collect_mode=sess.get('collect_mode', ''))
                from src.core.event_handler import emit_session_expired
                emit_session_expired(
                    session_id=sess['id'],
                    purpose=sess['purpose'],
                    collect_mode=sess.get('collect_mode', ''),
                    reason='预检失败（verify_session 返回 false，cookie 可能已失效）',
                    source='pre-flight'
                )

        if valid_session is None:
            summary['status'] = 'error'
            summary['errors'].append('All collect sessions expired — collection aborted')
            with _progress_lock:
                _progress['active'] = False
                _progress['phase'] = 'done'
            return summary

        # Use the first valid collect session
        cookie = valid_session['cookie_value']
        _collector._set_cookie(cookie)

        # 2. Ensure content sources exist in DB (bootstrap from PRODUCTS for backward compat)
        existing_sources = {s['name']: s for s in list_content_sources('nsfocus')}
        for name, entry_url in _collector_products().items():
            if name not in existing_sources:
                upsert_source(name, 'nsfocus', entry_url, 'standard', category='security')
        existing_sources = {s['name']: s for s in list_content_sources('nsfocus')}

        # 3. Collect
        _emit('collecting', product='Starting...')
        all_items = []
        zero_hashes_by_name: dict = {}  # 默认空,full 模式不填充

        if mode == 'quick':
            all_items, zero_hashes_by_name = _collect_quick(existing_sources, cookie, _emit)
        elif mode == 'delta':
            # Legacy: redirect delta to quick
            all_items, zero_hashes_by_name = _collect_quick(existing_sources, cookie, _emit)
        else:
            all_items = _collect_full(existing_sources, list(collect_sessions.values()), cookie, _emit)

        with _progress_lock:
            _progress['products_done'] = len(existing_sources)

        if not all_items:
            summary['status'] = 'warning' if summary['errors'] else 'ok'
            summary['duration_s'] = int(time.time() - start)
            if mode == 'full':
                _last_full_run = datetime.utcnow()
                _save_last_full_scan()
            with _progress_lock:
                _progress['active'] = False
                _progress['phase'] = 'done'
                _progress['duration_s'] = summary['duration_s']
                _progress['finished_at'] = datetime.utcnow().isoformat()
            # 发送采集完成通知（即使无新包）
            summary['finished_at'] = datetime.utcnow().isoformat()
            # 扫描 app.log 中的网络错误，汇总通知
            net_errors = _scan_network_errors_from_log(summary['started_at'], summary.get('finished_at') or summary['started_at'])
            if net_errors:
                from src.core.event_handler import emit_network_error
                emit_network_error(net_errors)
            from src.core.event_handler import emit_collection_summary
            emit_collection_summary(summary, mode)
            return summary

        # 4. Run detection per source
        _emit('detecting')
        for name, src in existing_sources.items():
            src_items = [it for it in all_items if it.source_id == src['id']]
            if not src_items:
                continue

            # ── Capture "before" state for change logging ──────────────
            from src.models.database import query as _db_q
            before_snaps = _db_q(
                """SELECT id, file_name, md5_hash FROM snapshots
                   WHERE source_id=? AND status IN ('active','rollback','rollback_pending')""",
                (src['id'],)
            )
            before_files = {s['file_name'] for s in before_snaps}
            before_by_fname = {s['file_name']: s['md5_hash'] for s in before_snaps}

            result = run_detection(src['id'], src_items, ROLLBACK_CONFIRM,
                                  check_rollback=True,
                                  seen_ids={s['id'] for s in before_snaps})
            summary['total_new'] += len(result.new_items)
            summary['total_rollback'] += len(result.rollback_items)
            summary['products'][name] = summary['products'].get(name, {})
            summary['products'][name]['new'] = len(result.new_items)
            by_type = {}
            for _, snap in result.new_items:
                pt = snap.get('package_type') or 'other'
                by_type[pt] = by_type.get(pt, 0) + 1
            summary['products'][name]['by_type'] = by_type

            # ── Detailed Chinese logging ──────────────────────────────
            after_files = before_files.copy()
            new_files = []
            for sid, snap in result.new_items:
                fname = snap.get('file_name', '')
                after_files.add(fname)
                new_files.append(fname)
            gone_files = sorted(before_files - after_files)

            # Summary line per product
            total_items = len(src_items)
            new_count = len(result.new_items)
            roll_count = len(result.rollback_items)
            if new_count > 0 or roll_count > 0:
                logger.info(f'【{name}】采集完成：本次提取 {total_items} 个文件 | 新增 {new_count} | 回滚 {roll_count}')
                if new_files:
                    new_detail = ', '.join(sorted(new_files)[:15])
                    if len(new_files) > 15:
                        new_detail += f' ...（共{len(new_files)}个）'
                    logger.info(f'  新增文件：{new_detail}')
                if gone_files:
                    gone_detail = ', '.join(gone_files[:15])
                    if len(gone_files) > 15:
                        gone_detail += f' ...（共{len(gone_files)}个）'
                    logger.warning(f'  消失文件（可能被回滚）：{gone_detail}')
            else:
                logger.info(f'【{name}】采集完成：{total_items} 个文件，无变化')
            # ── end detailed logging ──────────────────────────────────

            with _progress_lock:
                _progress['total_new'] = summary['total_new']
                _progress['total_rollback'] = summary['total_rollback']

            # 5. Route notifications for new items
            if result.new_items:
                _emit('notifying', product=name)
                rules = get_enabled_rules()
                for rule in rules:
                    matched = get_new_for_subscription(rule, result.new_items)
                    for sid, snap in matched:
                        route_notifications(sid, rule['id'])

            # 6. Handle rollbacks (only for rules with notify_rollback enabled)
            for sid, snap in result.rollback_items:
                rules = get_enabled_rules()
                for rule in rules:
                    if not rule.get('notify_rollback', 1):
                        continue
                    # 过滤订阅条件：回滚也要符合规则的 filter_conditions
                    matched = get_new_for_subscription(rule, [(sid, snap)])
                    if not matched:
                        continue
                    route_notifications(sid, rule['id'], is_rollback=True)

        # 7. Process delayed queue
        process_delayed_queue()

        # 7b. 0-items 诊断:对比 url→hash 与 DB 中上次记录,只 UPDATE 实际变化的
        # 仅 quick 模式有意义(其他模式 zero_hashes_by_name 为空 dict)
        if zero_hashes_by_name:
            _build_zero_items_diag(summary, existing_sources, zero_hashes_by_name)

        summary['duration_s'] = int(time.time() - start)
        summary['finished_at'] = datetime.utcnow().isoformat()
        if mode == 'full':
            _last_full_run = datetime.utcnow()
            _save_last_full_scan()
            _check_package_types_fresh(existing_sources, cookie, _emit)

        # WAL checkpoint removed — was failing silently with TRUNCATE (requires exclusive lock).
        # Replaced by a daemon thread doing PASSIVE checkpoint every 5 minutes (no lock needed).
        # See: docs/database-is-locked-analysis.md
        from src.notifiers.router import _emit_push_summary
        _emit_push_summary()

        logger.info(f'Cycle complete ({mode}): {summary["total_new"]} new, '
                    f'{summary["total_rollback"]} rollbacks, {summary["duration_s"]}s')

        with _progress_lock:
            _progress['active'] = False
            _progress['phase'] = 'done'
            _progress['duration_s'] = summary['duration_s']
            _progress['finished_at'] = datetime.utcnow().isoformat()
            _progress['total_new'] = summary['total_new']
            _progress['total_rollback'] = summary['total_rollback']

        # Emit collection summary event
        from src.core.event_handler import emit_collection_summary, emit_network_error
        net_errors = _scan_network_errors_from_log(summary['started_at'], summary.get('finished_at') or summary['started_at'])
        if net_errors:
            emit_network_error(net_errors)
        emit_collection_summary(summary, mode)

        return summary

    except Exception as e:
        import traceback
        logger.error(f'Collection failed: {e}\n{traceback.format_exc()}')
        summary['status'] = 'error'
        summary['errors'].append(str(e)[:200])
        summary['duration_s'] = int(time.time() - start)
        summary['finished_at'] = datetime.utcnow().isoformat()
        with _progress_lock:
            _progress['active'] = False
            _progress['phase'] = 'done'
            _progress['errors'].append(str(e)[:200])
            _progress['duration_s'] = summary['duration_s']
            _progress['finished_at'] = datetime.utcnow().isoformat()
        # 发送采集失败通知
        from src.core.event_handler import emit_collection_summary, emit_network_error
        net_errors = _scan_network_errors_from_log(summary['started_at'], summary.get('finished_at') or summary['started_at'])
        if net_errors:
            emit_network_error(net_errors)
        emit_collection_summary(summary, mode)
        return summary

    finally:
        _is_running = False
        try:
            _clear_collection_running()
        except Exception as e:
            logger.warning(f'Failed to clear collection_running in finally block: {e}')


def _collect_quick(existing_sources: dict, cookie: str, emit) -> list:
    """Quick collection: revisit known snapshot URLs, only GET changed pages.

    Uses collector._collect_quick which does HEAD/page-hash checks on known detail pages.
    Falls back gracefully if no known URLs exist (new installation).
    Keeps the package-level dedup from delta mode as safety net.
    """
    _collector._set_cookie(cookie)

    # Note: No dedup safety net here — save_snapshot's 5-tuple unique key
    # (source_id, product_name, version_branch, package_type, md5_hash) handles
    # INSERT-or-UPDATE naturally, so re-feeding items that already exist in DB
    # is a no-op UPDATE (last_seen_at refresh). The previous dedup safety net
    # used (product_name, file_name, md5_hash) without version_branch, which
    # caused all chains sharing a single NSFocus URL (e.g. UTS /wcl_V2.0R00F06
    # shared by 7 chains) to be filtered out after the first chain wrote its row.

    all_items = []
    products = list(_collector_products().items())
    done = 0
    touched_source_ids = []  # batch: collect all touched source_ids for single SQL
    zero_hashes_by_name: dict = {}  # name -> {url: hash}  0 items 诊断

    for name, url in products:
        done += 1
        emit('collecting', product=name)
        if name not in existing_sources:
            continue
        src = existing_sources[name]
        try:
            # Quick mode: HEAD-check known URLs, GET only changed pages
            items, zhash = _collector._collect_quick(src['id'], name)
            all_items.extend(items)
            if items:
                logger.info(f'Quick: {name} extracted {len(items)} items')
            elif zhash:
                zero_hashes_by_name[name] = zhash
            emit('collecting', product=name, items=len(items))
            update_source_health(src['id'], 'ok', datetime.utcnow().isoformat())
            # Bump last_seen_at for unchanged packages too (reflects collection ran)
            touched_source_ids.append(src['id'])

        except SessionExpiredError:
            logger.error(f'Quick {name}: Session expired')
            emit('collecting', product=name, version='SESSION EXPIRED')
            _progress['errors'].append(f'{name}: Session expired')
        except Exception as e:
            logger.error(f'Quick {name}: {e}')
            _progress['errors'].append(f'{name}: {str(e)[:100]}')

        _progress['products_done'] = done

    # Batch update: single SQL for all sources (minimizes SQLite lock contention)
    if touched_source_ids:
        from src.models.snapshot import touch_active_snapshots
        touch_active_snapshots(touched_source_ids)

    return all_items, zero_hashes_by_name


def _collect_full(existing_sources: dict, sessions: list, cookie: str, emit) -> list:
    """Full collection: for each source, directly GET all known final-page URLs.

    Uses package_type_discovered.paths as URL source (same as _collect_quick).
    No recursive traversal needed — paths already contain all final-page URLs.
    Session fallback: on SessionExpiredError, try next available session.
    """
    all_items = []
    products = list(_collector_products().items())
    done = 0
    touched_source_ids = []  # batch: collect all touched source_ids for single SQL

    for name, url in products:
        done += 1
        emit('collecting', product=name)
        if name not in existing_sources:
            continue
        src = existing_sources[name]
        current_cookie = cookie
        session_idx = 0
        # Try each session in order on SessionExpiredError
        while session_idx < len(sessions):
            try:
                items = _collector._collect_quick(src['id'], name)
                all_items.extend(items)
                emit('collecting', product=name, items=len(items))
                update_source_health(src['id'], 'ok', datetime.utcnow().isoformat())
                touched_source_ids.append(src['id'])
                break  # success, move to next product
            except SessionExpiredError:
                session_idx += 1
                if session_idx < len(sessions):
                    current_cookie = sessions[session_idx]['cookie_value']
                    _collector._set_cookie(current_cookie)
                    logger.warning(f'Full [{name}]: session expired, trying backup session')
                else:
                    update_status(sessions[0]['id'], 'expired')
                    _progress['errors'].append(f'{name}: all sessions expired')
                    emit('collecting', product=name, version='ALL SESSIONS EXPIRED')
                    break
            except Exception as e:
                logger.error(f'Full {name}: {e}')
                _progress['errors'].append(f'{name}: {str(e)[:100]}')
                update_source_health(src['id'], 'error')
                break

        _progress['products_done'] = done

    # Batch update: single SQL for all sources (minimizes SQLite lock contention)
    if touched_source_ids:
        from src.models.snapshot import touch_active_snapshots
        touch_active_snapshots(touched_source_ids)

    return all_items


def is_full_scan_due() -> bool:
    """Return True if full scan is due (not called for > full_scan_interval hours).

    Temporarily disabled: full scan always returns False via system setting.
    Re-enable by setting system_settings.full_scan_enabled = '1'.
    """
    # Check master kill-switch
    from src.models.database import query
    rows = query("SELECT value FROM system_settings WHERE key = 'full_scan_enabled'")
    if rows and rows[0]['value'] == '0':
        return False
    return False  # full scan disabled pending re-evaluation


def _load_last_full_scan() -> Optional[datetime]:
    """Load last full scan timestamp from system_settings."""
    try:
        from src.models.database import query
        rows = query("SELECT value FROM system_settings WHERE key = 'last_full_scan_at'")
        if rows:
            return datetime.fromisoformat(rows[0]['value'])
    except Exception:
        pass
    return None


def _save_last_collect_at():
    """No-op — last_collect_at is now read from MAX(last_collected_at) in content_sources."""
    pass


def _save_last_full_scan():
    """Persist last full scan timestamp to DB."""
    try:
        from src.models.database import execute
        ts = _last_full_run.isoformat() if _last_full_run else ''
        execute(
            "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('last_full_scan_at', ?, datetime('now'))",
            (ts,)
        )
    except Exception:
        pass


def _check_package_types_fresh(existing_sources: dict, cookie: str, emit):
    """Full scan 后检查各产品包类型是否有变化，写入 DB 供前端确认。

    流程: discover_package_types -> 对比 DB package_type
    - 一致: 跳过
    - 变化: 写入 package_type_discovered，标记 package_type_changed=1
    - 删减时: 查询受影响的订阅规则，存入 affected_rules 供前端警告
    """
    import json
    from src.models.snapshot import update_source
    from src.models.database import query

    _collector._set_cookie(cookie)
    changed_products = []

    for name, src in existing_sources.items():
        try:
            discovered = _collector.discover_package_types(src['id'], cookie)
            if not discovered:
                continue  # 空产品跳过

            current_raw = src.get('package_type') or ''
            # 兼容新旧格式，统一取 types 列表的集合
            try:
                current_obj = json.loads(current_raw)
                if isinstance(current_obj, dict):
                    current_types = set(current_obj.get('types', []))
                elif isinstance(current_obj, list):
                    current_types = set(current_obj)
                else:
                    current_types = set()
            except Exception:
                current_types = set()

            new_types_list = discovered.get('types', [])
            new_types = set(new_types_list)

            if new_types != current_types:
                # 有变化: 构造新格式并存入 package_type_discovered
                # 复用现有 modes（如果当前有的话），新增的 type 默认 auto
                try:
                    current_obj = json.loads(current_raw) if current_raw else {}
                    if isinstance(current_obj, dict):
                        modes = dict(current_obj.get('modes', {}))
                    else:
                        modes = {}
                except Exception:
                    modes = {}

                for t in new_types_list:
                    if t not in modes:
                        modes[t] = 'auto'

                # 删减检测：被删除且被订阅规则引用的包类型
                removed_types = [t for t in current_types if t not in new_types]
                affected_rules = []
                if removed_types:
                    # 按产品名查找规则（filter_conditions.products 匹配产品名）
                    rules = query(
                        "SELECT id, name, filter_conditions FROM subscription_rules WHERE enabled=1"
                    )
                    for rule in rules:
                        fc = rule.get('filter_conditions') or '{}'
                        try:
                            fc_obj = json.loads(fc) if isinstance(fc, str) else fc
                        except Exception:
                            fc_obj = {}
                        rule_products = fc_obj.get('products') or []
                        if rule_products and name not in rule_products:
                            continue  # 规则不订阅此产品，跳过
                        rule_pkg_types = fc_obj.get('package_types') or []
                        # 扁平化: ['rule,sys', 'av'] -> {'rule', 'sys', 'av'}
                        flat = set()
                        for pt in rule_pkg_types:
                            for t in pt.split(','):
                                t = t.strip()
                                if t:
                                    flat.add(t)
                        # 规则引用的且被删除的类型
                        impacted = flat & set(removed_types)
                        if impacted:
                            affected_rules.append({
                                'id': rule['id'],
                                'name': rule.get('name', ''),
                                'removed_types': list(impacted)
                            })

                disc_data = {
                    'types': new_types_list,
                    'paths': discovered.get('paths', []),
                    'modes': modes,
                }
                if affected_rules:
                    disc_data['affected_rules'] = affected_rules

                new_pkg = json.dumps(disc_data)
                update_source(src['id'],
                              package_type=new_pkg,
                              package_type_discovered=new_pkg,
                              package_type_changed=1)
                changed_products.append(name)
                logger.info(f'[pkg_refresh] {name}: {current_types} -> {new_types}, '
                            f'affected_rules={len(affected_rules)}')
        except Exception as e:
            logger.warning(f'[pkg_refresh] {name}: {e}')

    if changed_products:
        logger.info(f'[pkg_refresh] {len(changed_products)} products changed: {changed_products}')


def refresh_pkg_type_single(source_id: int, session_cookie: str):
    """后台执行单个产品包类型刷新，同步更新 _pkg_refresh_state。

    由 ThreadPoolExecutor 调用，不阻塞 HTTP 请求。
    发现结果通过 scheduler._pkg_refresh_state 传递，HTTP handler 在 SSE 中轮询。
    """
    import json
    import threading
    from datetime import datetime
    from src.models.snapshot import get_source, update_source
    from src.collectors.nsfocus import NsfocusCollector

    collector = NsfocusCollector()

    def log_fn(msg: str):
        with _pkg_refresh_lock:
            _pkg_refresh_state['log_lines'].append(msg)

    src = get_source(source_id)
    if not src:
        with _pkg_refresh_lock:
            _pkg_refresh_state.update({
                'active': False, 'phase': 'error',
                'log_lines': _pkg_refresh_state['log_lines'] + [f'产品 {source_id} 不存在']
            })
        return

    with _pkg_refresh_lock:
        _pkg_refresh_state.update({
            'active': True, 'source_id': source_id,
            'source_name': src['name'],
            'phase': 'fetching_home',
            'log_lines': [f'开始刷新: {src["name"]}'],
            'started_at': datetime.utcnow().isoformat(),
            'finished_at': None,
        })

    def progress_fn(phase: str, current: int = 0, total: int = 0):
        """Called when starting each top-level branch to update phase."""
        label = f'{phase} ({current}/{total})'
        with _pkg_refresh_lock:
            _pkg_refresh_state['phase'] = label

    try:
        collector._set_cookie(session_cookie)
        log_fn('访问首页…')
        types_dict = collector.discover_package_types(source_id, session_cookie, log_fn, progress_fn)

        if types_dict and types_dict.get('types'):
            pkg_json = json.dumps(types_dict)
            update_source(source_id, package_type=pkg_json,
                           package_type_discovered=pkg_json,
                           package_type_changed=0)
            log_fn(f'✅ 已保存，共 {len(types_dict["types"])} 种包类型，{len(types_dict["paths"])} 条路径')
            with _pkg_refresh_lock:
                _pkg_refresh_state['phase'] = 'done'
            update_source_health(source_id, 'ok', datetime.utcnow().isoformat())
        else:
            log_fn('⚠️ 未发现包类型（产品可能无独立升级页）')
            with _pkg_refresh_lock:
                _pkg_refresh_state['phase'] = 'done'
            update_source_health(source_id, 'ok', datetime.utcnow().isoformat())

    except Exception as e:
        log_fn(f'失败: {e}')
        with _pkg_refresh_lock:
            _pkg_refresh_state['phase'] = 'error'
        update_source_health(source_id, 'error')
    finally:
        with _pkg_refresh_lock:
            _pkg_refresh_state['active'] = False
            _pkg_refresh_state['finished_at'] = datetime.utcnow().isoformat()


def _clear_stale_collection_running(force=False):
    """Clear stale collection_running state on startup if it's older than threshold.

    Args:
        force: if True, unconditionally clear any collection_running record
               (used on process startup to handle crashed predecessor)
    """
    try:
        from src.models.database import query
        import json
        rows = query("SELECT value FROM system_settings WHERE key = 'collection_running'")
        if not rows or not rows[0]['value']:
            return
        try:
            data = json.loads(rows[0]['value'])
        except Exception:
            return
        if data.get('status') != '1':
            return
        started_str = data.get('started_at', '')
        if not started_str:
            return
        try:
            started = datetime.fromisoformat(started_str)
        except Exception:
            return

        if force:
            logger.warning(f'Auto-clearing collection_running on startup (force=true, '
                           f'started {started_str})')
            _clear_collection_running()
            return

        # Normal: only clear if elapsed exceeds threshold (handles in-process timeout)
        elapsed_hours = (datetime.utcnow() - started).total_seconds() / 3600
        threshold = COLLECT_INTERVAL * 2
        if elapsed_hours > threshold:
            logger.warning(f'Auto-clearing stale collection_running (started {elapsed_hours:.1f}h ago)')
            _clear_collection_running()
    except Exception:
        pass

def refresh_scheduler_jobs():
    """Add or remove all scheduled jobs based on scheduler_enabled setting."""
    global _scheduler
    if not _scheduler:
        return
    enabled = _get_setting('scheduler_enabled', '1') == '1'
    if enabled:
        interval_hours = int(_get_setting('collect_interval', '4'))
        hb_interval = int(_get_setting('heartbeat_interval', '30'))
        if not _scheduler.get_job('nsfocus_collect'):
            _scheduler.add_job(_smart_collect, 'interval', hours=interval_hours,
                               id='nsfocus_collect', name='NSFOCUS Smart Collection',
                               next_run_time=datetime.utcnow() + timedelta(seconds=10))
            logger.info(f'Collection job added: every {interval_hours}h')
        else:
            _scheduler.reschedule_job('nsfocus_collect', trigger='interval', hours=interval_hours)
        # 心跳任务：仅在 heartbeat_enabled='1' 时启用
        heartbeat_enabled = _get_setting('heartbeat_enabled', '0') == '1'
        if heartbeat_enabled:
            hb_interval = int(_get_setting('heartbeat_interval', '30'))
            if not _scheduler.get_job('nsfocus_heartbeat'):
                _scheduler.add_job(_session_heartbeat, 'interval', minutes=hb_interval,
                                  id='nsfocus_heartbeat', name='Session Heartbeat',
                                  next_run_time=datetime.utcnow())
                logger.info('[HEARTBEAT] nsfocus_heartbeat job ADDED, interval=%d min', hb_interval)
            else:
                _scheduler.reschedule_job('nsfocus_heartbeat', trigger='interval', minutes=hb_interval)
                logger.info('[HEARTBEAT] nsfocus_heartbeat job RESCHEDULED, interval=%d min', hb_interval)
        else:
            if _scheduler.get_job('nsfocus_heartbeat'):
                _scheduler.remove_job('nsfocus_heartbeat')
                logger.info('[HEARTBEAT] nsfocus_heartbeat job REMOVED (heartbeat_enabled=0)')
        if not _scheduler.get_job('nsfocus_digest'):
            _scheduler.add_job(_digest_check, 'interval', hours=6,
                                id='nsfocus_digest', name='Digest Summary Check',
                                next_run_time=datetime.utcnow())
        # 数据库清理：每天凌晨 3 点执行
        if not _scheduler.get_job('nsfocus_db_cleanup'):
            _scheduler.add_job(_run_db_cleanup, 'cron', hour=3, minute=0,
                               id='nsfocus_db_cleanup', name='DB Cleanup',
                               next_run_time=_next_cleanup_run())
        else:
            _scheduler.reschedule_job('nsfocus_db_cleanup', trigger='cron',
                                      hour=3, minute=0)
        # nsfocus_health removed: _health_check is disabled
    else:
        for job_id in ['nsfocus_collect', 'nsfocus_heartbeat', 'nsfocus_digest', 'nsfocus_db_cleanup']:
            job = _scheduler.get_job(job_id)
            if job:
                _scheduler.remove_job(job_id)
                logger.info(f'Job removed: {job_id}')
    logger.info(f'Scheduler jobs refreshed (enabled={enabled})')


def start_scheduler(app=None):
    """Start the APScheduler background scheduler."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        global _scheduler, _start_time
        _start_time = datetime.utcnow()
        sched = BackgroundScheduler(max_workers=1)
        sched.start()
        _scheduler = sched

        # WAL checkpoint removed (journal_mode=DELETE, no WAL file)

        _clear_stale_collection_running(force=True)
        # 强制复位 _is_running，避免上一个 crash 的进程遗留此标志
        global _is_running
        _is_running = False
        # 启动时执行一次数据库清理
        _run_db_cleanup()
        refresh_scheduler_jobs()

        # ── Clean shutdown: clear collection_running on SIGTERM/exit ──
        # APScheduler itself does shutdown() on atexit, we just clear the flag.
        def _shutdown_cleanup():
            global _scheduler
            try:
                if _scheduler and _scheduler.running:
                    _scheduler.shutdown(wait=False)
            except Exception:
                pass
            # 清理 collection_running 标记，避免重启后误判
            _clear_collection_running()

        atexit.register(_shutdown_cleanup)

        # 延迟5秒后执行首次运行检查（在主线程同步执行，避免与 health check 竞争 _is_running）
        import time as _time
        def _delayed_first_run():
            _time.sleep(5)
            _check_first_run()
        threading.Thread(target=_delayed_first_run, daemon=True).start()

        # Build URL→Chain cache for subscription chain matching
        _build_url_chain_cache()

        # Mark startup complete — now _smart_collect job is allowed to run
        global _startup_complete
        _startup_complete = True

        return sched
    except ImportError:
        logger.warning('APScheduler not installed; using manual trigger only')
        return None


def reschedule_collect():
    """Reschedule the collection job with current interval from DB."""
    global _scheduler
    if _scheduler:
        interval_hours = int(_get_setting('collect_interval', '4'))
        job = _scheduler.get_job('nsfocus_collect')
        if job:
            _scheduler.reschedule_job('nsfocus_collect', trigger='interval', hours=interval_hours)
            logger.info(f'Collection rescheduled: every {interval_hours}h')


def reschedule_heartbeat():
    """Reschedule the session heartbeat job. Delegates to refresh_scheduler_jobs for full logic."""
    refresh_scheduler_jobs()


def _smart_collect():
    """Smart collection: full scan if due, quick otherwise. Eliminates race condition."""
    if not _startup_complete:
        return
    if is_full_scan_due():
        logger.info('Full scan interval reached, running full collection')
        run_now(mode='full')
    else:
        run_now(mode='quick')


def _check_first_run():
    """Check if this is first run (no source_url populated) and trigger full scan."""
    try:
        from src.models.database import query
        rows = query("SELECT COUNT(*) as cnt FROM snapshots WHERE source_url != '' AND status = 'active'")
        if rows and rows[0]['cnt'] == 0:
            active = query("SELECT COUNT(*) as cnt FROM snapshots WHERE status = 'active'")
            if active and active[0]['cnt'] > 0:
                logger.info('First run detected: no source_url populated, scheduling immediate full scan')
                import threading
                t = threading.Thread(target=lambda: run_now(mode='full'), daemon=True)
                t.start()
    except Exception as e:
        logger.warning(f'First-run check failed: {e}')


def _session_heartbeat():
    """Send lightweight requests to keep all active PHPSESSID alive.

    PHP default session.gc_maxlifetime is 24 min, so 30 min heartbeat
    should prevent server-side session expiry between collection cycles.
    Skips heartbeat if collection is in progress to avoid cookie conflicts.

    Anti-detection: jitter + skip probability to break periodic patterns.

    All active sessions (collect + discover) get a heartbeat.
    Pollution detection (format check) only applies to collect sessions.
    """
    global _last_heartbeat_run, _last_heartbeat_success
    _last_heartbeat_run = datetime.utcnow()

    logger.info('[HEARTBEAT] _session_heartbeat invoked, _is_running=%s', _is_running)
    import time as _time

    # Anti-detection: random initial delay (0-30s) to break periodic pattern
    _time.sleep(random.uniform(0, 30))

    # Anti-detection: 5% skip probability — simulates human patrol gaps
    if random.random() < 0.05:
        logger.debug('Heartbeat skipped: random skip (anti-detection)')
        return
    import requests as _requests
    from src.models.user_session import update_heartbeat, log_heartbeat, update_status

    if _is_running:
        logger.info('[HEARTBEAT] skipped: collection in progress')
        return

    sessions = get_active_sessions()
    if not sessions:
        return

    # interval = int(_get_setting('heartbeat_interval', '30'))  # minutes — not used yet
    BASE_URL = 'https://update.nsfocus.com'

    for sess in sessions:
        # Anti-detection: random delay between sessions (2-15s) to spread traffic
        _time.sleep(random.uniform(2, 15))

        cookie = sess['cookie_value']
        session_id = sess['id']
        purpose = sess.get('purpose', 'collect')

        try:
            start = _time.time()
            resp = _requests.get(
                BASE_URL + HEALTH_URL,
                cookies={'PHPSESSID': cookie},
                timeout=10,
                allow_redirects=False
            )
            latency = int((_time.time() - start) * 1000)

            # Pollution detection: collect session only
            if purpose == 'collect':
                if 'downloadsVm/id' in resp.text:
                    try:
                        update_status(session_id, 'expired')
                        update_heartbeat(session_id, '污染')
                        log_heartbeat(session_id, '污染', error_msg='collect session 返回 downloadsVm/id，说明上下文被 upLic/Vm 格式污染', purpose=purpose, collect_mode=sess.get('collect_mode', ''))
                    except Exception as ex:
                        logger.warning(f'Session {session_id} DB 更新失败（污染检测）: {ex}')
                    logger.warning(f'Session {session_id} 污染 (downloadsVm/id detected)')
                    from src.core.event_handler import emit_session_error
                    emit_session_error(
                        username=sess.get('username', ''),
                        product_name=sess.get('product_name', ''),
                        reason='Session 污染（上下文被 upLic/Vm 格式污染）'
                    )
                    continue

            # Session expiry: 302 redirect → expired (all sessions)
            if resp.status_code == 302:
                loc = resp.headers.get('Location', '')
                if '/portal/index' in loc:
                    try:
                        update_status(session_id, 'expired')
                        update_heartbeat(session_id, '过期')
                        log_heartbeat(session_id, '过期', error_msg=f'302 跳转 {loc}，session 已失效', purpose=purpose, collect_mode=sess.get('collect_mode', ''))
                    except Exception as ex:
                        logger.warning(f'Session {session_id} DB 更新失败（过期检测）: {ex}')
                    logger.warning(f'Session {session_id} 过期 (redirect to {loc})')
                    from src.core.event_handler import emit_session_error
                    emit_session_error(
                        username=sess.get('username', ''),
                        product_name=sess.get('product_name', ''),
                        reason=f'Session 过期（302 跳转 {loc}）'
                    )
                    continue

            # 200 OK + no pollution + no portal redirect → session alive
            try:
                update_heartbeat(session_id, '正常')
                log_heartbeat(session_id, '正常', latency_ms=latency, error_msg='200 OK，session 存活', purpose=purpose, collect_mode=sess.get('collect_mode', ''))
            except Exception as ex:
                logger.warning(f'Session {session_id} DB 更新失败（正常心跳）: {ex}')
            logger.debug(f'Heartbeat OK session={session_id} purpose={purpose} ({latency}ms)')
            _last_heartbeat_success = datetime.utcnow()

        except _requests.RequestException as e:
            err_str = str(e)
            # Extract key error type for readability
            if 'ConnectionRefused' in err_str or 'refused' in err_str.lower():
                detail = '连接被拒绝（目标服务器未开放端口）'
            elif 'Timeout' in err_str or 'timed out' in err_str.lower():
                detail = '连接超时（目标服务器响应过慢）'
            elif 'SSLError' in err_str or 'SSL' in err_str:
                detail = 'SSL 证书错误'
            elif 'ConnectionError' in err_str or 'NewConnectionError' in err_str:
                detail = '网络连接失败（无法到达目标服务器）'
            elif 'DNS' in err_str or 'gaierror' in err_str.lower():
                detail = 'DNS 解析失败'
            else:
                detail = err_str[:200]
            try:
                update_heartbeat(session_id, '错误')
                log_heartbeat(session_id, '错误', error_msg=f'网络错误: {detail}', purpose=purpose, collect_mode=sess.get('collect_mode', ''))
            except Exception as ex:
                logger.warning(f'Session {session_id} DB 更新失败（网络错误）: {ex}')
            logger.warning(f'Heartbeat failed session={session_id}: {detail}')
            from src.core.event_handler import emit_session_error
            emit_session_error(
                username=sess.get('username', ''),
                product_name=sess.get('product_name', ''),
                reason=f'网络错误: {detail}'
            )


def _digest_check():
    """Check for due digest summaries and send them."""
    from src.notifiers.router import process_digests
    try:
        process_digests()
    except Exception as e:
        logger.error(f'Digest check failed: {e}')


def _health_check():
    """Monitor scheduler health: heartbeat invocation and collection status.

    DISABLED: previously triggering concurrent run_now() calls that blocked collections.
    """
    return
    """Alerts if:
    - Heartbeat job hasn't run in > 2x interval (missing scheduler execution)
    - Collection hasn't succeeded in > 2x collection interval (stuck collection)
    - Both send via emit_session_error so the system_event notification handles delivery
    """
    global _last_health_alert_at
    import time as _time

    now = datetime.utcnow()
    hb_interval_min = int(_get_setting('heartbeat_interval', '10'))
    collect_interval_h = int(_get_setting('collect_interval', '4'))
    collect_interval_s = collect_interval_h * 3600

    # Grace period: skip alerts in first 15 minutes after startup
    if _start_time and (now - _start_time).total_seconds() < 900:
        return

    # Cooldown: at most one health alert per collection interval to avoid spam
    if _last_health_alert_at and (now - _last_health_alert_at).total_seconds() < collect_interval_s:
        return

    alerts = []

    # Check 1: heartbeat job invocation lag
    if _last_heartbeat_run:
        hb_lag_min = (now - _last_heartbeat_run).total_seconds() / 60
        if hb_lag_min > hb_interval_min * 2:
            alerts.append(f'心跳任务已有 {int(hb_lag_min)} 分钟未执行（间隔 {hb_interval_min} 分钟）')
            logger.warning('[HEALTH] Heartbeat job lag: %.0f min since last run', hb_lag_min)
    else:
        # Never run
        alerts.append(f'心跳任务从未执行（配置间隔 {hb_interval_min} 分钟）')
        logger.warning('[HEALTH] Heartbeat job has never run')

    # Check 2: heartbeat success lag (≥1 healthy session)
    if _last_heartbeat_success:
        hb_ok_lag_min = (now - _last_heartbeat_success).total_seconds() / 60
        hb_ok_lag_h = hb_ok_lag_min / 60
        if hb_ok_lag_min > hb_interval_min * 2:
            alerts.append(f'心跳健康检查已有 {hb_ok_lag_h:.1f} 小时未成功（Session 可能失效）')
            logger.warning('[HEALTH] Heartbeat success lag: %.1f hours since last healthy heartbeat', hb_ok_lag_h)
    else:
        # No successful heartbeat ever
        alerts.append('从未有成功的心跳健康检查')
        logger.warning('[HEALTH] No successful heartbeat ever recorded')

    # Check 3: collection stuck
    collect_interval_s = collect_interval_h * 3600
    last_collect = _get_setting('last_collect_at', '')
    if last_collect:
        try:
            from datetime import datetime as dt
            last_ts = dt.fromisoformat(last_collect.replace('Z', '+00:00'))
            collect_lag_h = (now - last_ts.replace(tzinfo=None)).total_seconds() / 3600
            if collect_lag_h > collect_interval_h * 2:
                alerts.append(f'采集任务已有 {collect_lag_h:.1f} 小时未成功（超过间隔 {collect_interval_h} 小时×2）')
                logger.warning('[HEALTH] Collection lag: %.1f hours since last success', collect_lag_h)
        except Exception:
            pass

    if alerts:
        reason = '；'.join(alerts)
        try:
            from src.core.event_handler import emit_session_error
            emit_session_error(
                username='scheduler',
                product_name='调度器健康检查',
                reason=reason,
                source='health_check'
            )
            _last_health_alert_at = now
            logger.info('[HEALTH] Health alert sent: %s', reason)
        except Exception as e:
            logger.error('[HEALTH] Failed to send health alert: %s', e)


# ── Database cleanup ──────────────────────────────────────────────────────────

def _next_cleanup_run():
    """返回明天凌晨 3:00 UTC 的 datetime，用于 cron trigger 的 next_run_time。"""
    tomorrow = datetime.utcnow().replace(hour=3, minute=0, second=0, microsecond=0)
    if tomorrow <= datetime.utcnow():
        tomorrow += timedelta(days=1)
    return tomorrow


def _run_db_cleanup():
    """清理过期数据：
      - heartbeat_log: 已废弃，全量清空
      - audit_log: 保留 30 天
      - delivery_log: 保留 90 天
    仅清理，不抛异常，由 scheduler 启动和定时任务调用。

    每次执行结束（或异常）后通过 emit_cleanup_summary 发送一条系统事件，
    在 system_event_log 留痕 + 按系统事件配置通知渠道。"""
    from src.models.database import execute, query, get_db
    from src.models.audit import cleanup_old
    from src.models.subscription import clear_history
    logger = get_logger('cleanup')

    # 收集每步执行情况，最后统一发系统事件
    summary = {
        'started_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S'),
        'steps': [],
        'errors': [],
    }

    def _record_step(name: str, ok: bool, detail: str, **extra):
        summary['steps'].append({
            'name': name,
            'ok': ok,
            'detail': detail,
            **extra,
        })

    try:
        # 1. heartbeat_log 已废弃，直接清空
        count = execute("DELETE FROM heartbeat_log") or 0
        if count > 0:
            logger.info('heartbeat_log 清空: %d 条', count)
        else:
            logger.debug('heartbeat_log 无数据')
        _record_step('heartbeat_log', True, f'清理 {count} 条', deleted=count)
    except Exception as e:
        logger.warning('heartbeat_log 清空失败: %s', e)
        summary['errors'].append(f'heartbeat_log: {e}')
        _record_step('heartbeat_log', False, str(e))

    try:
        # 2. audit_log 保留 30 天
        before = query("SELECT COUNT(*) as c FROM audit_log")[0]['c']
        cleanup_old(days=30)
        after = query("SELECT COUNT(*) as c FROM audit_log")[0]['c']
        logger.info('audit_log 清理完成: 清理前 %d 条，保留 %d 条（30天）', before, after)
        _record_step('audit_log', True, f'清理前 {before} 条，保留 {after} 条（30天）',
                     before=before, after=after, deleted=before - after)
    except Exception as e:
        logger.warning('audit_log 清理失败: %s', e)
        summary['errors'].append(f'audit_log: {e}')
        _record_step('audit_log', False, str(e))

    try:
        # 3. delivery_log 保留 90 天
        before = query("SELECT COUNT(*) as c FROM delivery_log")[0]['c']
        cleared = clear_history(older_than_days=90)
        after = query("SELECT COUNT(*) as c FROM delivery_log")[0]['c']
        logger.info('delivery_log 清理完成: 清理前 %d 条，删除 %d 条，保留 %d 条（90天）',
                     before, cleared, after)
        _record_step('delivery_log', True,
                     f'清理前 {before} 条，删除 {cleared} 条，保留 {after} 条（90天）',
                     before=before, after=after, deleted=cleared)
    except Exception as e:
        logger.warning('delivery_log 清理失败: %s', e)
        summary['errors'].append(f'delivery_log: {e}')
        _record_step('delivery_log', False, str(e))

    try:
        # 4. snapshots: 按 (source_id, path_id, version_branch, package_type) 分组，
        #    每组保留 N 条最新快照。
        #    关键：必须把 path_id 加进 group 维度。
        #    NSFocus 多 chain (海光/飞腾/鲲鹏/标准版/...) 共享同一升级文件，
        #    同一文件在 DB 里按 path_id 存了多条。如果 group 不带 path_id，
        #    这些跨 chain 行会被错误合并到一个组，每组数 > per_group 时
        #    凌晨清理会 DELETE 老 path_id 的行，下次 collect 重新入库形成
        #    phantom-新增（detector 误报为新包）。
        from src.models.database import query as db_query
        limit_cfg = db_query("SELECT value FROM system_settings WHERE key = 'snapshots_per_group'")
        per_group = int(limit_cfg[0]['value']) if limit_cfg else 5

        # 统计清理前
        before = db_query("SELECT COUNT(*) as c FROM snapshots")[0]['c']
        before_rb = db_query("SELECT COUNT(*) as c FROM snapshots WHERE status = 'rollback'")[0]['c']

        deleted = 0
        # 对每个 source 分别处理（跨 source 的同组合并不合并）
        db = get_db()
        cur = db.cursor()
        # 找出所有需要清理的组（组内记录数 > per_group 且有非 rollback 记录）
        cur.execute("""
            SELECT source_id, path_id, version_branch, package_type, COUNT(*) as cnt
            FROM snapshots
            WHERE status != 'rollback' AND path_id != ''
            GROUP BY source_id, path_id, version_branch, package_type
            HAVING COUNT(*) > ?
        """, (per_group,))
        groups = cur.fetchall()

        for g in groups:
            src_id, pid, ver, pkgtype, cnt = g['source_id'], g['path_id'], g['version_branch'], g['package_type'], g['cnt']
            # 保留该组最新 per_group 条，其余删除
            cur.execute("""
                DELETE FROM snapshots
                WHERE source_id = ?
                  AND path_id = ?
                  AND version_branch = ?
                  AND package_type = ?
                  AND status != 'rollback'
                  AND id NOT IN (
                      SELECT id FROM snapshots
                      WHERE source_id = ?
                        AND path_id = ?
                        AND version_branch = ?
                        AND package_type = ?
                        AND status != 'rollback'
                      ORDER BY published_at DESC
                      LIMIT ?
                  )
            """, (src_id, pid, ver, pkgtype, src_id, pid, ver, pkgtype, per_group))
            deleted += cur.rowcount

        # Cleanup fallback: 仍按旧的 (source_id, version_branch, package_type) 分组，
        # 但只处理 path_id 为空的 snap（legacy rows，pre-path_id migration）。
        # path_id 非空行已经在上面按 path_id 处理过了。
        cur.execute("""
            SELECT source_id, version_branch, package_type, COUNT(*) as cnt
            FROM snapshots
            WHERE status != 'rollback' AND path_id = ''
            GROUP BY source_id, version_branch, package_type
            HAVING COUNT(*) > ?
        """, (per_group,))
        legacy_groups = cur.fetchall()
        for g in legacy_groups:
            src_id, ver, pkgtype, cnt = g['source_id'], g['version_branch'], g['package_type'], g['cnt']
            cur.execute("""
                DELETE FROM snapshots
                WHERE source_id = ?
                  AND version_branch = ?
                  AND package_type = ?
                  AND path_id = ''
                  AND status != 'rollback'
                  AND id NOT IN (
                      SELECT id FROM snapshots
                      WHERE source_id = ?
                        AND version_branch = ?
                        AND package_type = ?
                        AND path_id = ''
                        AND status != 'rollback'
                      ORDER BY published_at DESC
                      LIMIT ?
                  )
            """, (src_id, ver, pkgtype, src_id, ver, pkgtype, per_group))
            deleted += cur.rowcount

        after = db_query("SELECT COUNT(*) as c FROM snapshots")[0]['c']
        after_rb = db_query("SELECT COUNT(*) as c FROM snapshots WHERE status = 'rollback'")[0]['c']
        logger.info('snapshots 清理完成: 清理前 %d 条（含rollback %d），删除 %d 条，保留 %d 条（rollback %d），每组上限 %d',
                    before, before_rb, deleted, after, after_rb, per_group)
        _record_step('snapshots', True,
                     f'清理前 {before} 条（含 rollback {before_rb}），删除 {deleted} 条，保留 {after} 条，每组上限 {per_group}',
                     before=before, before_rb=before_rb,
                     after=after, after_rb=after_rb,
                     deleted=deleted, per_group=per_group)
    except Exception as e:
        logger.warning('snapshots 清理失败: %s', e)
        summary['errors'].append(f'snapshots: {e}')
        _record_step('snapshots', False, str(e))

    # ── 发送清理汇总系统事件 ──
    try:
        from src.core.event_handler import emit_cleanup_summary
        summary['finished_at'] = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
        summary['ok'] = len(summary['errors']) == 0
        emit_cleanup_summary(summary)
    except Exception as e:
        logger.warning(f'清理汇总事件发送失败: {e}')




# ── 0-items 诊断:对比 url→hash 与 DB 中上次记录的 ──────────────────
def _build_zero_items_diag(summary: dict, existing_sources: dict,
                            zero_hashes_by_name: dict) -> None:
    """填充 summary['zero_items'] + UPDATE content_sources.config(仅 hash 变化时)。

    zero_hashes_by_name: {name: {url: hash}}   本次扫描 0 items 产品的 url→hash
    existing_sources:    {name: src_row}      从 list_content_sources 来
    summary:             dict                  会被原地修改 zero_items

    写入策略:仅当 current_hashes != stored_hashes 时 UPDATE content_sources.config,
    减少不必要的写。用 WHERE id=? AND json_extract(config,'$.quick_zero_hashes') != ?
    让 SQLite 自己判断。"""
    import json as _json
    from src.models.database import query as _db_q, execute as _db_e

    diag_list = []
    for name, curr_hashes in zero_hashes_by_name.items():
        if name not in existing_sources:
            continue
        src_id = existing_sources[name]['id']

        # 始终从 DB 读最新 config(existing_sources 里的可能 stale)
        stored = {}
        cfg = {}
        try:
            row = _db_q("SELECT config FROM content_sources WHERE id = ?", (src_id,))
            if row:
                cfg_raw = row[0].get('config') or '{}'
                cfg = _json.loads(cfg_raw) if cfg_raw.strip() else {}
                stored = cfg.get('quick_zero_hashes', {})
        except Exception:
            stored = {}

        changed = []
        stable = []
        first_seen = False
        for url, h in curr_hashes.items():
            prev = stored.get(url)
            if prev is None:
                first_seen = True
                changed.append({'url': url, 'hash': h, 'prev': None, 'first_seen': True})
            elif prev != h:
                changed.append({'url': url, 'hash': h, 'prev': prev, 'first_seen': False})
            else:
                stable.append(url)

        # 仅当本次 hash 集合与上次不同才写 DB(避免无意义 UPDATE)
        curr_key = sorted(curr_hashes.items())
        stored_key = sorted(stored.items())
        if curr_key != stored_key:
            new_cfg = dict(cfg) if isinstance(cfg, dict) else {}
            new_cfg['quick_zero_hashes'] = curr_hashes
            try:
                _db_e(
                    "UPDATE content_sources SET config = ? WHERE id = ?",
                    (_json.dumps(new_cfg, ensure_ascii=False), src_id)
                )
            except Exception as e:
                logger.warning(f'zero_items diag write fail for {name}: {e}')

        diag_list.append({
            'name': name,
            'changed_urls': changed,
            'stable_count': len(stable),
            'first_seen': first_seen and not stored,
        })

    summary['zero_items'] = diag_list
    n_changed = sum(1 for d in diag_list if d['changed_urls'])
    logger.info(f'Zero-items diagnostic: {len(diag_list)} products, '
                f'{n_changed} have hash changes, '
                f'{sum(1 for d in diag_list if d["first_seen"])} first-time records')
