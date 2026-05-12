"""邮件通知器 —— 支持附件下载."""

import os
import smtplib
import tempfile
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


class EmailNotifier(BaseNotifier):
    channel_type = 'email'

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
        # Rule-level override: attachment size
        rule_attach_mb = int(config.get('attachment_max_mb', 0))
        max_attach = (rule_attach_mb * 1048576) if rule_attach_mb > 0 else ATTACHMENT_MAX_SIZE

        if not smtp_host or not to_list:
            return DeliveryResult(False, 'email', config.get('name', ''),
                                  'Missing SMTP config or recipient list')

        subject = f'{_rollback_prefix(message)}{message.product_name} {_pkg_type_label(message.package_type)} 发布了新版本'

        html_body = _format_html_body(message, message.is_rollback)

        # Build email
        msg = MIMEMultipart('mixed')
        msg['Subject'] = subject
        msg['From'] = formataddr((from_name, smtp_user))
        msg['To'] = ', '.join(to_list)
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        # Download and attach file if applicable
        attachment_downloaded = False
        if (not message.is_rollback
                and message.file_size > 0
                and message.file_size <= max_attach
                and message.download_url):
            attachment_downloaded = self._attach_file(msg, message, max_attach)

        # Note about large files
        if not attachment_downloaded and not message.is_rollback:
            note = MIMEText(
                f'\n\n提示: 文件大小({message.size_display})超过附件上限，请点击下载链接获取。',
                'plain', 'utf-8'
            )
            msg.attach(note)

        try:
            if smtp_port == 465:
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
                server.starttls()

            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, to_list, msg.as_string())
            server.quit()

            logger.info(f'Email sent to {len(to_list)} recipients via {smtp_host}:{smtp_port}')
            return DeliveryResult(True, 'email', config.get('name', ''))
        except smtplib.SMTPAuthenticationError as e:
            err = f'SMTP认证失败，请检查邮箱地址和授权密码（非登录密码）'
            logger.error(f'{err}: {e}')
            return DeliveryResult(False, 'email', config.get('name', ''), err)
        except smtplib.SMTPConnectError as e:
            err = f'无法连接 {smtp_host}:{smtp_port}，请检查SMTP服务器地址和端口'
            logger.error(f'{err}: {e}')
            return DeliveryResult(False, 'email', config.get('name', ''), err)
        except smtplib.SMTPServerDisconnected as e:
            err = f'SMTP服务器断开连接，非SSL端口(非465)需服务器支持STARTTLS'
            logger.error(f'{err}: {e}')
            return DeliveryResult(False, 'email', config.get('name', ''), err)
        except smtplib.SMTPException as e:
            err = f'SMTP错误: {e}'
            logger.error(f'{err}')
            return DeliveryResult(False, 'email', config.get('name', ''), str(e))
        except Exception as e:
            logger.error(f'Email send failed: {e}')
            return DeliveryResult(False, 'email', config.get('name', ''), str(e))

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
