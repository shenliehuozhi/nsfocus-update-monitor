"""每日渠道体检：SMTP 凭据探测 + 编排 + 日志关键字覆盖。

Strategy:
- mock smtplib，绝不连接真实 SMTP，绝不发信
- scheduler._channel_health_check 用 monkeypatch 替换 list_active /
  check_credentials / emit_channel_error，只验证编排与告警内容
- scheduler 在测试函数内部 import（conftest 约定：不在模块顶层引入调度器）
"""
import smtplib
from unittest.mock import MagicMock, patch

import pytest

from src.notifiers.email import EmailNotifier
from src.core.log_scanner import ERROR_PATTERNS

BASE_CFG = {
    'smtp_host': 'smtp.example.com',
    'smtp_port': 465,
    'smtp_user': 'ops@example.com',
    'smtp_password': 'some-pass',
    'smtp_encryption': 'ssl',
}


def _cfg(**overrides):
    cfg = dict(BASE_CFG)
    cfg.update(overrides)
    return cfg


# ── EmailNotifier.check_credentials ──────────────────────────────────

def test_missing_host_or_user_is_reported():
    ok, reason = EmailNotifier().check_credentials(_cfg(smtp_host=''))
    assert ok is False
    assert '配置不完整' in reason


def test_unknown_encryption_rejected():
    ok, reason = EmailNotifier().check_credentials(_cfg(smtp_encryption=''))
    assert ok is False
    assert '加密方式' in reason


def test_success_logs_in_and_never_sends_mail():
    server = MagicMock()
    with patch('src.notifiers.email.smtplib.SMTP_SSL', return_value=server):
        ok, reason = EmailNotifier().check_credentials(_cfg())
    assert (ok, reason) == (True, '')
    server.login.assert_called_once_with('ops@example.com', 'some-pass')
    server.sendmail.assert_not_called()
    server.quit.assert_called_once()


def test_expired_password_surfaces_code_and_server_message():
    server = MagicMock()
    server.login.side_effect = smtplib.SMTPAuthenticationError(
        535, b'Error: authentication failed')
    with patch('src.notifiers.email.smtplib.SMTP_SSL', return_value=server):
        ok, reason = EmailNotifier().check_credentials(_cfg())
    assert ok is False
    assert '535' in reason and 'authentication failed' in reason
    server.quit.assert_called_once()


def test_starttls_without_extension_fails_cleanly():
    server = MagicMock()
    server.has_extn.return_value = False
    with patch('src.notifiers.email.smtplib.SMTP', return_value=server):
        ok, reason = EmailNotifier().check_credentials(
            _cfg(smtp_encryption='starttls', smtp_port=587))
    assert ok is False
    assert 'STARTTLS' in reason
    server.quit.assert_called_once()


# ── scheduler._channel_health_check 编排 ─────────────────────────────

def _patch_channels(monkeypatch, channels):
    import src.models.channel as channel_model
    monkeypatch.setattr(channel_model, 'list_active', lambda: channels)


def test_health_check_alerts_only_failing_email_channels(monkeypatch):
    import src.core.scheduler as sched
    import src.core.event_handler as evt

    _patch_channels(monkeypatch, [
        {'id': 1, 'name': '运维邮箱', 'type': 'email', 'config': _cfg()},
        {'id': 2, 'name': '客户通知邮箱', 'type': 'email', 'config': _cfg()},
        {'id': 3, 'name': '企微机器人', 'type': 'wecom', 'config': {'webhook_url': 'https://x'}},
    ])

    probe_calls = []

    def fake_probe(self, cfg):
        probe_calls.append(cfg)
        if len(probe_calls) == 1:
            return True, ''
        return False, "认证失败 535 b'Error: authentication failed'"

    alerts = []
    monkeypatch.setattr(EmailNotifier, 'check_credentials', fake_probe)
    monkeypatch.setattr(evt, 'emit_channel_error',
                        lambda failures, checked=0: alerts.append((failures, checked)))

    sched._channel_health_check()

    assert len(probe_calls) == 2          # 只探测 email 渠道
    assert len(alerts) == 1
    failures, checked = alerts[0]
    assert checked == 2
    assert [f['channel_id'] for f in failures] == [2]
    assert failures[0]['channel_name'] == '客户通知邮箱'
    assert '535' in failures[0]['reason']


