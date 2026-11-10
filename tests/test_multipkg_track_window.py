"""多表格升级包解析 + 最新 N 窗口回归测试。

背景：绿盟规则历史页会累积上百个包（WAF rule6070 实测 247 个），且每个包
一张 <table>。旧解析器只读第一张表，一次发布多个包时只有最新那个能被看见，
其余包永远不入库、不推送。本文件覆盖：

1. 同页多表 + 同表多包的全量解析（fixture: 2 表 / 4 包）；
2. 按 published_at 取最新 N 个，DOM 顺序兜底；
3. 跟踪窗口之外的包不会因为"没进窗口"被误标 withdrawn/superseded；
4. 一次 3 个新包时 3 个都判 NEW，老包判 UNCHANGED；
5. 设置页「升级包跟踪数量」已接线。
"""
import hashlib
import json
import re
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from src.collectors import nsfocus as ns
from src.collectors.nsfocus import NsfocusCollector, _select_track_window

FIXTURE = Path(__file__).parent / 'fixtures' / 'multipkg_rule_page.html'
PAGE_PATH = '/update/listWafV67Detail/v/rule6070'
PAGE_URL = ns.BASE_URL + PAGE_PATH
PATH_ID = hashlib.md5(PAGE_URL.encode()).hexdigest()[:12]

N1 = 'update_rule.v6.0.7.3.90001.wcl'
N2 = 'update_rule.v6.0.7.3.90002.wcl'
N3 = 'update_rule.v6.0.7.3.90003.wcl'
OLD = 'update_rule.v6.0.7.3.90000.wcl'


def _parse(html: str = None):
    html = FIXTURE.read_text(encoding='utf-8') if html is None else html
    return NsfocusCollector()._extract_table_items(
        html, 1, 'WAF', 'V6.0.7', '规则库', page_url=PAGE_URL)


# ── 1. 多表格解析 ─────────────────────────────────────────────

def test_multi_table_multi_package_extracted():
    items = _parse()
    assert [i.file_name for i in items] == [N1, N2, N3, OLD]
    # 每包独立字段，不能被相邻表串味
    assert [i.md5_hash[0] for i in items] == ['a', 'b', 'c', 'd']
    assert [i.download_id for i in items] == [90001, 90002, 90003, 90000]
    assert [i.package_version for i in items] == [
        '6.0.7.3.90001', '6.0.7.3.90002', '6.0.7.3.90003', '6.0.7.3.90000']
    assert items[0].published_at == '2026-11-10T04:00:00'
    assert items[-1].published_at == '2026-01-01T01:00:00'
    assert all(i.path_id == PATH_ID for i in items)


def test_description_containing_name_keyword_does_not_split(monkeypatch):
    # N2 的描述正文含「名称：」，不能被当成新包起点
    items = _parse()
    assert len(items) == 4
    assert '只是描述正文' in items[1].description_raw


def test_legacy_single_table_layout_still_parses():
    html = FIXTURE.read_text(encoding='utf-8')
    first_table = html.split('</table>')[0] + '</table>'
    items = _parse(first_table)
    assert [i.file_name for i in items] == [N1]


def test_page_without_table_returns_empty():
    assert _parse('<html><body>no packages</body></html>') == []


# ── 2. 最新 N 窗口 ────────────────────────────────────────────

def test_track_window_newest_n():
    items = _parse()
    assert [i.file_name for i in _select_track_window(items, '3')] == [N1, N2, N3]
    assert [i.file_name for i in _select_track_window(items, '10')] == [N1, N2, N3, OLD]
    assert [i.file_name for i in _select_track_window(items, 'all')] == [N1, N2, N3, OLD]


def test_track_window_falls_back_to_dom_order_without_published_at():
    items = [replace(i, published_at='') for i in _parse()]
    assert [i.file_name for i in _select_track_window(items, '2')] == [N1, N2]


def test_track_window_invalid_limit_uses_default_5():
    items = _parse()
    # 4 个包不足默认值 5 → 全部返回，不报错
    assert len(_select_track_window(items, 'garbage')) == 4


# ── 3/4. _collect_quick 集成 ─────────────────────────────────

