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


# 字段名对齐:所有 label 不加粗,用半角空格 pad 到固定字符数,使冒号对齐
# 格式:'发布页面 :   xxx'(label 后 1 空格 + 冒号 + 3 空格 + 值)
_LABEL_PAD_WIDTH = 4  # label 半角字符总数(不含 '**' 标记)


def _pad_label(label: str) -> str:
    """label 不含 '**' 标记,在末尾追加半角空格使 label 达到固定字符数。

    调用处负责在返回的 label 后追加 ' : ' (1空 + : + 1空)再接值。
    """
    pad_count = max(0, _LABEL_PAD_WIDTH - len(label))
    return label + ' ' * pad_count


def _build_chain(msg: 'NotificationMessage', db_path: str | None = None) -> tuple[list[str], str]:
    """Look up the chain for a notification message by matching source_url → relative path → paths chain.

    Returns (chain_list, full_detail_url). chain_list is empty if not found.
    """
    import hashlib, json
    if db_path is None:
        from src.models.database import DB_PATH
        db_path = DB_PATH
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
    """Unified notification message format.

    2026-07-22: 6 个原必填字段加 default='',允许系统级汇总通知
    (如 push_summary)直接构造,不依赖 snap dict。
    业务通知仍走 from_snapshot(),这些字段从 snap 自动填。
    """
    title: str = ''
    product_name: str = ''
    version_branch: str = ''
    package_type: str = ''
    file_name: str = ''
    package_version: str = ''
    md5_hash: str = ''
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
            title=f'{snap.get("product_name", "")} {snap.get("package_type", "")}',
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
    sender: str = ''  # 发件邮箱/发件标识(仅 email 类型填 smtp_user,其他类型留空)


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
                          skip_empty_meta: bool = False, line_break: str = '\n') -> str:
    """Format a NotificationMessage as markdown for IM channels (single message).

    Args:
        skip_empty_meta: if True, omit metadata lines whose value is empty/whitespace.
                         Use for system-event messages that carry meaningful body in
                         description_full but have no product/version/pkg fields.
        line_break: string used to separate lines within the output. Default '\\n'
            (WeCom markdown). DingTalk markdown client does not render '\\n' as a
            newline — pass '<br/>' instead.
    """
    icon = '⚠️' if for_rollback else msg.urgency_label
    chain_text = ' / '.join(msg.chain) if msg.chain else ''
    # 第 1 行:有 chain 时显示完整链 + 更新,无 chain 时 fallback 到原 title
    first_line = f'{chain_text} 更新' if chain_text else msg.title
    lines = [
        f'{icon} **{first_line}**',
        '',
    ]

    if for_rollback:
        lines.append(f'> ⚠️ 软件包已被撤回，请暂缓升级')
        lines.append('')

    # Chain / 发布页面 line — 显示文本=URL(与"下载地址"对齐:label 指示语义,链接文本就是 URL 本身)
    if msg.chain_url:
        type_line = f'{_pad_label("发布页面")} :   [{msg.chain_url}]({msg.chain_url})'
    elif msg.source_url:
        type_line = f'{_pad_label("发布页面")} :   [{msg.source_url}]({msg.source_url})'
    else:
        type_line = f'{_pad_label("发布页面")} :   {msg.package_type}'

    meta = [
        ('文件名称', f'`{msg.file_name}`' if msg.file_name else None),
        ('版本信息', f'`{msg.package_version}`' if msg.package_version else None),
        ('文件大小', f'`{msg.size_display}`' if msg.file_size > 0 else None),
        ('MD5', f'`{msg.md5_hash}`' if msg.md5_hash else None),  # 不带尾随空格
        ('发布时间', f'`{_utc_to_cst_display(msg.published_at)}`'),
        ('下载地址', f'[{msg.download_url}]({msg.download_url})' if msg.download_url else None),
    ]
    lines.append(type_line)

    # 字段名固定宽度(不含 '**' 标记),使冒号对齐
    # 字段名固定宽度(不含 '**' 标记),使冒号对齐
    # 所有 label 走 _pad_label pad 到 4 字符 → 末尾统一有 1 空格 + ':' 在第 6 列
    # (MD5 长度 3 字符 pad 1 空格,跟其他 label 4 字符 等宽,冒号自然对齐)
    for label, val in meta:
        padded_label = _pad_label(label)  # 所有 label 一律 4 字符宽
        if val is not None and str(val).strip():
            # 发布页面/下载地址冒号后 3 空格;其他 1 空格
            suffix = '   ' if label in ('发布页面', '下载地址') else ' '
            lines.append(f'{padded_label} :{suffix}{val}')
        elif not skip_empty_meta:
            lines.append(f'{padded_label} :')

    if msg.restart_required:
        lines.append('🔄 升级后需重启')

    if msg.description_full:
        lines.append('')
        lines.append('---')
        lines.append('')
        # 将描述按 \n 拆为多行,逐行 append,这样 line_break 真正统一控制换行
        # (钉钉 line_break='<br/>' 时,desc 行间也是 <br/>)
        # _highlight_attention_lines 在每行上加粗/高亮,行内 \n 已经处理完
        desc = _highlight_attention_lines(msg.description_full, 'markdown')
        lines.extend(desc.split('\n'))

    return line_break.join(lines)


