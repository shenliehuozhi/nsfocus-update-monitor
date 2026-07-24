"""升级场景测试:模拟用户机器上的老 DB,启动期自动迁移。"""
import os
import sys
import tempfile
import shutil
import sqlite3


def test_user_upgrade_auto_migrate():
    """模拟"用户升级 exe 后的第一次启动":
    - 老 DB 有旧算法 path_id(同物理包多 active 行,会假通知)
    - 没有 snapshots_migration_v3 marker
    - 启动期调 _migrate_snapshots_url_based_if_needed 应该自动迁移 + 写 marker
    - 再调一次应该 noop
    """
    tmp = tempfile.mkdtemp(prefix='user_old_')
    db_path = os.path.join(tmp, 'nsfocus_monitor.db')
    con = sqlite3.connect(db_path)
    con.executescript("""
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER,
    source_url TEXT,
    product_name TEXT,
    file_name TEXT,
    md5_hash TEXT,
    path_id TEXT,
    last_seen_at TEXT,
    status TEXT DEFAULT 'active'
);
INSERT INTO snapshots (source_id, source_url, file_name, md5_hash, path_id, status) VALUES
    (1, 'http://x.com/p/a.zip', 'a.zip', 'm1', 'old-chain-text-a', 'active'),
    (1, 'http://x.com/p/a.zip', 'a.zip', 'm1', 'old-chain-text-b', 'active'),
    (1, 'http://x.com/p/b.zip', 'b.zip', 'm2', 'old-chain-text-c', 'active'),
    (2, 'http://y.com/q/c.zip', 'c.zip', 'm3', 'old-chain-text-d', 'active');
CREATE TABLE system_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
""")
    con.commit()
    con.close()

    sys.path.insert(0, '/root/nsfocus-monitor')
    import src.models.database as db_mod
    db_mod.DB_PATH = db_path
    if hasattr(db_mod._local, 'conn'):
        db_mod._local.conn = None
    db_mod.get_db()

    # 迁移前
    con = sqlite3.connect(db_path)
    pre_marker_rows = con.execute(
        "SELECT value FROM system_settings WHERE key='snapshots_migration_v3'"
    ).fetchall()
    pre_rows = con.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
    pre_active = con.execute("SELECT COUNT(*) FROM snapshots WHERE status='active'").fetchone()[0]
    con.close()
    assert pre_marker_rows == [], f'老 DB 不应有 marker,实际={pre_marker_rows}'
    assert pre_rows == 4
    assert pre_active == 4
    print(f'迁移前: rows={pre_rows} active={pre_active} (老 schema,无 marker)')

    # 触发迁移
    from src.app import _migrate_snapshots_url_based_if_needed
    _migrate_snapshots_url_based_if_needed()

    # 迁移后
    con = sqlite3.connect(db_path)
    post_marker = con.execute(
        "SELECT value FROM system_settings WHERE key='snapshots_migration_v3'"
    ).fetchall()
    post_rows = con.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
    post_active = con.execute("SELECT COUNT(*) FROM snapshots WHERE status='active'").fetchone()[0]
    con.close()

    assert post_marker and post_marker[0][0] == '1', f'迁移后应写 marker,实际={post_marker}'
    # 4 行 → 3 行(删了 1 个 active 重复)
    assert post_rows == 3, f'应删 1 个重复 active 行,rows 应=3,实际={post_rows}'
    assert post_active == 3
    print(f'迁移后: rows={post_rows} active={post_active} marker={post_marker[0][0]}')

    # path_id 应该被重算成 MD5(URL)[:12]
    con = sqlite3.connect(db_path)
    for r in con.execute("SELECT source_url, path_id FROM snapshots ORDER BY id").fetchall():
        url = r[0]
        expected_pid = __import__('hashlib').md5(url.encode()).hexdigest()[:12]
        assert r[1] == expected_pid, f'path_id 没重算: url={url} got={r[1]} expected={expected_pid}'
        print(f'  url={url}  path_id={r[1]} ✓')
    con.close()

    # 幂等 — 再调一次应该 noop
    _migrate_snapshots_url_based_if_needed()
    con2 = sqlite3.connect(db_path)
    rows2 = con2.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
    assert rows2 == 3, f'幂等失败: rows={rows2}'
    print(f'幂等再跑: rows={rows2} (不变)')

    shutil.rmtree(tmp)
    print('=== 用户升级场景 PASS ===')


def test_user_already_migrated_noop():
    """dev DB 已经有 marker → 启动期跑应该 noop,不打印乱七八糟的"开始迁移"日志"""
    src_db = '/root/nsfocus-monitor/data/nsfocus_monitor.db'
    bak = '/tmp/test_already_migrated.db'
    shutil.copy(src_db, bak)

    import src.models.database as db_mod
    db_mod.DB_PATH = bak
    if hasattr(db_mod._local, 'conn'):
        db_mod._local.conn = None
    db_mod.get_db()

    # 确认有 marker
    con = sqlite3.connect(bak)
    pre = con.execute("SELECT value FROM system_settings WHERE key='snapshots_migration_v3'").fetchall()
    assert pre and pre[0][0] == '1', f'备份 DB 没 marker,测试无意义: {pre}'
    pre_rows = con.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
    con.close()

    from src.app import _migrate_snapshots_url_based_if_needed
    _migrate_snapshots_url_based_if_needed()

    con = sqlite3.connect(bak)
    post_rows = con.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
    con.close()
    assert pre_rows == post_rows, f'有 marker 的 DB 被改了 rows: {pre_rows} -> {post_rows}'
    print(f'已有 marker: rows={pre_rows} -> {post_rows} (不变)')

    os.remove(bak)
    print('=== 已迁移 DB noop PASS ===')


def test_user_safety_when_import_fails():
    """如果迁移模块 import 失败(monkeypatch 模拟),启动期不能崩,
    只能 warn 跳过 — 服务必须能起来"""
    import src.models.database as db_mod
    import builtins

    orig_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith('scripts.migrate_snapshots'):
            raise ImportError('simulated: scripts/ not in path on this user machine')
        return orig_import(name, *args, **kwargs)

    builtins.__import__ = fake_import
    try:
        from src.app import _migrate_snapshots_url_based_if_needed
        _migrate_snapshots_url_based_if_needed()  # 必须不抛
        print('=== import 失败时 PASS ===')
    finally:
        builtins.__import__ = orig_import


if __name__ == '__main__':
    test_user_upgrade_auto_migrate()
    print()
    test_user_already_migrated_noop()
    print()
    test_user_safety_when_import_fails()