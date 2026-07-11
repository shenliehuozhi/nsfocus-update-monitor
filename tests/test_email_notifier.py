"""Tests for EmailNotifier.

Strategy:
- Mock smtplib + requests + email_rate_limiter; NEVER connect to real SMTP
- Use in-memory DB via DB_PATH monkeypatch for sender contact / rate limiter
- Focus on precondition branches (recipient missing, smtp missing, rate limit,
  unsupported encryption, auth failure) which return DeliveryResult without
  touching the network
"""
import os
import smtplib
import sqlite3
import pytest
from unittest.mock import MagicMock, patch

from src.notifiers.email import EmailNotifier, ATTACHMENT_MAX_SIZE
from src.notifiers.base import NotificationMessage, DeliveryResult


@pytest.fixture
def base_msg():
    return NotificationMessage(
        title='IPS 更新',
        product_name='IPS',
        version_branch='V6',
        package_type='rule',
        file_name='sig.zip',
        package_version='2.0.0.45266',
        md5_hash='a1b2c3d4e5f60708090a0b0c0d0e0f00',
        file_size=1500000,
        description_summary='漏洞库升级',
        urgency='high',
        download_url='https://update.nsfocus.com/update/downloads/id/190001',
        source_url='/update/listNewipsDetail',
        published_at='2024-01-15T10:30:00',
        chain=['网络安全', '抗拒绝服务', 'ADS', 'V4.5R'],
    )