def _format_markdown_body_placeholder(msg: NotificationMessage, for_rollback: bool = False) -> str:  # pragma: no cover
    raise RuntimeError('legacy stub - replaced by base._format_markdown_body')


def _format_markdown_bodies_legacy(msg: NotificationMessage, for_rollback: bool = False,
                                   max_bytes: int = 4000, skip_empty_meta: bool = False
                                   ) -> list[str]:  # pragma: no cover
    """Legacy stub - real implementation has moved below. Kept as guard.

    This body was a known-broken stub that returned a single element containing
    the entire (potentially oversized) body, causing DingTalk pushes >4KB to
    fail silently. It has been superseded by the full implementation that
    appears below _chunk_text; this stub remains only to prevent import
    regressions during the module reorganization.
    """
    full_body = _format_markdown_body(msg, for_rollback, skip_empty_meta=skip_empty_meta, line_break=line_break)
    return [full_body]


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


def _sign_url(webhook_url: str, secret: str, *, timestamp_unit: str = 'ms',
              url_quote: bool = True) -> str:
    """Add HMAC-SHA256 sign parameters to a bot webhook URL.

    Used by DingTalk / Feishu (and any future platform that adopts HMAC-SHA256
    sign scheme). WeCom does not use sign — see WecomNotifier for the
    documented "secret is a reserved field" rationale.

    Algorithm (shared by DingTalk / Feishu / WeChatWork):
      1. timestamp = current epoch (seconds OR milliseconds)
      2. string_to_sign = f"{timestamp}\\n{secret}"
      3. hmac_code = HMAC-SHA256(secret, string_to_sign)  # secret as the key
      4. sign = base64(hmac_code), optionally URL-quoted (DingTalk yes, Feishu no)
      5. URL append: &timestamp=<ts>&sign=<sign>

    Args:
        webhook_url: original webhook URL (may already have query params).
        secret: the shared signing secret the user got from the bot config UI.
        timestamp_unit: 'ms' for DingTalk (milliseconds) or 's' for Feishu /
            WeChatWork (seconds). Unknown values fall back to 'ms'.
        url_quote: True → quote_plus the base64 (DingTalk), False → raw base64 (Feishu).

    Returns:
        webhook_url with &timestamp=...&sign=... appended.

    Reference:
      - 钉钉自定义机器人加签: https://open.dingtalk.com/document/orgapp/custom-robot-access
      - 飞书自定义机器人签名校验: https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
    """
    import base64
    import hashlib
    import hmac
    import time
    from urllib.parse import quote_plus

    if timestamp_unit == 's':
        timestamp = str(int(time.time()))
    else:
        timestamp = str(round(time.time() * 1000))

    if not secret:
        # No secret → don't append sign params. Callers can still pass a secret
        # to enable signing. (Empty secret means "no signing configured for
        # this channel" — common while user is migrating their bot config.)
        return webhook_url

    string_to_sign = f'{timestamp}\n{secret}'
    hmac_code = hmac.new(
        secret.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        digestmod=hashlib.sha256
    ).digest()
    encoded = base64.b64encode(hmac_code).decode('utf-8')
    sign = quote_plus(encoded) if url_quote else encoded

    sep = '&' if ('?' in webhook_url) else '?'
    return f'{webhook_url}{sep}timestamp={timestamp}&sign={sign}'


