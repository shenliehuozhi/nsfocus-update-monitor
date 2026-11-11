"""投递可靠性回归：失败重试/退避、终止条件、并发保护、本地时间判定、汇总部分失败。

背景（2026-11-11）：
1. process_delayed_queue 无论发送成功失败都 mark_pushed → 失败静默丢包；
2. 队列只在采集结束时处理，延迟项最坏要等 4h；
3. push_after 是本地时间，却与 SQLite datetime('now')（UTC）比较 → CST 晚 8h；
4. 汇总部分渠道失败时直接消费队列，失败渠道永远收不到。
"""
from datetime import datetime, timedelta

import pytest


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
    execute("""INSERT INTO subscription_rules (id, user_id, name, enabled)
               VALUES (3, 1, 'R', 1)""")
    from src.models.channel import create as create_channel
    create_channel(1, 'W', 'wecom', {})
    execute("""INSERT INTO rule_channels (rule_id, channel_id) VALUES (3, 1)""")
    return db_file


class _Notifier:
    def __init__(self, ok=True, error='boom', raises=False):
        self.ok = ok
        self.error = error
        self.raises = raises
        self.calls = 0

    def send(self, message, config):
        from src.notifiers.base import DeliveryResult
        self.calls += 1
        if self.raises:
            raise RuntimeError('network down')
        return DeliveryResult(self.ok, 'wecom', 'W', '' if self.ok else self.error)


def _past_local(minutes=5):
    return (datetime.now() - timedelta(minutes=minutes)).strftime('%Y-%m-%d %H:%M:%S')


def _cancel_row(reason, attempts=0, error=''):
    from src.models.database import execute
    _seed_queue()
    qid = _queue_row()['id']
    execute(
        """UPDATE delayed_queue
           SET status='cancelled', cancelled_reason=?, attempts=?, last_error=?
           WHERE id=?""",
        (reason, attempts, error, qid))
    return qid


def _seed_queue(attempts=0, push_after=None):
    from src.models.database import execute
    execute(
        """INSERT INTO delayed_queue (snapshot_id, rule_id, push_after, chain_json, attempts)
           VALUES (1, 3, ?, '[]', ?)""",
        (push_after or _past_local(), attempts))


def _queue_row():
    from src.models.database import query
    return query("SELECT * FROM delayed_queue ORDER BY id LIMIT 1")[0]


def _patch_router(monkeypatch, notifier):
    import src.notifiers.router as router
    monkeypatch.setattr(router, '_is_maintenance_mode', lambda: False)
    monkeypatch.setattr(router, 'is_quiet_time', lambda r: False)
    monkeypatch.setattr(router, 'is_window_time', lambda r: True)
    monkeypatch.setattr(router, '_throttle_im', lambda t: None)
    monkeypatch.setitem(router.NOTIFIERS, 'wecom', notifier)


# ── delayed_queue retry ─────────────────────────────────────


def test_failed_delivery_schedules_backoff_retry(fresh_db, monkeypatch):
    import src.notifiers.router as router
    from src.models.database import query

    _seed_queue()
    _patch_router(monkeypatch, _Notifier(ok=False, error='HTTP 500'))

    router.process_delayed_queue()

    row = _queue_row()
    assert row['status'] == 'pending'
    assert row['attempts'] == 1
    assert row['next_attempt_at'] > _past_local(0)
    assert 'HTTP 500' in row['last_error']
    failures = query(
        "SELECT * FROM delivery_log WHERE snapshot_id=1 AND delivery_status='failed'")
    assert failures


def test_failed_item_retries_and_eventually_succeeds(fresh_db, monkeypatch):
    import src.notifiers.router as router
    from src.models.database import execute

    _seed_queue()
    failing = _Notifier(ok=False, error='timeout')
    _patch_router(monkeypatch, failing)

    router.process_delayed_queue()
    assert _queue_row()['status'] == 'pending'

    # 模拟退避时间已到，并恢复渠道
    execute("UPDATE delayed_queue SET next_attempt_at = '' WHERE status='pending'")
    ok = _Notifier(ok=True)
    _patch_router(monkeypatch, ok)
    router.process_delayed_queue()

    row = _queue_row()
    assert row['status'] == 'pushed'
    assert row['attempts'] == 1
    assert ok.calls == 1


