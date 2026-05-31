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
# With DELETE mode + isolation_level=None, write contention is minimal;
# the lock provides an additional safety margin for multi-threaded access.
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
        import sys as _sys
        if getattr(_sys, 'frozen', False):
            import os as _os
            # Match app.py _data_dir() logic exactly:
            # 1. Respect MONITOR_DATA_DIR if set
            # 2. Probe exe directory for writability
            # 3. Fallback to LOCALAPPDATA (Windows) or ~/.local (Linux)
            env_dir = _os.environ.get('MONITOR_DATA_DIR')
            if env_dir:
                data_dir = env_dir
            else:
                _exe_dir = _os.path.dirname(_sys.executable)
                _probe = _os.path.join(_exe_dir, 'data')
                try:
                    _os.makedirs(_probe, exist_ok=True)
                    with open(_os.path.join(_probe, '.probe'), 'w') as _f:
                        _f.write('')
                    _os.remove(_os.path.join(_probe, '.probe'))
                    data_dir = _probe
                except Exception:
                    if _sys.platform == 'win32':
                        data_dir = _os.environ.get('LOCALAPPDATA', _os.path.expanduser('~/AppData/Local')) + '\\nsfocus-monitor-data'
                    else:
                        data_dir = _os.path.join(_os.path.expanduser('~/.local'), 'share', 'nsfocus-monitor-data')
        else:
            data_dir = os.getenv('MONITOR_DATA_DIR',
                                 os.path.join(os.path.dirname(__file__), '..', '..', 'data'))
    os.makedirs(data_dir, exist_ok=True)
    DB_PATH = os.path.join(data_dir, 'nsfocus_monitor.db')
    logger.info(f'Database path: {DB_PATH}')
    return DB_PATH


def get_db() -> sqlite3.Connection:
    """Get thread-local database connection."""
    if not hasattr(_local, 'conn') or _local.conn is None:
        if not DB_PATH:
            init_db()
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=DELETE")  # WAL causes write-lock contention; DELETE mode is safe for single-writer
        _local.conn.execute("PRAGMA busy_timeout=10000")  # 10s, sufficient "database is locked"
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def query(sql: str, params: tuple = ()) -> list:
    """Execute a SELECT query, return list of dicts."""
    db = get_db()
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def execute(sql: str, params: tuple = ()) -> int | None:
    """Execute an INSERT/UPDATE/DELETE, return lastrowid.

    Thread-safe: acquires _write_lock so all writes across all threads
    are serialized.  With DELETE mode + isolation_level=None, lock
    contention is minimal; the lock provides safety margin for
    multi-threaded access.

    Retries on "database is locked" (up to 30 attempts, 2s/4s/6s... sleep between).
    10s busy_timeout gives SQLite time to acquire the lock without hogging resources.
    Combined with isolation_level=None, lock contention is extremely rare.
    """
    import time as _time
    import logging as _log
    import traceback as _tb
    _dbg = _log.getLogger('monitor.database')

    for attempt in range(30):
        try:
            with _write_lock:
                # Use a fresh connection instead of get_db() to avoid thread-local issues
                import sqlite3 as _sqlite3
                db = _sqlite3.connect(DB_PATH, timeout=60, isolation_level=None)
                db.execute('PRAGMA busy_timeout=10000')
                db.row_factory = _sqlite3.Row
                _t0 = _time.time()
                cur = db.execute(sql, params)
                dur = _time.time() - _t0
                # No explicit commit() needed with isolation_level=None (autocommit mode)
                if dur > 1.0:
                    _dbg.warning(f'execute slow ({dur:.3f}s attempt {attempt+1}): {sql[:80]}')
                return cur.lastrowid
        except sqlite3.OperationalError as e:
            if attempt < 29 and 'database is locked' in str(e):
                _time.sleep(2 * (attempt + 1))  # 2s, 4s, 6s ... up to 60s total
                continue
            raise


def executemany(sql: str, params_list: list) -> None:
    """Execute multiple INSERT statements. Uses autocommit mode (isolation_level=None)."""
    with _write_lock:
        db = get_db()
        db.executemany(sql, params_list)
