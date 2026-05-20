"""Apprise 通知器 — 通过 Apprise API 发送通知."""

import requests

from src.notifiers.base import BaseNotifier, NotificationMessage, DeliveryResult

# 消息体最大字节数（超过则拆分）
_MAX_BYTES = 4000


class AppriseNotifier(BaseNotifier):
    channel_type = 'apprise'

    def send(self, message: NotificationMessage, config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')
        if not webhook_url:
            return DeliveryResult(False, 'apprise', '', 'Missing webhook_url')

        template = config.get('template', 'full')
        body_gen = self._build_simple if template == 'simple' else self._build_full
        title = self._build_title(message)
        parts = body_gen(message, title)

        results = []
        for part in parts:
            body = part['body']
            try:
                resp = requests.post(webhook_url, json={'title': title, 'body': body}, timeout=15)
                if resp.status_code in (200, 201):
                    results.append(True)
                else:
                    results.append(False)
            except Exception:
                results.append(False)

        success = all(results) if parts else False
        if not success:
            return DeliveryResult(False, 'apprise', config.get('name', ''), 'One or more parts failed')
        return DeliveryResult(True, 'apprise', config.get('name', ''))

    def _build_title(self, msg: NotificationMessage) -> str:
        icon = '⚠️ 撤回 ' if msg.is_rollback else '🔔 '
        return f'{icon}{msg.product_name} {msg.package_version}'

    def _build_simple(self, msg: NotificationMessage, title: str) -> list[dict]:
        """简易模板：只推送关键字段，无描述"""
        lines = [
            f'产品: {msg.product_name}',
            f'版本: {msg.version_branch}',
            f'类型: {msg.package_type}',
            f'文件: {msg.file_name}',
        ]
        if msg.is_rollback:
            lines.insert(0, '⚠️ 此软件包已被撤回，请暂缓升级')
        if msg.download_url:
            lines.append(f'下载: {msg.download_url}')
        body = '\n'.join(lines)
        return [{'body': body}]

    def _build_full(self, msg: NotificationMessage, title: str) -> list[dict]:
        """完整模板：推送全部字段，超长自动拆分为多条消息"""
        lines = [
            f'产品: {msg.product_name}',
            f'版本: {msg.version_branch}',
            f'类型: {msg.package_type}',
            f'文件: {msg.file_name}',
            f'大小: {msg.size_display}',
        ]
        if msg.is_rollback:
            lines.insert(0, '⚠️ 此软件包已被撤回，请暂缓升级')
        if msg.description_summary:
            lines.append(f'📋 {msg.description_summary}')
        if msg.min_sys_version:
            lines.append(f'⚠️ 依赖: 系统版本 ≥ {msg.min_sys_version}')
        if msg.download_url:
            lines.append(f'📥 下载: {msg.download_url}')

        body = '\n'.join(lines)
        return self._split_body(body, title)

    def _split_body(self, body: str, title: str) -> list[dict]:
        """将超长 body 拆分为多条，每条不超过 _MAX_BYTES 字节。"""
        parts = []
        lines = body.split('\n')
        current = ''
        for line in lines:
            candidate = current + ('\n' if current else '') + line
            if len(candidate.encode('utf-8')) > _MAX_BYTES:
                if current:
                    parts.append({'body': current})
                if len(line.encode('utf-8')) > _MAX_BYTES:
                    encoded = line.encode('utf-8')
                    offset = 0
                    while offset < len(encoded):
                        chunk = encoded[offset:offset + _MAX_BYTES - 3]
                        try:
                            parts.append({'body': chunk.decode('utf-8')})
                        except UnicodeDecodeError:
                            parts.append({'body': encoded[offset:offset + _MAX_BYTES - 3].decode('utf-8', errors='ignore')})
                        offset += _MAX_BYTES - 3
                    current = ''
                else:
                    current = line
            else:
                current = candidate
        if current:
            parts.append({'body': current})
        return parts if parts else [{'body': body}]