def test_send_exception_is_retried_not_crashed(fresh_db, monkeypatch):
    import src.notifiers.router as router

    _seed_queue()
    _patch_router(monkeypatch, _Notifier(raises=True))

    router.process_delayed_queue()

    row = _queue_row()
    assert row['status'] == 'pending'
    assert row['attempts'] == 1
    assert 'RuntimeError' in row['last_error']


def test_retry_exhaustion_marks_item_terminal(fresh_db, monkeypatch):
    import src.notifiers.router as router

    _seed_queue(attempts=router._DELIVERY_MAX_ATTEMPTS - 1)
    _patch_router(monkeypatch, _Notifier(ok=False, error='still failing'))

    router.process_delayed_queue()

    row = _queue_row()
    assert row['status'] == 'cancelled'
    assert row['cancelled_reason'].startswith('retry_exhausted')
    assert 'still failing' in row['last_error']


def test_no_deliverable_channel_is_terminal(fresh_db, monkeypatch):
    import src.notifiers.router as router
    from src.models.database import execute

    _seed_queue()
    execute("DELETE FROM rule_channels WHERE rule_id = 3")
    _patch_router(monkeypatch, _Notifier(ok=True))

    router.process_delayed_queue()

    row = _queue_row()
    assert row['status'] == 'cancelled'
    assert row['cancelled_reason'] == 'no_active_channel'


def test_terminal_deliveries_write_one_batched_system_event(fresh_db, monkeypatch):
    """终止项必须写 system_event_log，否则网页/事件中心完全看不到丢包。"""
    import json

    import src.notifiers.router as router
    from src.models.database import query

    _seed_queue(attempts=router._DELIVERY_MAX_ATTEMPTS - 1)
    _seed_queue(attempts=router._DELIVERY_MAX_ATTEMPTS - 1)
    _patch_router(monkeypatch, _Notifier(ok=False, error='still failing'))

    router.process_delayed_queue()

    events = query(
        "SELECT * FROM system_event_log WHERE event_type='delivery_error'")
    assert len(events) == 1, '同一轮终止项应合并为一条事件'
    payload = json.loads(events[0]['message'])
    assert payload['count'] == 2
    assert events[0]['severity'] == 'ERROR'
    item = payload['items'][0]
    assert item['queue_id']
    assert item['rule_name'] == 'R'
    assert item['product_name'] == 'WAF'
    assert item['reason'].startswith('retry_exhausted')
    assert 'still failing' in item['error']


def test_no_channel_terminal_event_carries_reason(fresh_db, monkeypatch):
    import json

    import src.notifiers.router as router
    from src.models.database import execute, query

    _seed_queue()
    execute("DELETE FROM rule_channels WHERE rule_id = 3")
    _patch_router(monkeypatch, _Notifier(ok=True))

    router.process_delayed_queue()

    events = query(
        "SELECT * FROM system_event_log WHERE event_type='delivery_error'")
    assert len(events) == 1
    payload = json.loads(events[0]['message'])
    assert payload['items'][0]['reason'] == 'no_active_channel'
    assert 'no deliverable channel' in payload['items'][0]['error']


def test_partial_channel_failure_keeps_item_for_retry(fresh_db, monkeypatch):
    import src.notifiers.router as router
    from src.models.database import execute
    from src.models.channel import create as create_channel

    create_channel(1, 'D', 'dingtalk', {})
    execute("INSERT INTO rule_channels (rule_id, channel_id) VALUES (3, 2)")
    _seed_queue()
    _patch_router(monkeypatch, _Notifier(ok=False, error='wecom down'))
    ok = _Notifier(ok=True)
    monkeypatch.setitem(router.NOTIFIERS, 'dingtalk', ok)

    router.process_delayed_queue()

    row = _queue_row()
    assert row['status'] == 'pending'
    assert row['attempts'] == 1
    assert ok.calls == 1  # 成功的渠道没有再被重复发送