@pytest.fixture
def in_memory_db(monkeypatch, tmp_path):
    """Point global DB at private file; create minimal schema for system_settings."""
    db_file = tmp_path / 'test_email.db'
    monkeypatch.setattr('src.models.database.DB_PATH', str(db_file))
    if hasattr(__import__('src.models.database', fromlist=['_local'])._local, 'conn'):
        monkeypatch.setattr(
            __import__('src.models.database', fromlist=['_local'])._local, 'conn', None
        )
    con = sqlite3.connect(str(db_file))
    con.executescript("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS email_rate_counters (
            key TEXT NOT NULL,
            bucket TEXT NOT NULL,
            count INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (key, bucket)
        );
    """)
    con.commit()
    con.close()
    return db_file


@pytest.fixture
def base_config():
    return {
        'smtp_host': 'smtp.example.com',
        'smtp_port': 465,
        'smtp_user': 'ops@example.com',
        'smtp_password': 'secret',
        'smtp_encryption': 'ssl',
        'from_name': '绿盟升级通知',
        'to_list': ['user1@example.com', 'user2@example.com'],
        'rule_emails': '',
        'name': 'email-test',
        '_channel_id': 1,
        '_customer_id': 1,
        '_email_hourly_limit': 0,
        '_email_daily_limit': 0,
        '_cust_hourly_limit': 10,
        '_cust_daily_limit': 50,
        '_global_hourly_limit': 100,
        '_global_daily_limit': 500,
    }


@pytest.fixture
def email_notifier():
    return EmailNotifier()


# ============================================================================
# _load_sender_contact — DB read
# ============================================================================

def test_load_sender_contact_empty_when_no_rows(email_notifier, in_memory_db):
    """No rows in system_settings → returns None."""
    assert email_notifier._load_sender_contact() is None


def test_load_sender_contact_reads_three_keys(email_notifier, in_memory_db):
    """All three ops_contact_* keys present → dict with name/email/phone."""
    import src.models.database as db_mod
    for k, v in [
        ('ops_contact_name', '张三'),
        ('ops_contact_email', 'zhangsan@example.com'),
        ('ops_contact_phone', '13800138000'),
    ]:
        db_mod.execute("INSERT INTO system_settings (key, value) VALUES (?, ?)", (k, v))
    contact = email_notifier._load_sender_contact()
    assert contact == {
        'name': '张三',
        'email': 'zhangsan@example.com',
        'phone': '13800138000',
    }


def test_load_sender_contact_partial_keys_returns_empty_strings(email_notifier, in_memory_db):
    """Some keys missing → those fields default to empty string."""
    import src.models.database as db_mod
    db_mod.execute("INSERT INTO system_settings (key, value) VALUES (?, ?)", ('ops_contact_name', '李四'))
    contact = email_notifier._load_sender_contact()
    assert contact is not None
    assert contact['name'] == '李四'
    assert contact['email'] == ''
    assert contact['phone'] == ''


def test_load_sender_contact_exception_returns_none(email_notifier, monkeypatch):
    """If DB raises (e.g. table missing), returns None gracefully."""
    # Drop the table to force an error
    monkeypatch.setattr('src.models.database.DB_PATH', '/nonexistent/path/db.sqlite')
    # Clear thread-local so it tries to reopen
    if hasattr(__import__('src.models.database', fromlist=['_local'])._local, 'conn'):
        monkeypatch.setattr(
            __import__('src.models.database', fromlist=['_local'])._local, 'conn', None
        )
    assert email_notifier._load_sender_contact() is None


# ============================================================================
# send() — precondition failures (no SMTP/requests calls needed)
# ============================================================================

def test_send_no_recipients_returns_failure(email_notifier, base_msg, in_memory_db):
    """to_list empty AND no rule_emails → DeliveryResult(False)."""
    config = {
        'smtp_host': 'smtp.example.com',
        'smtp_user': 'ops@x.com',
        'to_list': [],
        'rule_emails': '',
        'name': 'test',
    }
    result = email_notifier.send(base_msg, config)
    assert result.success is False
    assert 'recipient' in result.error_message.lower() or '收件人' in result.error_message


def test_send_missing_smtp_host_returns_failure(email_notifier, base_msg, in_memory_db):
    """smtp_host empty → DeliveryResult(False, 'Missing SMTP config')."""
    config = {
        'smtp_host': '',
        'smtp_user': 'ops@x.com',
        'to_list': ['someone@x.com'],
        'name': 'test',
    }
    result = email_notifier.send(base_msg, config)
    assert result.success is False
    assert 'smtp' in result.error_message.lower()


def test_send_unsupported_encryption_returns_failure(email_notifier, base_msg, base_config, in_memory_db):
    """smtp_encryption not in ('ssl','starttls') → DeliveryResult(False)."""
    base_config['smtp_encryption'] = 'plain'  # not allowed
    # Make file_size 0 so even if logic proceeded, no attachment download
    base_msg.file_size = 0
    with patch('src.notifiers.email.requests.get') as mock_get:
        result = email_notifier.send(base_msg, base_config)
    assert result.success is False
    assert 'ssl' in result.error_message.lower() or 'starttls' in result.error_message.lower()
    mock_get.assert_not_called()


def test_send_rate_limit_channel_blocked(email_notifier, base_msg, base_config, in_memory_db):
    """Channel rate limit reached → DeliveryResult(False, contains '渠道' or 'channel')."""
    base_config['_email_hourly_limit'] = 5  # must be > 0 to enable channel check
    # Pre-fill channel counter to trigger hourly limit
    # Key format: 'ch:<channel_id>', bucket format: 'YYYY-MM-DD-HH'
    import src.models.database as db_mod
    from datetime import datetime
    bucket = datetime.utcnow().strftime('%Y-%m-%d-%H')
    db_mod.execute(
        "INSERT INTO email_rate_counters (key, bucket, count) VALUES (?, ?, ?)",
        (f'ch:{base_config["_channel_id"]}', bucket, 999),
    )
    result = email_notifier.send(base_msg, base_config)
    assert result.success is False
    assert '渠道' in result.error_message or 'channel' in result.error_message.lower()


def test_send_rate_limit_customer_blocked(email_notifier, base_msg, base_config, in_memory_db):
    """Customer rate limit reached → DeliveryResult(False, contains '客户' or 'customer')."""
    import src.models.database as db_mod
    from datetime import datetime
    bucket = datetime.utcnow().strftime('%Y-%m-%d-%H')
    db_mod.execute(
        "INSERT INTO email_rate_counters (key, bucket, count) VALUES (?, ?, ?)",
        (f'cust:{base_config["_customer_id"]}', bucket, 999),
    )
    result = email_notifier.send(base_msg, base_config)
    assert result.success is False
    assert '客户' in result.error_message or 'customer' in result.error_message.lower()


def test_send_rate_limit_global_blocked(email_notifier, base_msg, base_config, in_memory_db):
    """Global rate limit reached → DeliveryResult(False, contains '全局' or 'global')."""
    import src.models.database as db_mod
    from datetime import datetime
    bucket = datetime.utcnow().strftime('%Y-%m-%d-%H')
    db_mod.execute(
        "INSERT INTO email_rate_counters (key, bucket, count) VALUES (?, ?, ?)",
        ('global', bucket, 999),
    )
    result = email_notifier.send(base_msg, base_config)
    assert result.success is False
    assert '全局' in result.error_message or 'global' in result.error_message.lower()


# ============================================================================
# send() — happy path with mocked smtplib + requests
# ============================================================================

def _make_smtp_ssl_server():
    server = MagicMock()
    server.sendmail.return_value = {}  # no refused
    return server


def test_send_happy_path_ssl(email_notifier, base_msg, base_config, in_memory_db):
    """Successful SSL SMTP send → DeliveryResult(success=True)."""
    server = _make_smtp_ssl_server()
    with patch('src.notifiers.email.smtplib.SMTP_SSL', return_value=server) as mock_ssl, \
         patch('src.notifiers.email.requests.get') as mock_get:
        # Mock attachment download (200, with small content)
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        resp_mock.headers = {}
        resp_mock.content = b'fake-attachment-content'
        mock_get.return_value = resp_mock

        result = email_notifier.send(base_msg, base_config)

    assert result.success is True
    assert result.channel_type == 'email'
    # Sender is the SMTP user
    assert result.sender == base_config['smtp_user']
    mock_ssl.assert_called_once()
    server.login.assert_called_once_with(base_config['smtp_user'], base_config['smtp_password'])
    server.sendmail.assert_called_once()
    server.quit.assert_called()


def test_send_starttls_path(email_notifier, base_msg, base_config, in_memory_db):
    """smtp_encryption='starttls' uses SMTP (not SMTP_SSL) + starttls() call."""
    base_config['smtp_encryption'] = 'starttls'
    server = _make_smtp_ssl_server()
    # Mock has_extn to return True
    server.has_extn.return_value = True
    server.esmtp_features = {'starttls': None, 'size': 1024}

    with patch('src.notifiers.email.smtplib.SMTP', return_value=server) as mock_smtp, \
         patch('src.notifiers.email.smtplib.SMTP_SSL') as mock_ssl, \
         patch('src.notifiers.email.requests.get') as mock_get:
        resp_mock = MagicMock()
        resp_mock.status_code = 200
        resp_mock.headers = {}
        resp_mock.content = b''
        mock_get.return_value = resp_mock

        result = email_notifier.send(base_msg, base_config)

    assert result.success is True
    mock_smtp.assert_called_once()
    mock_ssl.assert_not_called()  # SSL should NOT be called for starttls
    server.starttls.assert_called_once()


def test_send_starttls_server_no_support(email_notifier, base_msg, base_config, in_memory_db):
    """Server doesn't advertise STARTTLS → DeliveryResult(False)."""
    base_config['smtp_encryption'] = 'starttls'
    server = MagicMock()
    server.has_extn.return_value = False
    server.esmtp_features = {'size': 1024}  # no starttls

    with patch('src.notifiers.email.smtplib.SMTP', return_value=server), \
         patch('src.notifiers.email.requests.get'):
        result = email_notifier.send(base_msg, base_config)

    assert result.success is False
    assert 'starttls' in result.error_message.lower()


