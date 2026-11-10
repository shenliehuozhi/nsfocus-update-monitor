"""推送汇总回归：防重窗口、CST 显示、计数口径、落库/重试/节流、系统通知格式。

背景（2026-11-11 分析）：
1. collection_summary 的「5 分钟防重」拿 ISO 时间串和 SQLite 空格时间串比较，
   'T' > ' ' 让窗口退化成"当天已发过就跳过"——生产上 9/2 之后一天只剩一条；
2. 汇总头部的时间用 astimezone(utc) 等于没转，比实际早 8 小时；
3. 「共 N 个包」实际是渠道交付次数，邮件故障转移后一个包被记成 1✓1✗；
4. 钉钉/飞书把系统通知套进产品模板，渲染出空元数据/空标题；
5. 汇总不落 system_event_log、发送失败不重试、系统通知不过 IM 节流。
"""
import json
from datetime import datetime, timedelta

import pytest

import src.core.event_handler as eh
from src.notifiers.base import DeliveryResult, NotificationMessage


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
    return db_file


def _summary(**over):
    s = {
        'total': 1, 'success': 1, 'failed': 0, 'fallback': 0,
        'rule_name': 'R', 'customer_name': 'C',
        'channel_type': 'wecom', 'channel_name': 'W',
        'pushed_at': '2026-11-10T16:10:10+00:00',
        'items': [{'file_name': 'a.pkg', 'success': True, 'error': ''}],
    }
    s.update(over)
    return s


# ============================================================================
# 防重窗口
# ============================================================================

def test_recent_event_exists_uses_minutes_not_calendar_day(fresh_db):
    """10 分钟前的 ISO 行不能被当成"5 分钟内"（旧写法会误判为当天已发）。"""
    from src.models.database import execute

    old = (datetime.utcnow() - timedelta(minutes=10)).isoformat()
    execute("INSERT INTO system_event_log (event_type, created_at) VALUES (?, ?)",
            ('collection_summary', old))

    assert eh._recent_event_exists('collection_summary', 5) is False
    assert eh._recent_event_exists('collection_summary', 15) is True


