"""邮件通知器 —— 支持附件下载."""

import os
import smtplib
import tempfile
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formataddr

import requests

from src.core.logger import get_logger
from src.notifiers.base import BaseNotifier, NotificationMessage, DeliveryResult, _format_html_body, _pkg_type_label, _rollback_prefix

logger = get_logger('email')


ATTACHMENT_MAX_SIZE = int(os.getenv('MONITOR_ATTACHMENT_MAX_SIZE', '10485760'))


# ── Test log writers ───────────────────────────────────────────────
# During channel tests, the SMTP send process emits a detailed step-by-step
# trace. The trace is appended to /tmp/email_test_{channel_id}.log so it
# survives a page reload (in-memory only would be lost on refresh).
# The frontend reads this file via /api/channels/{id}/test-log.

class TestLogWriter:
    """Append-only writer that records the SMTP transaction to a file."""

    def __init__(self, channel_id: int, channel_name: str, log_path: str = None):
        self.channel_id = channel_id
        self.channel_name = channel_name
        self.path = log_path if log_path else f'/tmp/email_test_{channel_id}.log'
        self.started_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.lines: list[str] = []
        # Clear the file at the start of a new test (rotate)
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                f.write(f'=== Email Test Log: {channel_name} (channel_id={channel_id}) ===\n')
                f.write(f'Started: {self.started_at}\n')
                f.write('=' * 60 + '\n')
        except OSError as e:
            logger.warning(f'Failed to initialize test log file {self.path}: {e}')


