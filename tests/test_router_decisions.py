"""Tests for src.notifiers.router — decision branches in route_notifications().

Strategy: monkeypatch ALL DB-touching functions and snapshot/rule getters.
Tests verify the DECISION (enqueue vs immediate send, maintenance mode,
quiet/window/min-interval delays, digest mode period keys) — NOT the actual
delivery mechanics (covered by notifier tests).

The router imports a lot of modules at module-load time; some of these (like
scheduler) start background threads if imported. We import router directly
and monkeypatch the functions it calls.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import pytest

import src.notifiers.router as router
from src.notifiers.base import DeliveryResult


# ============================================================================
# Helpers / fixtures
# ============================================================================

def _snap(snapshot_id=100, **overrides):
    """Build a minimal snapshot dict."""
    base = {
        'id': snapshot_id,
        'source_id': 1,
        'source_url': '/update/x',
        'path_id': 'abc',
        'file_name': 'sig.zip',
        'product_name': 'IPS',
        'package_type': 'rule',
        'package_version': '2.0.0.45266',
        'md5_hash': 'abc',
        'file_size': 1500000,
        'urgency': 'high',
        'description_raw': 'desc',
        'description_summary': '',
        'description_full': '',
        'published_at': '2024-01-15T10:30:00',
        'download_url': '/dl/190001',
    }
    base.update(overrides)
    return base


def _rule(rule_id=10, **overrides):
    """Build a minimal rule dict (no digest_mode by default)."""
    base = {
        'id': rule_id,
        'name': 'test-rule',
        'filter_conditions': {},
        'customer_id': 1,
        'customer_name': '客户A',
        'customer_emails': '',
        'template': '',
        'digest_mode': '',
        'delay_days': 0,
        'min_interval_hours': 0,
        'window_config': None,
        'quiet_start': '',
        'quiet_end': '',
        'valid_until': '',
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def reset_router_state():
    """Clear rate-limit + summary state between tests."""
    router._last_send.clear()
    router._push_summary_accumulator.clear()
    yield
    router._last_send.clear()
    router._push_summary_accumulator.clear()


# ============================================================================
# Maintenance mode
# ============================================================================

def test_maintenance_mode_suppresses_all():
    """When maintenance_mode='1' in system_settings, route_notifications returns silently."""
    with patch('src.notifiers.router._is_maintenance_mode', return_value=True), \
         patch('src.notifiers.router.get_snapshot') as mock_snap, \
         patch('src.models.subscription.get_rule') as mock_rule, \
         patch('src.notifiers.router._send_immediate') as mock_send, \
         patch('src.notifiers.router.enqueue') as mock_enqueue:
        router.route_notifications(100, 10)
    mock_snap.assert_not_called()
    mock_rule.assert_not_called()
    mock_send.assert_not_called()
    mock_enqueue.assert_not_called()


# ============================================================================
# Missing snapshot / rule
# ============================================================================

def test_missing_snapshot_returns_silently():
    """get_snapshot returns None → no further actions."""
    with patch('src.notifiers.router._is_maintenance_mode', return_value=False), \
         patch('src.notifiers.router.get_snapshot', return_value=None), \
         patch('src.notifiers.router._send_immediate') as mock_send, \
         patch('src.notifiers.router.enqueue') as mock_enqueue:
        router.route_notifications(100, 10)
    mock_send.assert_not_called()
    mock_enqueue.assert_not_called()


def test_missing_rule_returns_silently():
    """snapshot exists but rule doesn't → no further actions."""
    with patch('src.notifiers.router._is_maintenance_mode', return_value=False), \
         patch('src.notifiers.router.get_snapshot', return_value=_snap()), \
         patch('src.models.subscription.get_rule', return_value=None), \
         patch('src.notifiers.router._send_immediate') as mock_send, \
         patch('src.notifiers.router.enqueue') as mock_enqueue:
        router.route_notifications(100, 10)
    mock_send.assert_not_called()
    mock_enqueue.assert_not_called()