def _split_with_marker(text: str, max_bytes: int = 4000) -> list[str]:
    """Split text into chunks of at most max_bytes each (UTF-8 safe), cut at line boundaries.
    Appends '(i/N)' pagination marker to each chunk when N > 1.

    Used by WeCom / DingTalk / Feishu to handle the 4KB IM hard limit. When the
    input is already short, returns it as a single-element list.

    Single-line oversized content is hard-cut at max_bytes with UTF-8 boundary
    safety (see _chunk_text).
    """
    if len(text.encode('utf-8')) <= max_bytes:
        return [text]

    # First pass: split at line boundaries
    raw_parts: list[str] = []
    current_bytes = b''
    for line in text.split('\n'):
        line_bytes = line.encode('utf-8')
        # Reserve room for '(n/m)' marker added later (~10 bytes worst case)
        overhead = len(f'(99/99)'.encode('utf-8'))
        if current_bytes and len(current_bytes) + len(line_bytes) + 1 + overhead > max_bytes:
            raw_parts.append(current_bytes.decode('utf-8'))
            current_bytes = b''
        current_bytes += line_bytes + b'\n'
    if current_bytes:
        raw_parts.append(current_bytes.decode('utf-8').rstrip('\n'))

    # Second pass: if any raw_part is still bigger than max_bytes, hard-cut it
    final: list[str] = []
    for rp in raw_parts:
        if len(rp.encode('utf-8')) <= max_bytes:
            final.append(rp)
        else:
            final.extend(_chunk_text(rp, max_bytes))

    # Reserve room for the marker by trimming if needed
    total = len(final)
    if total <= 1:
        return [final[0]] if final else [text[:max_bytes]]

    marker_len = len(f'\n\n({total}/{total})'.encode('utf-8'))
    budget = max(1, max_bytes - marker_len)
    safe = []
    for p in final:
        encoded = p.encode('utf-8')
        if len(encoded) > budget:
            # Try to cut at last newline within budget
            cut = encoded[:budget]
            try:
                safe.append(cut.decode('utf-8').rstrip('\n'))
            except UnicodeDecodeError:
                safe.append(cut.decode('utf-8', errors='ignore').rstrip('\n'))
        else:
            safe.append(p.rstrip('\n'))

    return [f'{p}\n\n({i+1}/{total})' for i, p in enumerate(safe)]


def _build_markdown_head_lines(msg: NotificationMessage, for_rollback: bool = False,
                               skip_empty_meta: bool = False) -> list[str]:
    """Build the head block lines (icon + title + meta) for markdown bodies.

    Returns a list[str] of header lines (NOT joined). Shared by
    `_format_markdown_bodies` (template A) and the new B/C/D templates so they
    stay structurally identical for head meta (发布页面/文件名称/版本信息...).
    """
    icon = '⚠️' if for_rollback else msg.urgency_label
    chain_text = ' / '.join(msg.chain) if msg.chain else ''
    first_line = f'{chain_text} 更新' if chain_text else msg.title
    header_lines = [
        f'{icon} **{first_line}**',
        '',
    ]
    if for_rollback:
        header_lines.append('> ⚠️ 软件包已被撤回，请暂缓升级')
        header_lines.append('')
    if msg.chain_url:
        type_line = f'{_pad_label("发布页面")} :   [{msg.chain_url}]({msg.chain_url})'
    elif msg.source_url:
        type_line = f'{_pad_label("发布页面")} :   [{msg.source_url}]({msg.source_url})'
    else:
        type_line = f'{_pad_label("发布页面")} :   {msg.package_type}'

    meta = [
        ('文件名称', f'`{msg.file_name}`' if msg.file_name else None),
        ('版本信息', f'`{msg.package_version}`' if msg.package_version else None),
        ('文件大小', f'`{msg.size_display}`' if msg.file_size > 0 else None),
        ('MD5', f'`{msg.md5_hash}`' if msg.md5_hash else None),  # 不带尾随空格
        ('发布时间', f'`{_utc_to_cst_display(msg.published_at)}`'),
        ('下载地址', f'[{msg.download_url}]({msg.download_url})' if msg.download_url else None),
    ]
    header_lines.append(type_line)

    def _fmt_meta_line(label, val):
        if label == 'MD5':
            padded = 'MD5' + ' ' * 6
        else:
            padded = _pad_label(label)
        suffix = '   ' if label in ('发布页面', '下载地址') else ' '
        return f'{padded} :{suffix}{val or ""}'

    if skip_empty_meta:
        for label, val in meta:
            if val is not None and str(val).strip():
                header_lines.append(_fmt_meta_line(label, val))
    else:
        for label, val in meta:
            header_lines.append(_fmt_meta_line(label, val))

    if msg.min_sys_version:
        header_lines.append(f'⚠️ 依赖: 系统版本 ≥ {msg.min_sys_version}')
    if msg.restart_required:
        header_lines.append('🔄 升级后需重启')

    return header_lines


