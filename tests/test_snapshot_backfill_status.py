"""历史包回填去噪 + 同路径状态语义回归。

背景：全表解析上线后，规则历史页里从未入过库的历史包被一次性 INSERT，
first_seen == last_seen → detector 判 NEW → 客户收到十几年前的「新包」推送
（线上 id 10354：发布时间 2025-09-08、首次发现 2026-11-10）。

本文件锁定：
1. 发布时间早于「开始监控该路径」的历史包以 superseded 入库，
   first_seen 回填为 published_at，不会被判 NEW；
2. 与最新 active 包同批（≤7 天）的新包仍全部 active + NEW；
3. 第二查（superseded 命中）参数不再错位（旧代码把 page_hash 写进 status，
   触发 CHECK constraint failed），老包不会被误复活；
4. 保存最新包时，同路径更老的 active 行降级为 superseded。
"""
import hashlib
import sqlite3

import pytest

from src.collectors.base import UnifiedContentItem
from src.detector.change import run_detection
from src.models.snapshot import get_snapshot, save_snapshot

URL = 'https://update.nsfocus.com/update/listWafFTDetail/v/WAF-HG-sys'
PATH_ID = hashlib.md5(URL.encode()).hexdigest()[:12]


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    import src.models.database as db_mod

    db_file = tmp_path / 'nsfocus_monitor.db'
    monkeypatch.setattr(db_mod, 'DB_PATH', str(db_file))
    if hasattr(db_mod._local, 'conn'):
        monkeypatch.setattr(db_mod._local, 'conn', None)
    db_mod.get_db()

    from src.models import init_all_tables
    init_all_tables()

    con = sqlite3.connect(str(db_file))
    con.execute("DELETE FROM content_sources")
    con.execute(
        """INSERT INTO content_sources
           (id, name, source_type, entry_url)
           VALUES (1, 'WAF', 'nsfocus', '')""")
    con.commit()
    con.close()
    return db_file


def _insert_row(file_name, md5, published_at, first_seen, status='active'):
    from src.models.database import execute
    execute(
        """INSERT INTO snapshots
           (source_id, product_name, version_branch, package_type, file_name,
            package_version, md5_hash, file_size, description_raw, urgency,
            download_id, published_at, first_seen_at, last_seen_at, status,
            source_url, path_id)
           VALUES (1, 'WAF', 'V6.0.8', '系统升级', ?, ?, ?, 1, '', 'normal',
                   0, ?, ?, ?, ?, ?, ?)""",
        (file_name, file_name, md5, published_at, first_seen, first_seen,
         status, URL, PATH_ID))


def _item(file_name, md5, published_at):
    return UnifiedContentItem(
        source_id=1,
        source_type='nsfocus',
        product_name='WAF',
        version_branch='V6.0.8',
        package_type='系统升级',
        file_name=file_name,
        package_version=file_name,
        md5_hash=md5,
        file_size=1,
        published_at=published_at,
        source_url=URL,
        path_id=PATH_ID,
    )


@pytest.fixture(autouse=True)
def _stub_chain(monkeypatch):
    monkeypatch.setattr(
        'src.core.scheduler._get_chain',
        lambda *a, **kw: [['WAF', 'V6.0.8', '系统升级']])


def test_historical_backfill_is_superseded_and_not_new(fresh_db):
    """线上 10354 场景：路径已监控，比最新包早 13 个月 → 不 active、不推送。"""
    _insert_row('update.WAF.V6.0R08F01H05.s.90023.x86_64.bin', 'h' * 32,
                '2026-10-23T15:47:40', '2026-10-23 18:40:27')

    item = _item('update.WAF.V6.0R08F01SP02.72179.HG.x86_64.bin', 'a' * 32,
                 '2025-09-08T07:20:34')
    result = run_detection(1, [item])

    assert result.errors == []
    assert result.new_items == []
    assert result.unchanged_count == 1

    sid = save_snapshot(item.to_snapshot_dict())
    snap = get_snapshot(sid)
    assert snap['status'] == 'superseded'
    assert snap['first_seen_at'] == '2025-09-08 07:20:34'
    assert snap['first_seen_at'] != snap['last_seen_at']


