"""推送汇总长度/频率回归。

背景：2026-11-11 00:10 一轮推了 24 条（20 成功 + 4 失败）后，企业微信汇总消息
超过 4096 字节被拒（errcode 40058）。三处根因：
1. wecom.py 系统通知分支不分片，整条硬发；
2. 汇总每组 3 行头，包多/chain 多时段头就占 3KB；
3. IM 节流只有 3s 最小间隔（=20 条/分钟），正好踩企微硬限，突发时第 21 条起失败。

另锁一个连带问题：clear_history 旧 SQL 把 sent_at IS NULL 的失败行每次清理都删掉。
"""
import time

import pytest

from src.core.event_handler import SUMMARY_MAX_ITEMS_PER_GROUP, _build_push_summary_message
from src.notifiers.base import NotificationMessage
from src.notifiers.wecom import WecomNotifier


# ============================================================================
# wecom 系统通知分片
# ============================================================================

class _OkResp:
    status_code = 200

    def json(self):
        return {'errcode': 0, 'errmsg': 'ok'}


def _summary(rule, chain_idx, n_ok, n_fail=0):
    items = [
        {'file_name': f'update.pkg.{chain_idx}.{i}.bin', 'success': True, 'error': ''}
        for i in range(n_ok)
    ]
    items += [
        {'file_name': f'update.pkg.{chain_idx}.bad{i}.bin', 'success': False,
         'error': 'Wecom error [1/1]: api freq out of limit'}
        for i in range(n_fail)
    ]
    return {
        'total': len(items),
        'success': n_ok,
        'failed': n_fail,
        'rule_name': rule,
        'customer_name': '绿盟科技',
        'channel_type': 'wecom',
        'channel_name': '绿盟企业微信',
        'items': items,
        'pushed_at': '2026-11-10T16:10:10+00:00',
    }


def test_wecom_splits_oversized_system_notification(monkeypatch):
    """description_full（系统通知）超过 4096 时必须分片，而不是单条硬发。"""
    import src.notifiers.wecom as wecom_mod

    posts = []

    def fake_post(url, json=None, timeout=None):
        posts.append(json)
        return _OkResp()

    monkeypatch.setattr(wecom_mod.requests, 'post', fake_post)

    body = '\n'.join(f'  ✓ update.pkg.{i}.bin | 说明' for i in range(400))
    assert len(body.encode('utf-8')) > 4096

    notifier = WecomNotifier()
    result = notifier.send(
        NotificationMessage(title='绿盟监控 - 推送汇总', description_full=body),
        {'webhook_url': 'https://example.invalid/webhook'},
    )

    assert result.success
    assert len(posts) > 1, '超长系统通知应拆成多条'
    for payload in posts:
        assert len(payload['markdown']['content'].encode('utf-8')) <= 4000
    assert any('(2/' in p['markdown']['content'] for p in posts)


def test_wecom_short_system_notification_stays_single(monkeypatch):
    import src.notifiers.wecom as wecom_mod

    posts = []

    def fake_post(url, json=None, timeout=None):
        posts.append(json)
        return _OkResp()

    monkeypatch.setattr(wecom_mod.requests, 'post', fake_post)

    result = WecomNotifier().send(
        NotificationMessage(title='t', description_full='【推送汇总】本次无推送'),
        {'webhook_url': 'https://example.invalid/webhook'},
    )

    assert result.success
    assert len(posts) == 1
    assert posts[0]['markdown']['content'] == '【推送汇总】本次无推送'


# ============================================================================
# 汇总瘦身
# ============================================================================

