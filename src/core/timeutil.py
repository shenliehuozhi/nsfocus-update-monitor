"""时间口径工具。

硬规则：
  - 数据库只存 UTC（naive ISO / `YYYY-MM-DD HH:MM:SS`），不存服务器本地时间；
  - 展示层和业务边界（月度报告、静默期、推送窗口）显式转换到 UTC+8；
  - 不依赖服务器 TZ 设置，固定使用 UTC+8（CST，无夏令时）。

`parse_ts()` 同时接受 naive、`Z`、`+00:00` 与 SQLite 空格分隔格式。
"""

from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))
DB_FMT = '%Y-%m-%d %H:%M:%S'


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_str(fmt: str = DB_FMT) -> str:
    """当前 UTC 时间，naive 字符串（写 DB 用）。"""
    return utc_now().strftime(fmt)


def cst_now() -> datetime:
    """当前 UTC+8 时间（业务边界 / 展示用）。"""
    return datetime.now(CST)


def parse_ts(value) -> datetime | None:
    """解析 DB / API 时间字符串；无法解析返回 None。

    naive 视为 UTC，这是 DB 的存储约定。
    """
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(('Z', 'z')):
        text = text[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def to_utc(dt: datetime) -> datetime:
    """naive 视为 UTC；aware 统一换算到 UTC。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_cst(dt: datetime) -> datetime:
    return to_utc(dt).astimezone(CST)


def utc_iso(value=None, timespec: str = 'seconds') -> str:
    """把任意时间归一化为 naive UTC ISO 字符串。"""
    dt = parse_ts(value) if value is not None else utc_now()
    if dt is None:
        raise ValueError(f'invalid timestamp: {value!r}')
    return to_utc(dt).replace(tzinfo=None).isoformat(timespec=timespec)


def cst_month_bounds(year: int, month: int) -> tuple[str, str]:
    """返回 UTC+8 自然月的 [start, end) 边界，输出 DB 约定格式（naive UTC 空格分隔）。"""
    if not 1 <= month <= 12:
        raise ValueError(f'invalid month: {month}')
    start = datetime(year, month, 1, tzinfo=CST)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=CST)
    else:
        end = datetime(year, month + 1, 1, tzinfo=CST)
    return (start.astimezone(timezone.utc).strftime(DB_FMT),
            end.astimezone(timezone.utc).strftime(DB_FMT))


# ── One-time migration: delayed_queue local → UTC ────────────

_QUEUE_UTC_FLAG = 'delayed_queue_timestamps_utc'


def migrate_delayed_queue_to_utc() -> bool:
    """把旧 delayed_queue 的本地时间字段一次性转成 UTC。

    历史版本用 `datetime.now()`（服务器本地）写 push_after / pushed_at /
    next_attempt_at / last_attempt_at，而 created_at 是 SQLite UTC 默认值。
    统一为 UTC 后，所有队列比较都按 UTC 进行。

    幂等：成功后写 `system_settings.delayed_queue_timestamps_utc=1`，
    后续启动直接跳过。返回 True 表示本次执行了迁移。
    """
    from src.models.database import query, execute
    from src.core.logger import get_logger

    logger = get_logger('timeutil')
    try:
        rows = query("SELECT value FROM system_settings WHERE key = ?", (_QUEUE_UTC_FLAG,))
        if rows and str(rows[0]['value']) == '1':
            return False
        cols = {c['name'] for c in query("PRAGMA table_info(delayed_queue)")}
        if not cols:
            return False
        fields = [f for f in ('push_after', 'pushed_at', 'next_attempt_at', 'last_attempt_at')
                  if f in cols]
        if not fields:
            return False

        offset = datetime.now().astimezone().utcoffset() or timedelta(0)
        if offset != timedelta(0):
            modifier = f'{int(-offset.total_seconds())} seconds'
            assignments = ', '.join(
                f"{f} = CASE WHEN {f} IS NULL OR {f} = '' THEN {f} "
                f"ELSE datetime({f}, ?) END"
                for f in fields
            )
            execute(f"UPDATE delayed_queue SET {assignments}", tuple([modifier] * len(fields)))
            logger.info('delayed_queue migrated to UTC (offset %s)', offset)

        execute(
            "INSERT OR REPLACE INTO system_settings (key, value, updated_at) "
            "VALUES (?, '1', datetime('now'))",
            (_QUEUE_UTC_FLAG,),
        )
        return True
    except Exception as e:
        logger.warning(f'delayed_queue UTC migration failed: {e}')
        return False
