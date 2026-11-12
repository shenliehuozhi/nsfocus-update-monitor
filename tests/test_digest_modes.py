"""汇总模式（每周/每月/每季）回归：周期归属、到期判定、失败重试、加签与渲染。

背景（2026-11-11 分析）：
1. 发送只取当前 period_key 的 pending → 上期汇总后才到货的包永远 pending；
2. 周/月目标日严格相等 + 6h 轮询 → 错过一天整期丢失；
3. 发送失败/无渠道仍 mark sent → 静默吞队列；
4. weekly 用日历年拼 key → 跨年那周入队与发送对不上；
5. 同一包多 chain 重复计数；汇总直发 webhook 不走加签。
"""
from datetime import datetime

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
    # content_sources 由 init_all_tables 预置(id=1 是 WAF)
    execute("""INSERT INTO snapshots
               (id, source_id, product_name, version_branch, package_type,
                package_version, file_name, md5_hash, status)
               VALUES (1, 1, 'WAF', 'V6.0', 'sys', 'V6.0.9', 'a.bin', 'md5', 'active')""")
    return db_file


def _rule(mode='weekly', **over):
    r = {
        'id': 3, 'user_id': 1, 'name': 'R', 'enabled': 1,
        'digest_mode': mode, 'digest_config': {}, 'digest_last_sent': '',
        'customer_id': 1, 'window_config': {},
    }
    r.update(over)
    return r


def _snap(dq_id=1, snapshot_id=1, chain='[]', status='active'):
    return {
        'id': dq_id, 'rule_id': 3, 'snapshot_id': snapshot_id,
        'period_key': '2026-W45', 'chain_json': chain,
        'snapshot_status': status, 'product_name': 'WAF',
        'version_branch': 'V6.0', 'package_type': 'sys',
        'package_version': 'V6.0.9', 'file_name': 'a.bin',
        'md5_hash': 'md5', 'download_id': 0, 'source_url': 'https://x/rule',
        'published_at': '2026-11-01 10:00:00', 'urgency': '',
    }


# ── 周期 key ─────────────────────────────────────────────────


def test_weekly_period_key_uses_iso_year():
    from src.models.subscription import digest_period_key

    # 2026-12-31 与 2027-01-03 属于同一个 ISO 周(2026-W53)
    assert digest_period_key('weekly', datetime(2026, 12, 31)) == '2026-W53'
    assert digest_period_key('weekly', datetime(2027, 1, 3)) == '2026-W53'


def test_monthly_and_quarterly_period_keys():
    from src.models.subscription import digest_period_key

    assert digest_period_key('monthly', datetime(2026, 11, 11)) == '2026-11'
    assert digest_period_key('quarterly', datetime(2026, 11, 11)) == '2026-Q4'


# ── 到期判定 ─────────────────────────────────────────────────


def _insert_rule(**over):
    import json as _json

    from src.models.database import execute

    r = _rule(**over)
    execute("""INSERT INTO subscription_rules
               (id, user_id, name, enabled, digest_mode, digest_config,
                digest_last_sent, customer_id)
               VALUES (?, ?, ?, ?, ?, ?, '', ?)""",
            (r['id'], r['user_id'], r['name'], r['enabled'],
             r['digest_mode'], _json.dumps(r['digest_config']), r['customer_id']))


def _insert_pending(period='2026-W45', snapshot_id=1, chain='[]'):
    from src.models.database import execute

    execute("""INSERT INTO digest_queue (rule_id, snapshot_id, period_key, chain_json)
               VALUES (3, ?, ?, ?)""", (snapshot_id, period, chain))


def test_weekly_due_on_later_days_in_same_week(fresh_db):
    """目标日周一,周三检查也应到期(进程周一没跑时能补)。"""
    from src.models.subscription import get_rules_due_for_digest

    _insert_rule(digest_mode='weekly', digest_config={'weekday': 0})
    due = get_rules_due_for_digest(now=datetime(2026, 11, 11))  # 周三
    assert [r['id'] for r in due] == [3]