def test_send_auth_failure_returns_failure(email_notifier, base_msg, base_config, in_memory_db):
    """smtplib.SMTPAuthenticationError → DeliveryResult(False)."""
    server = MagicMock()
    server.login.side_effect = smtplib.SMTPAuthenticationError(535, b'auth failed')
    with patch('src.notifiers.email.smtplib.SMTP_SSL', return_value=server), \
         patch('src.notifiers.email.requests.get'):
        result = email_notifier.send(base_msg, base_config)
    assert result.success is False
    assert '535' in result.error_message or 'auth' in result.error_message.lower()


def test_send_recipients_refused_returns_failure(email_notifier, base_msg, base_config, in_memory_db):
    """smtplib.SMTPRecipientsRefused → DeliveryResult(False)."""
    server = MagicMock()
    server.login.return_value = None
    server.sendmail.side_effect = smtplib.SMTPRecipientsRefused({
        'user1@example.com': (550, b'user unknown'),
    })
    with patch('src.notifiers.email.smtplib.SMTP_SSL', return_value=server), \
         patch('src.notifiers.email.requests.get'):
        result = email_notifier.send(base_msg, base_config)
    assert result.success is False
    assert '550' in result.error_message or 'refused' in result.error_message.lower()


def test_send_server_disconnected_returns_failure(email_notifier, base_msg, base_config, in_memory_db):
    """smtplib.SMTPServerDisconnected → DeliveryResult(False)."""
    server = MagicMock()
    server.login.side_effect = smtplib.SMTPServerDisconnected('server gone')
    with patch('src.notifiers.email.smtplib.SMTP_SSL', return_value=server), \
         patch('src.notifiers.email.requests.get'):
        result = email_notifier.send(base_msg, base_config)
    assert result.success is False
    assert 'disconnect' in result.error_message.lower() or 'gone' in result.error_message.lower()