def _format_markdown_bodies(msg: NotificationMessage, for_rollback: bool = False,
                            max_bytes: int = 4000, skip_empty_meta: bool = False,
                            line_break: str = '\n'
                            ) -> list[str]:
    """Format message as one or more markdown bodies, splitting at max_bytes limit.

    Returns a list of body strings. Never truncates — splits into multiple
    messages so each respects the WeCom / DingTalk 4KB hard limit.

    Behavior:
      - First chunk: header + metadata + restart flag + start of description
      - Subsequent chunks: continued description (with '(i/N)' marker)

    Args:
        line_break: string used to separate lines within each part. Default '\\n'
            (WeCom markdown). DingTalk markdown client does not render '\\n' as a
            newline — use '<br/>' instead.
    """
    full_body = _format_markdown_body(msg, for_rollback, skip_empty_meta=skip_empty_meta, line_break=line_break)
    if len(full_body.encode('utf-8')) <= max_bytes:
        return [full_body]

    # Need to split. Strategy:
    # 1. Build "head" = everything BEFORE description (header + meta lines)
    # 2. Build "tail" = description block ('---\\n\\n<desc>')
    # 3. If head already exceeds max_bytes (description is empty / tiny but metadata is huge),
    #    just hard-split full_body.
    # 4. Otherwise pack head in part 1, then add description chunks until budget filled.

    header_lines = _build_markdown_head_lines(msg, for_rollback, skip_empty_meta=skip_empty_meta)
    head = line_break.join(header_lines)

    if not msg.description_full:
        # No description to continue — just hard-split head if even that overflows.
        sep_str = line_break * 2
        return _split_with_marker(head + sep_str, max_bytes) if len((head + sep_str).encode('utf-8')) > max_bytes else [full_body]

    sep_str = line_break * 2
    head_with_sep = head + sep_str + '---' + sep_str
    head_with_sep_bytes = len(head_with_sep.encode('utf-8'))
    desc_text = _highlight_attention_lines(msg.description_full, 'markdown')

    if head_with_sep_bytes >= max_bytes:
        # Head alone overflows. Just split the full body.
        return _split_with_marker(full_body, max_bytes)

    remaining = max_bytes - head_with_sep_bytes
    # Split description into line-aware chunks of <= remaining bytes.
    # A single oversized line is hard-cut using _chunk_text (UTF-8 safe).
    # NOTE: we still split description by '\n' (real newlines in source text)
    # because users want their description paragraphs preserved verbatim across
    # the split — only the inter-line break separator (line_break) varies by platform.
    desc_chunks: list[str] = []
    cur = ''
    # ⚠️ bug fix (2026-07-10):desc 行间不能再用 '\n' 拼回 — 这会让钉钉 marked.js
    # 把 desc 内部换行折叠成空格(§32.4 陷阱 1+3)。让 line_break 接管行间分隔。
    sep_inner = line_break
    for line in desc_text.split('\n'):
        line_bytes_len = len(line.encode('utf-8'))
        if line_bytes_len > remaining:
            # Flush pending cur first, then hard-cut this single line.
            if cur:
                desc_chunks.append(cur)
                cur = ''
            desc_chunks.extend(_chunk_text(line, remaining))
            continue
        candidate = cur + (sep_inner if cur else '') + line
        if len(candidate.encode('utf-8')) > remaining:
            if cur:
                desc_chunks.append(cur)
            cur = line
        else:
            cur = candidate
    if cur:
        desc_chunks.append(cur)

    if len(desc_chunks) <= 1:
        # Whole description fits in part 1
        # ⚠️ bug fix (2026-07-10):同样把 desc 内部 \n 替换为 line_break,确保钉钉 / 飞书渲染换行。
        desc_text_normalized = desc_text.replace('\n', line_break)
        joined = head_with_sep + desc_text_normalized
        if len(joined.encode('utf-8')) > max_bytes:
            return [_split_with_marker(joined, max_bytes)[0]]
        return [joined.rstrip('\n')]  # rstrip 真实 \n,在 multi-part 末段后可能残留

    # Part 1 = head + first desc chunk (no marker yet; total unknown at this point)
    parts = [(head_with_sep + desc_chunks[0]).rstrip('\n')]
    # Remaining description chunks go in subsequent parts (just the description block).
    # Each chunk goes with its own (i/N) marker AFTER all parts built.
    for c in desc_chunks[1:]:
        parts.append(c.rstrip('\n'))

    total = len(parts)
    if total == 1:
        return parts
    # Append markers using line_break as separator.
    marker_template = sep_str + f'({{i}}/{{t}})'
    marker_len = len(marker_template.format(i='0', t='00').encode('utf-8'))
    out = []
    for i, p in enumerate(parts):
        body = p
        marker = marker_template.format(i=i + 1, t=total)
        if len(body.encode('utf-8')) + marker_len <= max_bytes:
            body = body + marker
        else:
            # Trim to fit marker
            encoded = body.encode('utf-8')
            budget = max(1, max_bytes - marker_len)
            if budget >= len(encoded):
                body = body + marker
            else:
                trimmed = encoded[:budget]
                try:
                    body = trimmed.decode('utf-8').rstrip('\n') + marker
                except UnicodeDecodeError:
                    body = trimmed.decode('utf-8', errors='ignore').rstrip('\n') + marker
        out.append(body)
    return out