def test_overdue_pending_makes_rule_due_off_target_day(fresh_db):
    """今天不是推送日,但队列里有上期 pending → 立即补发。"""
    from src.models.subscription import get_rules_due_for_digest

    _insert_rule(digest_mode='weekly', digest_config={'weekday': 6})  # 周日
    _insert_pending(period='2026-W45')
    # 2026-11-11 周三:既不是周日,>= 也不成立,只能靠 overdue 触发
    due = get_rules_due_for_digest(now=datetime(2026, 11, 11))
    assert [r['id'] for r in due] == [3]

    # 幂等:本周期已发过就不再补
    from src.models.database import execute
    execute("UPDATE subscription_rules SET digest_last_sent = '2026-W46' WHERE id = 3")
    assert get_rules_due_for_digest(now=datetime(2026, 11, 11)) == []


def test_digest_snapshots_include_previous_periods(fresh_db):
    """上期汇总后才到货的包,下期必须还能取到。"""
    from src.models.subscription import get_digest_snapshots

    _insert_rule()
    _insert_pending(period='2026-W45')
    _insert_pending(period='2026-W44')
    snaps = get_digest_snapshots(rule_id=3)
    assert len(snaps) == 2


# ── 发送结果驱动 mark ────────────────────────────────────────


def _patch_process(monkeypatch, rule, snaps, send_result):
    import src.notifiers.router as router
    import src.models.subscription as sub

    monkeypatch.setattr(sub, 'get_rules_due_for_digest', lambda: [dict(rule, _period_key='2026-W46')])
    monkeypatch.setattr(sub, 'get_digest_snapshots', lambda rule_id=None: [dict(s) for s in snaps])
    monkeypatch.setattr(sub, 'cancel_digest_item', lambda dq_id: None)
    calls = {}
    monkeypatch.setattr(sub, 'mark_digest_sent',
                        lambda rule_id, **kw: calls.setdefault('mark', (rule_id, kw)))
    monkeypatch.setattr(sub, 'mark_digest_rule_sent',
                        lambda rule_id, period_key: calls.setdefault('mark_rule', period_key))
    monkeypatch.setattr(router, '_send_digest_split', lambda *a, **kw: send_result)
    events = []
    import src.models.event_log as el
    monkeypatch.setattr(el, 'log_event', lambda **kw: events.append(kw))
    return calls, events


def test_digest_all_channels_failed_keeps_queue_pending(monkeypatch, fresh_db):
    import src.notifiers.router as router

    router._DIGEST_FAIL_LOG.clear()
    rule = _rule()
    calls, events = _patch_process(
        monkeypatch, rule, [_snap()],
        {'sent': [], 'failed': ['W(wecom): HTTP 400'], 'skipped': []})

    router.process_digests()

    assert 'mark' not in calls, '全失败不能把队列标记为已发送'
    assert 'mark_rule' not in calls
    assert events and events[0]['severity'] == 'ERROR'
    assert events[0]['event_type'] == 'digest_summary'


def test_digest_success_marks_up_to_sent_max_id(monkeypatch, fresh_db):
    import src.notifiers.router as router

    rule = _rule()
    calls, events = _patch_process(
        monkeypatch, rule, [_snap(dq_id=5), _snap(dq_id=9, chain='["a"]')],
        {'sent': ['W(wecom)'], 'failed': [], 'skipped': []})

    router.process_digests()

    assert calls['mark'] == (3, {'max_id': 9})
    # 补发往期内容:last_sent 记最早一行的周期,不占用本周期发送名额
    assert calls['mark_rule'] == '2026-W45'
    assert events and events[0]['severity'] == 'INFO'
    assert events[0]['message']['packages'] == 1  # 同 snapshot 两行去重


def test_digest_first_failure_logs_event_on_fresh_clock(monkeypatch, fresh_db):
    """开机不足 1 小时(monotonic < 3600)时首次失败也必须写事件。"""
    import src.notifiers.router as router

    router._DIGEST_FAIL_LOG.clear()
    monkeypatch.setattr(router.time, 'monotonic', lambda: 100)
    rule = _rule()
    calls, events = _patch_process(
        monkeypatch, rule, [_snap()],
        {'sent': [], 'failed': ['W(wecom): HTTP 400'], 'skipped': []})

    router.process_digests()

    assert events and events[0]['event_type'] == 'digest_summary'
    assert events[0]['severity'] == 'ERROR'