# ── failed delivery_log retention ──────────────────────────


def _delivery_row(status, created_at, sent_at=None):
    from src.models.database import execute
    execute(
        """INSERT INTO delivery_log
           (snapshot_id, rule_id, channel_id, channel_type, delivery_status,
            sent_at, created_at)
           VALUES (1, 3, 1, 'wecom', ?, ?, ?)""",
        (status, sent_at, created_at))


def test_failed_delivery_cleanup_expires_only_old_dated_rows(fresh_db):
    from src.models.database import query
    from src.models.subscription import cleanup_failed_delivery_log

    old = (datetime.now() - timedelta(days=200)).strftime('%Y-%m-%dT%H:%M:%S')
    recent = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%dT%H:%M:%S')
    _delivery_row('failed', old)
    _delivery_row('failed', recent)
    _delivery_row('sent', old, old)
    _delivery_row('failed', None)  # 历史失败行没有时间戳：无法判断年龄，保留

    removed = cleanup_failed_delivery_log(older_than_days=180)

    assert removed == 1
    rows = query("SELECT delivery_status, created_at FROM delivery_log")
    assert len(rows) == 3
    assert any(r['delivery_status'] == 'sent' for r in rows)
    assert sum(1 for r in rows if r['delivery_status'] == 'failed') == 2


# ── manual retry ───────────────────────────────────────────


def test_manual_retry_requeues_exhausted_item(fresh_db):
    from src.models.subscription import reset_delivery_for_retry

    qid = _cancel_row('retry_exhausted(6)', attempts=6, error='boom')

    row = reset_delivery_for_retry(qid)

    assert row['status'] == 'pending'
    assert row['attempts'] == 0
    assert row['next_attempt_at'] == ''
    assert row['cancelled_reason'] == ''
    assert row['last_error'] == ''
    assert row['push_after'] <= _past_local(0), '重试项应立即可被队列取走'


def test_manual_retry_accepts_no_active_channel(fresh_db):
    from src.models.subscription import reset_delivery_for_retry

    qid = _cancel_row('no_active_channel', error='no deliverable channel')

    assert reset_delivery_for_retry(qid)['status'] == 'pending'


def test_manual_retry_refuses_business_cancel(fresh_db):
    from src.models.subscription import reset_delivery_for_retry

    qid = _cancel_row('reset_by_new_package')

    assert reset_delivery_for_retry(qid) is None
    assert _queue_row()['status'] == 'cancelled'


def test_manual_retry_refuses_missing_or_pushed(fresh_db):
    from src.models.database import execute
    from src.models.subscription import reset_delivery_for_retry

    _seed_queue()
    qid = _queue_row()['id']
    execute("UPDATE delayed_queue SET status='pushed' WHERE id=?", (qid,))

    assert reset_delivery_for_retry(qid) is None
    assert reset_delivery_for_retry(99999) is None


def test_retry_api_requeues_terminal_item(fresh_db, monkeypatch, tmp_path):
    from flask import Flask

    from src.web.auth import create_token
    from src.web.routes import api_routes

    monkeypatch.setenv('MONITOR_LOG_DIR', str(tmp_path))
    qid = _cancel_row('retry_exhausted(6)', attempts=6, error='boom')

    app = Flask(__name__)
    app.register_blueprint(api_routes.bp_history)
    resp = app.test_client().post(
        f'/api/history/queue/{qid}/retry',
        headers={'Authorization': 'Bearer ' + create_token(1, 'tester')})

    assert resp.status_code == 200
    assert resp.get_json()['code'] == 0
    assert _queue_row()['status'] == 'pending'


def test_retry_api_rejects_non_terminal_item(fresh_db, monkeypatch, tmp_path):
    from flask import Flask

    from src.web.auth import create_token
    from src.web.routes import api_routes

    monkeypatch.setenv('MONITOR_LOG_DIR', str(tmp_path))
    _seed_queue()
    qid = _queue_row()['id']

    app = Flask(__name__)
    app.register_blueprint(api_routes.bp_history)
    resp = app.test_client().post(
        f'/api/history/queue/{qid}/retry',
        headers={'Authorization': 'Bearer ' + create_token(1, 'tester')})

    assert resp.status_code == 404
    assert _queue_row()['status'] == 'pending'


