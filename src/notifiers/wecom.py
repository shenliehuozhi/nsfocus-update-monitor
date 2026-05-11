"""企业微信机器人通知器."""

import json
import requests

from src.notifiers.base import BaseNotifier, NotificationMessage, DeliveryResult, _format_markdown_bodies


class WecomNotifier(BaseNotifier):
    channel_type = 'wecom'

    def send(self, message: NotificationMessage, config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')
        if not webhook_url:
            return DeliveryResult(False, 'wecom', '', 'Missing webhook_url')

        bodies = _format_markdown_bodies(message, message.is_rollback)
        name = config.get('name', '')

        for i, body in enumerate(bodies):
            payload = {
                'msgtype': 'markdown',
                'markdown': {'content': body}
            }
            try:
                resp = requests.post(webhook_url, json=payload, timeout=10)
                result = resp.json()
                if result.get('errcode') != 0:
                    return DeliveryResult(False, 'wecom', name,
                                          f"Wecom error [{i+1}/{len(bodies)}]: {result.get('errmsg', 'unknown')}")
            except Exception as e:
                return DeliveryResult(False, 'wecom', name,
                                      f"Wecom error [{i+1}/{len(bodies)}]: {str(e)}")

        return DeliveryResult(True, 'wecom', name,
                              f"Sent {len(bodies)} message(s)" if len(bodies) > 1 else '')

    def send_confirmation(self, message: NotificationMessage,
                          results: list[DeliveryResult], config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')
        if not webhook_url:
            return DeliveryResult(False, 'wecom', '', 'Missing webhook_url')

        status_lines = ['✅ **推送完成**', '', f'> {message.product_name} {message.package_version}', '']
        for r in results:
            icon = '✅' if r.success else '❌'
            status_lines.append(f'{icon} {r.channel_type} {r.channel_name}')
            if r.error_message:
                status_lines.append(f'   _{r.error_message}_')

        payload = {'msgtype': 'markdown', 'markdown': {'content': '\n'.join(status_lines)}}
        try:
            requests.post(webhook_url, json=payload, timeout=10)
        except Exception:
            pass
        return DeliveryResult(True, 'wecom', config.get('name', ''))
