"""Channel model — notification delivery channels."""

SCHEMA_CHANNEL = """
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('wecom', 'dingtalk', 'feishu', 'email')),
    config TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
)
"""


def create_tables(db):
    db.execute(SCHEMA_CHANNEL)
    # Migration: add email rate limit columns (v2)
    try:
        db.execute("ALTER TABLE channels ADD COLUMN email_hourly_limit INTEGER DEFAULT 0")
    except:
        pass
    try:
        db.execute("ALTER TABLE channels ADD COLUMN email_daily_limit INTEGER DEFAULT 0")
    except:
        pass


def create(user_id: int, name: str, channel_type: str, config: dict, **kwargs) -> int:
    from src.models.database import execute
    from src.core.crypto import encrypt
    import json
    encrypted_config = encrypt(json.dumps(config, ensure_ascii=False))
    extra_cols = []
    extra_vals = []
    for k in ('email_hourly_limit', 'email_daily_limit'):
        if k in kwargs:
            extra_cols.append(k)
            extra_vals.append(kwargs[k])
    cols = 'user_id, name, type, config' + (', ' + ', '.join(extra_cols) if extra_cols else '')
    vals = (user_id, name, channel_type, encrypted_config) + tuple(extra_vals)
    return execute(
        f"INSERT INTO channels ({cols}) VALUES ({','.join('?' for _ in vals)})",
        vals
    )


def get_by_id(channel_id: int) -> dict | None:
    from src.models.database import query
    rows = query("SELECT * FROM channels WHERE id = ?", (channel_id,))
    return _decrypt_config(rows[0]) if rows else None


def list_by_user(user_id: int) -> list:
    from src.models.database import query
    rows = query("SELECT * FROM channels WHERE user_id = ? ORDER BY type, name", (user_id,))
    return [_decrypt_config(r) for r in rows]


def list_active() -> list:
    from src.models.database import query
    rows = query("SELECT * FROM channels WHERE is_active = 1")
    return [_decrypt_config(r) for r in rows]


def update(channel_id: int, **kwargs) -> None:
    from src.models.database import execute
    from src.core.crypto import encrypt
    import json
    if 'config' in kwargs:
        kwargs['config'] = encrypt(json.dumps(kwargs['config'], ensure_ascii=False))
    sets = ', '.join(f'{k} = ?' for k in kwargs)
    execute(f"UPDATE channels SET {sets} WHERE id = ?", tuple(kwargs.values()) + (channel_id,))


def delete(channel_id: int) -> None:
    from src.models.database import execute
    execute("DELETE FROM rule_channels WHERE channel_id = ?", (channel_id,))
    execute("UPDATE delivery_log SET channel_id = NULL WHERE channel_id = ?", (channel_id,))
    execute("DELETE FROM channels WHERE id = ?", (channel_id,))


def _decrypt_config(row: dict) -> dict:
    from src.core.crypto import decrypt
    import json
    if row.get('config'):
        try:
            row['config'] = json.loads(decrypt(row['config']))
        except (json.JSONDecodeError, TypeError):
            row['config'] = {}
    return row