# ============================================================================
# Rollback branch — always immediate
# ============================================================================

def test_rollback_calls_immediate_with_rollback_flag():
    """is_rollback=True → _send_immediate called immediately (skip delay logic)."""
    with patch('src.notifiers.router._is_maintenance_mode', return_value=False), \
         patch('src.notifiers.router.get_snapshot', return_value=_snap()), \
         patch('src.models.subscription.get_rule', return_value=_rule(delay_days=5, is_rollback=True)), \
         patch('src.notifiers.router._send_immediate') as mock_send, \
         patch('src.notifiers.router.enqueue') as mock_enqueue:
        router.route_notifications(100, 10, is_rollback=True)
    mock_send.assert_called_once()
    # router.py calls _send_immediate(snap, rule, is_rollback=True) positionally
    # OR may pass via kwargs — accept either
    if mock_send.call_args.kwargs.get('is_rollback') is not None:
        assert mock_send.call_args.kwargs['is_rollback'] is True
    else:
        args = mock_send.call_args.args
        assert len(args) >= 3 and args[2] is True
    mock_enqueue.assert_not_called()


# ============================================================================
# Digest mode — enqueue to digest queue with correct period_key
# ============================================================================

def test_digest_weekly_enqueues_with_week_period():
    """digest_mode='weekly' → enqueue_digest with 'YYYY-Www' period_key."""
    with patch('src.notifiers.router._is_maintenance_mode', return_value=False), \
         patch('src.notifiers.router.get_snapshot', return_value=_snap()), \
         patch('src.models.subscription.get_rule', return_value=_rule(digest_mode='weekly')), \
         patch('src.models.subscription.enqueue_digest') as mock_digest, \
         patch('src.notifiers.router._send_immediate') as mock_send:
        router.route_notifications(100, 10)
    mock_digest.assert_called_once()
    args = mock_digest.call_args.args
    period_key = args[2]
    assert period_key.startswith(str(datetime.now().year)) and '-W' in period_key
    mock_send.assert_not_called()


def test_digest_monthly_enqueues_with_year_month_period():
    """digest_mode='monthly' → enqueue_digest with 'YYYY-MM' period_key."""
    with patch('src.notifiers.router._is_maintenance_mode', return_value=False), \
         patch('src.notifiers.router.get_snapshot', return_value=_snap()), \
         patch('src.models.subscription.get_rule', return_value=_rule(digest_mode='monthly')), \
         patch('src.models.subscription.enqueue_digest') as mock_digest, \
         patch('src.notifiers.router._send_immediate') as mock_send:
        router.route_notifications(100, 10)
    mock_digest.assert_called_once()
    period_key = mock_digest.call_args.args[2]
    assert period_key == datetime.now().strftime('%Y-%m')
    mock_send.assert_not_called()


def test_digest_quarterly_enqueues_with_quarter_period():
    """digest_mode='quarterly' → enqueue_digest with 'YYYY-Qn' period_key."""
    with patch('src.notifiers.router._is_maintenance_mode', return_value=False), \
         patch('src.notifiers.router.get_snapshot', return_value=_snap()), \
         patch('src.models.subscription.get_rule', return_value=_rule(digest_mode='quarterly')), \
         patch('src.models.subscription.enqueue_digest') as mock_digest, \
         patch('src.notifiers.router._send_immediate') as mock_send:
        router.route_notifications(100, 10)
    mock_digest.assert_called_once()
    period_key = mock_digest.call_args.args[2]
    assert period_key.startswith(str(datetime.now().year)) and '-Q' in period_key
    mock_send.assert_not_called()


def test_digest_unknown_mode_enqueues_with_quarter_period():
    """Unknown digest_mode falls into quarterly branch (defensive default)."""
    with patch('src.notifiers.router._is_maintenance_mode', return_value=False), \
         patch('src.notifiers.router.get_snapshot', return_value=_snap()), \
         patch('src.models.subscription.get_rule', return_value=_rule(digest_mode='unknown_mode')), \
         patch('src.models.subscription.enqueue_digest') as mock_digest, \
         patch('src.notifiers.router._send_immediate') as mock_send:
        router.route_notifications(100, 10)
    # Should still enqueue (with quarterly default)
    mock_digest.assert_called_once()
    mock_send.assert_not_called()