def test_health_check_stays_silent_when_all_channels_ok(monkeypatch):
    import src.core.scheduler as sched
    import src.core.event_handler as evt

    _patch_channels(monkeypatch, [
        {'id': 1, 'name': '运维邮箱', 'type': 'email', 'config': _cfg()},
    ])
    alerts = []
    monkeypatch.setattr(EmailNotifier, 'check_credentials', lambda self, cfg: (True, ''))
    monkeypatch.setattr(evt, 'emit_channel_error',
                        lambda failures, checked=0: alerts.append((failures, checked)))

    sched._channel_health_check()

    assert alerts == []


def test_health_check_skips_when_no_email_channel(monkeypatch):
    import src.core.scheduler as sched
    import src.core.event_handler as evt

    _patch_channels(monkeypatch, [
        {'id': 9, 'name': '钉钉', 'type': 'dingtalk', 'config': {}},
    ])
    probed = []
    monkeypatch.setattr(EmailNotifier, 'check_credentials',
                        lambda self, cfg: probed.append(cfg) or (False, 'x'))
    monkeypatch.setattr(evt, 'emit_channel_error', lambda *a, **k: pytest.fail('should not alert'))

    sched._channel_health_check()

    assert probed == []


def test_health_check_swallows_exceptions(monkeypatch):
    import src.core.scheduler as sched
    import src.models.channel as channel_model

    def boom():
        raise RuntimeError('db is locked')

    monkeypatch.setattr(channel_model, 'list_active', boom)
    sched._channel_health_check()  # 异常必须被吞掉，不能拖垮调度线程


def test_job_registered_daily_and_toggled_by_setting(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler
    import src.core.scheduler as sched

    settings = {'scheduler_enabled': '1', 'collect_interval': '4',
                'heartbeat_enabled': '0', 'channel_health_enabled': '1'}
    monkeypatch.setattr(sched, '_get_setting',
                        lambda key, default='': settings.get(key, default))
    # 所有任务函数换成占位，测试只验证注册关系，绝不真跑采集/探测
    for name in ('_smart_collect', '_session_heartbeat', '_digest_check',
                 '_run_db_cleanup', '_channel_health_check'):
        monkeypatch.setattr(sched, name, lambda *a, **k: None)

    s = BackgroundScheduler(max_workers=1)
    s.start()
    monkeypatch.setattr(sched, '_scheduler', s)
    try:
        sched.refresh_scheduler_jobs()
        job = s.get_job('nsfocus_channel_health')
        assert job is not None
        assert "hour='4'" in str(job.trigger) and "minute='0'" in str(job.trigger)

        settings['channel_health_enabled'] = '0'
        sched.refresh_scheduler_jobs()
        assert s.get_job('nsfocus_channel_health') is None

        settings['channel_health_enabled'] = '1'
        sched.refresh_scheduler_jobs()
        assert s.get_job('nsfocus_channel_health') is not None
    finally:
        s.shutdown(wait=False)


# ── log_scanner 认证错误关键字覆盖 ───────────────────────────────────

SMTP_AUTH_LINES = [
    "[ERROR] monitor.email: [EMAIL] SMTP auth failed: 535 b'Login error'",
    "smtplib.SMTPAuthenticationError: (535, b'Error: authentication failed')",
    "[ERROR] monitor.email: [EMAIL] SMTP auth failed: 5 5.7.3 b'Authentication unsuccessful'",
    "[ERROR] monitor.email: [EMAIL] SMTP auth failed: 535 b'Username and Password not accepted'",
    "[ERROR] monitor.email: [EMAIL] SMTP auth failed: 535 b'登录失败：用户名或密码不正确'",
]


@pytest.mark.parametrize('line', SMTP_AUTH_LINES)
def test_smtp_auth_failure_is_scanned(line):
    assert any(p.search(line) for p in ERROR_PATTERNS['认证错误']), line


def test_unrelated_smtp_network_error_not_flagged_as_auth():
    line = "[ERROR] monitor.email: [EMAIL] Network error: [Errno -2] Name or service not known"
    assert not any(p.search(line) for p in ERROR_PATTERNS['认证错误'])
