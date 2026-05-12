"""邮件频率限流 — 三维度硬限制，防 Bug 邮件轰炸。

三层限制（按优先级检查）：
  1. 邮件渠道维度 (per SMTP relay):  默认 0=不限制
  2. 单个客户维度 (per customer):    默认 10/时, 50/天
  3. 全局兜底 (system-wide):         默认 100/时, 500/天

计数器按小时/天 bucket 存 DB，服务重启不丢。超限静默跳过 + 记日志。
"""

from datetime import datetime
from src.models.database import query, execute
from src.core.logger import get_logger

logger = get_logger('email_limiter')

SCHEMA = """
CREATE TABLE IF NOT EXISTS email_rate_counters (
    key TEXT NOT NULL,
    bucket TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (key, bucket)
)
"""

# Default limits (overridable via system_settings)
DEFAULT_CHANNEL_HOURLY = 0    # 0 = no limit
DEFAULT_CHANNEL_DAILY = 0
DEFAULT_CUSTOMER_HOURLY = 10
DEFAULT_CUSTOMER_DAILY = 50
DEFAULT_GLOBAL_HOURLY = 100
DEFAULT_GLOBAL_DAILY = 500


def create_tables(db) -> None:
    db.execute(SCHEMA)


def _hour_bucket() -> str:
    """Return current UTC hour bucket: '2026-05-12-15'"""
    return datetime.utcnow().strftime('%Y-%m-%d-%H')


def _day_bucket() -> str:
    """Return current UTC day bucket: '2026-05-12'"""
    return datetime.utcnow().strftime('%Y-%m-%d')


def _check(key: str, limit: int, bucket: str, label: str) -> tuple[bool, str]:
    """Core check: if current bucket count >= limit, deny.
    
    Returns (allowed, reason).
    """
    if limit <= 0:
        return True, ''
    
    rows = query(
        "SELECT count FROM email_rate_counters WHERE key = ? AND bucket = ?",
        (key, bucket)
    )
    current = rows[0]['count'] if rows else 0
    
    if current >= limit:
        reason = f'[邮件限流-{label}] {key} 已达{limit}封/{bucket_type(bucket)}，跳过'
        logger.warning(reason)
        return False, reason
    
    return True, ''


def bucket_type(bucket: str) -> str:
    """Return human-readable bucket type."""
    return '小时' if len(bucket) > 10 else '天'


def check_channel(channel_id: int, hourly_limit: int = 0, daily_limit: int = 0) -> tuple[bool, str]:
    """Check channel-level limits. Returns (allowed, reason)."""
    if hourly_limit <= 0 and daily_limit <= 0:
        return True, ''
    
    # Daily check first (broader)
    allowed, reason = _check(
        f'ch:{channel_id}', daily_limit, _day_bucket(), f'渠道{channel_id}-日'
    )
    if not allowed:
        return False, reason
    
    return _check(
        f'ch:{channel_id}', hourly_limit, _hour_bucket(), f'渠道{channel_id}-时'
    )


def check_customer(customer_id: int,
                   hourly_limit: int = DEFAULT_CUSTOMER_HOURLY,
                   daily_limit: int = DEFAULT_CUSTOMER_DAILY) -> tuple[bool, str]:
    """Check customer-level limits. Returns (allowed, reason)."""
    if hourly_limit <= 0 and daily_limit <= 0:
        return True, ''
    
    allowed, reason = _check(
        f'cust:{customer_id}', daily_limit, _day_bucket(), f'客户{customer_id}-日'
    )
    if not allowed:
        return False, reason
    
    return _check(
        f'cust:{customer_id}', hourly_limit, _hour_bucket(), f'客户{customer_id}-时'
    )


def check_global(hourly_limit: int = DEFAULT_GLOBAL_HOURLY,
                 daily_limit: int = DEFAULT_GLOBAL_DAILY) -> tuple[bool, str]:
    """Check global system-wide limits. Returns (allowed, reason)."""
    if hourly_limit <= 0 and daily_limit <= 0:
        return True, ''
    
    allowed, reason = _check('global', daily_limit, _day_bucket(), '全局-日')
    if not allowed:
        return False, reason
    
    return _check('global', hourly_limit, _hour_bucket(), '全局-时')


def record(channel_id: int, customer_id: int) -> None:
    """Increment all three counter layers after a successful send."""
    now = datetime.utcnow().isoformat()
    hb = _hour_bucket()
    db = _day_bucket()
    
    for key in [f'ch:{channel_id}', f'cust:{customer_id}', 'global']:
        for bucket in [hb, db]:
            execute(
                "INSERT INTO email_rate_counters (key, count, bucket, updated_at) "
                "VALUES (?, 1, ?, ?) ON CONFLICT(key, bucket) DO UPDATE SET "
                "count = count + 1, updated_at = excluded.updated_at",
                (key, bucket, now)
            )


def get_today_stats() -> dict:
    """Return today's email counts per dimension."""
    db = _day_bucket()
    rows = query(
        "SELECT key, count FROM email_rate_counters WHERE bucket = ?", (db,)
    )
    result = {'global': 0, 'channels': {}, 'customers': {}}
    for r in rows:
        k = r['key']
        if k == 'global':
            result['global'] = r['count']
        elif k.startswith('ch:'):
            result['channels'][k[3:]] = r['count']
        elif k.startswith('cust:'):
            result['customers'][k[5:]] = r['count']
    return result
