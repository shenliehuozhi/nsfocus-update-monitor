"""快照保留清理：分组保留、外键子表先删、引用保护与幂等。"""

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    import src.models.database as db_mod

    try:
        db_mod.close_db()
    except Exception:
        pass
    monkeypatch.setattr(db_mod, 'DB_PATH', str(tmp_path / 'nsfocus_monitor.db'))
    if hasattr(db_mod._local, 'conn'):
        monkeypatch.setattr(db_mod._local, 'conn', None)
    db_mod.get_db()

    from src.models import init_all_tables
    init_all_tables()

    from src.models.database import execute
    execute("INSERT OR IGNORE INTO content_sources (id, name, source_type, is_active) "
            "VALUES (1, 'WAF', 'nsfocus', 1)")
    execute("INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'u', 'x')")
    execute("INSERT OR IGNORE INTO subscription_rules (id, user_id, name, enabled) "
            "VALUES (1, 1, 'r', 1)")
    return db_mod


def _add_snapshot(sid, published, path_id='p1'):
    from src.models.database import execute
    execute(
        "INSERT INTO snapshots (id, source_id, product_name, version_branch, package_type, "
        "file_name, md5_hash, published_at, status, source_url, path_id) "
        "VALUES (?, 1, 'WAF', 'V6.0', 'sys', ?, ?, ?, 'superseded', 'u', ?)",
        (sid, f'f{sid}.bin', f'md5-{sid}', published, path_id),
    )


def _add_children(sid):
    from src.models.database import execute
    execute("INSERT INTO snapshot_cve (snapshot_id, cve_id) VALUES (?, ?)",
            (sid, 'CVE-2026-1000'))
    execute("INSERT INTO cve_scan_state (snapshot_id, desc_hash) VALUES (?, ?)",
            (sid, f'h{sid}'))


def test_cleanup_deletes_children_before_snapshots_and_keeps_referenced(db):
    from src.core.scheduler import cleanup_snapshots
    from src.models.database import execute, query

    for sid in range(1, 7):
        _add_snapshot(sid, f'2026-11-0{sid}T00:00:00')
        _add_children(sid)
    # 被延迟队列 / 投递历史引用的快照必须保留，不能触发外键失败
    execute("INSERT INTO delayed_queue (snapshot_id, rule_id, push_after) "
            "VALUES (2, 1, '2026-11-06T00:00:00')")
    execute("INSERT INTO delivery_log (snapshot_id, channel_type, channel_name) "
            "VALUES (3, 'test', 'c')")

    stats = cleanup_snapshots(per_group=2)

    remaining = {r['id'] for r in query("SELECT id FROM snapshots")}
    assert remaining == {2, 3, 5, 6}
    assert stats == {'before': 6, 'deleted': 2, 'after': 4, 'per_group': 2}
    assert {r['snapshot_id'] for r in query("SELECT snapshot_id FROM snapshot_cve")} == remaining
    assert {r['snapshot_id'] for r in query("SELECT snapshot_id FROM cve_scan_state")} == remaining


def test_cleanup_groups_legacy_blank_path_id(db):
    from src.core.scheduler import cleanup_snapshots
    from src.models.database import query

    for sid in (11, 12, 13):
        _add_snapshot(sid, f'2026-10-{sid - 10:02d}T00:00:00', path_id='')
        _add_children(sid)

    stats = cleanup_snapshots(per_group=1)

    assert {r['id'] for r in query("SELECT id FROM snapshots")} == {13}
    assert stats['deleted'] == 2


def test_cleanup_keeps_each_group_and_is_idempotent(db):
    from src.core.scheduler import cleanup_snapshots
    from src.models.database import execute, query

    for sid, path in ((21, 'pA'), (22, 'pA'), (23, 'pA'),
                      (24, 'pB'), (25, 'pB'), (26, 'pB')):
        _add_snapshot(sid, f'2026-09-{sid - 20:02d}T00:00:00', path_id=path)

    first = cleanup_snapshots(per_group=2)
    assert first['deleted'] == 2
    assert {r['id'] for r in query("SELECT id FROM snapshots")} == {22, 23, 25, 26}

    second = cleanup_snapshots(per_group=2)
    assert second['deleted'] == 0

    execute("INSERT OR REPLACE INTO system_settings (key, value) "
            "VALUES ('snapshots_per_group', '1')")
    third = cleanup_snapshots()
    assert third['per_group'] == 1
    assert {r['id'] for r in query("SELECT id FROM snapshots")} == {23, 26}
