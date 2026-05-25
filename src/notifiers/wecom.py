"""企业微信机器人通知器."""

import json
import requests

from src.notifiers.base import BaseNotifier, NotificationMessage, DeliveryResult, _format_markdown_body


class WecomNotifier(BaseNotifier):
    channel_type = 'wecom'

    def send(self, message: NotificationMessage, config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')
        if not webhook_url:
            return DeliveryResult(False, 'wecom', '', 'Missing webhook_url')

        name = config.get('name', '')

        # 产品通知（有 product_name）走完整格式，包含元数据行
        # 系统事件通知（product_name 为空）直接发 description_full 纯文本
        if message.product_name:
            content = _format_markdown_body(message, for_rollback=message.is_rollback)
        else:
            content = message.description_full or ''
            if not content:
                return DeliveryResult(False, 'wecom', name, 'Empty message body')

        # 按 4096 字节硬限分割（WeCom 单条 markdown 上限）
        MAX_BYTES = 4000
        encoded = content.encode('utf-8')
        if len(encoded) <= MAX_BYTES:
            bodies = [content]
        else:
            bodies = self._split_bodies(content, MAX_BYTES)

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

    def _split_bodies(self, text: str, max_bytes: int = 4000) -> list[str]:
        """将长文本按字节数分割为多条，每条带 (n/m) 分页标记。"""
        from src.notifiers.base import _chunk_text
        encoded = text.encode('utf-8')
        parts = []
        current = b''
        total_len = 0

        for line in text.split('\n'):
            line_bytes = line.encode('utf-8')
            overhead = len(f'\n\n({len(parts)+2}/m)\n'.encode('utf-8'))
            if len(current) + len(line_bytes) + overhead > max_bytes:
                if current:
                    parts.append(current.decode('utf-8'))
                current = b''
            current += line_bytes + b'\n'

        if current:
            parts.append(current.decode('utf-8'))

        total = len(parts)
        result = []
        for i, part in enumerate(parts):
            part = part.rstrip('\n')
            if total > 1:
                part += f'\n\n({i+1}/{total})'
            result.append(part)

        return result if result else [text[:max_bytes]]

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