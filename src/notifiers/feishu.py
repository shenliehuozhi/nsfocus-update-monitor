"""飞书机器人通知器."""

import json
import requests

from src.notifiers.base import BaseNotifier, NotificationMessage, DeliveryResult


class FeishuNotifier(BaseNotifier):
    channel_type = 'feishu'

    def send(self, message: NotificationMessage, config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')
        if not webhook_url:
            return DeliveryResult(False, 'feishu', '', 'Missing webhook_url')

        # 飞书使用富文本 (post) 格式
        content = self._build_post(message)

        payload = {
            'msg_type': 'post',
            'content': {
                'post': {
                    'zh_cn': content
                }
            }
        }

        try:
            resp = requests.post(webhook_url, json=payload, timeout=10)
            result = resp.json()
            if result.get('code') == 0 or result.get('StatusCode') == 0:
                return DeliveryResult(True, 'feishu', config.get('name', ''))
            else:
                return DeliveryResult(False, 'feishu', config.get('name', ''),
                                      f"Feishu error: {result.get('msg', 'unknown')}")
        except Exception as e:
            return DeliveryResult(False, 'feishu', config.get('name', ''), str(e))

    def send_confirmation(self, message: NotificationMessage,
                          results: list[DeliveryResult], config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')
        if not webhook_url:
            return DeliveryResult(False, 'feishu', '', '')

        status_text = f'✅ 推送完成: {message.product_name} {message.package_version}\n'
        for r in results:
            icon = '✅' if r.success else '❌'
            status_text += f'{icon} {r.channel_type} {r.channel_name}\n'

        payload = {
            'msg_type': 'text',
            'content': {'text': status_text}
        }
        try:
            requests.post(webhook_url, json=payload, timeout=10)
        except Exception:
            pass
        return DeliveryResult(True, 'feishu', config.get('name', ''))

    def _build_post(self, msg: NotificationMessage) -> dict:
        """Build feishu post format content."""
        title = f'{"⚠️ 撤回" if msg.is_rollback else "🔔"} {msg.product_name} {msg.package_version}'

        paragraphs = [
            [{'tag': 'text', 'text': f'产品: {msg.product_name}'}],
            [{'tag': 'text', 'text': f'版本: {msg.version_branch}'}],
            [{'tag': 'text', 'text': f'类型: {msg.package_type}'}],
            [{'tag': 'text', 'text': f'文件: {msg.file_name}'}],
            [{'tag': 'text', 'text': f'大小: {msg.size_display}'}],
        ]

        if msg.is_rollback:
            paragraphs.insert(0, [{'tag': 'text', 'text': '⚠️ 此软件包已被撤回，请暂缓升级'}])

        if msg.description_summary:
            paragraphs.append([{'tag': 'text', 'text': f'📋 {msg.description_summary}'}])

        if msg.min_sys_version:
            paragraphs.append([{'tag': 'text', 'text': f'⚠️ 依赖: 系统版本 ≥ {msg.min_sys_version}'}])

        if msg.download_url:
            paragraphs.append([{'tag': 'a', 'text': '📥 下载升级包', 'href': msg.download_url}])

        return {
            'title': title,
            'content': paragraphs
        }
