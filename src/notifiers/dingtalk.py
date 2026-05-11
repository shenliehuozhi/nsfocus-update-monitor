"""钉钉机器人通知器."""

import json
import time
import hmac
import hashlib
import base64
import requests
from urllib.parse import quote_plus

from src.notifiers.base import BaseNotifier, NotificationMessage, DeliveryResult, _format_markdown_bodies


class DingtalkNotifier(BaseNotifier):
    channel_type = 'dingtalk'

    def _sign_url(self, webhook_url: str, secret: str) -> str:
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f'{timestamp}\n{secret}'
        hmac_code = hmac.new(
            secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        sign = quote_plus(base64.b64encode(hmac_code).decode('utf-8'))
        return f'{webhook_url}&timestamp={timestamp}&sign={sign}'

    def send(self, message: NotificationMessage, config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')
        secret = config.get('secret', '')
        if not webhook_url:
            return DeliveryResult(False, 'dingtalk', '', 'Missing webhook_url')

        url = self._sign_url(webhook_url, secret) if secret else webhook_url
        bodies = _format_markdown_bodies(message, message.is_rollback)
        name = config.get('name', '')
        title = f'{"⚠️ 撤回" if message.is_rollback else "🔔 升级通知"} {message.product_name} {message.package_version}'

        for i, body in enumerate(bodies):
            payload = {
                'msgtype': 'markdown',
                'markdown': {
                    'title': title,
                    'text': body
                }
            }
            try:
                resp = requests.post(url, json=payload, timeout=10)
                result = resp.json()
                if result.get('errcode') != 0:
                    return DeliveryResult(False, 'dingtalk', name,
                                          f"Dingtalk error [{i+1}/{len(bodies)}]: {result.get('errmsg', 'unknown')}")
            except Exception as e:
                return DeliveryResult(False, 'dingtalk', name,
                                      f"Dingtalk error [{i+1}/{len(bodies)}]: {str(e)}")

        return DeliveryResult(True, 'dingtalk', name,
                              f"Sent {len(bodies)} message(s)" if len(bodies) > 1 else '')

    def send_confirmation(self, message: NotificationMessage,
                          results: list[DeliveryResult], config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')
        if not webhook_url:
            return DeliveryResult(False, 'dingtalk', '', '')

        status_lines = ['✅ **推送完成**', '', f'> {message.product_name} {message.package_version}', '']
        for r in results:
            icon = '✅' if r.success else '❌'
            status_lines.append(f'{icon} {r.channel_type} {r.channel_name}')

        title = f'推送确认: {message.product_name}'
        payload = {'msgtype': 'markdown', 'markdown': {'title': title, 'text': '\n'.join(status_lines)}}
        try:
            requests.post(webhook_url, json=payload, timeout=10)
        except Exception:
            pass
        return DeliveryResult(True, 'dingtalk', config.get('name', ''))