def test_event_center_wires_delivery_retry():
    from tests.frontend_source import js_source

    src = js_source()
    assert 'value="delivery_error"' in src
    assert 'retryDelivery(' in src
    assert "'/history/queue/'+queueId+'/retry'" in src


def test_queue_processing_is_not_reentrant(fresh_db, monkeypatch):
    import src.notifiers.router as router

    called = []
    monkeypatch.setattr(router, 'get_due_items', lambda: called.append(1) or [])
    monkeypatch.setattr(router, '_is_maintenance_mode', lambda: False)

    assert router._QUEUE_PROCESS_LOCK.acquire(blocking=False)
    try:
        router.process_delayed_queue()
    finally:
        router._QUEUE_PROCESS_LOCK.release()

    assert called == [], '并发进入时应直接跳过，不读取队列'


def test_get_due_items_uses_local_time(fresh_db, monkeypatch):
    """CST 服务器上 push_after(本地) 不能被 SQLite datetime('now')(UTC) 误判。"""
    import os
    import time as _time

    from src.models.database import execute
    from src.models.subscription import get_due_items

    old_tz = os.environ.get('TZ')
    os.environ['TZ'] = 'CST-8'
    _time.tzset()
    try:
        execute("""INSERT INTO delayed_queue
                   (snapshot_id, rule_id, push_after, chain_json, attempts)
                   VALUES (1, 3, ?, '[]', 0)""", (_past_local(1),))
        due = get_due_items()
        assert [r['id'] for r in due]
    finally:
        if old_tz is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = old_tz
        _time.tzset()


# ── digest retry ────────────────────────────────────────────


def _rule():
    return {
        'id': 3, 'user_id': 1, 'name': 'R', 'enabled': 1,
        'digest_mode': 'weekly', 'digest_config': {}, 'digest_last_sent': '',
        'customer_id': None, 'window_config': {},
    }


def _snap(attempts=0, next_attempt_at=''):
    return {
        'id': 1, 'rule_id': 3, 'snapshot_id': 1, 'period_key': '2026-W45',
        'chain_json': '[]', 'snapshot_status': 'active',
        'product_name': 'WAF', 'version_branch': 'V6.0', 'package_type': 'sys',
        'package_version': 'V6.0.9', 'file_name': 'a.bin', 'md5_hash': 'md5',
        'download_id': 0, 'source_url': 'https://x/rule',
        'published_at': '2026-11-01 10:00:00', 'urgency': '',
        'attempts': attempts, 'next_attempt_at': next_attempt_at,
    }


def _patch_digest(monkeypatch, send_result, snaps):
    import src.models.subscription as sub
    import src.notifiers.router as router

    calls = {}
    monkeypatch.setattr(sub, 'get_rules_due_for_digest',
                        lambda: [dict(_rule(), _period_key='2026-W46')])
    monkeypatch.setattr(sub, 'get_digest_snapshots', lambda rule_id=None: [dict(s) for s in snaps])
    monkeypatch.setattr(sub, 'cancel_digest_item', lambda dq_id: None)
    monkeypatch.setattr(sub, 'mark_digest_sent',
                        lambda rule_id, **kw: calls.setdefault('mark', (rule_id, kw)))
    monkeypatch.setattr(sub, 'mark_digest_rule_sent',
                        lambda rule_id, period_key: calls.setdefault('mark_rule', period_key))
    monkeypatch.setattr(sub, 'defer_digest_items',
                        lambda rule_id, max_id, attempts, next_at, error='':
                        calls.setdefault('defer', (attempts, next_at, error)))
    monkeypatch.setattr(router, 'is_window_time', lambda r: True)
    monkeypatch.setattr(router, '_log_digest_failure',
                        lambda rule, period, detail: calls.setdefault('failure', detail))
    monkeypatch.setattr(router, '_send_digest_split', lambda *a, **kw: dict(send_result))
    events = []
    import src.models.event_log as el
    monkeypatch.setattr(el, 'log_event', lambda **kw: events.append(kw))
    return calls, events


