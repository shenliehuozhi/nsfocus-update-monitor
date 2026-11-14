"""SQLite 数据库自动备份。

默认每 30 天备份一次（`backup_interval_days` 可在系统设置调整），
调度器每天 03:30 检查是否到期。备份走 SQLite 在线备份 API，
生成一致性快照后 gzip 压缩、原子替换，只保留最近 6 份。
"""

import gzip
import os
import re
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from src.core.logger import get_logger

logger = get_logger('db_backup')

SETTING_KEY = 'backup_interval_days'
DEFAULT_INTERVAL_DAYS = 30
MIN_INTERVAL_DAYS = 1
MAX_INTERVAL_DAYS = 365

KEEP_KEY = 'backup_keep_count'
DEFAULT_KEEP_COUNT = 6
MIN_KEEP_COUNT = 1
MAX_KEEP_COUNT = 24

LAST_AT_KEY = 'backup_last_at'
LAST_SIZE_KEY = 'backup_last_size'
LAST_NAME_KEY = 'backup_last_name'
TOTAL_SIZE_KEY = 'backup_total_size'

_NAME_RE = re.compile(r'^nsfocus_monitor-(\d{8}_\d{6}_\d{6})\.db\.gz$')
_lock = threading.Lock()


def _db_path() -> Path:
    from src.models import database
    path = database.DB_PATH
    if not path:
        path = database.init_db()
    return Path(path)


def backup_dir() -> Path:
    return _db_path().parent / 'backups'


def get_interval_days() -> int:
    return _get_int_setting(SETTING_KEY, DEFAULT_INTERVAL_DAYS, MIN_INTERVAL_DAYS, MAX_INTERVAL_DAYS)


def get_keep_count() -> int:
    return _get_int_setting(KEEP_KEY, DEFAULT_KEEP_COUNT, MIN_KEEP_COUNT, MAX_KEEP_COUNT)


def _get_int_setting(key: str, default: int, minimum: int, maximum: int) -> int:
    raw = None
    try:
        from src.models.database import query
        rows = query("SELECT value FROM system_settings WHERE key = ?", (key,))
        if rows:
            raw = rows[0]['value']
    except Exception as e:
        logger.debug(f'read {key} failed: {e}')
    try:
        value = int(str(raw).strip()) if raw not in (None, '') else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def list_backups() -> list[Path]:
    """返回按时间倒序排列的备份文件列表。"""
    directory = backup_dir()
    try:
        files = [p for p in directory.iterdir() if p.is_file() and _NAME_RE.match(p.name)]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.name, reverse=True)


def latest_backup() -> Path | None:
    files = list_backups()
    return files[0] if files else None


def _created_at(path: Path) -> datetime | None:
    m = _NAME_RE.match(path.name)
    if m:
        try:
            return datetime.strptime(m.group(1), '%Y%m%d_%H%M%S_%f').replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def is_due(now: datetime | None = None) -> bool:
    """没有备份或距上次备份超过配置天数时返回 True。"""
    latest = latest_backup()
    if latest is None:
        return True
    created = _created_at(latest)
    if created is None:
        return True
    current = now or datetime.now(timezone.utc)
    age = (current - created).total_seconds()
    return age >= get_interval_days() * 86400


def _snapshot(src_path: Path, dest_path: Path):
    """用 SQLite backup API 复制一致性快照（不阻塞应用写入）。"""
    source = sqlite3.connect(str(src_path), timeout=30)
    dest = sqlite3.connect(str(dest_path))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()


def _write_setting(key: str, value: str):
    from src.models.database import execute
    execute(
        "INSERT OR REPLACE INTO system_settings (key, value, updated_at) "
        "VALUES (?, ?, datetime('now'))",
        (key, value),
    )


def _record_metadata(created: datetime, size: int, name: str):
    try:
        values = {
            # 与 last_quick_scan_at 等一致：naive UTC，前端 fmtTZ 再转本地时间
            LAST_AT_KEY: created.replace(tzinfo=None).isoformat(timespec='seconds'),
            LAST_SIZE_KEY: str(size),
            LAST_NAME_KEY: name,
        }
        for key, value in values.items():
            _write_setting(key, value)
    except Exception as e:
        logger.warning(f'persist backup metadata failed: {e}')


def total_size() -> int:
    total = 0
    for path in list_backups():
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def _save_total_size() -> int:
    total = total_size()
    try:
        _write_setting(TOTAL_SIZE_KEY, str(total))
    except Exception as e:
        logger.warning(f'persist backup total size failed: {e}')
    return total


def prune_backups(keep: int | None = None) -> list[str]:
    """只保留最近 keep 份（默认读配置），返回被删除的文件名。"""
    limit = get_keep_count() if keep is None else max(1, keep)
    removed = []
    for path in list_backups()[limit:]:
        try:
            path.unlink()
            removed.append(path.name)
        except OSError as e:
            logger.warning(f'remove old backup {path.name} failed: {e}')
    _save_total_size()
    if removed:
        logger.info(f'[DB-BACKUP] pruned {len(removed)} old backup(s): {", ".join(removed)}')
    return removed


def sync_retention() -> list[str]:
    """保留份数配置变更后立即清理超出的旧备份。"""
    removed = prune_backups()
    logger.info('[DB-BACKUP] retention synced: keep=%d total=%dB', get_keep_count(), total_size())
    return removed


def create_backup() -> dict:
    """生成一份 gzip 备份（调用方需先确认到期或强制）。"""
    src_path = _db_path()
    if not src_path.is_file():
        raise FileNotFoundError(f'database not found: {src_path}')
    directory = backup_dir()
    directory.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    stamp = now.strftime('%Y%m%d_%H%M%S_%f')
    name = f'nsfocus_monitor-{stamp}.db.gz'
    final_path = directory / name
    tmp_db = directory / f'.{name}.db.tmp'
    tmp_gz = directory / f'.{name}.tmp'
    try:
        _snapshot(src_path, tmp_db)
        with open(tmp_db, 'rb') as fin, gzip.open(tmp_gz, 'wb', compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout)
        os.chmod(tmp_gz, 0o600)
        os.replace(tmp_gz, final_path)
    finally:
        for leftover in (tmp_db, tmp_gz):
            try:
                leftover.unlink()
            except OSError:
                pass

    size = final_path.stat().st_size
    _record_metadata(now, size, name)
    prune_backups()
    return {
        'name': name,
        'path': str(final_path),
        'size': size,
        'created_at': now.isoformat(timespec='seconds'),
    }


def run_if_due(force: bool = False) -> dict | None:
    """到期（或 force）时执行一次备份；未到期/并发执行时返回 None。"""
    if not _lock.acquire(blocking=False):
        logger.info('[DB-BACKUP] another backup is running, skip')
        return None
    try:
        if not _db_path().is_file():
            return None
        if not force and not is_due():
            logger.debug(f'[DB-BACKUP] not due (interval={get_interval_days()}d)')
            return None
        info = create_backup()
        logger.info(f"[DB-BACKUP] created {info['name']} ({info['size'] / 1024:.1f} KB)")
        return info
    except Exception as e:
        logger.error(f'[DB-BACKUP] failed: {e}')
        return None
    finally:
        _lock.release()