def test_send_timeout_returns_failure(email_notifier, base_msg, base_config, in_memory_db):
    """TimeoutError → DeliveryResult(False)."""
    server = MagicMock()
    server.login.side_effect = TimeoutError('timed out')
    with patch('src.notifiers.email.smtplib.SMTP_SSL', return_value=server), \
         patch('src.notifiers.email.requests.get'):
        result = email_notifier.send(base_msg, base_config)
    assert result.success is False
    assert 'timeout' in result.error_message.lower()


def test_send_unknown_exception_returns_failure(email_notifier, base_msg, base_config, in_memory_db):
    """Generic Exception → DeliveryResult(False)."""
    server = MagicMock()
    server.login.side_effect = RuntimeError('something weird')
    with patch('src.notifiers.email.smtplib.SMTP_SSL', return_value=server), \
         patch('src.notifiers.email.requests.get'):
        result = email_notifier.send(base_msg, base_config)
    assert result.success is False
    assert 'runtimeerror' in result.error_message.lower() or 'something' in result.error_message.lower()


# ============================================================================
# Rule emails merge with to_list (dedup)
# ============================================================================

def test_to_list_rule_emails_merged_and_deduped(email_notifier, base_msg, base_config, in_memory_db):
    """rule_emails gets split on ',' and merged with to_list, preserving order, deduped."""
    base_config['to_list'] = ['a@x.com', 'b@x.com']
    base_config['rule_emails'] = 'b@x.com, c@x.com ,d@x.com'  # b dup, c has space
    server = _make_smtp_ssl_server()
    with patch('src.notifiers.email.smtplib.SMTP_SSL', return_value=server), \
         patch('src.notifiers.email.requests.get'):
        # Make file_size = 0 so no attachment download happens
        base_msg.file_size = 0
        result = email_notifier.send(base_msg, base_config)
    assert result.success is True
    # Check what got sent
    called_args = server.sendmail.call_args
    # sendmail(from, to_list, msg) — second positional arg is the to_list
    sent_to = called_args[0][1]
    assert 'a@x.com' in sent_to
    assert 'b@x.com' in sent_to  # deduped
    assert 'c@x.com' in sent_to
    assert 'd@x.com' in sent_to
    # Should be exactly 4 unique recipients
    assert len(sent_to) == 4


# ============================================================================
# Attachment size logic
# ============================================================================

def test_attachment_max_size_env_override(monkeypatch):
    """ATTACHMENT_MAX_SIZE respects MONITOR_ATTACHMENT_MAX_SIZE env var."""
    monkeypatch.setenv('MONITOR_ATTACHMENT_MAX_SIZE', '5242880')  # 5MB
    # Re-import the module to pick up the new constant
    import importlib
    import src.notifiers.email as email_mod
    importlib.reload(email_mod)
    assert email_mod.ATTACHMENT_MAX_SIZE == 5242880


def test_attachment_max_size_default(monkeypatch):
    """ATTACHMENT_MAX_SIZE defaults to 10MB when env not set."""
    monkeypatch.delenv('MONITOR_ATTACHMENT_MAX_SIZE', raising=False)
    import importlib
    import src.notifiers.email as email_mod
    importlib.reload(email_mod)
    assert email_mod.ATTACHMENT_MAX_SIZE == 10485760  # 10MB


def test_channel_type_is_email(email_notifier):
    assert email_notifier.channel_type == 'email'


# ============================================================================
# Attachment skipped when conditions not met
# ============================================================================

def test_no_attachment_when_file_size_zero(email_notifier, base_msg, base_config, in_memory_db):
    """file_size = 0 → attachment_possible = False → no requests.get call."""
    base_msg.file_size = 0
    server = _make_smtp_ssl_server()
    with patch('src.notifiers.email.smtplib.SMTP_SSL', return_value=server), \
         patch('src.notifiers.email.requests.get') as mock_get:
        result = email_notifier.send(base_msg, base_config)
    assert result.success is True
    # requests.get should not be called (no attachment)
    mock_get.assert_not_called()


