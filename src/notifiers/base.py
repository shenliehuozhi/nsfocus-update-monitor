"""Base notifier + notification message format."""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# ── Attention line highlight ─────────────────────────────────────────────────
# Description lines containing "注意" are highlighted in orange.
# Used for both markdown (WeCom/DingTalk/Feishu) and HTML (email).

def _highlight_attention_lines(text: str, fmt: str = 'markdown') -> str:
    """Highlight any line containing '注意' with orange bold.

    Args:
        text: Raw description text (may contain multiple lines).
        fmt:  'markdown' → **bold** text (WeCom/DingTalk/Feishu)
              'html'     → <strong style="color:...">text</strong> (email)

    Returns:
        Text with highlighted lines unchanged when no '注意' is found.
    """
    if not text or '注意' not in text:
        return text

    ORANGE = '#f5a623'
    lines = text.split('\n')
    result_lines = []
    for line in lines:
        if '注意' in line:
            if fmt == 'html':
                line = f'<strong style="color:{ORANGE}">{line}</strong>'
            else:
                line = f'**{line}**'
        result_lines.append(line)
    return '\n'.join(result_lines)


# Package type Chinese name mapping
_PKG_TYPE_CN = {
    'sys': '系统包', 'rule': '规则包', 'nti': '威胁情报', 'av': '病毒库',
    'apprule': '应用规则', 'url': 'URL分类', 'wcs': '恶意站点', 'judge': '研判规则',
    'geo': '地理库', 'interface': '接口', 'special': '特殊', 'other': '其他',
    'merge': '合并包', 'client': '客户端', 'av_stream': '流式病毒库',
}


def _pkg_type_label(pkg_type: str) -> str:
    """Return Chinese+English label for a package type, e.g. '规则包 (rule)'."""
    cn = _PKG_TYPE_CN.get(pkg_type, pkg_type)
    return f'{cn} ({pkg_type})' if pkg_type and pkg_type != cn else cn


def _rollback_prefix(msg) -> str:
    """Return rollback prefix for email subject, empty string for normal."""
    return '⚠️【已撤回】' if msg.is_rollback else '🔔'


