"""Audit log model."""

import json

SCHEMA_AUDIT = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    details TEXT DEFAULT '{}',
    ip_address TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
)
"""

INDEX_AUDIT = "CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, created_at)"


def create_tables(db):
    db.execute(SCHEMA_AUDIT)
    db.execute(INDEX_AUDIT)


def log(user_id: int, action: str, details: dict = None, ip_address: str = '') -> int:
    from src.models.database import execute
    return execute(
        "INSERT INTO audit_log (user_id, action, details, ip_address) VALUES (?, ?, ?, ?)",
        (user_id, action, json.dumps(details or {}, ensure_ascii=False), ip_address)
    )


def list_by_user(user_id: int, limit: int = 50) -> list:
    from src.models.database import query
    rows = query(
        "SELECT * FROM audit_log WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit)
    )
    for r in rows:
        try:
            r['details'] = json.loads(r['details'])
        except (json.JSONDecodeError, TypeError):
            pass
    return rows


def cleanup_old(days: int = 365):
    from src.models.database import execute
    execute("DELETE FROM audit_log WHERE created_at < datetime('now', ?)", (f'-{days} days',))