def test_recent_event_exists_handles_legacy_space_format(fresh_db):
    """老格式 'YYYY-MM-DD HH:MM:SS' 的行同样要能命中窗口。"""
    from src.models.database import execute

    recent = (datetime.utcnow() - timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M:%S')
    execute("INSERT INTO system_event_log (event_type, created_at) VALUES (?, ?)",
            ('cleanup_summary', recent))

    assert eh._recent_event_exists('cleanup_summary', 5) is True


def test_recent_event_exists_ignores_other_event_types(fresh_db):
    from src.models.database import execute

    execute("INSERT INTO system_event_log (event_type, created_at) VALUES (?, ?)",
            ('log_error', datetime.utcnow().isoformat()))
    assert eh._recent_event_exists('collection_summary', 5) is False


# ============================================================================
# 消息构造：口径 + CST
# ============================================================================

def test_summary_header_counts_deliveries_not_packages():
    msg = eh._build_push_summary_message([_summary()])
    assert '条交付' in msg
    assert '个包' not in msg


def test_summary_time_rendered_in_cst():
    msg = eh._build_push_summary_message([_summary()])
    # 16:10 UTC → 次日 00:10 CST
    assert '2026-11-11 00:10:10' in msg


def test_summary_failover_is_resupplied_not_failed():
    s = _summary(
        total=2, success=1, failed=0, fallback=1,
        items=[
            {'file_name': 'a.pkg', 'success': False, 'error': 'SMTP auth failed',
             'failover': True},
            {'file_name': 'a.pkg', 'success': True, 'error': ''},
        ],
    )
    msg = eh._build_push_summary_message([s])

    assert '失败 0' in msg
    assert '已补发 1' in msg
    assert '1↻' in msg
    assert '已由备用渠道补发' in msg
    assert msg.count('  ✗ ') == 1


def test_summary_folds_long_failure_list():
    items = [{'file_name': f'b{i}.pkg', 'success': False, 'error': 'e'} for i in range(25)]
    msg = eh._build_push_summary_message(
        [_summary(total=25, success=0, failed=25, items=items)])

    assert msg.count('  ✗ ') == eh.SUMMARY_MAX_FAILED_PER_GROUP
    assert '另有 5 个失败' in msg


# ============================================================================
# 落库 / 重试 / 节流 / 邮件渠道保护
# ============================================================================

def test_emit_push_summary_writes_event_row(fresh_db, monkeypatch):
    monkeypatch.setattr(eh, 'is_event_enabled', lambda et: True)
    monkeypatch.setattr(eh, 'get_notify_channel', lambda: None)

    eh.emit_push_summary([_summary()])

    from src.models.database import query
    rows = query("SELECT * FROM system_event_log WHERE event_type='push_summary'")
    assert len(rows) == 1
    payload = json.loads(rows[0]['message'])
    assert payload['total'] == 1 and payload['success'] == 1
    assert payload['groups'][0]['channel_name'] == 'W'


def test_deliver_event_rejects_email_channel(monkeypatch):
    called = []
    monkeypatch.setattr(eh, '_get_notifier',
                        lambda ch: called.append(ch) or object())

    assert eh._deliver_event({'type': 'email', 'config': {}}, 't', 'x', tag='t') is None
    assert called == []


def test_deliver_event_throttles_each_attempt_and_retries(monkeypatch):
    class _Notifier:
        def __init__(self):
            self.calls = 0

        def send(self, msg, cfg):
            self.calls += 1
            if self.calls == 1:
                return DeliveryResult(False, 'wecom', '', 'api freq out of limit')
            return DeliveryResult(True, 'wecom', '')

    notifier = _Notifier()
    sleeps = []
    throttles = []
    monkeypatch.setattr(eh, '_get_notifier', lambda ch: notifier)
    monkeypatch.setattr(eh.time, 'sleep', lambda s: sleeps.append(s))

    import src.notifiers.router as router
    monkeypatch.setattr(router, '_throttle_im', lambda t: throttles.append(t))

    result = eh._deliver_event({'type': 'wecom', 'config': {}}, 't', 'x', tag='t')

    assert result.success and notifier.calls == 2
    assert throttles == ['wecom', 'wecom']
    assert sleeps == [3]


# ============================================================================
# 钉钉 / 飞书 系统通知格式
# ============================================================================

class _OkResp:
    status_code = 200

    def json(self):
        return {'errcode': 0, 'code': 0, 'errmsg': 'ok', 'msg': 'ok'}


def test_dingtalk_system_notification_skips_product_template(monkeypatch):
    import src.notifiers.dingtalk as dt

    posts = []
    monkeypatch.setattr(dt.requests, 'post',
                        lambda url, json=None, timeout=None: posts.append(json) or _OkResp())

    res = dt.DingtalkNotifier().send(
        NotificationMessage(title='绿盟监控 - 推送汇总',
                            description_full='【推送汇总】共 1 条交付\n─ R / C / 企微 W'),
        {'webhook_url': 'https://example.invalid/hook'})

    assert res.success
    text = posts[0]['markdown']['text']
    assert '发布页面' not in text
    assert '文件名称' not in text
    assert text.startswith('【推送汇总】')
    assert '<br/>' in text
    assert posts[0]['markdown']['title'] == '绿盟监控 - 推送汇总'


def test_feishu_system_notification_uses_text_message(monkeypatch):
    import src.notifiers.feishu as fs

    posts = []
    monkeypatch.setattr(fs.requests, 'post',
                        lambda url, json=None, timeout=None: posts.append(json) or _OkResp())

    res = fs.FeishuNotifier().send(
        NotificationMessage(title='绿盟监控 - 推送汇总',
                            description_full='【推送汇总】共 1 条交付'),
        {'webhook_url': 'https://example.invalid/hook'})

    assert res.success
    assert posts[0]['msg_type'] == 'text'
    assert posts[0]['content']['text'] == '【推送汇总】共 1 条交付'


# ============================================================================
# Digest：返回码校验 + 邮件渠道不再静默跳过
# ============================================================================

def _patch_digest_collaborators(monkeypatch, channel_type='wecom'):
    import src.notifiers.router as router

    # _send_digest_split 内部从 src.models.subscription 重新 import,不能只 patch router 属性
    monkeypatch.setattr('src.models.subscription.get_rule_channels',
                        lambda rid: [{'channel_id': 9, 'customer_id': 0}])
    monkeypatch.setattr(router, 'get_by_id', lambda cid: {
        'id': 9, 'type': channel_type, 'name': 'w', 'is_active': 1,
        'config': {'webhook_url': 'https://example.invalid/hook'},
    })
    monkeypatch.setattr(router, '_throttle_im', lambda t: None)


def test_digest_marks_failed_on_webhook_error(monkeypatch):
    import requests

    import src.notifiers.router as router

    _patch_digest_collaborators(monkeypatch)

    class _Resp:
        status_code = 200
        content = b'{"errcode":45009}'

        def json(self):
            return {'errcode': 45009, 'errmsg': 'api freq out of limit'}

    monkeypatch.setattr(requests, 'post', lambda *a, **k: _Resp())

    logged = []
    monkeypatch.setattr('src.models.subscription.log_delivery',
                        lambda **kw: logged.append(kw))

    router._send_digest_split(
        {'id': 3, 'customer_id': 1}, '正文', [{'snapshot_id': 1, 'chain_json': '[]'}])

    assert logged and logged[0]['status'] == 'failed'
    assert '45009' in logged[0]['error']


def test_digest_skips_email_channel_without_logging_sent(monkeypatch):
    import requests

    import src.notifiers.router as router

    _patch_digest_collaborators(monkeypatch, channel_type='email')

    posted = []
    monkeypatch.setattr(requests, 'post', lambda *a, **k: posted.append(k))

    logged = []
    monkeypatch.setattr('src.models.subscription.log_delivery',
                        lambda **kw: logged.append(kw))

    router._send_digest_split(
        {'id': 3, 'customer_id': 1}, '正文', [{'snapshot_id': 1, 'chain_json': '[]'}])

    assert posted == []
    assert logged == []
