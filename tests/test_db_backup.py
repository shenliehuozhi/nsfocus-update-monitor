"""数据库自动备份：到期判断、快照内容、保留策略与设置接线。"""
import gzip
import os
import sqlite3
import stat
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    import src.models.database as db_mod

    monkeypatch.setattr(db_mod, 'DB_PATH', str(tmp_path / 'nsfocus_monitor.db'))
    if hasattr(db_mod._local, 'conn'):
        monkeypatch.setattr(db_mod._local, 'conn', None)
    db_mod.get_db()
    from src.models import init_all_tables
    init_all_tables()
    return db_mod


def _set_setting(key, value):
    from src.models.database import execute
    execute(
        "INSERT OR REPLACE INTO system_settings (key, value, updated_at) "
        "VALUES (?, ?, datetime('now'))",
        (key, str(value)),
    )


def _backup_count():
    import src.core.db_backup as backup
    return len(backup.list_backups())


def test_create_backup_writes_valid_gzip_sqlite(db, tmp_path):
    import src.core.db_backup as backup
    from src.models.database import execute, query

    execute(
        "INSERT OR REPLACE INTO system_settings (key, value, updated_at) "
        "VALUES ('backup_marker', 'hello', datetime('now'))"
    )
    info = backup.create_backup()

    path = backup.backup_dir() / info['name']
    assert path.is_file()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert info['size'] == path.stat().st_size

    restored = tmp_path / 'restored.db'
    with gzip.open(path, 'rb') as f:
        restored.write_bytes(f.read())
    conn = sqlite3.connect(restored)
    try:
        row = conn.execute(
            "SELECT value FROM system_settings WHERE key = 'backup_marker'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 'hello'

    meta = {r['key']: r['value'] for r in query(
        "SELECT key, value FROM system_settings WHERE key LIKE 'backup_last_%'"
    )}
    assert meta[backup.LAST_NAME_KEY] == info['name']
    assert meta[backup.LAST_SIZE_KEY] == str(info['size'])
    assert meta[backup.LAST_AT_KEY]


def test_interval_defaults_clamps_and_reads_setting(db):
    import src.core.db_backup as backup

    assert backup.get_interval_days() == 30

    _set_setting(backup.SETTING_KEY, '0')
    assert backup.get_interval_days() == backup.MIN_INTERVAL_DAYS

    _set_setting(backup.SETTING_KEY, '9999')
    assert backup.get_interval_days() == backup.MAX_INTERVAL_DAYS

    _set_setting(backup.SETTING_KEY, 'abc')
    assert backup.get_interval_days() == backup.DEFAULT_INTERVAL_DAYS

    _set_setting(backup.SETTING_KEY, '7')
    assert backup.get_interval_days() == 7


def test_is_due_follows_interval_and_latest_backup_age(db):
    import src.core.db_backup as backup

    assert backup.is_due() is True

    backup.create_backup()
    assert backup.is_due() is False

    latest = backup.latest_backup()
    old_stamp = (datetime.now(timezone.utc) - timedelta(days=10)).strftime('%Y%m%d_%H%M%S_%f')
    old_path = latest.parent / f'nsfocus_monitor-{old_stamp}.db.gz'
    latest.rename(old_path)

    assert backup.is_due() is False  # 默认 30 天

    _set_setting(backup.SETTING_KEY, '7')
    assert backup.is_due() is True

    # 配置改回 30 天后，同一份 10 天前的备份不再触发
    _set_setting(backup.SETTING_KEY, '30')
    assert backup.is_due() is False


def test_keep_count_defaults_clamps_and_reads_setting(db):
    import src.core.db_backup as backup

    assert backup.get_keep_count() == 6

    _set_setting(backup.KEEP_KEY, '0')
    assert backup.get_keep_count() == backup.MIN_KEEP_COUNT

    _set_setting(backup.KEEP_KEY, '999')
    assert backup.get_keep_count() == backup.MAX_KEEP_COUNT

    _set_setting(backup.KEEP_KEY, 'x')
    assert backup.get_keep_count() == backup.DEFAULT_KEEP_COUNT

    _set_setting(backup.KEEP_KEY, '3')
    assert backup.get_keep_count() == 3


def test_run_if_due_skips_and_force_creates(db):
    import src.core.db_backup as backup

    first = backup.run_if_due()
    assert first and _backup_count() == 1

    assert backup.run_if_due() is None
    assert _backup_count() == 1

    forced = backup.run_if_due(force=True)
    assert forced and _backup_count() == 2


def test_prune_keeps_latest_six(db):
    import src.core.db_backup as backup

    directory = backup.backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    base = datetime.now(timezone.utc) - timedelta(days=10)
    names = []
    for i in range(8):
        stamp = (base + timedelta(minutes=i)).strftime('%Y%m%d_%H%M%S_%f')
        name = f'nsfocus_monitor-{stamp}.db.gz'
        (directory / name).write_bytes(b'x')
        names.append(name)

    removed = backup.prune_backups()

    remaining = sorted(p.name for p in backup.list_backups())
    assert len(remaining) == backup.DEFAULT_KEEP_COUNT
    assert remaining == sorted(names[2:])
    assert sorted(removed) == sorted(names[:2])


def test_prune_follows_configured_keep_count(db):
    import src.core.db_backup as backup

    _set_setting(backup.KEEP_KEY, '2')
    directory = backup.backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    base = datetime.now(timezone.utc) - timedelta(days=10)
    names = []
    for i in range(5):
        stamp = (base + timedelta(minutes=i)).strftime('%Y%m%d_%H%M%S_%f')
        name = f'nsfocus_monitor-{stamp}.db.gz'
        (directory / name).write_bytes(b'x' * 10)
        names.append(name)

    removed = backup.sync_retention()

    remaining = sorted(p.name for p in backup.list_backups())
    assert remaining == sorted(names[-2:])
    assert sorted(removed) == sorted(names[:3])
    from src.models.database import query
    total = query("SELECT value FROM system_settings WHERE key = ?", (backup.TOTAL_SIZE_KEY,))
    assert int(total[0]['value']) == 20


def test_run_if_due_without_database_is_noop(db, monkeypatch):
    import src.core.db_backup as backup
    from pathlib import Path

    monkeypatch.setattr(backup, '_db_path', lambda: Path('/nonexistent/nsfocus.db'))
    assert backup.run_if_due(force=True) is None


def test_scheduler_metrics_and_settings_are_wired():
    from pathlib import Path
    from tests.frontend_source import WEB, js_source

    root = Path(__file__).resolve().parents[1]
    scheduler = (root / 'src' / 'core' / 'scheduler.py').read_text(encoding='utf-8')
    assert "id='nsfocus_db_backup'" in scheduler
    assert 'nsfocus_db_backup' in (root / 'src' / 'web' / 'routes' / 'metrics_routes.py').read_text(encoding='utf-8')
    assert 'nsfocus_db_backup' in (WEB / 'static' / 'js' / 'observability.js').read_text(encoding='utf-8')

    source = js_source()
    assert 'id="cfg-backup-days"' in source
    assert 'id="cfg-backup-keep"' in source
    assert source.count(
        "backup_interval_days:document.getElementById('cfg-backup-days').value"
    ) == 2
    assert source.count(
        "backup_keep_count:document.getElementById('cfg-backup-keep').value"
    ) == 2

    api = (root / 'src' / 'web' / 'routes' / 'api_routes.py').read_text(encoding='utf-8')
    assert "config['backup_keep_count']" in api
    assert api.count('sync_retention()') == 2
