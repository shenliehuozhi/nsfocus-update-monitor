"""User model."""

SCHEMA_USER = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
)
"""

# Login failure tracking (brute-force protection)
SCHEMA_LOGIN_BAN = """
CREATE TABLE IF NOT EXISTS login_bans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL UNIQUE,
    attempts INTEGER DEFAULT 0,
    banned_until TEXT DEFAULT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
)
"""


def create_tables(db):
    db.execute(SCHEMA_USER)


def create_login_ban_table(db):
    db.execute(SCHEMA_LOGIN_BAN)


# ── Login rate limiting ────────────────────────────────────────────

def is_ip_banned(ip: str) -> bool:
    """Check if IP is banned due to too many failed login attempts."""
    from src.models.database import query
    rows = query(
        "SELECT banned_until FROM login_bans WHERE ip = ? AND banned_until IS NOT NULL",
        (ip,)
    )
    if not rows:
        return False
    from datetime import datetime
    banned_until = rows[0]['banned_until']
    if datetime.utcnow().isoformat() > banned_until:
        # Ban expired, clear it
        clear_login_failure(ip)
        return False
    return True


def record_login_failure(ip: str) -> bool:
    """Record a failed login attempt. Returns True if IP is now banned."""
    from src.models.database import query, execute
    from datetime import datetime, timedelta

    rows = query("SELECT attempts FROM login_bans WHERE ip = ?", (ip,))
    attempts = (rows[0]['attempts'] if rows else 0) + 1

    # Ban after 5 consecutive failures for 15 minutes
    MAX_ATTEMPTS = 5
    BAN_MINUTES = 15

    if attempts >= MAX_ATTEMPTS:
        banned_until = (datetime.now() + timedelta(minutes=BAN_MINUTES)).isoformat()
        execute(
            "INSERT OR REPLACE INTO login_bans (ip, attempts, banned_until, updated_at) VALUES (?, ?, ?, ?)",
            (ip, attempts, banned_until, datetime.now().isoformat())
        )
        return True
    else:
        execute(
            "INSERT OR REPLACE INTO login_bans (ip, attempts, banned_until, updated_at) VALUES (?, ?, NULL, ?)",
            (ip, attempts, datetime.now().isoformat())
        )
        return False


def clear_login_failure(ip: str):
    """Clear login failure record on successful login."""
    from src.models.database import execute
    execute("DELETE FROM login_bans WHERE ip = ?", (ip,))


def create_user(username: str, password_hash: str, is_admin: bool = False) -> int:
    from src.models.database import execute
    return execute(
        "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
        (username, password_hash, int(is_admin))
    )


def get_by_username(username: str) -> dict | None:
    from src.models.database import query
    rows = query("SELECT * FROM users WHERE username = ?", (username,))
    return rows[0] if rows else None


def get_by_id(user_id: int) -> dict | None:
    from src.models.database import query
    rows = query("SELECT * FROM users WHERE id = ?", (user_id,))
    return rows[0] if rows else None


def list_users() -> list:
    from src.models.database import query
    return query("SELECT id, username, is_admin, created_at FROM users ORDER BY id")


def update_password(user_id: int, password_hash: str):
    from src.models.database import execute
    execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
