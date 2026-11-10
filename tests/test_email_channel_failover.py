"""同一订阅规则绑定多个 email 渠道时的收件人去重 + 故障转移。

场景：规则绑了两个 email 渠道（主/备 SMTP），解析出的收件人都是同一个邮箱。
旧逻辑去重键带 channel_id，两个渠道各发一封；现在按收件人集合分组，按绑定顺序
发送，前一个成功就不再发后面的，失败才继续下一个；不同收件人的渠道互不影响。
"""
from unittest.mock import MagicMock, patch

import pytest

import src.notifiers.router as router
from src.notifiers.base import DeliveryResult


def _snap(snapshot_id=100):
    return {
        'id': snapshot_id,
        'source_id': 1,
        'source_url': '/update/x',
        'path_id': 'abc',
        'file_name': 'sig.zip',
        'product_name': 'IPS',
        'package_type': 'rule',
        'package_version': '2.0.0',
        'md5_hash': 'abc',
        'file_size': 1000,
        'urgency': 'high',
        'description_raw': 'desc',
        'published_at': '2024-01-15T10:30:00',
        'download_url': '/dl/1',
    }


def _rule():
    return {
        'id': 10, 'name': 'r', 'filter_conditions': {}, 'customer_id': 1,
        'customer_name': 'c', 'customer_emails': '', 'template': '',
        'delay_days': 0, 'min_interval_hours': 0, 'window_config': None,
        'quiet_start': '', 'quiet_end': '', 'valid_until': '',
    }


def _binding(channel_id, recipients):
    return {
        'channel_id': channel_id, 'customer_id': 1,
        'customer_emails': recipients, 'template': '', 'attachment_max_mb': 0,
    }


def _channel(cid, ctype='email'):
    return {
        'id': cid, 'type': ctype, 'is_active': 1, 'name': f'ch{cid}',
        'config': {}, 'email_hourly_limit': 0, 'email_daily_limit': 0,
    }


def _run(bindings, channels, send_results, query_rows=None, **kwargs):
    """Run _send_immediate with DB/notifier collaborators patched."""
    fake = MagicMock()
    fake.send.side_effect = send_results

    def fake_query(sql, params=()):
        if query_rows and 'channel_id IN' in sql:
            return query_rows
        return []

    with patch.object(router, 'get_rule_channels', return_value=bindings), \
         patch.object(router, 'get_by_id', side_effect=lambda cid: channels[cid]), \
         patch('src.models.database.query', side_effect=fake_query), \
         patch.object(router, 'NOTIFIERS', {'email': fake, 'feishu': fake}), \
         patch.object(router, '_get_email_rate_settings',
                      return_value={'cust_hourly': 10, 'cust_daily': 50,
                                    'global_hourly': 100, 'global_daily': 500}), \
         patch('src.models.subscription.log_delivery') as mock_log, \
         patch.object(router.time, 'sleep'):
        router._send_immediate(_snap(), _rule(), **kwargs)

    return fake, mock_log


@pytest.fixture(autouse=True)
def reset_state():
    router._last_send.clear()
    router._recent_sends.clear()
    router._push_summary_accumulator.clear()
    yield
    router._last_send.clear()
    router._recent_sends.clear()
    router._push_summary_accumulator.clear()


def test_duplicate_recipient_channels_send_once():
    """两个渠道收件人相同（大小写不同也算同一个邮箱）→ 只发第一个。"""
    bindings = [_binding(11, 'a@x.com'), _binding(12, 'A@X.com')]
    channels = {11: _channel(11), 12: _channel(12)}

    fake, mock_log = _run(
        bindings, channels,
        [DeliveryResult(True, 'email', 'ch11')])

    assert fake.send.call_count == 1
    assert fake.send.call_args.args[1]['_channel_id'] == 11
    assert mock_log.call_count == 1
    assert mock_log.call_args.kwargs['channel_id'] == 11
    assert mock_log.call_args.kwargs['status'] == 'sent'


def test_failover_to_second_channel_on_failure():
    """第一个渠道失败 → 继续用第二个，两个渠道各留一条 delivery_log。"""
    bindings = [_binding(11, 'a@x.com'), _binding(12, 'a@x.com')]
    channels = {11: _channel(11), 12: _channel(12)}

    fake, mock_log = _run(
        bindings, channels,
        [DeliveryResult(False, 'email', 'ch11', 'SMTP auth failed'),
         DeliveryResult(True, 'email', 'ch12')])

    assert fake.send.call_count == 2
    sent_channels = [c.args[1]['_channel_id'] for c in fake.send.call_args_list]
    assert sent_channels == [11, 12]
    statuses = [c.kwargs['status'] for c in mock_log.call_args_list]
    assert statuses == ['failed', 'sent']
    assert mock_log.call_args_list[1].kwargs['channel_id'] == 12
    # 两种状态的 recipient 都是同一个邮箱
    assert mock_log.call_args_list[0].kwargs['recipient'] == 'a@x.com'


