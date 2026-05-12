"""Rate limiter for manual push — prevent abuse.

Tracks pushes per key (email or channel) in a 1-minute sliding window.
Max 5 pushes per window. Exceeding triggers a 10-minute ban.
State is persisted in SQLite so bans survive service restarts.
"""

import time
from src.models.database import get_db, query, execute

SCHEMA = """
CREATE TABLE IF NOT EXISTS push_rate_limits (
    key TEXT PRIMARY KEY,
    count INTEGER DEFAULT 0,
    window_start TEXT,
    banned_until TEXT
)
"""

MAX_PER_WINDOW = 5
WINDOW_SECONDS = 60
BAN_SECONDS = 600  # 10 minutes


def create_tables(db) -> None:
    db.execute(SCHEMA)


def _now_iso() -> str:
    """Return current UTC time as ISO string for DB storage."""
    from datetime import datetime
    return datetime.utcnow().isoformat()


def _parse_iso(s: str | None):
    """Parse ISO string to datetime, return None if empty."""
    if not s:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def check(key: str) -> tuple[bool, str, int]:
    """Check if a push is allowed for the given key.

    Returns:
        (allowed, error_message, retry_after_seconds)
        - allowed=True: push can proceed
        - allowed=False: blocked by rate limit or ban
    """
    from datetime import datetime, timedelta

    now = datetime.utcnow()

    rows = query("SELECT * FROM push_rate_limits WHERE key = ?", (key,))
    if not rows:
        return True, '', 0

    row = rows[0]

    # Check if currently banned
    banned_until = _parse_iso(row.get('banned_until'))
    if banned_until and banned_until > now:
        remaining = int((banned_until - now).total_seconds())
        mins, secs = divmod(remaining, 60)
        return False, f'推送频率超限，功能已禁用，请在 {mins}分{secs}秒 后重试', remaining

    # Check 1-minute window
    window_start = _parse_iso(row.get('window_start'))
    count = row.get('count', 0)

    if window_start and (now - window_start).total_seconds() < WINDOW_SECONDS:
        # Still in current window
        if count >= MAX_PER_WINDOW:
            # Ban it
            banned_until_dt = now + timedelta(seconds=BAN_SECONDS)
            execute(
                "UPDATE push_rate_limits SET banned_until = ? WHERE key = ?",
                (banned_until_dt.isoformat(), key)
            )
            return False, f'推送频率超限（1分钟内超过{MAX_PER_WINDOW}次），功能已禁用10分钟', BAN_SECONDS
        return True, '', 0

    # Window expired — will be reset on record()
    return True, '', 0


def record(key: str) -> None:
    """Record a successful push for the given key."""
    from datetime import datetime

    now = datetime.utcnow()
    rows = query("SELECT * FROM push_rate_limits WHERE key = ?", (key,))

    if not rows:
        execute(
            "INSERT INTO push_rate_limits (key, count, window_start) VALUES (?, 1, ?)",
            (key, now.isoformat())
        )
        return

    row = rows[0]
    window_start = _parse_iso(row.get('window_start'))
    count = row.get('count', 0)

    # Reset window if expired
    if not window_start or (now - window_start).total_seconds() >= WINDOW_SECONDS:
        execute(
            "UPDATE push_rate_limits SET count = 1, window_start = ?, banned_until = NULL WHERE key = ?",
            (now.isoformat(), key)
        )
    else:
        execute(
            "UPDATE push_rate_limits SET count = count + 1 WHERE key = ?",
            (key,)
        )


def clear_ban(key: str | None = None) -> int:
    """Clear ban for a specific key, or all keys if key is None.

    Returns number of rows affected.
    """
    if key:
        return execute(
            "UPDATE push_rate_limits SET banned_until = NULL, count = 0, window_start = NULL WHERE key = ?",
            (key,)
        )
    else:
        return execute(
            "UPDATE push_rate_limits SET banned_until = NULL, count = 0, window_start = NULL"
        )


def get_all_bans() -> list:
    """Return list of currently banned keys with remaining time."""
    from datetime import datetime
    now = datetime.utcnow()
    rows = query("SELECT key, count, window_start, banned_until FROM push_rate_limits WHERE banned_until IS NOT NULL")
    result = []
    for r in rows:
        bu = _parse_iso(r['banned_until'])
        remaining = int((bu - now).total_seconds()) if bu and bu > now else 0
        if remaining > 0:
            result.append({
                'key': r['key'],
                'count': r['count'],
                'banned_until': r['banned_until'],
                'remaining_seconds': remaining,
            })
    return result