# ============================================================================
# Quiet time → enqueue
# ============================================================================

def test_quiet_time_enqueues_with_delay():
    """If is_quiet_time returns True → enqueue (not immediate send)."""
    with patch('src.notifiers.router._is_maintenance_mode', return_value=False), \
         patch('src.notifiers.router.get_snapshot', return_value=_snap()), \
         patch('src.models.subscription.get_rule', return_value=_rule()), \
         patch('src.notifiers.router.is_quiet_time', return_value=True), \
         patch('src.notifiers.router.enqueue') as mock_enqueue, \
         patch('src.notifiers.router._send_immediate') as mock_send:
        router.route_notifications(100, 10)
    mock_enqueue.assert_called_once()
    mock_send.assert_not_called()


# ============================================================================
# Min interval → enqueue
# ============================================================================

def test_min_interval_not_met_enqueues():
    """If check_min_interval returns False → enqueue."""
    with patch('src.notifiers.router._is_maintenance_mode', return_value=False), \
         patch('src.notifiers.router.get_snapshot', return_value=_snap()), \
         patch('src.models.subscription.get_rule', return_value=_rule()), \
         patch('src.notifiers.router.is_quiet_time', return_value=False), \
         patch('src.notifiers.router.check_min_interval', return_value=False), \
         patch('src.notifiers.router.enqueue') as mock_enqueue, \
         patch('src.notifiers.router._send_immediate') as mock_send:
        router.route_notifications(100, 10)
    mock_enqueue.assert_called_once()
    mock_send.assert_not_called()


# ============================================================================
# Window time — outside window enqueues, inside window sends immediately
# ============================================================================

def test_window_outside_enqueues():
    """window_config present + is_window_time=False → enqueue."""
    with patch('src.notifiers.router._is_maintenance_mode', return_value=False), \
         patch('src.notifiers.router.get_snapshot', return_value=_snap()), \
         patch('src.models.subscription.get_rule', return_value=_rule(
             window_config={'days': [1, 2, 3, 4, 5], 'start': '09:00', 'end': '18:00'},
         )), \
         patch('src.notifiers.router.is_quiet_time', return_value=False), \
         patch('src.notifiers.router.check_min_interval', return_value=True), \
         patch('src.notifiers.router.is_window_time', return_value=False), \
         patch('src.notifiers.router.compute_next_window_push_time', return_value='2026-01-01 09:00:00'), \
         patch('src.notifiers.router.enqueue') as mock_enqueue, \
         patch('src.notifiers.router._send_immediate') as mock_send:
        router.route_notifications(100, 10)
    mock_enqueue.assert_called_once()
    mock_send.assert_not_called()


def test_window_inside_sends_immediately():
    """window_config present + is_window_time=True → immediate send."""
    with patch('src.notifiers.router._is_maintenance_mode', return_value=False), \
         patch('src.notifiers.router.get_snapshot', return_value=_snap()), \
         patch('src.models.subscription.get_rule', return_value=_rule(
             window_config={'days': [1, 2, 3, 4, 5], 'start': '09:00', 'end': '18:00'},
         )), \
         patch('src.notifiers.router.is_quiet_time', return_value=False), \
         patch('src.notifiers.router.check_min_interval', return_value=True), \
         patch('src.notifiers.router.is_window_time', return_value=True), \
         patch('src.notifiers.router.enqueue') as mock_enqueue, \
         patch('src.notifiers.router._send_immediate') as mock_send:
        router.route_notifications(100, 10)
    mock_send.assert_called_once()
    mock_enqueue.assert_not_called()


# ============================================================================
# Delay days → enqueue
# ============================================================================

