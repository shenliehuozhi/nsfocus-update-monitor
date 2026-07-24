"""System event log and config model.

Tables:
- system_event_log: 历史事件记录（只记录，不查询）
- system_event_config: 系统事件通知配置
"""

import json
from typing import Optional
from datetime import datetime

# ── Schema ────────────────────────────────────────────────

SCHEMA_SYSTEM_EVENT_LOG = """
CREATE TABLE IF NOT EXISTS system_event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,        -- push_success/push_failed/collection_summary/log_error/session_error
    severity TEXT DEFAULT 'INFO',    -- CRITICAL/WARNING/INFO
    product_name TEXT,               -- 可选，关联产品
    source_url TEXT,                 -- 可选，关联URL
    rule_id INTEGER,                 -- 可选，关联规则
    channel_id INTEGER,              -- 通知渠道
    channel_type TEXT,               -- 渠道类型
    customer_id INTEGER,             -- 客户ID
    is_rollback INTEGER DEFAULT 0,   -- 是否回滚通知
    message TEXT,                    -- JSON格式的详情
    created_at TEXT DEFAULT (datetime('now', 'utc'))
)
"""

SCHEMA_SYSTEM_EVENT_CONFIG = """
CREATE TABLE IF NOT EXISTS system_event_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    enabled INTEGER DEFAULT 0,
    channel_id INTEGER,               -- 关联通知渠道，不设FK避免初始化顺序问题
    event_types TEXT DEFAULT '[]',    -- JSON数组
    created_at TEXT DEFAULT (datetime('now', 'utc')),
    updated_at TEXT DEFAULT (datetime('now', 'utc'))
)
"""

# Schema-sync source-of-truth for system_event_log / system_event_config.
# Keep these in sync with INSERT/UPDATE usage in log_event / get_config /
# update_config below.  Startup sync ALTERs existing DBs to add any missing
# columns.
EXPECTED_SYSTEM_EVENT_LOG_COLUMNS = [
    ('event_type', 'TEXT', "''"),
    ('severity', 'TEXT', "'INFO'"),
    ('product_name', 'TEXT', "''"),
    ('source_url', 'TEXT', "''"),
    ('rule_id', 'INTEGER', '0'),
    ('channel_id', 'INTEGER', '0'),
    ('channel_type', 'TEXT', "''"),
    ('customer_id', 'INTEGER', '0'),
    ('is_rollback', 'INTEGER', '0'),
    ('message', 'TEXT', "''"),
    # created_at — DEFAULT applied by SQLite via CURRENT_TIMESTAMP; sync no-op
]

EXPECTED_SYSTEM_EVENT_CONFIG_COLUMNS = [
    ('enabled', 'INTEGER', '0'),
    ('channel_id', 'INTEGER', '0'),
    ('event_types', 'TEXT', "'[]'"),
    ('created_at', 'TEXT', "''"),
    ('updated_at', 'TEXT', "''"),
]


# ── Table Init ─────────────────────────────────────────────

def create_tables(db):
    """Create system event tables."""
    db.execute(SCHEMA_SYSTEM_EVENT_LOG)
    db.execute(SCHEMA_SYSTEM_EVENT_CONFIG)

    # 老 DB 自动补列。 全新部署两个表已带所有列,sync no-op。
    from src.models.database import sync_table_columns
    sync_table_columns(db, 'system_event_log', EXPECTED_SYSTEM_EVENT_LOG_COLUMNS)
    sync_table_columns(db, 'system_event_config', EXPECTED_SYSTEM_EVENT_CONFIG_COLUMNS)

def get_config() -> dict:
    """获取系统事件通知配置，不存在则创建默认空配置"""
    from src.models.database import query, execute
    rows = query("SELECT * FROM system_event_config LIMIT 1")
    if rows:
        row = rows[0]
        return {
            'enabled': bool(row['enabled']),
            'channel_id': row['channel_id'],
            'event_types': json.loads(row['event_types'] or '[]'),
        }
    # 创建默认配置
    execute("INSERT INTO system_event_config (enabled, event_types) VALUES (0, '[]')")
    return {'enabled': False, 'channel_id': None, 'event_types': []}


def update_config(enabled: bool = None, channel_id: int = None,
                  event_types: list = None) -> dict:
    """更新系统事件通知配置"""
    from src.models.database import query, execute
    updates = []
    params = []
    if enabled is not None:
        updates.append("enabled = ?")
        params.append(1 if enabled else 0)
    if channel_id is not None:
        updates.append("channel_id = ?")
        params.append(channel_id)
    if event_types is not None:
        updates.append("event_types = ?")
        params.append(json.dumps(event_types, ensure_ascii=False))
    if updates:
        updates.append("updated_at = datetime('now', 'utc')")
        execute(
            f"UPDATE system_event_config SET {','.join(updates)} WHERE id = 1",
            tuple(params)
        )
    return get_config()


def is_event_enabled(event_type: str) -> bool:
    """检查某事件类型是否启用"""
    config = get_config()
    if not config['enabled']:
        return False
    # 空数组表示全选，所有事件都启用
    if not config['event_types']:
        return True
    return event_type in config['event_types']


def get_notify_channel() -> dict | None:
    """获取通知渠道配置"""
    from src.models.channel import get_by_id
    config = get_config()
    if not config.get('channel_id'):
        return None
    return get_by_id(config['channel_id'])


# ── Event Log ─────────────────────────────────────────────

def log_event(event_type: str, severity: str = 'INFO',
              product_name: str = None, source_url: str = None,
              rule_id: int = None, channel_id: int = None,
              channel_type: str = None, customer_id: int = None,
              is_rollback: bool = False, message: dict = None) -> int:
    """记录系统事件到日志表"""
    from src.models.database import execute
    msg_json = json.dumps(message, ensure_ascii=False) if message else '{}'
    return execute(
        """INSERT INTO system_event_log
           (event_type, severity, product_name, source_url, rule_id,
            channel_id, channel_type, customer_id, is_rollback, message)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_type, severity, product_name, source_url, rule_id,
         channel_id, channel_type, customer_id, 1 if is_rollback else 0, msg_json)
    )


def get_recent_events(limit: int = 50, event_type: str = None) -> list:
    """查询最近事件记录（供调试用）"""
    from src.models.database import query
    sql = "SELECT * FROM system_event_log"
    params = []
    if event_type:
        sql += " WHERE event_type = ?"
        params.append(event_type)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = query(sql, tuple(params))
    for row in rows:
        row['message'] = json.loads(row['message'] or '{}')
    return rows