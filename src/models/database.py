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


def sync_table_columns(db, table: str, expected: list[tuple[str, str, str]]) -> None:
    """Auto-migrate an existing table: add any columns the code expects but the DB lacks.

    Used at startup to handle the "升级后老 DB 缺列 / 初次部署 CREATE TABLE IF NOT EXISTS
    错过了新增列" 场景。 配合 `CREATE TABLE IF NOT EXISTS` 使用:
      - 全新部署: CREATE TABLE 建表 → sync 找到 0 缺失 → no-op
      - 老 DB 升级: CREATE TABLE IF NOT EXISTS 不动老 schema → sync 补上缺的列

    Args:
        db: open sqlite3 connection (use get_db() or the per-thread connection).
        table: table name to inspect.
        expected: list of (column_name, sqlite_type, default_sql) tuples
                  representing what the running code expects to find. Order does not matter.
                  default_sql is the full `DEFAULT ...` clause WITHOUT leading space,
                  e.g. "''", "'{}'", "'full'", "'INFO'", "0", "" (empty → no DEFAULT).
                  Use "" for nullable columns with no default.

    Notes:
        - Only ADD COLUMN is supported. Drops/renames/type-changes must be done manually.
        - DEFAULT values are baked into existing rows at ALTER time, so existing data
          stays intact (SQLite 3.x supports this for all simple types we use).
        - Safe to call repeatedly: existing columns are skipped.
        - The `_expected_cols_log` module-level dict accumulates a one-shot log per
          (table, column) so we don't spam the same "added X to Y" line on every restart.
    """
    global _expected_cols_log
    try:
        existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.OperationalError as e:
        # Table doesn't exist yet — CREATE TABLE IF NOT EXISTS in create_tables()
        # should have run first. If it really doesn't, log and skip.
        logger.warning(f'sync_table_columns: {table} missing (PRAGMA failed: {e}); skipping')
        return

    added_any = False
    for col, sqltype, default_sql in expected:
        if col in existing:
            continue
        default_clause = f' DEFAULT {default_sql}' if default_sql else ''
        sql = f'ALTER TABLE {table} ADD COLUMN {col} {sqltype}{default_clause}'
        try:
            db.execute(sql)
            added_any = True
            key = f'{table}.{col}'
            if key not in _expected_cols_log:
                logger.info(f'schema sync: added {col} {sqltype}{default_clause} to {table}')
                _expected_cols_log.add(key)
        except sqlite3.OperationalError as e:
            # Race / pre-existing via different DDL — log and move on
            logger.warning(f'schema sync: failed to add {col} to {table}: {e}')

    if added_any:
        logger.info(f'schema sync: {table} is now in sync (added {len([c for c,_,_ in expected if c not in existing])} column(s))')


# Tracks columns we've already announced to the logger (in-process only).
# Reset on every process restart, so each fresh start logs once per addition.
_expected_cols_log: set[str] = set()