def test_no_attachment_when_no_download_url(email_notifier, base_msg, base_config, in_memory_db):
    """download_url empty → attachment_possible = False → no requests.get call."""
    base_msg.download_url = ''
    server = _make_smtp_ssl_server()
    with patch('src.notifiers.email.smtplib.SMTP_SSL', return_value=server), \
         patch('src.notifiers.email.requests.get') as mock_get:
        result = email_notifier.send(base_msg, base_config)
    assert result.success is True
    mock_get.assert_not_called()


def test_no_attachment_when_file_too_large(email_notifier, base_msg, base_config, in_memory_db):
    """file_size > max_attach → attachment_possible = False → no requests.get call."""
    base_msg.file_size = 999_999_999_999  # huge
    server = _make_smtp_ssl_server()
    with patch('src.notifiers.email.smtplib.SMTP_SSL', return_value=server), \
         patch('src.notifiers.email.requests.get') as mock_get:
        result = email_notifier.send(base_msg, base_config)
    assert result.success is True
    mock_get.assert_not_called()


# ============================================================================
# _attach_file — direct tests
# ============================================================================

def test_attach_file_302_redirect_returns_false(email_notifier, base_msg):
    """302 redirect (no PHPSESSID) → returns False, attachment not added."""
    from email.mime.multipart import MIMEMultipart
    msg = MIMEMultipart('mixed')
    resp = MagicMock()
    resp.status_code = 302
    resp.headers = {'Location': 'https://update.nsfocus.com/login'}
    with patch('src.notifiers.email.requests.get', return_value=resp):
        result = email_notifier._attach_file(msg, base_msg, 10_000_000)
    assert result is False
    # No attachment part added
    assert len(msg.get_payload()) == 0


def test_attach_file_http_error_returns_false(email_notifier, base_msg):
    """Non-200/302 status code → returns False."""
    from email.mime.multipart import MIMEMultipart
    msg = MIMEMultipart('mixed')
    resp = MagicMock()
    resp.status_code = 404
    with patch('src.notifiers.email.requests.get', return_value=resp):
        result = email_notifier._attach_file(msg, base_msg, 10_000_000)
    assert result is False


def test_attach_file_empty_content_returns_false(email_notifier, base_msg):
    """Empty content (200 OK but 0 bytes) → returns False."""
    from email.mime.multipart import MIMEMultipart
    msg = MIMEMultipart('mixed')
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.content = b''
    with patch('src.notifiers.email.requests.get', return_value=resp):
        result = email_notifier._attach_file(msg, base_msg, 10_000_000)
    assert result is False


def test_attach_file_oversized_returns_false(email_notifier, base_msg):
    """Downloaded content > max_size → returns False (don't bloat email)."""
    from email.mime.multipart import MIMEMultipart
    msg = MIMEMultipart('mixed')
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.content = b'X' * 1000
    with patch('src.notifiers.email.requests.get', return_value=resp):
        result = email_notifier._attach_file(msg, base_msg, max_size=500)
    assert result is False


def test_attach_file_success_attaches_part(email_notifier, base_msg):
    """Successful download → returns True, attachment part added."""
    from email.mime.multipart import MIMEMultipart
    msg = MIMEMultipart('mixed')
    content = b'fake-zip-content'
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {'Content-Disposition': 'attachment; filename="real-name.zip"'}
    resp.content = content
    with patch('src.notifiers.email.requests.get', return_value=resp):
        result = email_notifier._attach_file(msg, base_msg, max_size=10_000_000)
    assert result is True
    # One attachment part should be in the payload
    assert len(msg.get_payload()) == 1
    part = msg.get_payload()[0]
    # Content-Disposition header reflects filename from server header
    assert 'attachment' in part.get('Content-Disposition', '').lower()


def test_attach_file_request_exception_returns_false(email_notifier, base_msg):
    """requests.RequestException → returns False (not raise)."""
    import requests
    from email.mime.multipart import MIMEMultipart
    msg = MIMEMultipart('mixed')
    with patch('src.notifiers.email.requests.get', side_effect=requests.ConnectionError('no net')):
        result = email_notifier._attach_file(msg, base_msg, 10_000_000)
    assert result is False