# ─── Template B / C / D (通知模板 B/C/D,2026-07-10) ────────────────────────
# subscription_rules.template ∈ {full / strip / brief / feishu_full}
# full / strip / brief 可用渠道:钉钉 / 飞书 / wecom
# feishu_full 仅飞书可用
# 4 个枚举值与 schema 已有列对齐,UI 在订阅规则 create/edit 表单暴露下拉


def _has_chinese(s: str) -> bool:
    """Whether string contains any CJK Unified Ideograph (汉字) char."""
    return any('\u4e00' <= c <= '\u9fff' for c in s)


def _is_pure_english_line(line: str) -> bool:
    """A line counts as 'pure English' only if it has text but no Chinese char.

    Empty/whitespace lines don't count on either side — they're treated as
    inter-segment separators.
    """
    if not line.strip():
        return False
    return not _has_chinese(line)


def strip_english_lines(lines: list[str], min_run: int = 2) -> list[str]:
    """Remove continuous runs of ≥ min_run pure-English lines (and blank lines
    between them) from a list of lines. Mixed lines (CN + EN) are kept verbatim.

    Decision rule: a run is 'English' only if it's pure-ASCII line(s) and the
    run length is ≥ min_run. A standalone English line (e.g. version number,
    single-word term) is preserved.

    Args:
        lines: input lines, no trailing newline.
        min_run: minimum run length to qualify as an English segment.

    Returns:
        New list with English segments removed.
    """
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _is_pure_english_line(lines[i]):
            j = i
            while j < len(lines) and (not lines[j].strip() or _is_pure_english_line(lines[j])):
                j += 1
            if j - i >= min_run:
                # 真英文段:整段跳过(包括段内空白)
                i = j
                continue
            else:
                # 单行孤英文(版本号 / 术语):保留
                out.append(lines[i])
                i += 1
        else:
            out.append(lines[i])
            i += 1
    return out


