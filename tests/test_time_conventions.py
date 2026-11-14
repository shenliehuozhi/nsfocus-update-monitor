"""时间口径统一：UTC 存储 + CST(UTC+8) 业务边界。

覆盖跨天/跨月/跨年/闰月边界，以及 delayed_queue 从本地时间到 UTC 的一次性迁移。
这些是纯函数与 SQL 语义测试，不触发任何通知。
"""
import os
import time as _time
from datetime import datetime, timedelta, timezone

import pytest

from src.core.timeutil import (
    CST,
    cst_month_bounds,
    cst_now,
    parse_ts,
    to_cst,
    to_utc,
    utc_iso,
)


# ── 解析 / 归一化 ────────────────────────────────────────────


@pytest.mark.parametrize('text', [
    '2026-11-12 03:04:05',
    '2026-11-12T03:04:05',
    '2026-11-12T03:04:05Z',
    '2026-11-12T03:04:05+00:00',
])
def test_parse_naive_and_utc_forms_are_equivalent(text):
    dt = parse_ts(text)
    assert dt is not None
    assert to_utc(dt) == datetime(2026, 11, 12, 3, 4, 5, tzinfo=timezone.utc)


def test_parse_offset_string_converts_to_utc():
    dt = parse_ts('2026-11-12T11:04:05+08:00')
    assert to_utc(dt) == datetime(2026, 11, 12, 3, 4, 5, tzinfo=timezone.utc)


@pytest.mark.parametrize('value', [None, '', '   ', 'not-a-time'])
def test_parse_invalid_returns_none(value):
    assert parse_ts(value) is None


def test_utc_iso_normalizes_to_naive_utc():
    assert utc_iso('2026-11-12T11:04:05+08:00') == '2026-11-12T03:04:05'
    assert utc_iso('2026-11-12 03:04:05') == '2026-11-12T03:04:05'


# ── 跨天 / 固定 UTC+8（无夏令时）────────────────────────────


def test_cst_is_fixed_utc_plus_8_across_dst_dates():
    # 欧美夏令时切换日不应影响 CST 偏移
    for utc in (
        datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc),
    ):
        assert to_cst(utc).utcoffset() == timedelta(hours=8)
        assert to_cst(utc).hour == (utc.hour + 8) % 24


def test_cross_day_boundary_at_cst_midnight():
    before = to_cst(parse_ts('2026-11-12T15:59:59Z'))
    after = to_cst(parse_ts('2026-11-12T16:00:00Z'))
    assert before.strftime('%Y-%m-%d %H:%M:%S') == '2026-11-12 23:59:59'
    assert after.strftime('%Y-%m-%d %H:%M:%S') == '2026-11-13 00:00:00'


def test_roundtrip_utc_cst_utc():
    original = datetime(2026, 11, 12, 3, 4, 5, tzinfo=timezone.utc)
    assert to_utc(to_cst(original)) == original


# ── 月度边界 ────────────────────────────────────────────────


@pytest.mark.parametrize('year,month,expected_start,expected_end', [
    # 12 月跨年
    (2026, 12, '2026-11-30 16:00:00', '2026-12-31 16:00:00'),
    (2027, 1, '2026-12-31 16:00:00', '2027-01-31 16:00:00'),
    # 平年 2 月
    (2027, 2, '2027-01-31 16:00:00', '2027-02-28 16:00:00'),
    # 闰年 2 月
    (2028, 2, '2028-01-31 16:00:00', '2028-02-29 16:00:00'),
    (2026, 11, '2026-10-31 16:00:00', '2026-11-30 16:00:00'),
])
def test_cst_month_bounds(year, month, expected_start, expected_end):
    assert cst_month_bounds(year, month) == (expected_start, expected_end)


def test_cst_month_bounds_include_first_cst_instant():
    start, end = cst_month_bounds(2026, 11)
    # 2026-11-01 00:00 CST == 2026-10-31 16:00 UTC
    assert to_utc(parse_ts(start)) == datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc)
    assert to_utc(parse_ts(end)) - to_utc(parse_ts(start)) == timedelta(days=30)


@pytest.mark.parametrize('month', [0, 13, -1])
def test_cst_month_bounds_rejects_invalid_month(month):
    with pytest.raises(ValueError):
        cst_month_bounds(2026, month)


def test_digest_period_key_uses_cst_boundary():
    from src.models.subscription import digest_period_key
    # UTC 10/31 16:00 已是 CST 11/01，应归入 11 月
    assert digest_period_key('monthly', datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc)) == '2026-11'
    assert digest_period_key('quarterly', datetime(2027, 1, 31, 16, 0, tzinfo=timezone.utc)) == '2027-Q1'
    assert digest_period_key('monthly', datetime(2026, 11, 1, 0, 0, tzinfo=CST)) == '2026-11'


