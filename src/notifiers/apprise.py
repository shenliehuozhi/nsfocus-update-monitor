"""Apprise 通知器 — 通过 Apprise API 发送通知."""

import requests

from src.notifiers.base import BaseNotifier, NotificationMessage, DeliveryResult


class AppriseNotifier(BaseNotifier):
    channel_type = 'apprise'

    def send(self, message: NotificationMessage, config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')
        if not webhook_url:
            return DeliveryResult(False, 'apprise', '', 'Missing webhook_url')

        body = self._build_body(message)

        try:
            resp = requests.post(webhook_url, json=body, timeout=15)
            if resp.status_code in (200, 200, 201):
                return DeliveryResult(True, 'apprise', config.get('name', ''))
            else:
                return DeliveryResult(False, 'apprise', config.get('name', ''),
                                     f'HTTP {resp.status_code}: {resp.text[:100]}')
        except Exception as e:
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