def test_digest_no_bindings_not_consumed(monkeypatch, fresh_db):
    import src.notifiers.router as router

    router._DIGEST_FAIL_LOG.clear()
    rule = _rule()
    calls, events = _patch_process(
        monkeypatch, rule, [_snap()],
        {'sent': [], 'failed': [], 'skipped': ['规则未绑定渠道']})

    router.process_digests()

    assert 'mark' not in calls
    assert events[0]['message']['error'] == '规则未绑定渠道'


def test_mark_digest_sent_respects_max_id(fresh_db):
    from src.models.database import query
    from src.models.subscription import mark_digest_sent

    _insert_rule()
    _insert_pending(period='2026-W45')
    _insert_pending(period='2026-W45', chain='["a"]')
    _insert_pending(period='2026-W46')

    mark_digest_sent(3, max_id=2)

    rows = query("SELECT id, status FROM digest_queue ORDER BY id")
    assert [r['status'] for r in rows] == ['sent', 'sent', 'pending']


# ── 多 chain 去重 + 文案 ─────────────────────────────────────


def test_digest_text_dedupes_chains_and_shows_version(monkeypatch, fresh_db):
    import src.notifiers.router as router

    rule = _rule()
    captured = {}

    def fake_send(rule_, text, snaps):
        captured['text'] = text
        captured['n'] = len(snaps)
        return {'sent': ['W(wecom)'], 'failed': [], 'skipped': []}

    _patch_process(monkeypatch, rule, [_snap(dq_id=1), _snap(dq_id=2, chain='["a"]')],
                   {'sent': [], 'failed': [], 'skipped': []})
    import src.models.subscription as sub
    monkeypatch.setattr(sub, 'get_digest_snapshots', lambda rule_id=None: [_snap(dq_id=1), _snap(dq_id=2, chain='["a"]')])
    monkeypatch.setattr(router, '_send_digest_split', fake_send)

    router.process_digests()

    assert captured['n'] == 1
    assert '**本期新增**: 1 个升级包' in captured['text']
    assert '每周升级汇总' in captured['text']
    assert '版本: V6.0 V6.0.9' in captured['text']


# ── 加签与飞书渲染 ───────────────────────────────────────────


def _patch_channel(monkeypatch, channel):
    import src.notifiers.router as router
    import src.models.subscription as sub

    monkeypatch.setattr(sub, 'get_rule_channels', lambda rid: [{'channel_id': channel['id'], 'customer_id': 0}])
    monkeypatch.setattr(router, 'get_by_id', lambda cid: channel)
    monkeypatch.setattr(router, '_throttle_im', lambda t: None)
    monkeypatch.setattr(sub, 'log_delivery', lambda **kw: None)


def test_digest_uses_signed_url_per_channel(monkeypatch):
    import requests
    import src.notifiers.router as router

    _patch_channel(monkeypatch, {
        'id': 8, 'type': 'dingtalk', 'name': 'ding', 'is_active': 1,
        'config': {'webhook_url': 'https://oapi.dingtalk.com/robot/send?access_token=x',
                   'secret': 'SECabc'},
    })
    urls = []

    class _Resp:
        status_code = 200
        content = b'{"errcode":0}'

        def json(self):
            return {'errcode': 0}

    monkeypatch.setattr(requests, 'post', lambda url, **kw: urls.append(url) or _Resp())

    result = router._send_digest_split(_rule(), '正文', [{'snapshot_id': 1, 'chain_json': '[]'}])

    assert result['sent']
    assert 'timestamp=' in urls[0] and 'sign=' in urls[0]


