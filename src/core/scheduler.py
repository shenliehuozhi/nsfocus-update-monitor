"""Scheduler — periodic collection + detection + notification pipeline.

Supports two modes:
  'delta' (fast) — only check list pages, ~20s, for frequent runs
  'full'  (slow) — traverse all detail pages, ~15-20min, for weekly deep scan
"""

import os
import time
import threading
from datetime import datetime, timedelta
from typing import Optional

from src.core.logger import get_logger
from src.collectors.nsfocus import NsfocusCollector, SessionExpiredError, _get_products as _collector_products
from src.detector.change import run_detection, get_new_for_subscription
from src.notifiers.router import route_notifications, process_delayed_queue
from src.models.user_session import get_active_sessions, update_status
from src.models.snapshot import (
    get_source_by_name, update_source_health,
    list_sources as list_content_sources,
    create_source as create_content_source,
    get_active_snapshots,
    upsert_source,
)
from src.models.subscription import get_enabled_rules

logger = get_logger('scheduler')


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


COLLECT_INTERVAL = int(_get_setting('collect_interval', '4'))
ROLLBACK_CONFIRM = int(_get_setting('rollback_confirm', '2'))
FULL_SCAN_INTERVAL = int(_get_setting('full_scan_interval', '24'))  # hours, default 1 day

_collector = NsfocusCollector()
_last_run: Optional[datetime] = None
_last_full_run: Optional[datetime] = None
_last_mode: str = ''  # 'quick' or 'full'
_is_running = False

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
        'last_run': _last_run.isoformat() if _last_run else None,
        'last_full_run': _last_full_run.isoformat() if _last_full_run else None,
        'last_mode': _last_mode,
        'is_running': _is_running,
        'current_mode': _progress.get('mode', ''),
        'interval_hours': interval_hours,
        'full_scan_interval_hours': full_interval_hours,
        'next_mode': next_mode,
        'next_full_scan': next_full,
    }