def attention_segment(lines: list[str]) -> list[str]:
    """Extract the '注意事项 / Announcements' trailing segment.

    Returns the substring starting at the first line whose text contains
    '注意' (the keyword for the nsfocus '注意事项' / 'Announcements:' block),
    through the end of the list. If no line contains the keyword, returns [].
    """
    for idx, line in enumerate(lines):
        if '注意' in line:
            return lines[idx:]
    return []


def _join_head_with_desc(head_lines: list[str], desc_text: str,
                          line_break: str = '\n') -> str:
    """Join head lines and description with the standard `---` separator.

    Description 内部的 `\n` 也替换为 line_break,确保钉钉 / 飞书渲染换行
    (见 §32.4 陷阱 1+3,本 PR 修)。否则 marked.js 会把 `\n` 折叠成空格,
    导致 desc 整段挤一行。
    """
    sep_str = line_break * 2
    desc_normalized = desc_text.replace('\n', line_break)
    return (line_break.join(head_lines) + sep_str + '---' + sep_str + desc_normalized).rstrip('\n')


def _format_template_strip(msg: NotificationMessage, *,
                            max_bytes: int = 4000,
                            line_break: str = '\n') -> list[str]:
    """Template B (template='strip'): 4-staged fallback chain.

      Stage 0 (lazy)  : head + full description              — keep if ≤ 4K
      Stage 1 (strip) : head + strip_english(desc)          — if still > 4K
      Stage 2 (extreme): head + attention_segment only      — if still > 4K
      Stage 3 (brief) : head + '详情见 {source_url}' line   — last-resort fallback

    The selected body is then passed through `_split_with_marker` to enforce
    the 4KB hard limit when even the smallest variant overflows (preserves
    multi-part send with (i/N) marker — same UX as template A).

    Currently used by: 钉钉 / 飞书 / wecom when subscription.template='strip'.
    """
    head_lines = _build_markdown_head_lines(msg)
    head_str = line_break.join(head_lines)
    full_desc = msg.description_full or ''

    # Stage 0 — 不动 desc,整段试
    body0 = _join_head_with_desc(head_lines, full_desc, line_break)
    if len(body0.encode('utf-8')) <= max_bytes:
        return [body0]

    # Stage 1 — strip 整段英文
    desc_lines = full_desc.split('\n')
    stripped_lines = strip_english_lines(desc_lines, min_run=2)
    desc1 = _highlight_attention_lines(line_break.join(stripped_lines), 'markdown')
    body1 = _join_head_with_desc(head_lines, desc1, line_break)
    if len(body1.encode('utf-8')) <= max_bytes:
        return [body1]

    # Stage 2 — 仅注意事项段
    att_lines = attention_segment(stripped_lines)
    desc2 = _highlight_attention_lines(line_break.join(att_lines), 'markdown') if att_lines else ''
    body2 = _join_head_with_desc(head_lines, desc2, line_break)
    if len(body2.encode('utf-8')) <= max_bytes:
        return [body2]

    # Stage 3 — brief 兜底(head + source_url 一行)
    return _format_template_brief(msg, line_break=line_break, max_bytes=max_bytes)


def _format_template_brief(msg: NotificationMessage, *,
                            line_break: str = '\n',
                            max_bytes: int = 4000) -> list[str]:
    """Template C (template='brief'): head + '详情见 {source_url}' line, no desc.

    Used by: 钉钉 / 飞书 / wecom when subscription.template='brief'.
    Falls back to multi-part split (via _split_with_marker) if head itself
    exceeds 4KB (very unlikely in practice).
    """
    head_lines = _build_markdown_head_lines(msg)
    hint_url = msg.chain_url or msg.source_url or ''
    if hint_url:
        head_lines.append('')
        head_lines.append(f'> 详情见 [{hint_url}]({hint_url})')
    body = line_break.join(head_lines).rstrip('\n')
    if len(body.encode('utf-8')) <= max_bytes:
        return [body]
    return _split_with_marker(body, max_bytes)