def test_digest_feishu_uses_post_payload(monkeypatch):
    import requests
    import src.notifiers.router as router

    _patch_channel(monkeypatch, {
        'id': 7, 'type': 'feishu', 'name': 'fs', 'is_active': 1,
        'config': {'webhook_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/x'},
    })
    payloads = []

    class _Resp:
        status_code = 200
        content = b'{"code":0}'

        def json(self):
            return {'code': 0}

    monkeypatch.setattr(requests, 'post', lambda url, **kw: payloads.append(kw['json']) or _Resp())

    router._send_digest_split(
        _rule(), '## 标题\n\n**产品**: WAF\n下载: [a.bin](https://x/a.bin)',
        [{'snapshot_id': 1, 'chain_json': '[]'}])

    assert payloads[0]['msg_type'] == 'post'
    post = payloads[0]['content']['post']['zh_cn']
    assert post['content'][0][0]['text'] == '标题'
    # 链接转成 a 标签,不再出现 markdown 方括号
    flat = [tag for para in post['content'] for tag in para]
    assert any(t.get('tag') == 'a' and t.get('href') == 'https://x/a.bin' for t in flat)


def test_digest_skips_inactive_channel_with_reason(monkeypatch):
    import requests
    import src.notifiers.router as router

    _patch_channel(monkeypatch, {
        'id': 9, 'type': 'wecom', 'name': 'w', 'is_active': 0,
        'config': {'webhook_url': 'https://qyapi.weixin.qq.com/x'},
    })
    posted = []
    monkeypatch.setattr(requests, 'post', lambda *a, **k: posted.append(k))

    result = router._send_digest_split(_rule(), '正文', [{'snapshot_id': 1, 'chain_json': '[]'}])

    assert posted == []
    assert result['sent'] == []
    assert any('已停用' in s for s in result['skipped'])


# ── 邮箱汇总 ─────────────────────────────────────────────────


def _email_channel(cid, name='ops-mail', smtp_user='ops@nsfocus.com'):
    return {
        'id': cid, 'type': 'email', 'name': name, 'is_active': 1,
        'config': {
            'smtp_host': 'smtp.example.com', 'smtp_port': 465,
            'smtp_user': smtp_user, 'smtp_password': 'secret',
            'smtp_encryption': 'ssl', 'from_name': '绿盟升级通知',
            'to_list': [],
        },
    }


def _patch_email_bindings(monkeypatch, bindings, channels, send_fn):
    import src.notifiers.router as router
    import src.models.subscription as sub

    by_id = {c['id']: c for c in channels}
    logged = []

    monkeypatch.setattr(sub, 'get_rule_channels', lambda rid: bindings)
    monkeypatch.setattr(router, 'get_by_id', lambda cid: by_id.get(cid))
    monkeypatch.setattr(sub, 'log_delivery', lambda **kw: logged.append(kw))
    monkeypatch.setattr(router, '_digest_channel_already_sent', lambda *a: False)
    monkeypatch.setattr(router, '_digest_group_already_sent', lambda *a: False)
    monkeypatch.setattr(router, '_get_email_rate_settings', lambda: {
        'cust_hourly': 10, 'cust_daily': 50,
        'global_hourly': 100, 'global_daily': 500,
    })
    monkeypatch.setattr(router.EmailNotifier, 'send', send_fn)
    return logged


def test_digest_sends_email_with_resolved_recipients(monkeypatch):
    import src.notifiers.router as router
    from src.notifiers.base import DeliveryResult

    bindings = [{'channel_id': 20, 'customer_id': 1,
                 'customer_emails': 'a@nsfocus.com, b@example.com'}]
    calls = []

    def fake_send(self, message, config):
        calls.append((message, config))
        return DeliveryResult(True, 'email', 'ops-mail', sender='ops@nsfocus.com')

    logged = _patch_email_bindings(
        monkeypatch, bindings, [_email_channel(20)], fake_send)

    text = '## 📊 R — 每周升级汇总\n\n**产品**: WAF\n下载: [a.bin](https://x/a.bin)'
    result = router._send_digest_split(
        _rule(), text, [{'snapshot_id': 1, 'chain_json': '[]'}])

    assert result['sent'] == ['ops-mail(email)']
    assert result['failed'] == []
    assert len(calls) == 1
    message, config = calls[0]
    assert message.title == '📊 R — 每周升级汇总'
    assert '<strong>产品</strong>' in message.description_html
    assert '<a href="https://x/a.bin"' in message.description_html
    assert config['rule_emails'] == 'a@nsfocus.com, b@example.com'
    assert config['_channel_id'] == 20
    # 机器人渠道 recipient 为空,邮箱记录实际收件人
    assert 'a@nsfocus.com' in logged[0]['recipient']
    assert logged[0]['status'] == 'sent'


