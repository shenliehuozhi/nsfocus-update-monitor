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
    created_at TEXT DEFAULT (datetime('now', 'utc')),
    acked_at TEXT,                   -- 已读时间（UTC）；不等于已解决
    resolved_at TEXT,                -- 解决时间（UTC）；NULL = 未解决
    resolved_by TEXT                 -- 解决来源：auto / user:<id>
)
"""

# 事件生命周期状态（派生列，不单独存储）：
#   open     — 未读
#   acked    — 已读，但问题可能仍在
#   resolved — 已解决（总是同时视为已读）
EVENT_STATE_SQL = (
    "CASE WHEN resolved_at IS NOT NULL THEN 'resolved' "
    "WHEN acked_at IS NOT NULL THEN 'acked' ELSE 'open' END"
)

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
    # 生命周期列（2026-11-12）：老库由 sync 自动补齐
    ('acked_at', 'TEXT', ''),
    ('resolved_at', 'TEXT', ''),
    ('resolved_by', 'TEXT', ''),
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
        updates.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
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
    """记录系统事件到日志表
    2026-09-02: created_at 改用 Python datetime.utcnow().isoformat() 显式传 ISO UTC,
    避免 SQLite 在 CST 服务器上 datetime('now','utc') 算错 -8h 的 bug
    (详见 commit 日志: SQLite 编译时假设系统 UTC,把 CST 减 8h 再当 UTC)
    """
    from src.models.database import execute
    msg_json = json.dumps(message, ensure_ascii=False) if message else '{}'
    return execute(
        """INSERT INTO system_event_log
           (event_type, severity, product_name, source_url, rule_id,
            channel_id, channel_type, customer_id, is_rollback, message, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_type, severity, product_name, source_url, rule_id,
         channel_id, channel_type, customer_id, 1 if is_rollback else 0,
         msg_json, datetime.utcnow().isoformat())
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


# ── Lifecycle: open → acked → resolved ────────────────────
# 「已读」只表示人看过；「已解决」才表示问题消失。两者必须分开，
# 否则未自愈的告警会被已读淹没，长期失真。

def _event_filters(event_type: str = None, event_types: list | None = None,
                   source_url: str = None, product_name: str = None,
                   severity: str = None, since_hours: int | None = None,
                   message_like: str = None) -> tuple[str, list]:
    where, params = [], []
    types = list(event_types or [])
    if event_type:
        types.append(event_type)
    if types:
        where.append('event_type IN (' + ','.join('?' * len(types)) + ')')
        params.extend(types)
    if source_url:
        where.append('source_url = ?')
        params.append(source_url)
    if product_name:
        where.append('product_name = ?')
        params.append(product_name)
    if severity:
        where.append('severity = ?')
        params.append(severity)
    if since_hours and since_hours > 0:
        where.append("datetime(created_at) >= datetime('now', ? || ' hours')")
        params.append(f'-{int(since_hours)}')
    if message_like:
        where.append('message LIKE ?')
        params.append(f'%{message_like}%')
    return ' AND '.join(where), params


def resolve_event(event_id: int, resolved_by: str = 'manual') -> int:
    """标记单个事件为已解决；已解决的事件不重复处理（幂等）。"""
    from src.models.database import query, execute
    rows = query(
        "SELECT COUNT(*) AS c FROM system_event_log "
        "WHERE id = ? AND resolved_at IS NULL",
        (event_id,),
    )
    count = rows[0]['c'] if rows else 0
    if count:
        execute(
            "UPDATE system_event_log SET "
            "acked_at = COALESCE(acked_at, datetime('now', 'utc')), "
            "resolved_at = datetime('now', 'utc'), resolved_by = ? "
            "WHERE id = ? AND resolved_at IS NULL",
            (resolved_by, event_id),
        )
    return count


def resolve_events(event_type: str = None, event_types: list | None = None,
                   source_url: str = None, product_name: str = None,
                   severity: str = None, since_hours: int = None,
                   message_like: str = None,
                   resolved_by: str = 'auto') -> int:
    """按条件批量标记已解决，返回本次实际解决条数。

    默认 resolved_by='auto'，供自愈场景（站点恢复 / DB 锁恢复）调用；
    人工操作传 'user:<id>'。
    """
    from src.models.database import query, execute
    filters, params = _event_filters(
        event_type=event_type, event_types=event_types, source_url=source_url,
        product_name=product_name, severity=severity,
        since_hours=since_hours, message_like=message_like,
    )
    where_sql = 'resolved_at IS NULL' + (f' AND {filters}' if filters else '')
    rows = query(
        f"SELECT COUNT(*) AS c FROM system_event_log WHERE {where_sql}",
        tuple(params),
    )
    count = rows[0]['c'] if rows else 0
    if count:
        execute(
            "UPDATE system_event_log SET "
            "acked_at = COALESCE(acked_at, datetime('now', 'utc')), "
            "resolved_at = datetime('now', 'utc'), resolved_by = ? "
            f"WHERE {where_sql}",
            tuple([resolved_by] + params),
        )
    return count


def cleanup_old_events(resolved_days: int = 180, info_days: int = 180,
                       unresolved_days: int = 365) -> int:
    """删除过期事件，返回删除条数。

    保留策略：
      - 已解决事件：resolved_days 天
      - INFO 记录：info_days 天（仅记录，不构成告警）
      - 未解决告警：unresolved_days 天（给足处理窗口，避免静默丢失）
    """
    from src.models.database import query, execute
    where_sql = (
        "(resolved_at IS NOT NULL AND datetime(created_at) < datetime('now', ?)) "
        "OR (severity = 'INFO' AND datetime(created_at) < datetime('now', ?)) "
        "OR (resolved_at IS NULL AND severity != 'INFO' "
        "    AND datetime(created_at) < datetime('now', ?))"
    )
    params = (f'-{int(resolved_days)} days', f'-{int(info_days)} days',
              f'-{int(unresolved_days)} days')
    rows = query(
        f"SELECT COUNT(*) AS c FROM system_event_log WHERE {where_sql}", params
    )
    count = rows[0]['c'] if rows else 0
    if count:
        execute(f"DELETE FROM system_event_log WHERE {where_sql}", params)
    return count