def test_digest_partial_failure_defers_without_consuming_queue(fresh_db, monkeypatch):
    import src.notifiers.router as router

    router._DIGEST_FAIL_LOG.clear()
    calls, _ = _patch_digest(monkeypatch, {
        'sent': ['A(wecom)'], 'failed': ['B(dingtalk): timeout'],
        'skipped': [], 'already': [],
    }, [_snap()])

    router.process_digests()

    assert 'mark' not in calls, '部分失败不能消费队列'
    assert calls['defer'][0] == 1
    assert calls['defer'][1]  # next_attempt_at 已排期
    assert 'timeout' in calls['failure']


def test_digest_retry_skips_already_delivered_channel(fresh_db, monkeypatch):
    import src.notifiers.router as router

    monkeypatch.setattr(router, '_digest_channel_already_sent', lambda *a: True)
    monkeypatch.setattr(router, 'get_by_id', lambda cid: {
        'id': 1, 'name': 'W', 'type': 'wecom', 'is_active': 1, 'config': {}})
    import src.models.subscription as sub
    monkeypatch.setattr(sub, 'get_rule_channels',
                        lambda rid: [{'channel_id': 1, 'customer_id': 0}])

    result = router._send_digest_split(_rule(), 'text', [_snap()])

    assert result['already'] == ['W(wecom)']
    assert result['sent'] == [] and result['failed'] == []


def test_digest_already_sent_requires_all_snapshots(fresh_db):
    from src.models.subscription import log_delivery
    from src.notifiers.router import _digest_channel_already_sent

    log_delivery(snapshot_id=1, rule_id=3, channel_id=1,
                 channel_type='wecom', status='sent')

    assert _digest_channel_already_sent(3, 1, [1]) is True
    assert _digest_channel_already_sent(3, 1, [1, 2]) is False


def test_digest_backoff_is_respected(fresh_db, monkeypatch):
    import src.notifiers.router as router

    future = (datetime.utcnow() + timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')
    calls, _ = _patch_digest(monkeypatch, {
        'sent': ['A(wecom)'], 'failed': [], 'skipped': [], 'already': [],
    }, [_snap(attempts=1, next_attempt_at=future)])

    router.process_digests()

    assert calls == {}, '退避未到不应发送，也不应消费队列'


def test_digest_retry_locks_previous_batch(fresh_db, monkeypatch):
    """重试只处理上一轮批次，发送期间新入队的包留到下一批。"""
    import src.notifiers.router as router

    old = _snap(attempts=1)
    new = _snap(attempts=0)
    new.update(id=2, snapshot_id=2)
    calls, _ = _patch_digest(monkeypatch, {
        'sent': ['A(wecom)'], 'failed': [], 'skipped': [], 'already': [],
    }, [old, new])
    seen = {}

    def fake_send(rule_, text, snaps):
        seen['ids'] = [s['id'] for s in snaps]
        return {'sent': ['A(wecom)'], 'failed': [], 'skipped': [], 'already': []}

    monkeypatch.setattr(router, '_send_digest_split', fake_send)

    router.process_digests()

    assert seen['ids'] == [1]
    assert calls['mark'] == (3, {'max_id': 1})


def test_digest_exhaustion_consumes_queue_and_logs_error(fresh_db, monkeypatch):
    import src.notifiers.router as router

    router._DIGEST_FAIL_LOG.clear()
    calls, events = _patch_digest(monkeypatch, {
        'sent': [], 'failed': ['A(wecom): HTTP 400'], 'skipped': [], 'already': [],
    }, [_snap(attempts=router._DIGEST_MAX_ATTEMPTS - 1)])

    router.process_digests()

    assert calls['mark'] == (3, {'max_id': 1})
    assert calls['mark_rule'] == '2026-W45'
    assert events and events[-1]['severity'] == 'ERROR'
    assert events[-1]['message']['retry_exhausted'] is True
    assert 'HTTP 400' in events[-1]['message']['error']
