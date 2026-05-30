"""User Session model — stores encrypted NSFOCUS PHPSESSID + heartbeat tracking."""

import datetime
import os
import pathlib
import threading

SCHEMA_USER_SESSION = """
CREATE TABLE IF NOT EXISTS user_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    cookie_value TEXT NOT NULL,
    last_valid TEXT,
    expires_at TEXT,
    status TEXT DEFAULT 'unknown',
    last_heartbeat_at TEXT,
    heartbeat_status TEXT DEFAULT '',
    heartbeat_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
)
"""

SCHEMA_HEARTBEAT_LOG = """
CREATE TABLE IF NOT EXISTS heartbeat_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES user_sessions(id),
    status TEXT NOT NULL,       -- 'ok', 'expired', 'error'
    latency_ms INTEGER DEFAULT 0,
    error_message TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
)
"""

INDEX_HB_SESSION = "CREATE INDEX IF NOT EXISTS idx_hb_session ON heartbeat_log(session_id, created_at)"


def create_tables(db):
    db.execute(SCHEMA_USER_SESSION)
    db.execute(SCHEMA_HEARTBEAT_LOG)
    db.execute(INDEX_HB_SESSION)
    # Migration: add columns if they don't exist (safe on existing DB)
    try:
        db.execute("ALTER TABLE user_sessions ADD COLUMN last_heartbeat_at TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE user_sessions ADD COLUMN heartbeat_status TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE user_sessions ADD COLUMN heartbeat_count INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE user_sessions ADD COLUMN purpose TEXT DEFAULT 'collect'")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE user_sessions ADD COLUMN collect_mode TEXT DEFAULT 'standard'")
    except Exception:
        pass


def create(user_id: int, cookie_value: str, purpose: str = 'collect',
           collect_mode: str = 'standard') -> int:
    from src.models.database import execute
    from src.core.crypto import encrypt
    encrypted = encrypt(cookie_value)
    return execute(
        "INSERT INTO user_sessions (user_id, cookie_value, status, purpose, collect_mode) "
        "VALUES (?, ?, 'unknown', ?, ?)",
        (user_id, encrypted, purpose, collect_mode)
    )


def get_by_user(user_id: int) -> list:
    from src.models.database import query
    return query(
        "SELECT id, user_id, cookie_value, status, purpose, collect_mode, "
        "last_valid, expires_at, "
        "last_heartbeat_at, heartbeat_status, heartbeat_count, created_at "
        "FROM user_sessions WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,))


def get_active_sessions() -> list:
    """Get all active sessions for the pool, with decrypted cookie values."""
    from src.models.database import query
    from src.core.crypto import decrypt
    rows = query("SELECT * FROM user_sessions WHERE status = 'active' ORDER BY last_valid DESC")
    for r in rows:
        r['cookie_value'] = decrypt(r['cookie_value'])
    return rows


def get_active_sessions_by_purpose(purpose: str) -> list:
    """Get active sessions filtered by purpose (discover/collect), with decrypted cookies."""
    from src.models.database import query
    from src.core.crypto import decrypt
    rows = query(
        "SELECT * FROM user_sessions WHERE status = 'active' AND purpose = ? ORDER BY last_valid DESC",
        (purpose,)
    )
    for r in rows:
        r['cookie_value'] = decrypt(r['cookie_value'])
    return rows


def get_active_collect_sessions() -> dict:
    """Get active collect sessions grouped by collect_mode (standard/vm), first cookie each."""
    from src.models.database import query
    from src.core.crypto import decrypt
    rows = query(
        "SELECT * FROM user_sessions WHERE status = 'active' AND purpose = 'collect' "
        "ORDER BY collect_mode, last_valid DESC"
    )
    result = {}  # collect_mode -> session row
    for r in rows:
        if r['collect_mode'] not in result:
            r['cookie_value'] = decrypt(r['cookie_value'])
            result[r['collect_mode']] = r
    return result


def update_purpose_mode(session_id: int, purpose: str, collect_mode: str):
    """Update session purpose and collect_mode."""
    from src.models.database import execute
    execute(
        "UPDATE user_sessions SET purpose = ?, collect_mode = ? WHERE id = ?",
        (purpose, collect_mode, session_id)
    )


def update_status(session_id: int, status: str, last_valid: str = None, expires_at: str = None):
    from src.models.database import execute
    if last_valid and expires_at:
        execute(
            "UPDATE user_sessions SET status = ?, last_valid = ?, expires_at = ? WHERE id = ?",
            (status, last_valid, expires_at, session_id)
        )
    elif last_valid:
        execute(
            "UPDATE user_sessions SET status = ?, last_valid = ? WHERE id = ?",
            (status, last_valid, session_id)
        )
    else:
        execute("UPDATE user_sessions SET status = ? WHERE id = ?", (status, session_id))


def update_heartbeat(session_id: int, hb_status: str):
    """Update heartbeat fields after a heartbeat run."""
    from src.models.database import execute
    execute(
        "UPDATE user_sessions SET last_heartbeat_at = datetime('now'), "
        "heartbeat_status = ?, heartbeat_count = heartbeat_count + 1 WHERE id = ?",
        (hb_status, session_id)
    )


# Rolling log for heartbeat events (max 10 lines, no DB writes)
_hb_lock = threading.RLock()
_HB_LOG_PATH = None

def _hb_log_path():
    global _HB_LOG_PATH
    if _HB_LOG_PATH is None:
        _HB_LOG_PATH = pathlib.Path(os.getenv('MONITOR_LOG_DIR', '/tmp')) / 'heartbeat.log'
    return _HB_LOG_PATH


def log_heartbeat(session_id: int, hb_status: str, latency_ms: int = 0, error_msg: str = ''):
    """Record a heartbeat event to a rolling log file (max 10 lines).

    Does NOT write to DB — caller (scheduler pre-flight or _session_heartbeat)
    is responsible for updating user_sessions heartbeat fields separately.
    """
    with _hb_lock:
        p = _hb_log_path()
        lines = []
        if p.exists():
            lines = p.read_text(encoding='utf-8').splitlines()
        ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        new_line = f'{ts} | sid={session_id} | {hb_status} | {latency_ms}ms | {error_msg}'
        lines.append(new_line)
        lines = lines[-10:]
        p.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def get_heartbeat_history(session_id: int, limit: int = 20) -> list:
    """Get recent heartbeat history for a session."""
    from src.models.database import query
    return query(
        "SELECT * FROM heartbeat_log WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
        (session_id, limit)
    )


def delete(session_id: int) -> None:
    from src.models.database import execute
    # Delete child table first (heartbeat_log -> user_sessions FK)
    execute("DELETE FROM heartbeat_log WHERE session_id = ?", (session_id,))
    execute("DELETE FROM user_sessions WHERE id = ?", (session_id,))


def count_by_status(status: str) -> int:
    from src.models.database import query
    rows = query("SELECT COUNT(*) as cnt FROM user_sessions WHERE status = ?", (status,))
    return rows[0]['cnt'] if rows else 0


def get_expired_active_count() -> int:
    """Count sessions marked as active but last heartbeat was expired/failed."""
    from src.models.database import query
    rows = query(
        "SELECT COUNT(*) as cnt FROM user_sessions "
        "WHERE status = 'active' AND heartbeat_status IN ('过期', '污染', '错误')"
    )
    return rows[0]['cnt'] if rows else 0