def run_now(mode: str = 'delta', progress_callback=None) -> dict:
    """Execute a collection+detection+notification cycle.

    Args:
        mode: 'delta' (fast, list pages only) or 'full' (deep traversal)
        progress_callback: optional callable(phase, detail) for progress updates
    """
    global _last_run, _last_full_run, _is_running, _progress, _last_mode

    if _is_running:
        return {'status': 'skipped', 'reason': 'Already running'}

    _is_running = True
    _last_mode = mode
    logger.info(f'Collection starting: mode={mode}')
    start = time.time()

    # Initialize progress
    with _progress_lock:
        _progress.update({
            'active': True, 'mode': mode, 'phase': 'init',
            'current_product': '', 'current_version': '',
            'products_done': 0, 'products_total': len(_collector_products()),
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
        'products': {},
        'total_new': 0, 'total_rollback': 0, 'total_notified': 0,
        'errors': [],
    }

    try:
        # 1. Get active session
        sessions = get_active_sessions()
        if not sessions:
            summary['status'] = 'error'
            summary['errors'].append('No active sessions')
            _is_running = False
            _last_run = datetime.utcnow()
            logger.warning('Collection aborted: no active sessions')
            with _progress_lock:
                _progress['active'] = False
                _progress['phase'] = 'done'
            return summary

        cookie = sessions[0]['cookie_value']
        _collector._set_cookie(cookie)

        # Pre-validate session before starting collection.
        # Avoids the scenario where an expired session causes a product
        # to collect 0 items → all its snapshots marked rollback.
        if not _collector.verify_session():
            logger.warning('Pre-flight session check failed, trying backup...')
            update_status(sessions[0]['id'], 'expired')
            if len(sessions) > 1:
                cookie = sessions[1]['cookie_value']
                _collector._set_cookie(cookie)
                if not _collector.verify_session():
                    logger.warning('Backup session also invalid')
                    update_status(sessions[1]['id'], 'expired')
                    summary['status'] = 'error'
                    summary['errors'].append('All sessions expired — collection aborted')
                    _is_running = False
                    _last_run = datetime.utcnow()
                    return summary
                logger.info('Switched to backup session')
            else:
                summary['status'] = 'error'
                summary['errors'].append('Session expired — collection aborted')
                _is_running = False
                _last_run = datetime.utcnow()
                return summary

        # 2. Ensure content sources exist in DB (bootstrap from PRODUCTS for backward compat)
        existing_sources = {s['name']: s for s in list_content_sources('nsfocus')}
        for name, entry_url in _collector_products().items():
            if name not in existing_sources:
                upsert_source(name, 'nsfocus', entry_url, 'standard', category='security')
        existing_sources = {s['name']: s for s in list_content_sources('nsfocus')}

        # 3. Collect
        _emit('collecting', product='Starting...')
        all_items = []

        if mode == 'quick':
            all_items = _collect_quick(existing_sources, _emit)
        elif mode == 'delta':
            # Legacy: redirect delta to quick
            all_items = _collect_quick(existing_sources, _emit)
        else:
            all_items = _collect_full(existing_sources, sessions, cookie, _emit)

        with _progress_lock:
            _progress['products_done'] = len(existing_sources)

        if not all_items:
            summary['status'] = 'warning' if summary['errors'] else 'ok'
            summary['duration_s'] = int(time.time() - start)
            _is_running = False
            _last_run = datetime.utcnow()
            if mode == 'full':
                _last_full_run = datetime.utcnow()
                _save_last_full_scan()
            with _progress_lock:
                _progress['active'] = False
                _progress['phase'] = 'done'
                _progress['duration_s'] = summary['duration_s']
                _progress['finished_at'] = datetime.utcnow().isoformat()
            return summary

        # 4. Run detection per source
        _emit('detecting')
        for name, src in existing_sources.items():
            src_items = [it for it in all_items if it.source_id == src['id']]
            if not src_items:
                continue

            result = run_detection(src['id'], src_items, ROLLBACK_CONFIRM,
                                  check_rollback=True)
            summary['total_new'] += len(result.new_items)
            summary['total_rollback'] += len(result.rollback_items)
            summary['products'][name] = summary['products'].get(name, {})
            summary['products'][name]['new'] = len(result.new_items)

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
                    route_notifications(sid, rule['id'], is_rollback=True)

        # 7. Process delayed queue
        process_delayed_queue()

        summary['duration_s'] = int(time.time() - start)
        _is_running = False
        _last_run = datetime.utcnow()
        if mode == 'full':
            _last_full_run = datetime.utcnow()
            _save_last_full_scan()
            _check_package_types_fresh(existing_sources, cookie, emit)

        # WAL checkpoint: prevent unlimited WAL file growth
        try:
            from src.models.database import get_db
            get_db().execute('PRAGMA wal_checkpoint(PASSIVE)')
        except Exception:
            pass

        logger.info(f'Cycle complete ({mode}): {summary["total_new"]} new, '
                    f'{summary["total_rollback"]} rollbacks, {summary["duration_s"]}s')

        with _progress_lock:
            _progress['active'] = False
            _progress['phase'] = 'done'
            _progress['duration_s'] = summary['duration_s']
            _progress['finished_at'] = datetime.utcnow().isoformat()
            _progress['total_new'] = summary['total_new']
            _progress['total_rollback'] = summary['total_rollback']

        return summary

    except Exception as e:
        import traceback
        logger.error(f'Collection failed: {e}\n{traceback.format_exc()}')
        summary['status'] = 'error'
        summary['errors'].append(str(e)[:200])
        summary['duration_s'] = int(time.time() - start)
        _is_running = False
        _last_run = datetime.utcnow()  # Prevent dashboard showing — forever
        with _progress_lock:
            _progress['active'] = False
            _progress['phase'] = 'done'
            _progress['errors'].append(str(e)[:200])
            _progress['duration_s'] = summary['duration_s']
            _progress['finished_at'] = datetime.utcnow().isoformat()
        return summary


def _collect_quick(existing_sources: dict, emit) -> list:
    """Quick collection: revisit known snapshot URLs, only GET changed pages.

    Uses collector._collect_quick which does HEAD/page-hash checks on known detail pages.
    Falls back gracefully if no known URLs exist (new installation).
    Keeps the package-level dedup from delta mode as safety net.
    """
    from src.models.snapshot import get_active_snapshots

    # Build known-package index for dedup safety net
    known_packages = set()
    for src_name in existing_sources:
        snaps = get_active_snapshots(existing_sources[src_name]['id'])
        for s in snaps:
            known_packages.add((
                s.get('product_name', ''),
                s.get('file_name', ''),
                s.get('md5_hash', ''),
            ))

    all_items = []
    products = list(_collector_products().items())
    done = 0

    for name, url in products:
        done += 1
        emit('collecting', product=name)
        if name not in existing_sources:
            continue
        src = existing_sources[name]
        try:
            # Quick mode: HEAD-check known URLs, GET only changed pages
            items = _collector._collect_quick(src['id'], name)

            # Dedup safety net
            new_items = []
            for item in items:
                key = (item.product_name, item.file_name, item.md5_hash)
                if key not in known_packages:
                    new_items.append(item)

            skipped = len(items) - len(new_items)
            if new_items:
                logger.info(f'Quick: {name} has {len(new_items)} new packages (skipped {skipped} known)')
            elif items:
                logger.debug(f'Quick: {name} {len(items)} known, no changes')
            # No log when items is empty (no known URLs or all unchanged)

            all_items.extend(new_items)
            emit('collecting', product=name, items=len(new_items))
            update_source_health(src['id'], 'ok', datetime.utcnow().isoformat())
            # Bump last_seen_at for unchanged packages too (reflects collection ran)
            from src.models.snapshot import touch_active_snapshots
            touch_active_snapshots(src['id'])

        except SessionExpiredError:
            logger.error(f'Quick {name}: Session expired')
            emit('collecting', product=name, version='SESSION EXPIRED')
            _progress['errors'].append(f'{name}: Session expired')
        except Exception as e:
            logger.error(f'Quick {name}: {e}')
            _progress['errors'].append(f'{name}: {str(e)[:100]}')

        _progress['products_done'] = done

    return all_items


def _collect_full(existing_sources: dict, sessions: list, cookie: str, emit) -> list:
    """Full collection: traverse all detail pages for all products."""
    all_items = []
    products = list(_collector_products().items())
    done = 0

    for name, url in products:
        done += 1
        emit('collecting', product=name)
        if name not in existing_sources:
            continue
        src = existing_sources[name]
        strategy = src.get('strategy', 'standard')
        try:
            if strategy == 'recursive':
                items = _collector._collect_recursive(src['id'], name, url, max_depth=4)
            else:
                items = _collector._collect_standard(src['id'], name, url)

            all_items.extend(items)
            emit('collecting', product=name, items=len(items))
            update_source_health(src['id'], 'ok', datetime.utcnow().isoformat())
            # Bump last_seen_at for all active snapshots (reflects collection ran)
            from src.models.snapshot import touch_active_snapshots
            touch_active_snapshots(src['id'])

        except SessionExpiredError:
            if sessions:
                update_status(sessions[0]['id'], 'expired')
            if len(sessions) > 1:
                cookie = sessions[1]['cookie_value']
                _collector._set_cookie(cookie)
                logger.warning(f'Switched to backup session')
            _progress['errors'].append(f'{name}: Session expired')
            emit('collecting', product=name, version='SESSION EXPIRED')
        except Exception as e:
            logger.error(f'{name}: {e}')
            _progress['errors'].append(f'{name}: {str(e)[:100]}')
            update_source_health(src['id'], 'error')

        _progress['products_done'] = done

    return all_items


def is_full_scan_due() -> bool:
    """Check if full scan is due based on FULL_SCAN_INTERVAL.
    
    Persists last full scan time to DB so it survives restarts.
    """
    global _last_full_run
    if _last_full_run is None:
        # Restore from DB on startup
        _last_full_run = _load_last_full_scan()
    if _last_full_run is None:
        return True
    elapsed = (datetime.utcnow() - _last_full_run).total_seconds() / 3600
    interval = int(_get_setting('full_scan_interval', '24'))
    return elapsed >= interval


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
        else:
            log_fn('⚠️ 未发现包类型（产品可能无独立升级页）')
            with _pkg_refresh_lock:
                _pkg_refresh_state['phase'] = 'done'

    except Exception as e:
        log_fn(f'失败: {e}')
        with _pkg_refresh_lock:
            _pkg_refresh_state['phase'] = 'error'
    finally:
        with _pkg_refresh_lock:
            _pkg_refresh_state['active'] = False
            _pkg_refresh_state['finished_at'] = datetime.utcnow().isoformat()


def start_scheduler(app=None):
    """Start the APScheduler background scheduler."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        sched = BackgroundScheduler()

        # Smart collection: checks if full scan is due, otherwise runs quick
        sched.add_job(
            _smart_collect,
            'interval',
            hours=COLLECT_INTERVAL,
            id='nsfocus_collect',
            name='NSFOCUS Smart Collection',
            next_run_time=datetime.utcnow(),
        )

        sched.start()
        _scheduler = sched
        logger.info(f'Scheduler started: collection every {COLLECT_INTERVAL}h, '
                    f'full scan every {FULL_SCAN_INTERVAL}h')

        # First-run check: if no snapshots have source_url populated, trigger immediate full scan
        _check_first_run()

        # Session heartbeat: keep PHPSESSID alive (configurable interval)
        hb_interval = int(_get_setting('heartbeat_interval', '30'))
        sched.add_job(
            _session_heartbeat,
            'interval',
            minutes=hb_interval,
            id='nsfocus_heartbeat',
            name='Session Heartbeat',
            next_run_time=datetime.utcnow(),
        )

        # Digest check: run daily to send weekly/monthly/quarterly summaries
        sched.add_job(
            _digest_check,
            'interval',
            hours=6,  # Check every 6 hours
            id='nsfocus_digest',
            name='Digest Summary Check',
            next_run_time=datetime.utcnow(),
        )

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


def _smart_collect():
    """Smart collection: full scan if due, quick otherwise. Eliminates race condition."""
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
    """Send a lightweight request to keep PHPSESSID alive.

    PHP default session.gc_maxlifetime is 24 min, so 30 min heartbeat
    should prevent server-side session expiry between collection cycles.
    Skips heartbeat if collection is in progress to avoid cookie conflicts.
    """
    import time as _time
    from src.models.user_session import update_heartbeat, log_heartbeat

    if _is_running:
        logger.debug('Heartbeat skipped: collection in progress')
        return

    sessions = get_active_sessions()
    if not sessions:
        return

    interval = int(_get_setting('heartbeat_interval', '30'))  # minutes
    cookie = sessions[0]['cookie_value']
    session_id = sessions[0]['id']
    _collector._set_cookie(cookie)

    try:
        start = _time.time()
        _collector._fetch('/update/wafIndex')
        latency = int((_time.time() - start) * 1000)

        update_heartbeat(session_id, 'ok')
        log_heartbeat(session_id, 'ok', latency_ms=latency)
        logger.debug(f'Heartbeat OK ({latency}ms)')

    except SessionExpiredError:
        logger.warning(f'Heartbeat: session {session_id} expired')
        update_status(session_id, 'expired')
        update_heartbeat(session_id, 'expired')
        log_heartbeat(session_id, 'expired', error_msg='Session expired (redirect to login)')

        logger.warning(
            f'⚠️ SESSION EXPIRED: session {session_id} is no longer valid. '
            f'Please add a new PHPSESSID in the web UI.')

    except Exception as e:
        logger.warning(f'Heartbeat failed: {e}')
        update_heartbeat(session_id, 'error')
        log_heartbeat(session_id, 'error', error_msg=str(e)[:200])


def _digest_check():
    """Check for due digest summaries and send them."""
    from src.notifiers.router import process_digests
    try:
        process_digests()
    except Exception as e:
        logger.error(f'Digest check failed: {e}')