def test_historical_backfill_on_watched_path_stays_superseded(fresh_db):
    """回填行每轮都会再次被采到，第二查不能把它复活成 active。"""
    _insert_row('H05.bin', 'h' * 32, '2026-10-23T15:47:40', '2026-10-23 18:40:27')
    _insert_row('SP02.bin', 'a' * 32, '2025-09-08T07:20:34', '2025-09-08 07:20:34',
                status='superseded')

    sid = save_snapshot(_item('SP02.bin', 'a' * 32, '2025-09-08T07:20:34').to_snapshot_dict())
    snap = get_snapshot(sid)
    assert snap['status'] == 'superseded'
    assert snap['first_seen_at'] == '2025-09-08 07:20:34'


def test_superseded_row_resurrects_only_when_it_is_current(fresh_db):
    """同 md5 的 superseded 行重新出现：仍是当前批次 → active。"""
    _insert_row('OLD.bin', 'o' * 32, '2026-01-01T00:00:00', '2026-01-01 00:00:00',
                status='superseded')
    _insert_row('BASE.bin', 'b' * 32, '2026-11-10T04:00:00', '2026-11-10 04:00:00')

    sid = save_snapshot(_item('OLD.bin', 'o' * 32, '2026-11-10T05:00:00').to_snapshot_dict())
    assert get_snapshot(sid)['status'] == 'active'


def test_same_batch_packages_all_active_and_new(fresh_db):
    """一次发布 3 个包（间隔 1 小时）→ 3 个都 active、都判 NEW。"""
    _insert_row('OLD.bin', 'o' * 32, '2026-01-01T00:00:00', '2026-01-01 00:00:00')

    items = [
        _item('N1.bin', '1' * 32, '2026-11-10T04:00:00'),
        _item('N2.bin', '2' * 32, '2026-11-10T03:00:00'),
        _item('N3.bin', '3' * 32, '2026-11-10T02:00:00'),
    ]
    result = run_detection(1, items)

    assert len(result.new_items) == 3
    assert result.errors == []
    statuses = {get_snapshot(sid)['file_name']: get_snapshot(sid)['status']
                for sid, _snap in result.new_items}
    assert statuses == {'N1.bin': 'active', 'N2.bin': 'active', 'N3.bin': 'active'}

    # 比最新包早远超一个批次的旧 active 行被降级
    from src.models.database import query
    old = query("SELECT status FROM snapshots WHERE file_name='OLD.bin'")[0]
    assert old['status'] == 'superseded'


def test_new_path_old_backlog_is_not_pushed(fresh_db):
    """首次接入一个只有历史包的路径：整页都不推（最新那个也只是 active 不 NEW）。"""
    from src.models.database import query

    newest = _item('B-2020.bin', 'b' * 32, '2020-01-01T00:00:00')
    oldest = _item('A-2019.bin', 'a' * 32, '2019-01-01T00:00:00')

    # 绿盟列表页按发布时间新→旧排列，采集窗口保持 DOM 顺序
    result = run_detection(1, [newest, oldest])

    assert result.new_items == []
    statuses = {
        r['file_name']: r['status']
        for r in query("SELECT file_name, status FROM snapshots ORDER BY file_name")
    }
    assert statuses == {'A-2019.bin': 'superseded', 'B-2020.bin': 'active'}


def test_new_path_recent_release_is_new(fresh_db):
    """首次接入路径时，7 天内发布的包仍算新包并推送。"""
    from datetime import datetime, timedelta

    recent = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%S')
    result = run_detection(1, [_item('RECENT.bin', 'r' * 32, recent)])

    assert [s['file_name'] for _sid, s in result.new_items] == ['RECENT.bin']
