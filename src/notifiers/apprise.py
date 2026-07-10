"""Apprise 通知器 — 通过 Apprise API 发送通知."""

import requests

from src.core.logger import get_logger
from src.notifiers._log import get_log_writer
from src.notifiers.base import BaseNotifier, NotificationMessage, DeliveryResult


logger = get_logger('apprise')


class AppriseNotifier(BaseNotifier):
    channel_type = 'apprise'

    def send(self, message: NotificationMessage, config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')

        log = get_log_writer(config, 'apprise',
                             config.get('_channel_id', 0),
                             config.get('name', ''))
        log.info(f'开始推送 product={message.product_name} v={message.package_version}')
        if not webhook_url:
            log.error('webhook_url 为空,推送终止')
            return DeliveryResult(False, 'apprise', '', 'Missing webhook_url')
        body = self._build_body(message)
        body_bytes = len(str(body).encode('utf-8'))
        log.info(f'  payload bytes={body_bytes}')

        try:
            resp = requests.post(webhook_url, json=body, timeout=15)
            log.info(f'  HTTP {resp.status_code}  text={resp.text[:80]}')
            if resp.status_code in (200, 200, 201):
                log.ok('推送成功')
                return DeliveryResult(True, 'apprise', config.get('name', ''))
            log.error(f'HTTP {resp.status_code}')
            return DeliveryResult(False, 'apprise', config.get('name', ''),
                                 f'HTTP {resp.status_code}: {resp.text[:100]}')
        except Exception as e:
            log.error(f'HTTP exception: {type(e).__name__}: {e}')
            return DeliveryResult(False, 'apprise', config.get('name', ''), str(e))

    def _build_body(self, msg: NotificationMessage) -> dict:
        """Build a generic notification body compatible with Apprise API."""
        title = f'{"⚠️ 撤回 " if msg.is_rollback else "🔔 "}{msg.product_name} {msg.package_version}'
        body_lines = [
            f'产品: {msg.product_name}',
            f'版本: {msg.version_branch}',
            f'类型: {msg.package_type}',
            f'文件: {msg.file_name}',
            f'大小: {msg.size_display}',
        ]
        if msg.is_rollback:
            body_lines.insert(0, '⚠️ 此软件包已被撤回，请暂缓升级')
        if msg.description_summary:
            body_lines.append(f'📋 {msg.description_summary}')
        if msg.min_sys_version:
            body_lines.append(f'⚠️ 依赖: 系统版本 ≥ {msg.min_sys_version}')
        if msg.download_url:
            body_lines.append(f'📥 下载: {msg.download_url}')
        return {
            'title': title,
            'body': '\n'.join(body_lines),
        }