def test_different_recipients_still_both_send():
    """收件人不同 → 两个渠道都要发，不能把第二个当备用吞掉。"""
    bindings = [_binding(11, 'a@x.com'), _binding(12, 'b@x.com')]
    channels = {11: _channel(11), 12: _channel(12)}

    fake, mock_log = _run(
        bindings, channels,
        [DeliveryResult(True, 'email', 'ch11'),
         DeliveryResult(True, 'email', 'ch12')])

    assert fake.send.call_count == 2
    assert mock_log.call_count == 2


def test_already_delivered_group_skips_all_members():
    """上一轮已由（备用）渠道 12 送达 → 本轮两个渠道都跳过，不再重复发。"""
    bindings = [_binding(11, 'a@x.com'), _binding(12, 'a@x.com')]
    channels = {11: _channel(11), 12: _channel(12)}

    fake, mock_log = _run(
        bindings, channels,
        [DeliveryResult(True, 'email', 'ch11')],
        query_rows=[{'channel_id': 12}])

    assert fake.send.call_count == 0
    assert mock_log.call_count == 0


def test_non_email_channels_unchanged():
    """非 email 渠道（两个飞书机器人）保持各发各的，不做故障转移。"""
    bindings = [_binding(11, ''), _binding(12, '')]
    channels = {11: _channel(11, 'feishu'), 12: _channel(12, 'feishu')}

    fake, mock_log = _run(
        bindings, channels,
        [DeliveryResult(True, 'feishu', 'ch11'),
         DeliveryResult(True, 'feishu', 'ch12')])

    assert fake.send.call_count == 2
    assert mock_log.call_count == 2


def test_failover_marks_first_attempt_resupplied_in_summary():
    """先失败后补发成功：汇总里失败数回退为 0，记 1 条"已补发"。"""
    bindings = [_binding(11, 'a@x.com'), _binding(12, 'a@x.com')]
    channels = {11: _channel(11), 12: _channel(12)}

    _run(bindings, channels,
         [DeliveryResult(False, 'email', 'ch11', 'SMTP auth failed'),
          DeliveryResult(True, 'email', 'ch12')])

    groups = list(router._push_summary_accumulator.values())
    assert len(groups) == 2
    primary, backup = groups  # 按绑定顺序插入
    assert (primary['total'], primary['success'], primary['failed'], primary['fallback']) \
        == (1, 0, 0, 1)
    assert primary['items'][0]['failover'] is True
    assert primary['items'][0]['error'] == 'SMTP auth failed'
    assert (backup['total'], backup['success'], backup['failed']) == (1, 1, 0)


def test_accumulate_false_does_not_pollute_summary():
    """网页「重新推送」不计入下一轮采集汇总。"""
    bindings = [_binding(11, 'a@x.com')]
    channels = {11: _channel(11)}

    _run(bindings, channels, [DeliveryResult(True, 'email', 'ch11')],
         accumulate=False)

    assert router._push_summary_accumulator == {}


def test_recipient_precedence_per_channel_over_rule():
    """rule_channels.customer_emails 优先于 rule.customer_emails。"""
    from src.notifiers.router import _resolve_email_recipients

    rule = dict(_rule(), customer_emails='rule@x.com')
    binding = _binding(11, 'chan@x.com')
    assert _resolve_email_recipients(binding, rule, {}) == ('chan@x.com', ['chan@x.com'])

    binding = _binding(11, '')
    assert _resolve_email_recipients(binding, rule, {}) == ('rule@x.com', ['rule@x.com'])


def test_recipient_falls_back_to_customer_email():
    from src.notifiers.router import _resolve_email_recipients

    binding = {'channel_id': 11, 'customer_id': 7, 'customer_emails': ''}
    with patch('src.models.database.query',
               return_value=[{'email': ' cust@x.com '}]):
        rc_emails, recips = _resolve_email_recipients(binding, _rule(), {})
    assert rc_emails == 'cust@x.com'
    assert recips == ['cust@x.com']