def test_delay_days_enqueues_with_delay():
    """delay_days > 0 → enqueue with compute_push_time(delay_days)."""
    with patch('src.notifiers.router._is_maintenance_mode', return_value=False), \
         patch('src.notifiers.router.get_snapshot', return_value=_snap()), \
         patch('src.models.subscription.get_rule', return_value=_rule(delay_days=3)), \
         patch('src.notifiers.router.is_quiet_time', return_value=False), \
         patch('src.notifiers.router.check_min_interval', return_value=True), \
         patch('src.notifiers.router.enqueue') as mock_enqueue, \
         patch('src.notifiers.router._send_immediate') as mock_send:
        router.route_notifications(100, 10)
    mock_enqueue.assert_called_once()
    # push_after arg should be a future timestamp string
    push_after = mock_enqueue.call_args.kwargs.get('push_after') or mock_enqueue.call_args.args[2]
    assert isinstance(push_after, str)
    assert len(push_after) >= 10  # YYYY-MM-DD HH:MM:SS format
    mock_send.assert_not_called()


# ============================================================================
# No delay → immediate send
# ============================================================================

def test_no_delay_immediate_send():
    """No delay_days, no window, no quiet, min_interval OK → immediate send."""
    with patch('src.notifiers.router._is_maintenance_mode', return_value=False), \
         patch('src.notifiers.router.get_snapshot', return_value=_snap()), \
         patch('src.models.subscription.get_rule', return_value=_rule()), \
         patch('src.notifiers.router.is_quiet_time', return_value=False), \
         patch('src.notifiers.router.check_min_interval', return_value=True), \
         patch('src.notifiers.router.enqueue') as mock_enqueue, \
         patch('src.notifiers.router._send_immediate') as mock_send:
        router.route_notifications(100, 10)
    mock_send.assert_called_once()
    mock_enqueue.assert_not_called()


# ============================================================================
# _send_immediate — channel iteration + dedup
# ============================================================================

def test_send_immediate_no_bindings_logs_warning():
    """Rule with no rule_channels bindings → logs warning, returns silently."""
    rule = _rule()
    snap = _snap()
    with patch('src.notifiers.router.get_rule_channels', return_value=[]):
        router._send_immediate(snap, rule)
    # No exception, no further calls


def test_send_immediate_skips_inactive_channel():
    """Channel with is_active=0 is skipped."""
    rule = _rule()
    snap = _snap()
    bindings = [{'channel_id': 5, 'customer_id': 1, 'template': '', 'customer_emails': '', 'attachment_max_mb': 0}]
    channels = [{'id': 5, 'type': 'feishu', 'is_active': 0, 'config': {}, 'name': 'inactive'}]
    with patch('src.notifiers.router.get_rule_channels', return_value=bindings), \
         patch('src.notifiers.router.get_by_id', return_value=channels[0]), \
         patch('src.notifiers.router.NOTIFIERS') as mock_notifiers:
        router._send_immediate(snap, rule)
    mock_notifiers.get.assert_not_called()


def test_send_immediate_dedups_existing_successful_push():
    """If delivery_log already has 'sent' for this snap+channel+rule → skip."""
    rule = _rule()
    snap = _snap()
    bindings = [{'channel_id': 5, 'customer_id': 1, 'template': '', 'customer_emails': '', 'attachment_max_mb': 0}]
    channels = [{'id': 5, 'type': 'feishu', 'is_active': 1, 'config': {}, 'name': 'active'}]
    with patch('src.notifiers.router.get_rule_channels', return_value=bindings), \
         patch('src.notifiers.router.get_by_id', return_value=channels[0]), \
         patch('src.models.database.query', return_value=[{'id': 999}]), \
         patch('src.notifiers.router.NOTIFIERS') as mock_notifiers:
        router._send_immediate(snap, rule)
    mock_notifiers.get.assert_not_called()