def test_cst_now_is_aware_cst():
    now = cst_now()
    assert now.utcoffset() == timedelta(hours=8)


def test_next_window_push_time_returns_utc():
    from unittest.mock import patch

    from src.detector.change import compute_next_window_push_time

    rule = {
        'delay_strategy': 'window',
        'window_config': {'days': [], 'start': '09:00', 'end': '18:00'},
    }
    # CST 周一 08:00 尚未开窗，下一次 09:00 CST == 01:00 UTC
    with patch('src.detector.change.cst_now',
               return_value=datetime(2026, 11, 9, 8, 0, tzinfo=CST)):
        assert compute_next_window_push_time(rule) == '2026-11-09 01:00:00'


# ── delayed_queue 到期判断（UTC）────────────────────────────


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

    from src.models.database import execute
    execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'u', 'x')")
    execute("""INSERT INTO snapshots
               (id, source_id, product_name, version_branch, package_type,
                package_version, file_name, md5_hash, status)
               VALUES (1, 1, 'WAF', 'V6.0', 'sys', 'V6.0.9', 'a.bin', 'md5', 'active')""")
    execute("INSERT INTO subscription_rules (id, user_id, name, enabled) VALUES (3, 1, 'R', 1)")
    return db_file


def _seed_queue(push_after, next_attempt_at=''):
    from src.models.database import execute
    execute("""INSERT INTO delayed_queue
               (snapshot_id, rule_id, push_after, next_attempt_at, chain_json, attempts)
               VALUES (1, 3, ?, ?, '[]', 0)""", (push_after, next_attempt_at))


def _utc_str(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).strftime('%Y-%m-%d %H:%M:%S')


def test_due_queue_compares_utc(fresh_db):
    from src.models.subscription import get_due_items
    _seed_queue(_utc_str(minutes=-5))
    _seed_queue(_utc_str(hours=1))
    due = get_due_items()
    assert len(due) == 1


def test_due_queue_normalizes_legacy_t_separator(fresh_db):
    from src.models.subscription import get_due_items
    _seed_queue(_utc_str(minutes=-5).replace(' ', 'T'))
    assert len(get_due_items()) == 1


def test_due_queue_respects_utc_retry_backoff(fresh_db):
    from src.models.subscription import get_due_items
    _seed_queue(_utc_str(minutes=-5), next_attempt_at=_utc_str(hours=1))
    _seed_queue(_utc_str(minutes=-5), next_attempt_at=_utc_str(minutes=-1))
    assert len(get_due_items()) == 1


def test_mark_pushed_writes_utc(fresh_db):
    from src.models.database import query
    from src.models.subscription import get_due_items, mark_pushed

    _seed_queue(_utc_str(minutes=-5))
    due = get_due_items()
    mark_pushed(due[0]['id'])
    row = query("SELECT * FROM delayed_queue WHERE id = ?", (due[0]['id'],))[0]
    pushed = datetime.strptime(row['pushed_at'], '%Y-%m-%d %H:%M:%S')
    assert abs((datetime.utcnow() - pushed).total_seconds()) < 5


# ── 一次性迁移：本地时间 → UTC ──────────────────────────────


def test_delayed_queue_migration_converts_local_to_utc(fresh_db, monkeypatch):
    import src.core.timeutil as tu
    from src.models.database import execute, query

    execute("DELETE FROM system_settings WHERE key = ?", (tu._QUEUE_UTC_FLAG,))
    _seed_queue('2026-01-01 12:00:00')

    old_tz = os.environ.get('TZ')
    os.environ['TZ'] = 'CST-8'  # POSIX 写法：UTC+8
    _time.tzset()
    try:
        assert tu.migrate_delayed_queue_to_utc() is True
    finally:
        if old_tz is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = old_tz
        _time.tzset()

    row = query("SELECT push_after FROM delayed_queue LIMIT 1")[0]
    assert row['push_after'] == '2026-01-01 04:00:00'
    flag = query("SELECT value FROM system_settings WHERE key = ?", (tu._QUEUE_UTC_FLAG,))[0]
    assert flag['value'] == '1'


def test_delayed_queue_migration_is_idempotent(fresh_db):
    import src.core.timeutil as tu
    from src.models.database import execute, query

    execute("DELETE FROM system_settings WHERE key = ?", (tu._QUEUE_UTC_FLAG,))
    assert tu.migrate_delayed_queue_to_utc() is True
    _seed_queue('2026-01-01 04:00:00')
    assert tu.migrate_delayed_queue_to_utc() is False
    row = query("SELECT push_after FROM delayed_queue LIMIT 1")[0]
    assert row['push_after'] == '2026-01-01 04:00:00'