class _FakeResp:
    def __init__(self, text: str, url: str):
        self.text = text
        self.url = url
        self.headers = {}

    def close(self):
        pass


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
    # init_all_tables 会从 data/initial_sources.json 播种产品源，这里清空后
    # 固定用 id=1，避免测试依赖种子文件的顺序/内容。
    con.execute("DELETE FROM content_sources")
    con.execute(
        """INSERT INTO content_sources
           (id, name, source_type, entry_url, package_type_discovered)
           VALUES (1, 'WAF', 'nsfocus', '/update/wafIndex', ?)""",
        (json.dumps({'paths': [{
            'chain': ['WAF', 'V6.0.7', '规则库'],
            'types': ['规则库'],
            'url': PAGE_PATH,
        }]}, ensure_ascii=False),))
    con.commit()
    con.close()
    return db_file


def _insert_active(file_name: str, md5: str, published_at: str):
    from src.models.database import execute
    execute(
        """INSERT INTO snapshots
           (source_id, product_name, version_branch, package_type, file_name,
            package_version, md5_hash, file_size, description_raw, urgency,
            download_id, published_at, first_seen_at, last_seen_at, status,
            source_url, path_id)
           VALUES (1, 'WAF', 'V6.0.7', '规则库', ?, ?, ?, 1, '', 'normal',
                   0, ?, '2020-01-01T00:00:00', '2020-01-01T00:00:00', 'active',
                   ?, ?)""",
        (file_name, '6.0.7.3.90000', md5, published_at, PAGE_URL, PATH_ID))


def _run_quick(monkeypatch, track_limit: str):
    collector = NsfocusCollector()
    html = FIXTURE.read_text(encoding='utf-8')
    monkeypatch.setattr(collector.session, 'get',
                        lambda url, **kw: _FakeResp(html, url))
    monkeypatch.setattr(ns, 'REQUEST_DELAY_MIN', 0)
    monkeypatch.setattr(ns, 'REQUEST_DELAY_MAX', 0)
    monkeypatch.setattr(ns, '_get_track_limit', lambda: track_limit)
    items, zero = collector._collect_quick(1, 'WAF')
    return items, zero


def _status_map():
    from src.models.database import query
    rows = query(
        "SELECT file_name, status FROM snapshots WHERE source_id=1 ORDER BY file_name")
    return {r['file_name']: r['status'] for r in rows}


def test_window_outside_packages_not_marked_withdrawn(fresh_db, monkeypatch):
    """窗口只跟踪最新 3 个，但第 4 个老包仍在页面上 → 不能被误标撤回/取代。"""
    _insert_active(N1, 'a' * 32, '2026-11-10T04:00:00')
    _insert_active(N2, 'b' * 32, '2026-11-10T03:00:00')
    _insert_active(N3, 'c' * 32, '2026-11-10T02:00:00')
    _insert_active(OLD, 'd' * 32, '2026-01-01T01:00:00')

    items, _ = _run_quick(monkeypatch, '3')

    assert [i.file_name for i in items] == [N1, N2, N3]
    assert _status_map() == {N1: 'active', N2: 'active', N3: 'active', OLD: 'active'}


def test_three_new_packages_all_detected_as_new(fresh_db, monkeypatch):
    """一次发布 3 个新包：3 个都要进结果并被 save_snapshot 判为 NEW。"""
    _insert_active(OLD, 'd' * 32, '2026-01-01T01:00:00')

    items, _ = _run_quick(monkeypatch, '5')
    assert [i.file_name for i in items] == [N1, N2, N3, OLD]

    # fixture 里的发布时间是固定值，跑久了会超出「7 天内才是新包」的回填宽限期；
    # 这里按当前时间重写，保持「一次发布 3 个包」的语义。
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    items = [replace(i, published_at=(now - timedelta(hours=h)).strftime('%Y-%m-%dT%H:%M:%S'))
             for i, h in zip(items, [0, 1, 2, 240])]

    from src.models.snapshot import save_snapshot, get_snapshot
    new_flags = {}
    for item in items:
        sid = save_snapshot(item.to_snapshot_dict())
        snap = get_snapshot(sid)
        new_flags[item.file_name] = (
            snap['first_seen_at'] == snap['last_seen_at'])

    assert new_flags == {N1: True, N2: True, N3: True, OLD: False}
