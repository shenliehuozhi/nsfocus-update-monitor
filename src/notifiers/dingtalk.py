"""钉钉机器人通知器."""

import json
import requests

from src.notifiers.base import BaseNotifier, NotificationMessage, DeliveryResult, _format_markdown_bodies, _sign_url


class DingtalkNotifier(BaseNotifier):
    channel_type = 'dingtalk'

    def send(self, message: NotificationMessage, config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')
        secret = config.get('secret', '')
        if not webhook_url:
            return DeliveryResult(False, 'dingtalk', '', 'Missing webhook_url')

        # 钉钉机器人后台 3 选 1 安全设置:加签 / 自定义关键词 / IP 白名单
        # 仅当后台启用了「加签」且本项目填了 secret 时,才能成功推送。
        # 算法见 _sign_url (base.py) — 通用加签,本渠道参数:timestamp 毫秒 + base64 url-quote。
        url = _sign_url(webhook_url, secret, timestamp_unit='ms', url_quote=True)
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