def _format_template_feishu_full(msg: NotificationMessage, *,
                                  line_break: str = '\n',
                                  max_bytes: int = 30000) -> list[str]:
    """Template D (template='feishu_full'): head + full description, single chunk.

    Feishu supports larger payloads (~30KB+), so we don't split into multiple
    parts. Used by: 飞书 when subscription.template='feishu_full' (default).
    max_bytes default raised to 30000 (vs 4000 in `_split_with_marker`) — this
    matches §32.1's documented feishu soft ceiling.
    """
    head_lines = _build_markdown_head_lines(msg)
    full_desc = _highlight_attention_lines(msg.description_full or '', 'markdown')
    body = _join_head_with_desc(head_lines, full_desc, line_break)
    if len(body.encode('utf-8')) <= max_bytes:
        return [body]
    # Even feishu has limits. Split with a conservative chunk size, no (i/N).
    return _split_with_marker(body, max_bytes)


# Templates known to the system. Channel-side guard validates (rule.template,
# channel.type) compatibility at delivery time.
TEMPLATE_NAMES: dict[str, str] = {
    'full':         '模板 A · 完整(切多段)',
    'strip':        '模板 B · 智能裁剪(strip 英文段)',
    'brief':        '模板 C · 极简(只 head + 链接)',
    'feishu_full':  '模板 D · 飞书全量(单段,飞书专属)',
}

# 渠道类型 → 默认模板。subscription_rules.template 字段无论是标量(老数据)还是
# JSON 字典(新数据)都没指定某个渠道时,router fallback 用这张表(钉钉/企微 A,
# 飞书 D,apprise A,email 用 base 默认)。
# 见订阅规则 §32.4(wechat/dingtalk A/B/C、feishu A/B/C/D、apprise=A、email=不动)。
DEFAULT_TEMPLATE_BY_CHANNEL: dict[str, str] = {
    'wecom':    'full',
    'dingtalk': 'full',
    'feishu':   'feishu_full',
    'apprise':  'full',
    'email':    'full',
}


def format_template_bodies(template: str, msg: NotificationMessage, *,
                            for_rollback: bool = False,
                            max_bytes: int = 4000,
                            skip_empty_meta: bool = False,
                            line_break: str = '\n') -> list[str]:
    """Top-level template dispatcher used by notifier implementations.

    Args:
        template: one of 'full' / 'strip' / 'brief' / 'feishu_full'.
        msg: the NotificationMessage to render.
        for_rollback / skip_empty_meta / line_break: forwarded to template A.
        max_bytes: per-part UTF-8 byte budget. Default 4000 (typical IM 4KB limit).
            Template D bumps this internally to 30000 because Feishu is more lenient.

    Returns a list of body strings (1+). When the body already fits in budget,
    the list has exactly one element. When the body must be split, the list has
    N elements each ≤ max_bytes.
    """
    if template == 'full':
        return _format_markdown_bodies(
            msg, for_rollback=for_rollback, max_bytes=max_bytes,
            skip_empty_meta=skip_empty_meta, line_break=line_break,
        )
    if template == 'strip':
        return _format_template_strip(
            msg, max_bytes=max_bytes, line_break=line_break,
        )
    if template == 'brief':
        return _format_template_brief(
            msg, line_break=line_break, max_bytes=max_bytes,
        )
    if template == 'feishu_full':
        return _format_template_feishu_full(
            msg, line_break=line_break,
        )
    # Unknown template — defensive fallback to A so we never silently drop.
    return _format_markdown_bodies(
        msg, for_rollback=for_rollback, max_bytes=max_bytes,
        skip_empty_meta=skip_empty_meta, line_break=line_break,
    )


