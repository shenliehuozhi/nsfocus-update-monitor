"""钉钉机器人通知器."""

import json
import re

import requests

from src.notifiers.base import BaseNotifier, NotificationMessage, DeliveryResult, _format_markdown_bodies, _sign_url


class DingtalkNotifier(BaseNotifier):
    channel_type = 'dingtalk'

    # 紧急度 → 钉钉 markdown 颜色(<font color=""> 支持)
    URGENCY_COLOR = {
        'normal':   '#1689ed',  # 蓝色 - 信息
        'high':     '#ff8800',  # 橙色 - 警示
        'critical': '#f5454a',  # 红色 - 紧急
    }
    ROLLBACK_COLOR = '#ff4d4f'

    @classmethod
    def _title_color(cls, msg: NotificationMessage) -> str:
        if msg.is_rollback:
            return cls.ROLLBACK_COLOR
        return cls.URGENCY_COLOR.get(msg.urgency, cls.URGENCY_COLOR['normal'])

    @classmethod
    def _wrap_title_with_color(cls, body: str, msg: NotificationMessage) -> str:
        """Wrap the first line's bold title with a <font color> span.

        DingTalk markdown client supports <font color> HTML tags (verified via
        marked.js sanitization off). WeCom markdown ignores them silently —
        so it's safe to apply this transformation in DingTalk consumer before
        posting.

        The first line is whatever sits before the FIRST separator (whichever
        of '\\n' / '<br/>' / '<br>' is present). After line_break substitution
        (line_break='<br/>'), '\\n' is gone, so we must check multiple separators.
        """
        color = cls._title_color(msg)
        # Detect separator (priority: <br/> (5 bytes) → <br> → \n)
        sep = None
        for candidate in ('<br/>', '<br>', '\n'):
            if candidate in body:
                sep = candidate
                break
        if sep is None:
            return body  # no separator at all: single-line, still safe to wrap (no-op regex will skip)
        first_line, rest = body.split(sep, 1)
        # Match `<icon> **<title>**` pattern (icon is 1 emoji like ℹ️/⚠️/🔴)
        m = re.match(r'^(\S+\s+\*\*.*\*\*)\s*$', first_line)
        if not m:
            return body
        new_first = f'<font color="{color}">{m.group(1)}</font>'
        return new_first + sep + (rest if rest else '')

    # Label → color (钉钉 meta 行 label 染橙色,让字段名跳出来)
    LABEL_COLOR = '#c27800'  # 项目邮件 cfg-hint 同款橙色,跨视觉系统一致

    # 已知 label 名字(必须跟 base.py meta 列表保持一致)
    # 修改 _format_markdown_body / _format_markdown_bodies 时,这里也要同步加
    KNOWN_LABELS = (
        '发布页面', '文件名称', '版本信息', '文件大小',
        'MD5', '发布时间', '下载地址',
    )
    _LABEL_RE = re.compile(r'^(' + '|'.join(KNOWN_LABELS) + r')\s*:')

    @classmethod
    def _wrap_labels_with_color(cls, body: str) -> str:
        """Wrap each meta-row label with <font color>.

        Iterate through body line by line (separator-agnostic), apply label
        wrap only to lines whose first non-whitespace token is one of the known
        field labels. Description block, title, and "(i/N)" markers are left
        untouched.
        """
        # Detect separator first (钉钉版通常用 <br/>)
        sep = None
        for candidate in ('<br/>', '<br>', '\n'):
            if candidate in body:
                sep = candidate
                break
        if sep is None:
            return body
        lines = body.split(sep)
        out = []
        for line in lines:
            m = cls._LABEL_RE.match(line)
            if m:
                # Only color the bare label text, NOT trailing whitespace/padding
                # (so visual alignment of "MD5       :" remains unchanged).
                # `m.group(1)` is the label; rest starts with leading spaces (MD5 padding).
                label = m.group(1)
                rest = line[len(label):]
                line = f'<font color="{cls.LABEL_COLOR}">{label}</font>{rest}'
            out.append(line)
        return sep.join(out)

    def send(self, message: NotificationMessage, config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')
        secret = config.get('secret', '')
        if not webhook_url:
            return DeliveryResult(False, 'dingtalk', '', 'Missing webhook_url')

        # 钉钉机器人后台 3 选 1 安全设置:加签 / 自定义关键词 / IP 白名单
        # 仅当后台启用了「加签」且本项目填了 secret 时,才能成功推送。
        # 算法见 _sign_url (base.py) — 通用加签,本渠道参数:timestamp 毫秒 + base64 url-quote。
        url = _sign_url(webhook_url, secret, timestamp_unit='ms', url_quote=True)
        # 钉钉 markdown 客户端不识别 '\n' 作为换行,使用 '<br/>' 强制换行
        bodies = _format_markdown_bodies(message, message.is_rollback, line_break='<br/>')
        # 给 title 一行加 <font color> + 给每个 meta 行 label 染橙色
        bodies = [self._wrap_labels_with_color(self._wrap_title_with_color(b, message))
                  for b in bodies]
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