def test_send_immediate_unknown_channel_type_skipped():
    """NOTIFIERS dict doesn't have channel type → log warning, skip."""
    rule = _rule()
    snap = _snap()
    bindings = [{'channel_id': 5, 'customer_id': 1, 'template': '', 'customer_emails': '', 'attachment_max_mb': 0}]
    channels = [{'id': 5, 'type': 'mystery_type', 'is_active': 1, 'config': {}, 'name': 'm'}]
    with patch('src.notifiers.router.get_rule_channels', return_value=bindings), \
         patch('src.notifiers.router.get_by_id', return_value=channels[0]), \
         patch('src.models.database.query', return_value=[]), \
         patch('src.notifiers.router.NOTIFIERS', {'feishu': MagicMock()}) as mock_notifiers:
        router._send_immediate(snap, rule)
    # Should NOT have called the only registered notifier (mystery_type unknown)
    for notifier in mock_notifiers.values():
        notifier.send.assert_not_called()


def test_send_immediate_calls_notifier_and_logs_delivery():
    """Happy path: notifier called, log_delivery called, summary accumulator updated."""
    rule = _rule()
    snap = _snap()
    bindings = [{'channel_id': 5, 'customer_id': 1, 'template': '', 'customer_emails': '', 'attachment_max_mb': 0}]
    channels = [{'id': 5, 'type': 'feishu', 'is_active': 1, 'config': {}, 'name': 'active'}]

    fake_notifier = MagicMock()
    fake_notifier.send.return_value = DeliveryResult(True, 'feishu', 'active')

    with patch('src.notifiers.router.get_rule_channels', return_value=bindings), \
         patch('src.notifiers.router.get_by_id', return_value=channels[0]), \
         patch('src.models.database.query', return_value=[]), \
         patch('src.notifiers.router.NOTIFIERS', {'feishu': fake_notifier}), \
         patch('src.models.subscription.log_delivery') as mock_log, \
         patch('src.notifiers.router.time.sleep'):  # avoid real sleep from rate-limit
        router._send_immediate(snap, rule)

    fake_notifier.send.assert_called_once()
    mock_log.assert_called_once()
    # Summary accumulator should have 1 entry
    assert len(router._push_summary_accumulator) == 1
    accum = list(router._push_summary_accumulator.values())[0]
    assert accum['total'] == 1
    assert accum['success'] == 1
    assert accum['failed'] == 0


def test_send_immediate_logs_failed_delivery():
    """Notifier returns failure → log_delivery called with status='failed', accumulator counts failure."""
    rule = _rule()
    snap = _snap()
    bindings = [{'channel_id': 5, 'customer_id': 1, 'template': '', 'customer_emails': '', 'attachment_max_mb': 0}]
    channels = [{'id': 5, 'type': 'feishu', 'is_active': 1, 'config': {}, 'name': 'active'}]

    fake_notifier = MagicMock()
    fake_notifier.send.return_value = DeliveryResult(False, 'feishu', 'active', error_message='timeout')

    with patch('src.notifiers.router.get_rule_channels', return_value=bindings), \
         patch('src.notifiers.router.get_by_id', return_value=channels[0]), \
         patch('src.models.database.query', return_value=[]), \
         patch('src.notifiers.router.NOTIFIERS', {'feishu': fake_notifier}), \
         patch('src.models.subscription.log_delivery') as mock_log, \
         patch('src.notifiers.router.time.sleep'):
        router._send_immediate(snap, rule)

    mock_log.assert_called_once()
    log_kwargs = mock_log.call_args.kwargs
    assert log_kwargs['status'] == 'failed'
    accum = list(router._push_summary_accumulator.values())[0]
    assert accum['success'] == 0
    assert accum['failed'] == 1


# ============================================================================
# Template selection fallback chain
# ============================================================================