def _format_html_body(msg: NotificationMessage, for_rollback: bool = False,
                       sender_contact: dict | None = None,
                       show_download_btn: bool = True) -> str:
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

    # Dependencies (matching markdown template) — 保持与上方元信息一致的两列对齐
    dep_html = ''
    if msg.min_sys_version:
        dep_html += f'<tr><td style="padding:4px 0;width:80px;color:#666">依赖</td><td style="word-break:break-all">系统版本 ≥ {msg.min_sys_version}</td></tr>'
    if msg.restart_required:
        dep_html += '<tr><td style="padding:4px 0;width:80px;color:#666">重启</td><td>升级后需重启</td></tr>'

    # 发布页面 row — 与 markdown 渠道对齐:label 改"发布页面",显示文本=URL
    if msg.chain_url:
        type_cell = f'<a href="{msg.chain_url}" style="color:{color};text-decoration:none;word-break:break-all">{msg.chain_url}</a>'
    elif msg.source_url:
        type_cell = f'<a href="{msg.source_url}" style="color:{color};text-decoration:none;word-break:break-all">{msg.source_url}</a>'
    else:
        type_cell = msg.package_type

    # 下载地址 row — 显示文本为 URL 本身,点击跳转到 URL(markdown 改造同步)
    download_cell = (f'<a href="{msg.download_url}" style="color:{color};text-decoration:none;word-break:break-all">{msg.download_url}</a>'
                     if msg.download_url else '')

    # Sender identification — yellow strip immediately under the title bar, only
    # renders when ALL three fields (name/email/phone) are non-empty. The
    # previous gray footer line has been removed per user request.
    sender_strip_html = ''
    has_strip = False
    if sender_contact:
        name = (sender_contact.get('name') or '').strip()
        email = (sender_contact.get('email') or '').strip()
        phone = (sender_contact.get('phone') or '').strip()
        if name and email and phone:
            has_strip = True
            sender_strip_html = (
                '<tr><td style="padding:12px 16px;background:#fff8e6;'
                'border-left:1px solid #e0e0e0;border-right:1px solid #e0e0e0;'
                'font-size:13px;color:#333;border-top:2px solid #f5c842">'
                '<span style="color:#c27800;font-weight:600">⚠️ 本邮件由绿盟科技工程师</span> '
                f'<strong>{name}</strong>'
                ' ('
                f'<a href="mailto:{email}" style="color:#1d4ed8;text-decoration:none">{email}</a>'
                ' / '
                f'<span style="white-space:nowrap">{phone}</span>'
                ') '
                '<span style="color:#c27800;font-weight:600">发送,如对邮件来源有疑问请联系核实。</span>'
                '</td></tr>'
            )
    # Outer card border: top edge from title bar, bottom edge from content area
    title_border = 'border:1px solid #e0e0e0;border-bottom:none'
    content_border = 'border:1px solid #e0e0e0'
    if has_strip:
        # No border between strip and content (seamless yellow→white transition)
        content_border = 'border-left:1px solid #e0e0e0;border-right:1px solid #e0e0e0;border-bottom:1px solid #e0e0e0;border-top:none'
    else:
        # No strip → content area directly under title, give it rounded bottom
        content_border = 'border:1px solid #e0e0e0;border-top:none;border-radius:0 0 8px 8px'

    # HTML 标题栏:与 markdown 渠道对齐 — 有 chain 时用完整链 + 更新,无 chain 时 fallback title
    chain_text = ' / '.join(msg.chain) if msg.chain else ''
    html_title_text = f'{chain_text} 更新' if chain_text else msg.title
    return f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto">
<table style="width:100%;border-collapse:collapse">
<tr><td style="background:{color};padding:16px;border-radius:8px 8px 0 0;{title_border}">
    <h2 style="color:#fff;margin:0">{icon} {html_title_text}</h2>
</td></tr>
{sender_strip_html}
<tr><td style="padding:16px;{content_border}">
    {rollback_banner}
    <table style="width:100%;font-size:14px;color:#333">
        <tr><td style="padding:4px 0;width:80px;color:#666">发布页面</td><td>{type_cell}</td></tr>
        <tr><td style="padding:4px 0;color:#666">文件名称</td><td>{msg.file_name or ''}</td></tr>
        <tr><td style="padding:4px 0;color:#666">下载地址</td><td>{download_cell}</td></tr>
        <tr><td style="padding:4px 0;color:#666">版本信息</td><td>{msg.package_version or ''}</td></tr>
        <tr><td style="padding:4px 0;color:#666">文件大小</td><td>{msg.size_display}</td></tr>
        <tr><td style="padding:4px 0;color:#666">MD5 </td><td>{msg.md5_hash or ''}</td></tr>
        <tr><td style="padding:4px 0;color:#666">发布时间</td><td>{_utc_to_cst_display(msg.published_at)}</td></tr>
        {dep_html}
        {desc_html}
    </table>
</td></tr>
</table>
</body></html>'''
