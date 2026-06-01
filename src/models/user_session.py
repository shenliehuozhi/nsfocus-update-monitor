"""User Session model — stores encrypted NSFOCUS PHPSESSID + heartbeat tracking."""

from datetime import datetime as _dt
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
        _HB_LOG_PATH = pathlib.Path(os.getenv('MONITOR_LOG_DIR', os.path.join(os.path.expanduser('~/.local/share/nsfocus-monitor-data'), 'logs'))) / 'heartbeat.log'
    return _HB_LOG_PATH


def log_heartbeat(session_id: int, hb_status: str, latency_ms: int = 0, error_msg: str = '', purpose: str = '', collect_mode: str = ''):
    """Record a heartbeat event to a rolling log file (max 10 lines).

    Does NOT write to DB — caller (scheduler pre-flight or _session_heartbeat)
    is responsible for updating user_sessions heartbeat fields separately.

    Format: ts | sid=N | purpose | collect_mode | status | latency_ms | msg
    """
    with _hb_lock:
        p = _hb_log_path()
        lines = []
        if p.exists():
            lines = p.read_text(encoding='utf-8').splitlines()
        ts = _dt.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        new_line = f'{ts} | sid={session_id} | {purpose} | {collect_mode} | {hb_status} | {latency_ms}ms | {error_msg}'
        lines.append(new_line)
        lines = lines[-10:]
        p.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def get_heartbeat_history(session_id: int, limit: int = 20) -> list:
    """Get recent heartbeat history for a session from heartbeat.log file."""
    p = _hb_log_path()
    if not p.exists():
        return []
    all_lines = p.read_text(encoding='utf-8').splitlines()
    # Filter to this session, reverse chronological ( newest last )
    matching = [ln for ln in all_lines if f'sid={session_id}|' in ln or f'sid={session_id} |' in ln]
    matching = matching[-limit:]
    # Parse each line back into a dict mimicking the old DB row shape
    import re
    from datetime import datetime
    rows = []
    for line in reversed(matching):
        m = re.match(r'([\d\-: ]+) UTC\s+\| sid=(\d+)\s+\| (\w*)\s+\| (\w*)\s+\| (\w+)\s+\| (\d+ms)\s+\| (.*)', line)
        if m:
            ts_str, sid, purpose, collect_mode, status, latency, msg = m.groups()
            # Convert "2026-05-30 17:33:37 UTC" -> "2026-05-30T17:33:37Z" for JS fmtTZ
            iso_ts = ts_str.strip().replace(' UTC', '') + 'Z'
            rows.append({
                'created_at': iso_ts,
                'session_id': int(sid),
                'status': status,
                'latency_ms': int(latency.rstrip('ms')),
                'error_msg': msg,
                'purpose': purpose,
                'collect_mode': collect_mode,
            })
        else:
            # Fallback for old 5-field format
            parts = line.split('|')
            if len(parts) >= 5:
                ts_str = parts[0].strip().replace(' UTC', '') + 'Z'
                rows.append({
                    'created_at': ts_str,
                    'session_id': session_id,
                    'status': parts[3].strip() if len(parts) > 3 else '',
                    'latency_ms': 0,
                    'error_msg': parts[4].strip() if len(parts) > 4 else '',
                    'purpose': '',
                    'collect_mode': '',
                })
    return rows


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