def _utc_to_cst_display(utc_str: str) -> str:
    """Convert UTC ISO string to CST display string for notifications.
    Input:  '2026-05-12T09:05:51' (UTC, from DB)
    Output: '2026-05-12 17:05:51' (CST, +8h)
    Returns original string if unparseable.
    """
    if not utc_str:
        return ''
    try:
        from datetime import datetime, timedelta, timezone
        utc = timezone.utc
        dt = datetime.strptime(utc_str[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=utc)
        cst = timezone(timedelta(hours=8))
        return dt.astimezone(cst).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return utc_str


def _build_chain(msg: 'NotificationMessage', db_path: str = '/root/nsfocus-monitor/data/nsfocus_monitor.db') -> tuple[list[str], str]:
    """Look up the chain for a notification message by matching source_url → relative path → paths chain.

    Returns (chain_list, full_detail_url). chain_list is empty if not found.
    """
    import hashlib, json
    if not msg.source_url:
        return [], ''

    BASE = 'https://update.nsfocus.com'
    rel_url = msg.source_url
    if rel_url.startswith(BASE):
        rel_url = rel_url[len(BASE):]

    try:
        import sqlite3
        conn = sqlite3.connect(db_path, timeout=10)
        cur = conn.execute(
            "SELECT package_type FROM content_sources WHERE id=? AND is_active=1",
            (msg.source_id,) if msg.source_id else (0,)
        )
        row = cur.fetchone()
        conn.close()
        if not row or not row[0]:
            return [], ''
        cfg = json.loads(row[0])
        paths = cfg.get('paths', [])
        for p in paths:
            if p.get('url', '') == rel_url:
                chain = p.get('chain', [])
                return chain, BASE + rel_url
    except Exception:
        pass
    return [], ''


@dataclass
class NotificationMessage:
    """Unified notification message format."""
    title: str
    product_name: str
    version_branch: str
    package_type: str
    file_name: str
    package_version: str
    md5_hash: str
    file_size: int = 0
    description_summary: str = ''
    description_full: str = ''
    description_parsed: dict = field(default_factory=dict)
    min_sys_version: str = ''
    restart_required: bool = False
    urgency: str = 'normal'  # normal | high | critical
    download_url: str = ''
    source_url: str = ''          # detail page URL on update.nsfocus.com
    published_at: str = ''
    is_rollback: bool = False
    source_id: int = 0           # content_sources.id, used for chain lookup
    chain: list[str] = field(default_factory=list)      # hierarchical path chain
    chain_url: str = ''           # full clickable detail page URL for the chain

    @property
    def urgency_label(self) -> str:
        return {'normal': 'ℹ️', 'high': '⚠️', 'critical': '🔴'}.get(self.urgency, 'ℹ️')

    @property
    def rollback_label(self) -> str:
        return '⚠️ 软件包已撤回' if self.is_rollback else ''

    @property
    def size_display(self) -> str:
        if self.file_size >= 1024 * 1024 * 1024:
            return f'{self.file_size / 1024 / 1024 / 1024:.2f}G'
        elif self.file_size >= 1024 * 1024:
            return f'{self.file_size / 1024 / 1024:.2f}M'
        elif self.file_size >= 1024:
            return f'{self.file_size / 1024:.1f}K'
        return f'{self.file_size}B'

    @classmethod
    def from_snapshot(cls, snap: dict, is_rollback: bool = False,
                      download_base: str = 'https://update.nsfocus.com/update/downloads/id/') -> 'NotificationMessage':
        parsed = snap.get('description_parsed', {})
        if isinstance(parsed, str):
            import json
            try:
                parsed = json.loads(parsed)
            except (json.JSONDecodeError, TypeError):
                parsed = {}

        added = parsed.get('added', [])
        modified = parsed.get('modified', [])
        deleted = parsed.get('deleted', [])

        summary_parts = []
        if added:
            summary_parts.append(f'新增{len(added)}条规则')
        if modified:
            summary_parts.append(f'修改{len(modified)}条规则')
        if deleted:
            summary_parts.append(f'删除{len(deleted)}条规则')

        dl_url = f'{download_base}{snap.get("download_id", "")}' if snap.get('download_id') else ''

        msg = cls(
            title=f'{snap.get("product_name", "")} {snap.get("package_type", "")}更新',
            product_name=snap.get('product_name', ''),
            version_branch=snap.get('version_branch', ''),
            package_type=snap.get('package_type', ''),
            file_name=snap.get('file_name', ''),
            package_version=snap.get('package_version', ''),
            md5_hash=snap.get('md5_hash', ''),
            file_size=snap.get('file_size', 0),
            description_summary='; '.join(summary_parts) if summary_parts else (snap.get('description_raw', '') or ''),
            description_full=snap.get('description_raw', ''),
            description_parsed=parsed,
            min_sys_version=snap.get('min_sys_version', ''),
            restart_required=bool(snap.get('restart_required', False)),
            urgency=snap.get('urgency', 'normal'),
            download_url=dl_url,
            source_url=snap.get('source_url', ''),
            published_at=snap.get('published_at', ''),
            is_rollback=is_rollback,
            source_id=int(snap.get('source_id') or 0),
        )
        msg.chain, msg.chain_url = _build_chain(msg)
        return msg


@dataclass
class DeliveryResult:
    success: bool
    channel_type: str
    channel_name: str
    error_message: str = ''


class BaseNotifier(ABC):
    """Abstract notifier."""
    channel_type: str = 'unknown'

    @abstractmethod
    def send(self, message: NotificationMessage, config: dict) -> DeliveryResult:
        ...

    def send_confirmation(self, message: NotificationMessage,
                          results: list[DeliveryResult], config: dict) -> DeliveryResult:
        """Send a delivery confirmation message (for IM channels)."""
        # Default: no-op, overridden by IM channels
        return DeliveryResult(success=True, channel_type=self.channel_type, channel_name='')


def _format_markdown_body(msg: NotificationMessage, for_rollback: bool = False,
                          skip_empty_meta: bool = False) -> str:
    """Format a NotificationMessage as markdown for IM channels (single message).

    Args:
        skip_empty_meta: if True, omit metadata lines whose value is empty/whitespace.
                         Use for system-event messages that carry meaningful body in
                         description_full but have no product/version/pkg fields.
    """
    icon = '⚠️' if for_rollback else msg.urgency_label

    lines = [
        f'{icon} **{msg.title}**',
        '',
    ]

    if for_rollback:
        lines.append(f'> ⚠️ 软件包已被撤回，请暂缓升级')
        lines.append('')

    # Chain / 类型 line — clickable if chain_url available, fallback text otherwise
    if msg.chain:
        chain_text = ' → '.join(msg.chain)
        if msg.chain_url:
            type_line = f'**类型**: [{chain_text}]({msg.chain_url})'
        else:
            type_line = f'**类型**: {chain_text}'
    elif msg.source_url:
        type_line = f'**类型**: [详情页，点击查看]({msg.source_url})'
    else:
        type_line = f'**类型**: {msg.package_type}'

    meta = [
        ('**文件**:', f'`{msg.file_name}`' if msg.file_name else None),
        ('**包版本**:', f'`{msg.package_version}`' if msg.package_version else None),
        ('**大小**:', f'`{msg.size_display}`' if msg.file_size > 0 else None),
        ('**MD5**:', f'`{msg.md5_hash}`' if msg.md5_hash else None),
        ('**发布时间**:', f'`{_utc_to_cst_display(msg.published_at)}`'),
        ('**下载地址**:', f'[{msg.file_name}]({msg.download_url})' if msg.download_url else None),
    ]
    lines.append(type_line)

    for label, val in meta:
        if val is not None and str(val).strip():
            lines.append(f'{label} {val}')
        elif not skip_empty_meta:
            lines.append(f'{label}')

    if msg.restart_required:
        lines.append('🔄 升级后需重启')

    if msg.description_full:
        lines.append('')
        lines.append('---')
        lines.append('')
        lines.append(_highlight_attention_lines(msg.description_full, 'markdown'))

    return '\n'.join(lines)


def _format_markdown_bodies(msg: NotificationMessage, for_rollback: bool = False,
                            max_bytes: int = 4000, skip_empty_meta: bool = False
                            ) -> list[str]:
    """Format message as one or more markdown bodies, splitting at 4000-byte limit.
    
    Returns a list of body strings. Never truncates — splits into multiple messages.
    Each message respects the WeCom/DingTalk 4096-byte hard limit.
    """
    full_body = _format_markdown_body(msg, for_rollback, skip_empty_meta=skip_empty_meta)
    if len(full_body.encode('utf-8')) <= max_bytes:
        return [full_body]

    # Need to split. Strategy: header/metadata in part 1, description in part 2+
    icon = '⚠️' if for_rollback else msg.urgency_label
    total = 0  # will compute after building parts
    parts = []

    # Part 1: header + metadata
    header_lines = [
        f'{icon} **{msg.title}**',
        '',
    ]
    if for_rollback:
        header_lines.append('> ⚠️ 软件包已被撤回，请暂缓升级')
        header_lines.append('')
    # Chain / 类型 line (same logic as _format_markdown_body)
    if msg.chain:
        chain_text = ' → '.join(msg.chain)
        type_line = (f'**类型**: [{chain_text}]({msg.chain_url})'
                     if msg.chain_url else f'**类型**: {chain_text}')
    elif msg.source_url:
        type_line = f'**类型**: [详情页，点击查看]({msg.source_url})'
    else:
        type_line = f'**类型**: {msg.package_type}'

    meta = [
        ('**文件**:', f'`{msg.file_name}`' if msg.file_name else None),
        ('**包版本**:', f'`{msg.package_version}`' if msg.package_version else None),
        ('**大小**:', f'`{msg.size_display}`'),
        ('**MD5**:', f'`{msg.md5_hash}`' if msg.md5_hash else None),
        ('**发布时间**:', f'`{_utc_to_cst_display(msg.published_at)}`'),
        ('**下载地址**:', f'[{msg.file_name}]({msg.download_url})' if msg.download_url else None),
    ]
    header_lines.append(type_line)

    if skip_empty_meta:
        for label, val in meta:
            if val is not None and str(val).strip():
                header_lines.append(f'{label}: {val}')
    else:
        header_lines.extend(f'{label} {val or ""}' for label, val in meta)

    extra_items = []
    if msg.min_sys_version:
        extra_items.append(f'⚠️ 依赖: 系统版本 ≥ {msg.min_sys_version}')
    if msg.restart_required:
        extra_items.append('🔄 升级后需重启')
    if msg.description_full:
        extra_items.append('')
        extra_items.append('---')
        extra_items.append('')
        extra_items.append(_highlight_attention_lines(msg.description_full, 'markdown'))
    part1 = '\n'.join(header_lines + extra_items)
    parts.append(part1)

    return parts


def _chunk_text(text: str, max_bytes: int) -> list[str]:
    """Split a long text into chunks of at most max_bytes each (UTF-8 safe)."""
    chunks = []
    encoded = text.encode('utf-8')
    offset = 0
    while offset < len(encoded):
        chunk = encoded[offset:offset + max_bytes]
        # Try to cut at a natural boundary
        try:
            chunk_str = chunk.decode('utf-8')
        except UnicodeDecodeError:
            # Trim trailing partial multi-byte char
            for trim in range(1, 4):
                try:
                    chunk_str = encoded[offset:offset + max_bytes - trim].decode('utf-8')
                    break
                except UnicodeDecodeError:
                    continue
            else:
                chunk_str = chunk.decode('utf-8', errors='ignore')
        chunks.append(chunk_str)
        offset += len(chunk_str.encode('utf-8'))
    return chunks


def _format_html_body(msg: NotificationMessage, for_rollback: bool = False) -> str:
    """Format a NotificationMessage as HTML for email — aligned with WeCom/DingTalk markdown template."""
    urgency_colors = {'normal': '#4a90d9', 'high': '#f5a623', 'critical': '#d0021b'}
    urgency_icons = {'normal': 'ℹ️', 'high': '⚠️', 'critical': '🔴'}
    color = urgency_colors.get(msg.urgency, '#4a90d9')
    icon = urgency_icons.get(msg.urgency, 'ℹ️')

    # Rollback banner (same as markdown)
    rollback_banner = ''
    if for_rollback:
        rollback_banner = f'''
        <tr><td style="background:#fff3cd;padding:12px;border-left:4px solid #f5a623">
            <strong>⚠️ 软件包已被撤回</strong> — 请暂缓升级操作。如已升级，请联系绿盟技术支持。
        </td></tr>
        <tr><td style="height:12px"></td></tr>
        '''

    # Description: summary text first, then parsed rule details
    desc_html = ''
    if msg.description_full:
        desc_text = _highlight_attention_lines(msg.description_full, 'html').replace('\n', '<br>')
        desc_html += f'''
        <tr><td colspan="2" style="padding:8px 0;border-top:1px solid #e0e0e0;word-break:break-word">
            <strong>📋 更新说明</strong>
            <div style="color:#555;margin-top:4px;line-height:1.8;font-size:15px">{desc_text}</div>
        </td></tr>'''

    # Parsed rule details (added/modified/deleted)
    parsed = msg.description_parsed
    if parsed.get('added') or parsed.get('modified') or parsed.get('deleted'):
        rules_html = ''
        if parsed.get('added'):
            ids = parsed['added'][:10]
            more = f' ...等共{len(parsed["added"])}条' if len(parsed['added']) > 10 else ''
            rules_html += f'<div>📋 新增: {", ".join(ids)}{more}</div>'
        if parsed.get('modified'):
            ids = parsed['modified'][:5]
            more = f' ...等共{len(parsed["modified"])}条' if len(parsed['modified']) > 5 else ''
            rules_html += f'<div>📝 修改: {", ".join(ids)}{more}</div>'
        if parsed.get('deleted'):
            ids = parsed['deleted'][:5]
            more = f' ...等共{len(parsed["deleted"])}条' if len(parsed['deleted']) > 5 else ''
            rules_html += f'<div>🗑️ 删除: {", ".join(ids)}{more}</div>'
        desc_html += f'''
        <tr><td colspan="2" style="padding:8px 0">
            <strong>规则变更详情</strong>
            <div style="color:#555;margin-top:4px;font-size:13px">{rules_html}</div>
        </td></tr>'''

    # Dependencies (matching markdown template)
    dep_html = ''
    if msg.min_sys_version:
        dep_html += f'<tr><td style="padding:4px 0"><strong>⚠️ 依赖</strong>: 系统版本 ≥ {msg.min_sys_version}</td></tr>'
    if msg.restart_required:
        dep_html += '<tr><td style="padding:4px 0"><strong>🔄 升级后需重启</strong></td></tr>'

    # Download button
    dl_btn = ''
    if msg.download_url and msg.file_name:
        dl_btn = f'''<tr><td colspan="2" style="padding:16px 0 0 0;text-align:center">
            <a href="{msg.download_url}" style="display:inline-block;background:{color};color:#fff;padding:10px 24px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:bold">下载升级包</a>
        </td></tr>'''

    # 类型 row — chain link or fallback (same logic as markdown)
    if msg.chain:
        chain_text = ' → '.join(msg.chain)
        type_cell = (f'<a href="{msg.chain_url}" style="color:{color};text-decoration:none">{chain_text}</a>'
                     if msg.chain_url else chain_text)
    elif msg.source_url:
        type_cell = f'<a href="{msg.source_url}" style="color:{color};text-decoration:none">详情页，点击查看</a>'
    else:
        type_cell = msg.package_type

    return f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto">
<table style="width:100%;border-collapse:collapse">
<tr><td style="background:{color};padding:16px;border-radius:8px 8px 0 0">
    <h2 style="color:#fff;margin:0">{icon} {msg.title}</h2>
</td></tr>
<tr><td style="padding:16px;border:1px solid #e0e0e0">
    {rollback_banner}
    <table style="width:100%;font-size:14px;color:#333">
        <tr><td style="padding:4px 0;width:80px;color:#666">类型</td><td>{type_cell}</td></tr>
        <tr><td style="padding:4px 0;color:#666">文件</td><td style="font-family:monospace">{msg.file_name or ''}</td></tr>
        <tr><td style="padding:4px 0;color:#666">包版本</td><td>{msg.package_version or ''}</td></tr>
        <tr><td style="padding:4px 0;color:#666">大小</td><td>{msg.size_display}</td></tr>
        <tr><td style="padding:4px 0;color:#666">MD5</td><td style="font-family:monospace">{msg.md5_hash or ''}</td></tr>
        <tr><td style="padding:4px 0;color:#666">发布时间</td><td>{_utc_to_cst_display(msg.published_at)}</td></tr>
        {dep_html}
        {desc_html}
        {dl_btn}
    </table>
</td></tr>
<tr><td style="padding:12px 16px;background:#f5f5f5;border-radius:0 0 8px 8px;color:#999;font-size:12px">
    此邮件由绿盟升级监控平台自动发送 | 如需调整订阅，请联系管理员
</td></tr>
</table>
</body></html>'''
