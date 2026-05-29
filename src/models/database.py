"""Database connection and schema management.

Single SQLite file at MONITOR_DATA_DIR/nsfocus_monitor.db.
Thread-safe via check_same_thread=False (Flask dev server).
Production should use a proper WSGI server with connection pooling.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager

from src.core.logger import get_logger

logger = get_logger('database')

# Thread-local connections
_local = threading.local()
DB_PATH: str = ''

# Global write lock — serializes all DB writes across all threads.
# Uses RLock (re-entrant) so the same thread can re-acquire it safely,
# preventing deadlocks when collector code paths call each other.
# Combined with WAL mode, this eliminates "database is locked" errors.
_write_lock = threading.RLock()
_WRITE_LOCK_TIMEOUT = 30  # seconds — allow longer for bulk inserts and network-dependent ops


def _monitor_lock():
    """Background thread to diagnose lock holder if lock is held > 5s."""
    import traceback as _tb
    _logger = __import__('logging').getLogger('monitor.database')
    while True:
        __import__('time').sleep(5)
        # Try to acquire non-blocking — if we get it immediately, lock is free
        acquired = _write_lock.acquire(timeout=0.1)
        if acquired:
            _write_lock.release()
            _logger.warning('[LOCK DIAG] _write_lock is FREE (as expected)')
        else:
            # Lock is held — dump stacks of all threads once, then try to re-acquire
            import sys as _sys, threading as _threading
            _logger.error('[LOCK DIAG] _write_lock is HELD by another thread! Stacks:')
            for t in _threading.enumerate():
                tid = getattr(t, 'ident', None)
                if tid:
                    try:
                        frame = _sys._current_frames().get(tid)
                        if frame:
                            _logger.error(f'Thread {t.name} (id={tid}):\n{chr(10).join(_tb.format_stack(frame))}')
                    except Exception:
                        pass


_monitor_started = False


def _start_lock_monitor():
    global _monitor_started
    if _monitor_started:
        return
    _monitor_started = True
    t = threading.Thread(target=_monitor_lock, daemon=True, name='lock-monitor')
    t.start()


def init_db(data_dir: str = None) -> str:
    """Initialize database path. Call once at startup."""
    global DB_PATH
    if data_dir is None:
        data_dir = os.getenv('MONITOR_DATA_DIR', os.path.join(os.path.dirname(__file__), '..', '..', 'data'))
    os.makedirs(data_dir, exist_ok=True)
    DB_PATH = os.path.join(data_dir, 'nsfocus_monitor.db')
    logger.info(f'Database path: {DB_PATH}')
    return DB_PATH


def get_db() -> sqlite3.Connection:
    """Get thread-local database connection."""
    if not hasattr(_local, 'conn') or _local.conn is None:
        if not DB_PATH:
            init_db()
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=60000")  # 60s, avoid "database is locked"
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


@contextmanager
def transaction():
    """Context manager for database transactions."""
    db = get_db()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise


def query(sql: str, params: tuple = ()) -> list:
    """Execute a SELECT query, return list of dicts."""
    db = get_db()
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def execute(sql: str, params: tuple = ()) -> int | None:
    """Execute an INSERT/UPDATE/DELETE, return lastrowid.

    Thread-safe: acquires _write_lock so all writes across all threads
    are serialized.  Combined with WAL mode, this eliminates concurrent
    write lock contention entirely.

    Retries on "database is locked" (up to 30 attempts, up to 60s total) to handle
    WAL write lock contention. Matches PRAGMA busy_timeout=60000 so we wait as long
    as SQLite itself is willing to wait before giving up.
    """
    import time as _time
    import logging as _log
    import traceback as _tb
    _dbg = _log.getLogger('monitor.database')
    _lock_held_since = None

    for attempt in range(30):
        acquired = _write_lock.acquire(timeout=_WRITE_LOCK_TIMEOUT)
        if not acquired:
            if attempt == 0:
                _start_lock_monitor()  # Start monitoring thread on first failure
            import threading, sys as _sys
            # Diagnose: try to find which thread might hold the lock
            # Since RLock doesn't expose holder info, log all thread stacks
            msg = (f'execute: failed to acquire _write_lock after {_WRITE_LOCK_TIMEOUT}s '
                   f'(attempt {attempt+1}/30). Lock held since: {_lock_held_since}. '
                   f'Active thread count: {threading.active_count()}.')
            _dbg.error(msg)
            # Log stacks of all threads to find the holder
            for t in threading.enumerate():
                tid = getattr(t, 'ident', None)
                if tid:
                    try:
                        frame = _sys._current_frames().get(tid)
                        if frame:
                            stack = ''.join(_tb.format_stack(frame))
                            _dbg.error(f'Thread {t.name} (id={tid}) stack: {stack}')
                    except Exception:
                        pass
            raise RuntimeError(f'Database write lock timeout after {_WRITE_LOCK_TIMEOUT}s')
        import time as _time
        _lock_held_since = _time.time()
        try:
            db = get_db()
            _t0 = _time.time()
            cur = db.execute(sql, params)
            db.commit()
            dur = _time.time() - _t0
            if dur > 1.0:
                _dbg.warning(f'execute slow ({dur:.3f}s attempt {attempt+1}): {sql[:80]}')
            return cur.lastrowid
        except sqlite3.OperationalError as e:
            _lock_held_since = None
            if attempt < 29 and 'database is locked' in str(e):
                _time.sleep(2 * (attempt + 1))  # 2s, 4s, 6s ... up to 60s total
                continue
            raise
        finally:
            _lock_held_since = None
            _write_lock.release()


def executemany(sql: str, params_list: list) -> None:
    """Execute multiple INSERT statements."""
    with _write_lock:
        db = get_db()
        db.executemany(sql, params_list)
        db.commit()
