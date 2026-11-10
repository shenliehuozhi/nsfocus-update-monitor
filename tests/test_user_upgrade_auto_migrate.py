"""升级场景测试:模拟用户机器上的老 DB,启动期自动迁移。

不读生产库:两种场景(老 schema 无 marker / 已迁移有 marker)都用临时库自造。
"""
import os
import sys
import hashlib
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_MARKER_KEY = 'snapshots_migration_v3'

_SCHEMA = """
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
CREATE TABLE system_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
"""


def _point_db_at(monkeypatch, db_path):
    """把 database 模块指向测试库;DB_PATH 由 monkeypatch 自动还原,
    thread-local 连接手工作废。"""
    import src.models.database as db_mod
    monkeypatch.setattr(db_mod, 'DB_PATH', str(db_path))
    if hasattr(db_mod._local, 'conn'):
        db_mod._local.conn = None
    db_mod.get_db()
    return db_path


def test_user_upgrade_auto_migrate(monkeypatch, tmp_path):
    """模拟"用户升级 exe 后的第一次启动":
    - 老 DB 有旧算法 path_id(同物理包多 active 行,会假通知)
    - 没有 snapshots_migration_v3 marker
    - 启动期调 _migrate_snapshots_url_based_if_needed 应该自动迁移 + 写 marker
    - 再调一次应该 noop
    """
    db_path = str(tmp_path / 'nsfocus_monitor.db')
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    con.executescript("""
INSERT INTO snapshots (source_id, source_url, file_name, md5_hash, path_id, status) VALUES
    (1, 'http://x.com/p/a.zip', 'a.zip', 'm1', 'old-chain-text-a', 'active'),
    (1, 'http://x.com/p/a.zip', 'a.zip', 'm1', 'old-chain-text-b', 'active'),
    (1, 'http://x.com/p/b.zip', 'b.zip', 'm2', 'old-chain-text-c', 'active'),
    (2, 'http://y.com/q/c.zip', 'c.zip', 'm3', 'old-chain-text-d', 'active');
""")
    con.commit()
    con.close()

    _point_db_at(monkeypatch, db_path)

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
        expected_pid = hashlib.md5(url.encode()).hexdigest()[:12]
        assert r[1] == expected_pid, f'path_id 没重算: url={url} got={r[1]} expected={expected_pid}'
        print(f'  url={url}  path_id={r[1]} ✓')
    con.close()

    # 幂等 — 再调一次应该 noop
    _migrate_snapshots_url_based_if_needed()
    con2 = sqlite3.connect(db_path)
    rows2 = con2.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
    assert rows2 == 3, f'幂等失败: rows={rows2}'
    print(f'幂等再跑: rows={rows2} (不变)')

    print('=== 用户升级场景 PASS ===')


def test_user_already_migrated_noop(monkeypatch, tmp_path):
    """已有 marker 的库 → 启动期再跑必须 noop,一行都不动。

    原先这个用例 copy 一份 19MB 生产库来判断 marker:绑死开发机、依赖线上行
    不被保留期清理删掉,而且 CI 上压根没有这个文件。现在自造已迁移库。
    """
    db_path = str(tmp_path / 'nsfocus_monitor.db')
    urls = ['http://x.com/p/a.zip', 'http://x.com/p/b.zip', 'http://y.com/q/c.zip']
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    con.executemany(
        "INSERT INTO snapshots (source_id, source_url, file_name, md5_hash, path_id, status) "
        "VALUES (?, ?, ?, ?, ?, 'active')",
        [(1, u, os.path.basename(u), f'm{i}', hashlib.md5(u.encode()).hexdigest()[:12])
         for i, u in enumerate(urls)],
    )
    con.execute("INSERT INTO system_settings (key, value) VALUES (?, '1')", (_MARKER_KEY,))
    con.commit()
    pre_rows = con.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
    pre_ids = [r[0] for r in con.execute('SELECT id FROM snapshots ORDER BY id').fetchall()]
    con.close()
    assert pre_rows == 3, f'自造库行数不对: {pre_rows}'

    _point_db_at(monkeypatch, db_path)

    # marker 必须能用产品自己那套 query() 读到 —— 否则 _migrate_... 会走
    # "marker check skipped" 异常分支 return,noop 断言就成了白测
    from src.models.database import query
    mk = query(f"SELECT value FROM system_settings WHERE key='{_MARKER_KEY}'")
    assert mk and mk[0]['value'] == '1', f'marker 没被产品代码读到: {mk}'

    from src.app import _migrate_snapshots_url_based_if_needed
    _migrate_snapshots_url_based_if_needed()

    con = sqlite3.connect(db_path)
    post_rows = con.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0]
    post_ids = [r[0] for r in con.execute('SELECT id FROM snapshots ORDER BY id').fetchall()]
    post_pids = [r[0] for r in con.execute('SELECT path_id FROM snapshots ORDER BY id').fetchall()]
    con.close()
    assert pre_rows == post_rows, f'有 marker 的 DB 被改了 rows: {pre_rows} -> {post_rows}'
    assert pre_ids == post_ids, f'有 marker 的 DB 行被增删: {pre_ids} -> {post_ids}'
    for u, pid in zip(urls, post_pids):
        assert pid == hashlib.md5(u.encode()).hexdigest()[:12], f'path_id 被动过: {u} -> {pid}'
    print(f'已有 marker: rows={pre_rows} -> {post_rows} (不变)')
    print('=== 已迁移 DB noop PASS ===')


def test_user_safety_when_import_fails(monkeypatch, tmp_path):
    """如果迁移模块 import 失败(模拟用户机器上 scripts/ 不在包内),启动期不能崩,
    只能 warn 跳过 — 服务必须能起来。

    指向临时库:marker 缺失才会真的走到 import 那一步,同时保证不碰生产库。
    """
    import builtins

    db_path = str(tmp_path / 'nsfocus_monitor.db')
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    con.commit()
    con.close()
    _point_db_at(monkeypatch, db_path)

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
    # 用例依赖 monkeypatch / tmp_path fixture,直接 python3 跑等价于 pytest 单文件
    import pytest
    sys.exit(pytest.main([__file__, '-q']))