class PushLogWriter(TestLogWriter):
    """Variant of TestLogWriter used by manual push flows.

    Inherits the append-only behavior and the same file format. The class
    exists as a distinct name so backend code reads naturally at the call
    site and so future push-specific behavior (e.g. richer context lines)
    can be added without affecting tests.
    """
    pass

    def _append(self, level: str, msg: str):
        ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]  # millisecond precision
        line = f'[{ts}] [{level:5s}] {msg}'
        self.lines.append(line)
        try:
            with open(self.path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass  # Don't fail the test just because we can't write the log

    def info(self, msg: str):
        self._append('INFO', msg)

    def warn(self, msg: str):
        self._append('WARN', msg)

    def error(self, msg: str):
        self._append('ERROR', msg)

    def ok(self, msg: str):
        self._append('OK', msg)


class _NullLogWriter:
    """No-op writer for production notifications (avoids spamming /tmp)."""

    def info(self, msg: str): pass
    def warn(self, msg: str): pass
    def error(self, msg: str): pass
    def ok(self, msg: str): pass


class EmailNotifier(BaseNotifier):
    channel_type = 'email'

    @staticmethod
    def _load_sender_contact() -> dict | None:
        """Read ops contact info from system_settings.

        Returns dict with keys name/email/phone (possibly empty) or None
        on any DB error. The renderer in base._format_html_body treats
        empty fields as "contact not configured" and skips the banner.
        """
        try:
            from src.models.database import query
            rows = query(
                "SELECT key, value FROM system_settings "
                "WHERE key IN ('ops_contact_name','ops_contact_email','ops_contact_phone')"
            )
            if not rows:
                return None
            data = {r['key']: (r['value'] or '') for r in rows}
            return {
                'name': data.get('ops_contact_name', ''),
                'email': data.get('ops_contact_email', ''),
                'phone': data.get('ops_contact_phone', ''),
            }
        except Exception as e:
            logger.debug(f'Failed to load sender contact: {e}')
            return None

    def send(self, message: NotificationMessage, config: dict) -> DeliveryResult:
        smtp_host = config.get('smtp_host', '')
        smtp_port = int(config.get('smtp_port', 465))
        smtp_user = config.get('smtp_user', '')
        smtp_password = config.get('smtp_password', '')
        from_name = config.get('from_name', '绿盟升级通知')
        to_list = config.get('to_list', [])
        # Rule-level override: customer emails
        rule_emails = config.get('rule_emails', '')
        if rule_emails:
            extra = [e.strip() for e in rule_emails.split(',') if e.strip()]
            to_list = list(dict.fromkeys(to_list + extra))  # dedup preserving order
        # Customer-level attachment size (router injects customer.attachment_max_mb
        # into ch_config before sending; 0/unset → ATTACHMENT_MAX_SIZE default).
        customer_attach_mb = int(config.get('attachment_max_mb', 0))
        max_attach = (customer_attach_mb * 1048576) if customer_attach_mb > 0 else ATTACHMENT_MAX_SIZE

        # Test-mode fallback: if no recipient was configured (the new UI no
        # longer collects to_list on channel config), send the test email to
        # the authenticated user itself so SMTP issues surface immediately.
        # Production pushes must always have to_list or rule_emails — empty
        # here is a configuration bug we refuse to silently "send to self".
        is_test_mode = bool(config.get('_test_log_writer'))
        if not to_list:
            if is_test_mode:
                to_list = [smtp_user] if smtp_user else []
            if not to_list:
                return DeliveryResult(False, 'email', config.get('name', ''),
                                      'No recipient configured (set customer_emails on subscription rule, or manually push to a specific recipient)')

        if not smtp_host or not to_list:
            return DeliveryResult(False, 'email', config.get('name', ''),
                                  'Missing SMTP config or recipient list')

        # ── Email rate limiting (3-layer) ──────────────────────────
        ch_id = int(config.get('_channel_id') or 0)
        cust_id = int(config.get('_customer_id') or 0)
        ch_hourly = int(config.get('_email_hourly_limit') or 0)
        ch_daily = int(config.get('_email_daily_limit') or 0)
        cust_hourly = int(config.get('_cust_hourly_limit') or 10)
        cust_daily = int(config.get('_cust_daily_limit') or 50)
        global_hourly = int(config.get('_global_hourly_limit') or 100)
        global_daily = int(config.get('_global_daily_limit') or 500)

        from src.core.email_rate_limiter import (
            check_channel, check_customer, check_global, record as limiter_record
        )
        # Layer 1: channel
        if ch_id:
            allowed, reason = check_channel(ch_id, ch_hourly, ch_daily)
            if not allowed:
                return DeliveryResult(False, 'email', config.get('name', ''), reason)
        # Layer 2: customer
        if cust_id:
            allowed, reason = check_customer(cust_id, cust_hourly, cust_daily)
            if not allowed:
                return DeliveryResult(False, 'email', config.get('name', ''), reason)
        # Layer 3: global
        allowed, reason = check_global(global_hourly, global_daily)
        if not allowed:
            return DeliveryResult(False, 'email', config.get('name', ''), reason)
        # ── End rate limiting ──────────────────────────────────────

        subject = f'{_rollback_prefix(message)}{message.product_name} {_pkg_type_label(message.package_type)} 发布了新版本'

        # Read ops contact from system_settings (only when all three fields set
        # does the email body show the banner + footer replacement line).
        sender_contact = self._load_sender_contact()
        html_body = _format_html_body(message, message.is_rollback, sender_contact=sender_contact)

        # Determine if attachment can be included
        attachment_downloaded = (
            message.file_size > 0
            and message.file_size <= max_attach
            and message.download_url
        )

        # Note about large files — embed directly in HTML body
        if not attachment_downloaded and not message.is_rollback:
            note_html = (
                f'<p style="color:#d0021b;font-size:14px;margin:16px 0 8px 0;padding:10px;background:#fff3cd;border-radius:4px;border-left:4px solid #d0021b">'
                f'⚠️ 文件大小({message.size_display})超过附件上限，请<a href="{message.download_url}" style="color:#d0021b;font-weight:bold">点击此处下载</a>获取升级包。</p>'
                f'<div style="margin-top:12px;padding:12px;background:#fff3cd;border-radius:4px;border-left:4px solid #d0021b;font-size:13px;color:#333">'
                f'<strong style="color:#d0021b">强烈建议下载完成后校验MD5是否一致</strong><br/><br/>'
                f'<strong>Linux/macOS：</strong><code style="background:#e8e8e8;padding:2px 6px;border-radius:3px;font-family:monospace">md5sum {message.file_name}</code><br/><br/>'
                f'<strong>Windows：</strong><code style="background:#e8e8e8;padding:2px 6px;border-radius:3px;font-family:monospace">certutil -hashfile {message.file_name} MD5</code><br/><br/>'
                f'预期值：<code style="background:#e8e8e8;padding:2px 6px;border-radius:3px;font-family:monospace">{message.md5_hash}</code>'
                f'</div>'
            )
            # Insert note before </table> that closes the metadata table
            html_body = html_body.replace(
                '</table>\n</td></tr>',
                f'</table>\n{note_html}\n</td></tr>',
                1
            )

        # Build email
        email_msg = MIMEMultipart('mixed')
        email_msg['Subject'] = subject
        email_msg['From'] = formataddr((from_name, smtp_user))
        email_msg['To'] = ', '.join(to_list)
        email_msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        # ── Synchronous SMTP send ─────────────────────────────────────
        # Previously this fired off a daemon thread and returned True
        # immediately, which masked all SMTP failures (5.7.1 auth mismatch,
        # connection timeouts, etc.) — the delivery_log always recorded
        # status='sent' regardless of actual outcome. The scheduler/router/
        # manual-push callers all key off result.success, so making this
        # synchronous is the only way to surface real failure status.
        # SMTP timeout is 15s; the scheduler is a background job and the
        # manual-push API request is fine to wait for the result.
        test_log_writer = config.get('_test_log_writer')  # injected by test_channel; None in prod
        log = test_log_writer if test_log_writer else _NullLogWriter()

        log.info(f'Sending test/notification email via {smtp_host}:{smtp_port}')
        log.info(f'Auth user: {smtp_user}')
        log.info(f'From: {formataddr((from_name, smtp_user))}')
        log.info(f'To: {", ".join(to_list)}')
        log.info(f'Subject: {subject}')

        server = None
        try:
            import socket
            t0 = time.monotonic()

            # Encryption mode is explicit and required. UI only offers
            # SSL/STARTTLS, so any other value here is a programming error.
            encryption = (config.get('smtp_encryption') or '').lower()
            if encryption not in ('ssl', 'starttls'):
                return DeliveryResult(False, 'email', config.get('name', ''),
                                      'Encryption required: choose SSL (465) or STARTTLS (587) — plaintext SMTP is not supported')

            if encryption == 'ssl':
                log.info(f'Connecting to {smtp_host}:{smtp_port} (SSL/TLS — implicit TLS)')
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)
            else:  # starttls
                log.info(f'Connecting to {smtp_host}:{smtp_port} (plaintext, will upgrade to TLS via STARTTLS)')
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
                server.ehlo()
                caps = sorted((server.esmtp_features or {}).keys())
                log.info(f'Server capabilities: {", ".join(caps)}')
                if not server.has_extn('starttls'):
                    # Server doesn't advertise STARTTLS — refuse to continue
                    # rather than silently sending AUTH credentials in cleartext.
                    return DeliveryResult(False, 'email', config.get('name', ''),
                                          'Server does not advertise STARTTLS — refusing plaintext AUTH (use SSL on port 465 instead)')
                log.info('Starting TLS')
                server.starttls()
                server.ehlo()

            log.info(f'Connected in {(time.monotonic()-t0)*1000:.0f}ms')
            log.info(f'AUTH LOGIN as {smtp_user}')
            t_login = time.monotonic()
            server.login(smtp_user, smtp_password)
            log.info(f'Login OK in {(time.monotonic()-t_login)*1000:.0f}ms')

            payload_size = len(email_msg.as_string().encode('utf-8'))
            log.info(f'Sending message ({payload_size} bytes, subject={subject!r})')
            t_send = time.monotonic()
            refused = server.sendmail(smtp_user, to_list, email_msg.as_string())
            log.info(f'Message accepted by server in {(time.monotonic()-t_send)*1000:.0f}ms')
            if refused:
                # sendmail returns dict of {recipient: (code, msg)} for refused recipients
                refused_str = '; '.join(f'{rcpt}={code} {msg}' for rcpt, (code, msg) in refused.items())
                log.warn(f'Server refused {len(refused)} recipient(s): {refused_str}')
                return DeliveryResult(False, 'email', config.get('name', ''),
                                      f'Recipients refused: {refused_str}')

            log.info(f'QUIT')
            server.quit()
            log.info(f'Done in {(time.monotonic()-t0)*1000:.0f}ms total')

            logger.info(f'Email sent to {len(to_list)} recipients via {smtp_host}:{smtp_port}')
            log.ok('Email sent successfully')

            # P2: rate limit only counts successful sends (was being recorded
            # unconditionally before SMTP even tried, double-counting on success)
            if ch_id or cust_id:
                limiter_record(ch_id, cust_id)

            return DeliveryResult(True, 'email', config.get('name', ''))

        except smtplib.SMTPAuthenticationError as e:
            log.error(f'SMTP authentication failed: {e.smtp_code} {e.smtp_error!r}')
            log.error('Hint: From address must match the authenticated user (most SMTP providers reject #5.7.1 otherwise)')
            return DeliveryResult(False, 'email', config.get('name', ''), f'{e.smtp_code} {e.smtp_error}')
        except smtplib.SMTPRecipientsRefused as e:
            refused_str = '; '.join(f'{rcpt}={code} {msg}' for rcpt, (code, msg) in e.recipients.items())
            log.error(f'All recipients refused: {refused_str}')
            return DeliveryResult(False, 'email', config.get('name', ''), f'Recipients refused: {refused_str}')
        except smtplib.SMTPServerDisconnected as e:
            log.error(f'Server disconnected unexpectedly: {e}')
            return DeliveryResult(False, 'email', config.get('name', ''), f'Server disconnected: {e}')
        except smtplib.SMTPException as e:
            log.error(f'SMTP error: {e}')
            return DeliveryResult(False, 'email', config.get('name', ''), str(e))
        except TimeoutError as e:
            log.error(f'Connection timed out: {e}')
            return DeliveryResult(False, 'email', config.get('name', ''), f'Timeout: {e}')
        except OSError as e:
            log.error(f'Network error: {e}')
            return DeliveryResult(False, 'email', config.get('name', ''), f'Network: {e}')
        except Exception as e:
            log.error(f'Unexpected error: {type(e).__name__}: {e}')
            return DeliveryResult(False, 'email', config.get('name', ''), f'{type(e).__name__}: {e}')
        finally:
            if server is not None:
                try:
                    server.quit()
                except Exception:
                    pass

    def _attach_file(self, msg: MIMEMultipart, message: NotificationMessage, max_size: int) -> bool:
        """Download the package file and attach to email. Returns True on success."""
        try:
            # Download with session cookie — for nsfocus downloads
            resp = requests.get(message.download_url, timeout=30, stream=True)
            if resp.status_code != 200:
                return False

            content = resp.content
            if len(content) > max_size:
                return False

            # Attach
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(content)
            encoders.encode_base64(part)
            # Use original filename from Content-Disposition if available
            filename = message.file_name
            cd = resp.headers.get('Content-Disposition', '')
            import re
            fn_match = re.search(r'filename="?([^"\s;]+)"?', cd)
            if fn_match:
                filename = fn_match.group(1)

            part.add_header(
                'Content-Disposition',
                f'attachment; filename="{filename}"'
            )
            msg.attach(part)
            return True

        except Exception:
            return False
