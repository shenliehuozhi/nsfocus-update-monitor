"""邮件通知器 —— 支持附件下载."""
import socket
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

class TestLogWriter:
    """Append-only writer that records the SMTP transaction to a file."""

    def __init__(self, channel_id: int, channel_name: str, log_path: str = None):
        self.channel_id = channel_id
        self.channel_name = channel_name
        self.path = log_path if log_path else f'/tmp/email_test_{channel_id}.log'
        self.started_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.lines: list[str] = []
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                f.write(f'=== Email Test Log: {channel_name} (channel_id={channel_id}) ===\n')
                f.write(f'Started: {self.started_at}\n')
                f.write('=' * 60 + '\n')
        except OSError as e:
            logger.warning(f'Failed to initialize test log file {self.path}: {e}')

    def _append(self, level: str, msg: str):
        ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        line = f'[{ts}] [{level:5s}] {msg}'
        self.lines.append(line)
        try:
            with open(self.path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass

    def info(self, msg: str):
        self._append('INFO', msg)

    def warn(self, msg: str):
        self._append('WARN', msg)

    def error(self, msg: str):
        self._append('ERROR', msg)

    def ok(self, msg: str):
        self._append('OK', msg)


class PushLogWriter(TestLogWriter):
    """Variant of TestLogWriter used by manual push flows."""
    pass


class _NullLogWriter:
    """No-op writer for production notifications."""

    def info(self, msg: str): pass
    def warn(self, msg: str): pass
    def error(self, msg: str): pass
    def ok(self, msg: str): pass


class EmailNotifier(BaseNotifier):
    channel_type = 'email'

    @staticmethod
    def _load_sender_contact() -> dict | None:
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
        # ── 1. 提取配置 ──────────────────────────────────────────
        smtp_host = config.get('smtp_host', '')
        smtp_port = int(config.get('smtp_port', 465))
        smtp_user = config.get('smtp_user', '')
        smtp_password = config.get('smtp_password', '')
        from_name = config.get('from_name', '绿盟升级通知')
        to_list = config.get('to_list', [])
        rule_emails = config.get('rule_emails', '')
        if rule_emails:
            extra = [e.strip() for e in rule_emails.split(',') if e.strip()]
            to_list = list(dict.fromkeys(to_list + extra))
        customer_attach_mb = int(config.get('attachment_max_mb', 0))
        logger.info(f'[EMAIL] customer_attach_mb is {customer_attach_mb}')
        max_attach = (customer_attach_mb * 1048576) if customer_attach_mb > 0 else ATTACHMENT_MAX_SIZE

        is_test_mode = bool(config.get('_test_log_writer'))
        log = config.get('_test_log_writer') if is_test_mode else _NullLogWriter()

        # ── 2. 记录接收到的消息摘要 ────────────────────────────
        logger.info(f'[EMAIL] Sending notification: product={message.product_name}, '
                    f'file={message.file_name}, size={message.file_size}B, '
                    f'url={message.download_url[:80] if message.download_url else "None"}...')
        log.info(f'📧 开始发送邮件通知')
        log.info(f'  产品: {message.product_name}')
        log.info(f'  文件: {message.file_name} ({message.size_display})')
        log.info(f'  MD5: {message.md5_hash}')
        log.info(f'  下载链接: {message.download_url}')
        log.info(f'  是否回滚: {message.is_rollback}')

        # ── 3. 收件人检查 ──────────────────────────────────────
        if not to_list:
            if is_test_mode:
                to_list = [smtp_user] if smtp_user else []
            if not to_list:
                log.error('❌ 无收件人 (to_list为空且无rule_emails)')
                logger.warning('[EMAIL] No recipient configured')
                return DeliveryResult(False, 'email', config.get('name', ''),
                                      'No recipient configured', sender=smtp_user)

        log.info(f'  收件人({len(to_list)}): {", ".join(to_list[:5])}{"..." if len(to_list)>5 else ""}')

        if not smtp_host:
            log.error('❌ SMTP_HOST 为空')
            return DeliveryResult(False, 'email', config.get('name', ''),
                                  'Missing SMTP config', sender=smtp_user)

        # ── 4. 速率限制 ──────────────────────────────────────────
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

        if ch_id:
            allowed, reason = check_channel(ch_id, ch_hourly, ch_daily)
            if not allowed:
                log.warn(f'⛔ 渠道限流: {reason}')
                logger.warning(f'[EMAIL] Channel rate limit: {reason}')
                return DeliveryResult(False, 'email', config.get('name', ''), reason, sender=smtp_user)
        if cust_id:
            allowed, reason = check_customer(cust_id, cust_hourly, cust_daily)
            if not allowed:
                log.warn(f'⛔ 客户限流: {reason}')
                logger.warning(f'[EMAIL] Customer rate limit: {reason}')
                return DeliveryResult(False, 'email', config.get('name', ''), reason, sender=smtp_user)
        allowed, reason = check_global(global_hourly, global_daily)
        if not allowed:
            log.warn(f'⛔ 全局限流: {reason}')
            logger.warning(f'[EMAIL] Global rate limit: {reason}')
            return DeliveryResult(False, 'email', config.get('name', ''), reason, sender=smtp_user)

        log.info('✅ 速率限制检查通过')

        # ── 5. 构建邮件主题 ──────────────────────────────────────
        subject = f'{_rollback_prefix(message)}{message.product_name} {_pkg_type_label(message.package_type)} 发布了新版本'
        log.info(f'  主题: {subject}')

        # ── 6. 附件处理（新增详细日志）─────────────────────────
        sender_contact = self._load_sender_contact()


        email_msg = MIMEMultipart('mixed')
        email_msg['Subject'] = subject
        email_msg['From'] = formataddr((from_name, smtp_user))
        email_msg['To'] = ', '.join(to_list)

        # 判断是否可能附加
        attachment_possible = (
            message.file_size > 0
            and message.file_size <= max_attach
            and message.download_url
        )

        logger.info(f'[EMAIL] Attachment check: file_size={message.file_size}, '
                    f'max_attach={max_attach}, download_url={bool(message.download_url)}, '
                    f'possible={attachment_possible}')

        log.info(f'📎 附件检查: 文件大小={message.size_display}, 上限={customer_attach_mb or "默认"}MB')
        if not message.download_url:
            log.warn('⚠️ 无下载链接，无法添加附件')
        elif message.file_size > max_attach:
            log.warn(f'⚠️ 文件大小({message.size_display})超过上限({max_attach//1048576}MB)，不附加')
        elif message.file_size <= 0:
            log.warn('⚠️ 文件大小为0，跳过附件')

        attachment_attached = False
        if attachment_possible:
            log.info('📥 开始下载附件...')
            attachment_attached = self._attach_file(email_msg, message, max_attach, log)
            if attachment_attached:
                log.ok(f'✅ 附件已成功附加: {message.file_name}')
                logger.info(f'[EMAIL] Attachment attached: {message.file_name}')
            else:
                log.warn(f'⚠️ 附件下载失败，将在正文中显示下载链接')
                logger.warning(f'[EMAIL] Attachment download failed: {message.file_name}')
        else:
            log.info('⏭️ 不满足附件附加条件，跳过')

        # 按钮只在无附件时显示;有附件时改为在 MD5 校验段后用文字提示兜底
        html_body = _format_html_body(message, message.is_rollback, sender_contact=sender_contact, show_download_btn=not attachment_attached)
        # ── 7. 构建HTML正文（含下载提示）─────────────────────
        if not message.is_rollback:
            md5_guide = f'''
            <div style="margin-top:12px;padding:12px;background:#f0f8ff;border-radius:4px;border-left:4px solid #1e90ff;font-size:13px;color:#333">
                <strong style="color:#1e90ff">🔍 MD5 校验</strong><br/>
                <span style="color:#666">建议下载后校验文件完整性：</span><br/>
                <strong>Linux/macOS：</strong><code style="background:#e8e8e8;padding:2px 6px;border-radius:3px;font-family:monospace">md5sum {message.file_name}</code><br/>
                <strong>Windows：</strong><code style="background:#e8e8e8;padding:2px 6px;border-radius:3px;font-family:monospace">certutil -hashfile {message.file_name} MD5</code><br/>
                预期值：<code style="background:#e8e8e8;padding:2px 6px;border-radius:3px;font-family:monospace">{message.md5_hash}</code>
            </div>
            '''
            # 兜底文字:仅在有附件时显示,告诉用户附件有问题可走链接(替代按钮)
            fallback_note = ''
            if attachment_attached and message.download_url:
                fallback_note = f'''
                <p style="color:#d0021b;font-size:14px;margin:16px 0 8px 0;padding:10px;background:#fff3cd;border-radius:4px;border-left:4px solid #d0021b">
                ⚠️ 如果附件下载失败或者校验异常，可以通过此链接直接下载附件：<a href="{message.download_url}" style="color:#d0021b;font-weight:bold">点此下载</a>
                </p>
                '''

            combined = md5_guide + fallback_note
            html_body = html_body.replace('</table>\n</td></tr>', f'</table>\n{combined}\n</td></tr>', 1)

        email_msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        # ── 8. SMTP 发送（已有日志）─────────────────────────────
        log.info(f'📤 开始SMTP发送: {smtp_host}:{smtp_port} (加密: {config.get("smtp_encryption", "ssl")})')
        log.info(f'  发件人: {formataddr((from_name, smtp_user))}')
        log.info(f'  收件人: {", ".join(to_list)}')

        server = None
        try:
            t0 = time.monotonic()
            encryption = (config.get('smtp_encryption') or '').lower()
            if encryption not in ('ssl', 'starttls'):
                log.error(f'❌ 不支持的加密方式: {encryption}')
                return DeliveryResult(False, 'email', config.get('name', ''),
                                      'Encryption required: SSL or STARTTLS', sender=smtp_user)

            if encryption == 'ssl':
                log.info(f'🔒 使用SSL连接 {smtp_host}:{smtp_port}')
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)
            else:
                log.info(f'🔓 使用STARTTLS连接 {smtp_host}:{smtp_port}')
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
                server.ehlo()
                caps = sorted((server.esmtp_features or {}).keys())
                log.info(f'  ESMTP能力: {", ".join(caps)}')
                if not server.has_extn('starttls'):
                    log.error('❌ 服务器不支持STARTTLS')
                    return DeliveryResult(False, 'email', config.get('name', ''),
                                          'Server does not advertise STARTTLS', sender=smtp_user)
                log.info('🔒 升级TLS...')
                server.starttls()
                server.ehlo()

            log.info(f'✅ 连接成功 ({(time.monotonic()-t0)*1000:.0f}ms)')
            log.info(f'🔑 登录用户: {smtp_user}')
            t_login = time.monotonic()
            try:
                server.sock.settimeout(60)  # 30 秒超时，覆盖 smtplib 的 timeout
                log.info(f'Socket timeout set to 30s')
            except Exception as e:
                log.warn(f'Could not set socket timeout: {e}')
            server.login(smtp_user, smtp_password)
            log.info(f'✅ 登录成功 ({(time.monotonic()-t_login)*1000:.0f}ms)')

            payload_size = len(email_msg.as_string().encode('utf-8'))
            log.info(f'📨 发送邮件 (大小: {payload_size//1024}KB, 附件: {"有" if attachment_attached else "无"})')
            t_send = time.monotonic()
            logger.info('[EMAIL] Before sendmail')

            refused = server.sendmail(smtp_user, to_list, email_msg.as_string())
            logger.info('[EMAIL] After sendmail, refused=' + str(refused))
            log.info(f'✅ 发送完成 ({(time.monotonic()-t_send)*1000:.0f}ms)')

            if refused:
                refused_str = '; '.join(f'{rcpt}={code} {msg}' for rcpt, (code, msg) in refused.items())
                log.warn(f'⚠️ 部分收件人拒绝: {refused_str}')
                server.quit()
                return DeliveryResult(False, 'email', config.get('name', ''),
                                      f'Recipients refused: {refused_str}', sender=smtp_user)

            server.quit()
            log.ok(f'✅ 邮件发送成功! 总耗时 {(time.monotonic()-t0)*1000:.0f}ms')

            if ch_id or cust_id:
                limiter_record(ch_id, cust_id)
                log.info(f'📊 已记录限流计数: channel={ch_id}, customer={cust_id}')

            logger.info(f'[EMAIL] Sent successfully to {len(to_list)} recipients: {message.file_name}')
            return DeliveryResult(True, 'email', config.get('name', ''), sender=smtp_user)

        except smtplib.SMTPAuthenticationError as e:
            log.error(f'❌ SMTP认证失败: {e.smtp_code} {e.smtp_error}')
            logger.error(f'[EMAIL] SMTP auth failed: {e.smtp_code} {e.smtp_error}')
            return DeliveryResult(False, 'email', config.get('name', ''), f'{e.smtp_code} {e.smtp_error}', sender=smtp_user)
        except smtplib.SMTPRecipientsRefused as e:
            refused_str = '; '.join(f'{rcpt}={code} {msg}' for rcpt, (code, msg) in e.recipients.items())
            log.error(f'❌ 收件人全部拒绝: {refused_str}')
            logger.error(f'[EMAIL] Recipients refused: {refused_str}')
            return DeliveryResult(False, 'email', config.get('name', ''), f'Recipients refused: {refused_str}', sender=smtp_user)
        except smtplib.SMTPServerDisconnected as e:
            log.error(f'❌ 服务器断开: {e}')
            logger.error(f'[EMAIL] Server disconnected: {e}')
            return DeliveryResult(False, 'email', config.get('name', ''), f'Server disconnected: {e}', sender=smtp_user)
        except smtplib.SMTPException as e:
            log.error(f'❌ SMTP错误: {e}')
            logger.error(f'[EMAIL] SMTP error: {e}')
            return DeliveryResult(False, 'email', config.get('name', ''), str(e), sender=smtp_user)
        except TimeoutError as e:
            log.error(f'❌ 连接超时: {e}')
            logger.error(f'[EMAIL] Timeout: {e}')
            return DeliveryResult(False, 'email', config.get('name', ''), f'Timeout: {e}', sender=smtp_user)
        except OSError as e:
            log.error(f'❌ 网络错误: {e}')
            logger.error(f'[EMAIL] Network error: {e}')
            return DeliveryResult(False, 'email', config.get('name', ''), f'Network: {e}', sender=smtp_user)
        except Exception as e:
            log.error(f'❌ 未知错误: {type(e).__name__}: {e}')
            logger.error(f'[EMAIL] Unexpected error: {type(e).__name__}: {e}')
            return DeliveryResult(False, 'email', config.get('name', ''), f'{type(e).__name__}: {e}', sender=smtp_user)
        finally:
            if server is not None:
                try:
                    server.quit()
                except Exception:
                    pass

    def _attach_file(self, msg: MIMEMultipart, message: NotificationMessage, max_size: int, log=None) -> bool:
        """下载并附加文件到邮件。返回 True 表示成功附加。

        增加详细日志以排查附件丢失问题。
        """
        if log is None:
            log = _NullLogWriter()

        log.info(f'📥 _attach_file: 开始下载 {message.download_url}')
        log.info(f'  文件名: {message.file_name}, 大小限制: {max_size}B')

        try:
            # ── 发起下载请求 ─────────────────────────────────────
            # 注意：这里没有携带 Cookie，绿盟下载需要 PHPSESSID
            log.info('  GET请求 (不带Cookie)')
            resp = requests.get(message.download_url, timeout=30, stream=True)
            log.info(f'  响应状态码: {resp.status_code}')

            # 检查302重定向（缺少Session）
            if resp.status_code == 302:
                location = resp.headers.get('Location', '')
                log.warn(f'⚠️ 收到302重定向 -> {location}')
                log.warn('⚠️ 可能原因: 缺少PHPSESSID Cookie (未登录)')
                logger.warning(f'[EMAIL] Attachment download 302 redirect: {message.download_url} -> {location}')
                return False

            if resp.status_code != 200:
                log.warn(f'⚠️ HTTP状态码异常: {resp.status_code}')
                logger.warning(f'[EMAIL] Attachment download HTTP {resp.status_code}: {message.download_url}')
                return False

            # ── 读取内容 ─────────────────────────────────────────
            content = resp.content
            content_size = len(content)
            log.info(f'  下载完成: {content_size}B')

            if content_size == 0:
                log.warn('⚠️ 下载内容为空')
                return False

            if content_size > max_size:
                log.warn(f'⚠️ 文件大小 {content_size}B 超过限制 {max_size}B')
                return False

            # ── 构建 MIME 附件 ──────────────────────────────────
            log.info('  📎 构建MIME附件...')
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(content)
            encoders.encode_base64(part)

            # 提取文件名
            filename = message.file_name
            cd = resp.headers.get('Content-Disposition', '')
            import re
            fn_match = re.search(r'filename="?([^"\s;]+)"?', cd)
            if fn_match:
                filename = fn_match.group(1)
                log.info(f'  从Content-Disposition提取文件名: {filename}')

            part.add_header('Content-Disposition', f'attachment; filename="{filename}"')
            msg.attach(part)

            log.ok(f'✅ 附件已挂载: {filename} ({content_size}B)')
            logger.info(f'[EMAIL] Attachment attached: {filename} ({content_size}B)')
            return True

        except requests.Timeout as e:
            log.warn(f'⚠️ 下载超时: {e}')
            logger.warning(f'[EMAIL] Attachment download timeout: {message.download_url}')
            return False
        except requests.ConnectionError as e:
            log.warn(f'⚠️ 连接错误: {e}')
            logger.warning(f'[EMAIL] Attachment connection error: {message.download_url} - {e}')
            return False
        except requests.RequestException as e:
            log.warn(f'⚠️ 请求异常: {e}')
            logger.warning(f'[EMAIL] Attachment request error: {message.download_url} - {e}')
            return False
        except Exception as e:
            log.warn(f'⚠️ 附件处理异常: {type(e).__name__}: {e}')
            logger.warning(f'[EMAIL] Attachment unexpected error: {type(e).__name__}: {e}')
            return False
