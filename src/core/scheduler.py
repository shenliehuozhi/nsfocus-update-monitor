"""Scheduler — periodic collection + detection + notification pipeline.

Supports two modes:
  'delta' (fast) — only check list pages, ~20s, for frequent runs
  'full'  (slow) — traverse all detail pages, ~15-20min, for weekly deep scan
"""

import os
import time
import threading
from datetime import datetime
from typing import Optional

from src.core.logger import get_logger
from src.collectors.nsfocus import NsfocusCollector, SessionExpiredError, PRODUCTS
from src.detector.change import run_detection, get_new_for_subscription
from src.notifiers.router import route_notifications, process_delayed_queue
from src.models.user_session import get_active_sessions, update_status
from src.models.snapshot import (
    get_source_by_name, update_source_health,
    list_sources as list_content_sources,
    create_source as create_content_source,
    get_active_snapshots,
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
_is_running = False

# ── Progress state (for async collection) ────────────────────────

_progress = {
    'active': False,
    'mode': '',
    'phase': '',        # 'init' | 'collecting' | 'detecting' | 'notifying' | 'done'
    'current_product': '',
    'current_version': '',
    'products_done': 0,
    'products_total': len(PRODUCTS),
    'items_collected': 0,
    'total_new': 0,
    'total_rollback': 0,
    'errors': [],
    'started_at': None,
    'finished_at': None,
    'duration_s': 0,
}
_progress_lock = threading.Lock()


def get_progress() -> dict:
    """Return current collection progress (thread-safe)."""
    with _progress_lock:
        return dict(_progress)


def get_status() -> dict:
    return {
        'last_run': _last_run.isoformat() if _last_run else None,
        'last_full_run': _last_full_run.isoformat() if _last_full_run else None,
        'is_running': _is_running,
        'interval_hours': COLLECT_INTERVAL,
        'full_scan_interval_hours': FULL_SCAN_INTERVAL,
    }


def run_now(mode: str = 'delta', progress_callback=None) -> dict:
    """Execute a collection+detection+notification cycle.

    Args:
        mode: 'delta' (fast, list pages only) or 'full' (deep traversal)
        progress_callback: optional callable(phase, detail) for progress updates
    """
    global _last_run, _last_full_run, _is_running, _progress

    if _is_running:
        return {'status': 'skipped', 'reason': 'Already running'}

    _is_running = True
    logger.info(f'Collection starting: mode={mode}')
    start = time.time()

    # Initialize progress
    with _progress_lock:
        _progress.update({
            'active': True, 'mode': mode, 'phase': 'init',
            'current_product': '', 'current_version': '',
            'products_done': 0, 'products_total': len(PRODUCTS),
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
                    'products_total': len(PRODUCTS),
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

        # 2. Ensure content sources exist in DB
        existing_sources = {s['name']: s for s in list_content_sources('nsfocus')}
        for name in PRODUCTS:
            if name not in existing_sources:
                create_content_source(name, 'nsfocus', category='security')
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
            _progress['products_done'] = len(PRODUCTS)

        if not all_items:
            summary['status'] = 'warning' if summary['errors'] else 'ok'
            summary['duration_s'] = int(time.time() - start)
            _is_running = False
            _last_run = datetime.utcnow()
            if mode == 'full':
                _last_full_run = datetime.utcnow()
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

            # 6. Handle rollbacks
            for sid, snap in result.rollback_items:
                rules = get_enabled_rules()
                for rule in rules:
                    route_notifications(sid, rule['id'], is_rollback=True)

        # 7. Process delayed queue
        process_delayed_queue()

        summary['duration_s'] = int(time.time() - start)
        _is_running = False
        _last_run = datetime.utcnow()
        if mode == 'full':
            _last_full_run = datetime.utcnow()

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
        logger.error(f'Collection failed: {e}')
        summary['status'] = 'error'
        summary['errors'].append(str(e)[:200])
        summary['duration_s'] = int(time.time() - start)
        _is_running = False
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
    products = list(PRODUCTS.items())
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
    products = list(PRODUCTS.items())
    done = 0

    for name, url in products:
        done += 1
        emit('collecting', product=name)
        if name not in existing_sources:
            continue
        src = existing_sources[name]
        try:
            if name in ('RSAS', 'NF'):
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
    """Check if full scan is due based on FULL_SCAN_INTERVAL."""
    global _last_full_run
    if _last_full_run is None:
        return True
    elapsed = (datetime.utcnow() - _last_full_run).total_seconds() / 3600
    return elapsed >= FULL_SCAN_INTERVAL


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
