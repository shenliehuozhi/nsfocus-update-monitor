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

    for attempt in range(30):
        with _write_lock:
            try:
                db = get_db()
                cur = db.execute(sql, params)
                db.commit()
                return cur.lastrowid
            except sqlite3.OperationalError as e:
                if attempt < 29 and 'database is locked' in str(e):
                    _time.sleep(2 * (attempt + 1))  # 2s, 4s, 6s ... up to 60s total
                    continue
                raise
    return None  # unreachable, satisfies static analyzer


def executemany(sql: str, params_list: list) -> None:
    """Execute multiple INSERT statements."""
    with _write_lock:
        db = get_db()
        db.executemany(sql, params_list)
        db.commit()