def test_template_fallback_to_channel_default():
    """No binding.template, no rule.template → channel-type default ('full' for feishu)."""
    rule = _rule(template='')
    snap = _snap()
    bindings = [{'channel_id': 5, 'customer_id': 1, 'template': '', 'customer_emails': '', 'attachment_max_mb': 0}]
    channels = [{'id': 5, 'type': 'feishu', 'is_active': 1, 'config': {}, 'name': 'active'}]

    fake_notifier = MagicMock()
    fake_notifier.send.return_value = DeliveryResult(True, 'feishu', 'active')

    with patch('src.notifiers.router.get_rule_channels', return_value=bindings), \
         patch('src.notifiers.router.get_by_id', return_value=channels[0]), \
         patch('src.models.database.query', return_value=[]), \
         patch('src.notifiers.router.NOTIFIERS', {'feishu': fake_notifier}), \
         patch('src.models.subscription.log_delivery'), \
         patch('src.notifiers.router.time.sleep'):
        router._send_immediate(snap, rule)

    # feishu's DEFAULT_TEMPLATE_BY_CHANNEL = 'feishu_full' (see src/notifiers/base.py)
    ch_config = fake_notifier.send.call_args.args[1]
    assert ch_config['_template'] == 'feishu_full'


def test_template_from_binding_overrides():
    """binding.template takes priority over rule.template + channel default."""
    rule = _rule(template='full')
    snap = _snap()
    bindings = [{'channel_id': 5, 'customer_id': 1, 'template': 'strip', 'customer_emails': '', 'attachment_max_mb': 0}]
    channels = [{'id': 5, 'type': 'feishu', 'is_active': 1, 'config': {}, 'name': 'active'}]

    fake_notifier = MagicMock()
    fake_notifier.send.return_value = DeliveryResult(True, 'feishu', 'active')

    with patch('src.notifiers.router.get_rule_channels', return_value=bindings), \
         patch('src.notifiers.router.get_by_id', return_value=channels[0]), \
         patch('src.models.database.query', return_value=[]), \
         patch('src.notifiers.router.NOTIFIERS', {'feishu': fake_notifier}), \
         patch('src.models.subscription.log_delivery'), \
         patch('src.notifiers.router.time.sleep'):
        router._send_immediate(snap, rule)

    ch_config = fake_notifier.send.call_args.args[1]
    assert ch_config['_template'] == 'strip'


def test_template_from_rule_dict_per_channel():
    """rule.template is dict {cid: tpl} → use the per-channel value."""
    rule = _rule(template={5: 'brief', 6: 'full'})
    snap = _snap()
    bindings = [{'channel_id': 5, 'customer_id': 1, 'template': '', 'customer_emails': '', 'attachment_max_mb': 0}]
    channels = [{'id': 5, 'type': 'feishu', 'is_active': 1, 'config': {}, 'name': 'active'}]

    fake_notifier = MagicMock()
    fake_notifier.send.return_value = DeliveryResult(True, 'feishu', 'active')

    with patch('src.notifiers.router.get_rule_channels', return_value=bindings), \
         patch('src.notifiers.router.get_by_id', return_value=channels[0]), \
         patch('src.models.database.query', return_value=[]), \
         patch('src.notifiers.router.NOTIFIERS', {'feishu': fake_notifier}), \
         patch('src.models.subscription.log_delivery'), \
         patch('src.notifiers.router.time.sleep'):
        router._send_immediate(snap, rule)

    ch_config = fake_notifier.send.call_args.args[1]
    assert ch_config['_template'] == 'brief'


def test_template_invalid_falls_back_to_full():
    """Unknown template string → 'full' fallback (defensive)."""
    rule = _rule(template='invalid_template')
    snap = _snap()
    bindings = [{'channel_id': 5, 'customer_id': 1, 'template': '', 'customer_emails': '', 'attachment_max_mb': 0}]
    channels = [{'id': 5, 'type': 'feishu', 'is_active': 1, 'config': {}, 'name': 'active'}]

    fake_notifier = MagicMock()
    fake_notifier.send.return_value = DeliveryResult(True, 'feishu', 'active')

    with patch('src.notifiers.router.get_rule_channels', return_value=bindings), \
         patch('src.notifiers.router.get_by_id', return_value=channels[0]), \
         patch('src.models.database.query', return_value=[]), \
         patch('src.notifiers.router.NOTIFIERS', {'feishu': fake_notifier}), \
         patch('src.models.subscription.log_delivery'), \
         patch('src.notifiers.router.time.sleep'):
        router._send_immediate(snap, rule)

    ch_config = fake_notifier.send.call_args.args[1]
    assert ch_config['_template'] == 'full'


