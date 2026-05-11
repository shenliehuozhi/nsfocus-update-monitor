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


def create_tables(db):
    db.execute(SCHEMA_USER)


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
