"""User Session model — stores encrypted NSFOCUS PHPSESSID + heartbeat tracking."""

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


def create(user_id: int, cookie_value: str) -> int:
    from src.models.database import execute
    from src.core.crypto import encrypt
    encrypted = encrypt(cookie_value)
    return execute(
        "INSERT INTO user_sessions (user_id, cookie_value, status) VALUES (?, ?, 'unknown')",
        (user_id, encrypted)
    )


def get_by_user(user_id: int) -> list:
    from src.models.database import query
    return query(
        "SELECT id, user_id, status, last_valid, expires_at, "
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


def log_heartbeat(session_id: int, hb_status: str, latency_ms: int = 0, error_msg: str = ''):
    """Record a heartbeat event in the log."""
    from src.models.database import execute
    execute(
        "INSERT INTO heartbeat_log (session_id, status, latency_ms, error_message) VALUES (?, ?, ?, ?)",
        (session_id, hb_status, latency_ms, error_msg)
    )


def get_heartbeat_history(session_id: int, limit: int = 20) -> list:
    """Get recent heartbeat history for a session."""
    from src.models.database import query
    return query(
        "SELECT * FROM heartbeat_log WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
        (session_id, limit)
    )


def delete(session_id: int) -> None:
    from src.models.database import execute
    execute("DELETE FROM user_sessions WHERE id = ?", (session_id,))
    execute("DELETE FROM heartbeat_log WHERE session_id = ?", (session_id,))


def count_by_status(status: str) -> int:
    from src.models.database import query
    rows = query("SELECT COUNT(*) as cnt FROM user_sessions WHERE status = ?", (status,))
    return rows[0]['cnt'] if rows else 0


def get_expired_active_count() -> int:
    """Count sessions marked as active but last heartbeat was expired/failed."""
    from src.models.database import query
    rows = query(
        "SELECT COUNT(*) as cnt FROM user_sessions "
        "WHERE status = 'active' AND heartbeat_status = 'expired'"
    )
    return rows[0]['cnt'] if rows else 0