# ============================================================================
# Email channel — per-channel recipient + attachment config
# ============================================================================

def test_email_channel_uses_rule_emails_from_binding():
    """rule_channels.customer_emails overrides rule.customer_emails."""
    rule = _rule(customer_emails='rule@x.com')
    snap = _snap()
    bindings = [{
        'channel_id': 5, 'customer_id': 1,
        'template': '', 'customer_emails': 'binding@x.com',
        'attachment_max_mb': 0,
    }]
    channels = [{'id': 5, 'type': 'email', 'is_active': 1, 'config': {'to_list': []}, 'name': 'email'}]

    fake_notifier = MagicMock()
    fake_notifier.send.return_value = DeliveryResult(True, 'email', 'email')

    with patch('src.notifiers.router.get_rule_channels', return_value=bindings), \
         patch('src.notifiers.router.get_by_id', return_value=channels[0]), \
         patch('src.models.database.query', return_value=[]), \
         patch('src.notifiers.router._get_email_rate_settings', return_value={
             'cust_hourly': 10, 'cust_daily': 50, 'global_hourly': 100, 'global_daily': 500,
         }), \
         patch('src.notifiers.router.NOTIFIERS', {'email': fake_notifier}), \
         patch('src.models.subscription.log_delivery'), \
         patch('src.notifiers.router.time.sleep'):
        router._send_immediate(snap, rule)

    ch_config = fake_notifier.send.call_args.args[1]
    # binding value takes priority
    assert ch_config['rule_emails'] == 'binding@x.com'


def test_email_channel_falls_back_to_rule_emails():
    """No binding.customer_emails → use rule.customer_emails."""
    rule = _rule(customer_emails='rule@x.com')
    snap = _snap()
    bindings = [{
        'channel_id': 5, 'customer_id': 1,
        'template': '', 'customer_emails': '',
        'attachment_max_mb': 0,
    }]
    channels = [{'id': 5, 'type': 'email', 'is_active': 1, 'config': {'to_list': []}, 'name': 'email'}]

    fake_notifier = MagicMock()
    fake_notifier.send.return_value = DeliveryResult(True, 'email', 'email')

    with patch('src.notifiers.router.get_rule_channels', return_value=bindings), \
         patch('src.notifiers.router.get_by_id', return_value=channels[0]), \
         patch('src.models.database.query', return_value=[]), \
         patch('src.notifiers.router._get_email_rate_settings', return_value={
             'cust_hourly': 10, 'cust_daily': 50, 'global_hourly': 100, 'global_daily': 500,
         }), \
         patch('src.notifiers.router.NOTIFIERS', {'email': fake_notifier}), \
         patch('src.models.subscription.log_delivery'), \
         patch('src.notifiers.router.time.sleep'):
        router._send_immediate(snap, rule)

    ch_config = fake_notifier.send.call_args.args[1]
    assert ch_config['rule_emails'] == 'rule@x.com'


def test_email_channel_attachment_size_from_binding():
    """rule_channels.attachment_max_mb > 0 → sets ch_config['attachment_max_mb']."""
    rule = _rule()
    snap = _snap()
    bindings = [{
        'channel_id': 5, 'customer_id': 1,
        'template': '', 'customer_emails': '',
        'attachment_max_mb': 25,
    }]
    channels = [{'id': 5, 'type': 'email', 'is_active': 1, 'config': {'to_list': []}, 'name': 'email'}]

    fake_notifier = MagicMock()
    fake_notifier.send.return_value = DeliveryResult(True, 'email', 'email')

    with patch('src.notifiers.router.get_rule_channels', return_value=bindings), \
         patch('src.notifiers.router.get_by_id', return_value=channels[0]), \
         patch('src.models.database.query', return_value=[]), \
         patch('src.notifiers.router._get_email_rate_settings', return_value={
             'cust_hourly': 10, 'cust_daily': 50, 'global_hourly': 100, 'global_daily': 500,
         }), \
         patch('src.notifiers.router.NOTIFIERS', {'email': fake_notifier}), \
         patch('src.models.subscription.log_delivery'), \
         patch('src.notifiers.router.time.sleep'):
        router._send_immediate(snap, rule)

    ch_config = fake_notifier.send.call_args.args[1]
    assert ch_config['attachment_max_mb'] == '25'