def test_summary_of_live_batch_fits_wecom_limit():
    """复刻 00:10 那轮：13 组 / 24 条，压缩后应远低于 4096。"""
    summaries = []
    # 12 组各 1~3 条成功，外加 1 组 2 条失败
    shapes = [1, 1, 1, 1, 1, 1, 1, 3, 3, 3, 3, 3]
    for idx, n in enumerate(shapes):
        summaries.append(_summary('绿盟企业微信', idx, n))
    summaries.append(_summary('绿盟企业微信', 99, 0, n_fail=2))

    msg = _build_push_summary_message(summaries)
    assert len(msg.encode('utf-8')) < 4096
    # 每组只有一行头
    headers = [ln for ln in msg.split('\n') if ln.startswith('─ ')]
    assert len(headers) == len(summaries)


def test_summary_folds_long_success_list_but_keeps_failures():
    summary = _summary('r', 1, 20, n_fail=2)

    msg = _build_push_summary_message([summary])
    lines = msg.split('\n')

    folded = 20 - SUMMARY_MAX_ITEMS_PER_GROUP
    assert f'另有 {folded} 个成功' in msg
    assert msg.count('  ✗ ') == 2, '失败条目必须全量保留'
    assert msg.count('  ✓ ') == SUMMARY_MAX_ITEMS_PER_GROUP
    # 折叠提示在最后
    assert lines[-1].strip().startswith('… 另有')


# ============================================================================
# IM 节流滑动窗口
# ============================================================================

def test_throttle_im_enforces_per_minute_window(monkeypatch):
    import src.notifiers.router as router_mod

    router_mod._last_send.clear()
    router_mod._recent_sends.clear()

    now = time.time()
    router_mod._recent_sends['wecom'] = [
        now - 30 + i * 0.1 for i in range(router_mod._RATE_LIMIT_PER_MINUTE['wecom'])
    ]
    slept = []
    monkeypatch.setattr(router_mod.time, 'sleep', lambda s: slept.append(s))

    router_mod._throttle_im('wecom')

    assert slept, '窗口已满时应暂停到最早一条滑出 60s 窗口'
    assert 29 <= slept[0] <= 32


def test_throttle_im_under_window_no_sleep(monkeypatch):
    import src.notifiers.router as router_mod

    router_mod._last_send.clear()
    router_mod._recent_sends.clear()

    slept = []
    monkeypatch.setattr(router_mod.time, 'sleep', lambda s: slept.append(s))

    router_mod._throttle_im('wecom')
    router_mod._throttle_im('wecom')

    # 第一次不睡；第二次撞 3s 最小间隔才睡
    assert len(slept) == 1
    assert len(router_mod._recent_sends['wecom']) == 2


# ============================================================================
# clear_history 不再删失败行
# ============================================================================

@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    import sqlite3

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
        """INSERT INTO content_sources (id, name, source_type, entry_url)
           VALUES (1, 'WAF', 'nsfocus', '')""")
    con.execute(
        """INSERT INTO snapshots
           (id, source_id, product_name, version_branch, package_type,
            file_name, md5_hash, status)
           VALUES (1, 1, 'WAF', 'V6.0', 'sys', 'a.bin', 'md5', 'active')""")
    con.commit()
    con.close()
    return db_file


def test_clear_history_keeps_failed_rows(fresh_db):
    from src.models.database import execute, query
    from src.models.subscription import clear_history

    execute("""INSERT INTO delivery_log
               (snapshot_id, channel_type, delivery_status, sent_at)
               VALUES (1, 'wecom', 'sent', datetime('now', '-100 days'))""")
    execute("""INSERT INTO delivery_log
               (snapshot_id, channel_type, delivery_status, sent_at)
               VALUES (1, 'wecom', 'sent', datetime('now', '-10 days'))""")
    execute("""INSERT INTO delivery_log
               (snapshot_id, channel_type, delivery_status, error_message, sent_at)
               VALUES (1, 'wecom', 'failed', 'api freq out of limit', NULL)""")

    clear_history(older_than_days=90)

    rows = query("SELECT delivery_status FROM delivery_log")
    # 100 天前那条 sent 被删；10 天前那条和失败行保留
    assert sorted(r['delivery_status'] for r in rows) == ['failed', 'sent']
