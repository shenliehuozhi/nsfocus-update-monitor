"""SQLite connections, bounded write contention and schema management."""

import os
import sqlite3
import threading
import time
from collections import Counter, deque
from contextlib import closing
from math import ceil

from src.core.logger import get_logger

logger = get_logger('database')

_local = threading.local()
DB_PATH: str = ''
_write_lock = threading.RLock()
_WRITE_LOCK_TIMEOUT = 10.0
_stats_lock = threading.Lock()
_write_samples = deque(maxlen=1024)
_write_totals = Counter()


def write_stats() -> dict:
    """Process counters and nearest-rank percentiles over the last 1024 writes."""
    with _stats_lock:
        samples = list(_write_samples)
        totals = dict(_write_totals)
    result = {
        'operations': totals.get('operations', 0),
        'errors': totals.get('errors', 0),
        'timeouts': totals.get('timeouts', 0),
        'python_lock_timeouts': totals.get('python_lock_timeouts', 0),
        'sqlite_busy_errors': totals.get('sqlite_busy_errors', 0),
        'sample_count': len(samples),
        'sample_limit': _write_samples.maxlen,
        'wait_budget_seconds': _WRITE_LOCK_TIMEOUT,
        'timeout_rate': totals.get('timeouts', 0) / totals['operations']
        if totals.get('operations') else 0.0,
    }
    for name in ('lock_wait_ms', 'sqlite_ms', 'total_ms'):
        values = sorted(sample[name] for sample in samples)
        result[name] = {
            f'p{p}': round(values[ceil(len(values) * p / 100) - 1], 3)
            if values else None
            for p in (50, 95, 99)
        }
    return result


def _record_write(lock_wait: float, sqlite_time: float, total: float, outcome: str):
    sample = {'lock_wait_ms': lock_wait * 1000,
              'sqlite_ms': sqlite_time * 1000, 'total_ms': total * 1000}
    with _stats_lock:
        _write_samples.append(sample)
        _write_totals['operations'] += 1
        if outcome != 'ok':
            _write_totals['errors'] += 1
        if outcome in ('python_lock_timeout', 'sqlite_busy'):
            _write_totals['timeouts'] += 1
            _write_totals['python_lock_timeouts' if outcome == 'python_lock_timeout'
                          else 'sqlite_busy_errors'] += 1
    if outcome != 'ok' or total >= 1:
        logger.warning(
            'db_write outcome=%s lock_wait_ms=%.1f sqlite_ms=%.1f total_ms=%.1f',
            outcome, sample['lock_wait_ms'], sample['sqlite_ms'], sample['total_ms'])


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
    """Get a connection owned by the current thread without changing journal mode."""
    if getattr(_local, 'conn', None) is None:
        if not DB_PATH:
            init_db()
        conn = sqlite3.connect(DB_PATH, timeout=_WRITE_LOCK_TIMEOUT, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON').close()
        except Exception:
            conn.close()
            raise
        _local.conn = conn
    return _local.conn


def close_db(error=None) -> None:
    conn = getattr(_local, 'conn', None)
    if conn is not None:
        _local.conn = None
        conn.close()


def query(sql: str, params: tuple = ()) -> list:
    """Execute a SELECT query, return list of dicts."""
    with closing(get_db().execute(sql, params)) as cursor:
        return [dict(row) for row in cursor.fetchall()]


def _run_write(sql, params, *, many=False, foreign_keys=False):
    started = time.monotonic()
    acquired = False
    lock_wait = sqlite_time = 0.0
    outcome = 'error'
    try:
        if not DB_PATH:
            init_db()
        acquired = _write_lock.acquire(timeout=max(0, _WRITE_LOCK_TIMEOUT -
                                                   (time.monotonic() - started)))
        lock_wait = time.monotonic() - started
        if not acquired:
            outcome = 'python_lock_timeout'
            raise sqlite3.OperationalError('database is locked: local write queue timed out')
        with closing(sqlite3.connect(DB_PATH, timeout=0, isolation_level=None)) as conn:
            if foreign_keys:
                conn.execute('PRAGMA foreign_keys=ON').close()
            with closing(conn.cursor()) as cursor:
                sqlite_started = time.monotonic()
                try:
                    # SQLite busy waits share the queue budget; SQL execution itself is not interrupted.
                    for values in params if many else (params,):
                        remaining = max(0, _WRITE_LOCK_TIMEOUT - (time.monotonic() - started))
                        conn.execute(f'PRAGMA busy_timeout={int(remaining * 1000)}').close()
                        cursor.execute(sql, values)
                    result = (cursor.lastrowid, cursor.rowcount)
                finally:
                    sqlite_time = time.monotonic() - sqlite_started
        outcome = 'ok'
        return result
    except sqlite3.OperationalError as exc:
        code = getattr(exc, 'sqlite_errorcode', 0) & 0xff
        if outcome != 'python_lock_timeout' and (
            code in (5, 6)
            or 'locked' in str(exc).lower() or 'busy' in str(exc).lower()
        ):
            outcome = 'sqlite_busy'
        raise
    finally:
        if acquired:
            _write_lock.release()
        _record_write(lock_wait, sqlite_time, time.monotonic() - started, outcome)


def execute(sql: str, params: tuple = ()) -> int | None:
    """Execute one autocommit write and return lastrowid."""
    # Preserve the existing write FK policy; enabling constraints requires a separate data audit.
    return _run_write(sql, params)[0]


def execute_rowcount(sql: str, params: tuple = ()) -> int:
    """Execute a constraint-checked autocommit write and return affected rows."""
    return _run_write(sql, params, foreign_keys=True)[1]


def executemany(sql: str, params_list: list) -> None:
    """Autocommit each row without replaying a partially successful batch."""
    _run_write(sql, params_list, many=True, foreign_keys=True)


def probe_write_lock(timeout: float) -> dict:
    """Try to start a write transaction under the shared write queue.

    Queue wait and SQLite lock wait use independent ``timeout`` budgets, so a
    busy local queue is reported as not attempted instead of as a SQLite lock
    failure. Probe traffic is excluded from ``write_stats()``.
    """
    result = {'attempted': False, 'ok': False, 'error': ''}
    if not DB_PATH:
        init_db()
    acquired = _write_lock.acquire(timeout=max(0.0, timeout))
    if not acquired:
        return result
    try:
        result['attempted'] = True
        with closing(sqlite3.connect(DB_PATH, timeout=0, isolation_level=None)) as conn:
            conn.execute(f'PRAGMA busy_timeout={int(max(0.0, timeout) * 1000)}').close()
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('ROLLBACK')
        result['ok'] = True
    except Exception as exc:
        result['error'] = str(exc)[:200] or exc.__class__.__name__
    finally:
        _write_lock.release()
    return result


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