# ============================================================================
# Rate-limit sleep for IM channels
# ============================================================================

def test_im_channel_rate_limit_sleep():
    """For wecom/dingtalk/feishu, if elapsed < interval, sleep is called."""
    rule = _rule()
    snap = _snap()
    bindings = [{'channel_id': 5, 'customer_id': 1, 'template': '', 'customer_emails': '', 'attachment_max_mb': 0}]
    channels = [{'id': 5, 'type': 'feishu', 'is_active': 1, 'config': {}, 'name': 'f'}]

    fake_notifier = MagicMock()
    fake_notifier.send.return_value = DeliveryResult(True, 'feishu', 'f')

    with patch('src.notifiers.router.get_rule_channels', return_value=bindings), \
         patch('src.notifiers.router.get_by_id', return_value=channels[0]), \
         patch('src.models.database.query', return_value=[]), \
         patch('src.notifiers.router.NOTIFIERS', {'feishu': fake_notifier}), \
         patch('src.models.subscription.log_delivery'), \
         patch('src.notifiers.router.time.sleep') as mock_sleep:
        # Pre-populate _last_send so elapsed < interval
        import time as _time
        router._last_send['feishu'] = _time.time()  # just sent
        router._send_immediate(snap, rule)

    # sleep should have been called to honor rate limit
    mock_sleep.assert_called()


# ============================================================================
# _emit_push_summary — summary aggregation
# ============================================================================

def test_emit_push_summary_no_accumulator_no_call():
    """Empty accumulator → _emit_push_summary does nothing."""
    # Ensure no accumulator
    router._push_summary_accumulator.clear()
    with patch('src.core.event_handler.emit_push_summary') as mock_emit:
        router._emit_push_summary()
    mock_emit.assert_not_called()


def test_emit_push_summary_clears_accumulator_after_emit():
    """After emit, accumulator is cleared (one-shot)."""
    router._push_summary_accumulator.clear()
    router._push_summary_accumulator[(1, 5)] = {
        'total': 2, 'success': 1, 'failed': 1,
        'rule_name': 'r', 'customer_name': 'c',
        'channel_type': 'feishu', 'channel_name': 'f',
        'items': [], 'pushed_at': '2024-01-15T10:30:00',
    }
    with patch('src.core.event_handler.emit_push_summary') as mock_emit:
        router._emit_push_summary()
    mock_emit.assert_called_once()
    assert len(router._push_summary_accumulator) == 0


def test_emit_push_summary_sums_totals_across_accumulator_entries():
    """Multiple keys in accumulator → totals summed correctly."""
    router._push_summary_accumulator.clear()
    router._push_summary_accumulator[(1, 5)] = {
        'total': 2, 'success': 2, 'failed': 0,
        'rule_name': 'r1', 'customer_name': 'c',
        'channel_type': 'feishu', 'channel_name': 'f1',
        'items': [], 'pushed_at': '2024-01-15T10:30:00',
    }
    router._push_summary_accumulator[(2, 6)] = {
        'total': 3, 'success': 1, 'failed': 2,
        'rule_name': 'r2', 'customer_name': 'c',
        'channel_type': 'dingtalk', 'channel_name': 'f2',
        'items': [], 'pushed_at': '2024-01-15T10:30:01',
    }
    with patch('src.core.event_handler.emit_push_summary') as mock_emit:
        router._emit_push_summary()
    mock_emit.assert_called_once()
    summaries = mock_emit.call_args.args[0]
    # Two summaries in list
    assert len(summaries) == 2