def test_digest_email_failover_uses_backup_and_clears_failure(monkeypatch):
    import src.notifiers.router as router
    from src.notifiers.base import DeliveryResult

    bindings = [
        {'channel_id': 20, 'customer_id': 1, 'customer_emails': 'a@nsfocus.com'},
        {'channel_id': 21, 'customer_id': 1, 'customer_emails': 'a@nsfocus.com'},
    ]
    attempts = []

    def fake_send(self, message, config):
        attempts.append(config['_channel_id'])
        if config['_channel_id'] == 20:
            return DeliveryResult(False, 'email', 'primary', 'smtp down')
        return DeliveryResult(True, 'email', 'backup', sender='backup@nsfocus.com')

    _patch_email_bindings(
        monkeypatch, bindings,
        [_email_channel(20, 'primary'), _email_channel(21, 'backup')], fake_send)

    result = router._send_digest_split(
        _rule(), '## 汇总\n正文', [{'snapshot_id': 1, 'chain_json': '[]'}])

    assert attempts == [20, 21]
    assert result['sent'] == ['backup(email)']
    # 备用渠道补发成功 → 主渠道失败不计入本轮,避免队列无意义重试
    assert result['failed'] == []


def test_digest_email_without_recipients_is_skipped(monkeypatch):
    import src.notifiers.router as router

    bindings = [{'channel_id': 20, 'customer_id': 0, 'customer_emails': ''}]
    calls = []
    _patch_email_bindings(
        monkeypatch, bindings, [_email_channel(20)],
        lambda self, message, config: calls.append(config))

    result = router._send_digest_split(
        _rule(), '## 汇总', [{'snapshot_id': 1, 'chain_json': '[]'}])

    assert calls == []
    assert result['sent'] == []
    assert any('未配置收件人' in s for s in result['skipped'])



# ── 手动重推 ─────────────────────────────────────────────────


def test_force_immediate_bypasses_digest_queue(monkeypatch):
    import src.notifiers.router as router
    import src.models.subscription as sub

    monkeypatch.setattr(router, '_is_maintenance_mode', lambda: False)
    monkeypatch.setattr(router, 'get_snapshot', lambda sid: {'id': sid})
    monkeypatch.setattr(sub, 'get_rule', lambda rid: _rule())
    sent, enqueued = [], []
    monkeypatch.setattr(router, '_send_immediate',
                        lambda snap, rule, **kw: sent.append(kw))
    monkeypatch.setattr(sub, 'enqueue_digest',
                        lambda *a, **kw: enqueued.append((a, kw)))

    router.route_notifications(1, 3, accumulate=False, force_immediate=True)

    assert sent and sent[0]['accumulate'] is False
    assert enqueued == []


# ── 队列清理 ─────────────────────────────────────────────────


def test_cleanup_digest_queue_keeps_pending(fresh_db):
    from src.models.database import execute, query
    from src.models.subscription import cleanup_digest_queue

    _insert_rule()
    execute("""INSERT INTO digest_queue (rule_id, snapshot_id, period_key, status, created_at)
               VALUES (3, 1, '2026-W01', 'sent', datetime('now', '-120 days'))""")
    execute("""INSERT INTO digest_queue (rule_id, snapshot_id, period_key, status, created_at)
               VALUES (3, 1, '2026-W01', 'cancelled', datetime('now', '-120 days'))""")
    execute("""INSERT INTO digest_queue (rule_id, snapshot_id, period_key, status, created_at)
               VALUES (3, 1, '2026-W01', 'pending', datetime('now', '-120 days'))""")

    removed = cleanup_digest_queue(older_than_days=90)

    assert removed == 2
    rows = query("SELECT status FROM digest_queue")
    assert [r['status'] for r in rows] == ['pending']
