"""企业微信机器人通知器."""

import json
import requests

from src.core.logger import get_logger
from src.notifiers._log import get_log_writer
from src.notifiers.base import (
    BaseNotifier, NotificationMessage, DeliveryResult,
    _format_markdown_body, format_template_bodies,
)


logger = get_logger('wecom')


class WecomNotifier(BaseNotifier):
    channel_type = 'wecom'

    def send(self, message: NotificationMessage, config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')

        log = get_log_writer(config, 'wecom',
                             config.get('_channel_id', 0),
                             config.get('name', ''))
        log.info(f'开始推送 product={message.product_name} v={message.package_version} file={message.file_name}')
        if not webhook_url:
            log.error('webhook_url 为空,推送终止')
            return DeliveryResult(False, 'wecom', '', 'Missing webhook_url')

        name = config.get('name', '')

        # NOTE: WeCom Bot config.secret 字段当前未使用。
        # 企业微信机器人(群机器人 webhook)目前没有像钉钉那样强制启用加签的安全设置,
        # 用 webhook URL 上的 key 即可。本项目不实现 WeCom 加签验签,secret 仅作为
        # 预留字段存在以便未来对齐多平台。
        # 其他 channel_type(dingtalk)的 send() 会读 config['secret'] 并用于 HMAC-SHA256 加签。
        _ = config.get('secret', '')  # noqa: F841 — 显式读取以表明 secret 字段无副作用

        # 产品通知（有 product_name）走完整格式，包含元数据行
        # 系统事件通知（product_name 为空）直接发 description_full 纯文本
        if message.product_name:
            template = config.get('_template', 'full')
            if template == 'full':
                # wecom 私有切分路径,行为不变(wecom.send 内部仍走 _format_markdown_body + _split_bodies)
                content = _format_markdown_body(message, for_rollback=message.is_rollback)
                MAX_BYTES = 4000
                if len(content.encode('utf-8')) <= MAX_BYTES:
                    bodies = [content]
                else:
                    bodies = self._split_bodies(content, MAX_BYTES)
            else:
                # 模板 B/C:走 base 的 strip/brief 实现,适配 wecom markdown line_break=\n
                bodies = format_template_bodies(template, message, line_break='\n')
        else:
            content = message.description_full or ''
            if not content:
                log.error('消息体为空 (product_name 缺 + description_full 缺)')
                return DeliveryResult(False, 'wecom', name, 'Empty message body')
            bodies = [content]   # 系统通知非切分:直接当 1 part 发

        # 注意:前面 if message.product_name: 已经产出 bodies。
        # 非 product 路径(content = description_full)走 else 分支后,此处不再重复切分。

        log.info(f'  parts={len(bodies)}, sizes={[len(b.encode("utf-8")) for b in bodies]}B')

        for i, body in enumerate(bodies):
            payload = {
                'msgtype': 'markdown',
                'markdown': {'content': body}
            }
            try:
                resp = requests.post(webhook_url, json=payload, timeout=10)
                result = resp.json()
                log.info(f'  HTTP {resp.status_code}  errcode={result.get("errcode")} errmsg={result.get("errmsg", "")}')
                if result.get('errcode') != 0:
                    log.error(f'Wecom error [{i+1}/{len(bodies)}]: {result.get("errmsg", "unknown")}')
                    return DeliveryResult(False, 'wecom', name,
                                         f"Wecom error [{i+1}/{len(bodies)}]: {result.get('errmsg', 'unknown')}")
                log.ok(f'part {i+1}/{len(bodies)} sent')
            except Exception as e:
                log.error(f'HTTP exception: {type(e).__name__}: {e}')
                return DeliveryResult(False, 'wecom', name,
                                      f"Wecom error [{i+1}/{len(bodies)}]: {str(e)}")

        log.ok(f'全部 {len(bodies)} 条推送成功')
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