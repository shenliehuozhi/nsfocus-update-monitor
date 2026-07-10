"""飞书机器人通知器."""

import json
import requests

from src.core.logger import get_logger
from src.notifiers._log import get_log_writer
from src.notifiers.base import BaseNotifier, NotificationMessage, DeliveryResult, _sign_url


logger = get_logger('feishu')


class FeishuNotifier(BaseNotifier):
    channel_type = 'feishu'

    def send(self, message: NotificationMessage, config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')
        secret = config.get('secret', '')

        log = get_log_writer(config, 'feishu',
                             config.get('_channel_id', 0),
                             config.get('name', ''))
        log.info(f'开始推送 product={message.product_name} v={message.package_version} file={message.file_name}')
        log.info(f'  urgency={message.urgency} is_rollback={message.is_rollback} has_secret={bool(secret)}')

        if not webhook_url:
            log.error('webhook_url 为空,推送终止')
            return DeliveryResult(False, 'feishu', '', 'Missing webhook_url')

        # 飞书机器人后台 3 选 1 安全设置:签名校验 / 自定义关键词 / IP 白名单
        # 仅当后台启用了「签名校验」且本项目填了 secret 时,才能成功推送。
        # 算法见 _sign_url (base.py) — 通用加签,本渠道参数:timestamp 秒 + base64 raw (不 quote)。
        url = _sign_url(webhook_url, secret, timestamp_unit='s', url_quote=False)
        log.info(f'  signed URL tail: ...{url[-60:]}')

        # 飞书使用富文本 (post) 格式 — 一次可能返回多个 payload(长描述分多条)
        payloads = self._build_post(message)
        name = config.get('name', '')
        log.info(f'  payloads={len(payloads)}, sizes={[len(json.dumps(p, ensure_ascii=False).encode("utf-8")) for p in payloads]}B')

        for i, content in enumerate(payloads):
            payload = {
                'msg_type': 'post',
                'content': {
                    'post': {
                        'zh_cn': content
                    }
                }
            }
            try:
                resp = requests.post(url, json=payload, timeout=10)
                result = resp.json()
                log.info(f'  HTTP {resp.status_code}  code={result.get("code")} msg={result.get("msg", "")}')
                if result.get('code') == 0 or result.get('StatusCode') == 0:
                    log.ok(f'payload {i+1}/{len(payloads)} sent')
                    continue
                log.error(f'Feishu error [{i+1}/{len(payloads)}]: {result.get("msg", "unknown")}')
                return DeliveryResult(False, 'feishu', name,
                                      f"Feishu error [{i+1}/{len(payloads)}]: {result.get('msg', 'unknown')}")
            except Exception as e:
                log.error(f'HTTP exception: {type(e).__name__}: {e}')
                return DeliveryResult(False, 'feishu', name, str(e))

        log.ok(f'全部 {len(payloads)} 条推送成功')
        return DeliveryResult(True, 'feishu', name,
                              f"Sent {len(payloads)} message(s)" if len(payloads) > 1 else '')

    def send_confirmation(self, message: NotificationMessage,
                          results: list[DeliveryResult], config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')
        secret = config.get('secret', '')
        if not webhook_url:
            return DeliveryResult(False, 'feishu', '', '')

        # 确认消息也按签名体系发(否则后台签名校验启用时本条会失败)
        url = _sign_url(webhook_url, secret, timestamp_unit='s', url_quote=False)

        status_text = f'✅ 推送完成: {message.product_name} {message.package_version}\n'
        for r in results:
            icon = '✅' if r.success else '❌'
            status_text += f'{icon} {r.channel_type} {r.channel_name}\n'

        payload = {
            'msg_type': 'text',
            'content': {'text': status_text}
        }
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception:
            pass
        return DeliveryResult(True, 'feishu', config.get('name', ''))

    def _build_post(self, msg: NotificationMessage) -> list[dict]:
        """Build one or more feishu post payloads.

        Returns a list (1 or N) of post payloads. Each payload has its
        own title and content paragraphs; multiple payloads are sent
        sequentially in `send()` to bypass the 4KB paragraph + 30KB
        payload hard limits.

        Field layout matches DingTalk / WeCom markdown for cross-channel
        consistency (7 fields):
          发布页面 / 文件名称 / 版本信息 / 文件大小 / MD5 / 发布时间 / 下载地址
        Labels are styled with ['bold', 'orange'] to mirror the
        <font color="#c27800"> labeling DingTalk applies.
        """
        from src.notifiers.base import _highlight_attention_lines

        # 跟钉钉/企微 markdown 一致:有 chain 时用完整链 (e.g. "网络安全 / 抗拒绝服务 / ADS / V4.5R"),
        # 无 chain 时 fallback 到 "product_name package_version" (e.g. "ADS V4.5R")
        chain_text = ' / '.join(msg.chain) if msg.chain else ''
        title_subject = chain_text if chain_text else f'{msg.product_name} {msg.package_version}'
        title_base = f'{"⚠️ 撤回" if msg.is_rollback else "🔔"} {title_subject}'

        # Field layout matches DingTalk / WeCom markdown 7 fields. Each labeled
        # row is a single paragraph (飞书 IM 客户端按 paragraph 自动换行)。
        #
        # 重要限制: 飞书机器人 webhook 不支持 text tag 的 inline style 字段
        # (实测返回 19002 "unknown content value")。所以飞书 label 无法做颜色/
        # 加粗样式 — 跟钉钉 <font color> 不同。退化为纯文本 label + plain value,
        # 视觉对齐靠字段名 + 冒号(同企业微信 markdown)。
        #
        # 飞书 IM 客户端也不渲染 markdown 反引号 (\`xxx\` 会显示字面量), 所以
        # value text tag 里的反引号要 strip 掉 — 飞书阅读体验一致, 钉钉 / 企微
        # 不受影响 (它们读 base._format_markdown_body 走另一条路)。
        head_paras: list[list[dict]] = []

        def field_p(label: str, value_tags: list) -> list:
            """Build a paragraph: plain text label + value_tags (text / a).

            飞书机器人 webhook 不支持 text tag 的 inline style 字段 — 所以
            label 退化为纯文本(无法做颜色 / 加粗)。视觉对齐靠 label + 冒号
            + value 模仿钉钉 markdown 风格。
            同时剥掉 value text tag 里的反引号(飞书不渲染 markdown
            反引号, 显示字面字符会显得「没有渲染干净」)。
            """
            stripped = []
            for t in value_tags:
                if t.get('tag') == 'text':
                    new_t = dict(t)
                    new_t['text'] = t['text'].replace('`', '')
                    stripped.append(new_t)
                else:
                    stripped.append(t)
            return [{'tag': 'text', 'text': f'{label} '}] + stripped

        if msg.is_rollback:
            head_paras.append([{'tag': 'text', 'text': '⚠️ 此软件包已被撤回,请暂缓升级'}])

        # 发布页面(发布页面 URL)
        if msg.chain_url:
            type_text = msg.chain_url
            url_text = msg.chain_url
        elif msg.source_url:
            type_text = msg.source_url
            url_text = msg.source_url
        else:
            type_text = msg.package_type or ''
            url_text = ''
        if url_text:
            head_paras.append(field_p(
                '发布页面',
                [{'tag': 'text', 'text': ':   '},
                 {'tag': 'a', 'text': url_text, 'href': url_text}],
            ))
        elif type_text:
            head_paras.append(field_p('发布页面', [{'tag': 'text', 'text': f':   {type_text}'}]))

        # 文件名称
        if msg.file_name:
            head_paras.append(field_p('文件名称', [{'tag': 'text', 'text': f': `{msg.file_name}`'}]))

        # 版本信息
        if msg.package_version:
            head_paras.append(field_p('版本信息', [{'tag': 'text', 'text': f': `{msg.package_version}`'}]))

        # 文件大小
        if msg.file_size > 0:
            head_paras.append(field_p('文件大小', [{'tag': 'text', 'text': f': `{msg.size_display}`'}]))

        # MD5
        if msg.md5_hash:
            head_paras.append(field_p('MD5', [{'tag': 'text', 'text': f': `{msg.md5_hash}`'}]))

        # 发布时间
        from src.notifiers.base import _utc_to_cst_display
        ts_str = _utc_to_cst_display(msg.published_at) if msg.published_at else ''
        if ts_str:
            head_paras.append(field_p('发布时间', [{'tag': 'text', 'text': f': `{ts_str}`'}]))

        if msg.min_sys_version:
            head_paras.append([{'tag': 'text', 'text': f'⚠️ 依赖: 系统版本 ≥ {msg.min_sys_version}'}])
        if msg.download_url:
            head_paras.append(field_p(
                '下载地址',
                [{'tag': 'text', 'text': ':   '},
                 {'tag': 'a', 'text': msg.download_url, 'href': msg.download_url}],
            ))

        def _serialize(title: str, paras: list) -> int:
            payload = {'title': title, 'content': paras}
            return len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))

        # If no description (or description is empty after stripping), single payload.
        desc = msg.description_summary or msg.description_full or ''
        if not desc:
            return [{'title': title_base, 'content': head_paras}]

        # Decide splitting strategy based on per-paragraph and total payload sizes.
        PARAGRAPH_BUDGET = 3800        # below 4KB tag-text hard limit
        PAYLOAD_BUDGET = 28000         # below 30KB total payload safety margin

        # Each LINE from desc_text becomes its own paragraph in the resulting post
        # payload (one paragraph = one display line on Feishu IM client). 飞书客户端
        # 按 paragraph 自动换行, 不依赖 '\n' 渲染 — 跟钉钉 markdown 不同。
        #
        # 但飞书 API 对一条 message 的总 payload 限制 ~30KB,且每段 paragraph 上限
        # 4KB。一个超长 desc 不能"每行一段 payload",所以:
        # - 返回 list[list[str]]:外层是 chunks,内层每个 chunk 是几个 paragraphs
        # - 当 chunk 总字节 > 30KB 时再拆分多 payload 并加 (i/N) marker
        def _desc_paragraphs(text: str) -> list[list[str]]:
            """Split text into chunks of paragraphs (list of paragraphs per chunk)."""
            current_chunk: list[str] = []
            current_bytes = 0

            def flush() -> list[str]:
                nonlocal current_chunk, current_bytes
                if current_chunk:
                    result = current_chunk
                    current_chunk = []
                    current_bytes = 0
                    return result
                return []

            chunks: list[list[str]] = []
            for line in text.split('\n'):
                if not line:
                    continue
                line_bytes_len = len(line.encode('utf-8'))
                # Hard-cut oversize single line.
                if line_bytes_len > PARAGRAPH_BUDGET:
                    # First flush current chunk before oversized lines.
                    flushed = flush()
                    if flushed:
                        chunks.append(flushed)
                    encoded = line.encode('utf-8')
                    offset = 0
                    while offset < len(encoded):
                        piece = encoded[offset:offset + PARAGRAPH_BUDGET]
                        try:
                            piece_str = piece.decode('utf-8')
                        except UnicodeDecodeError:
                            for trim in range(1, 4):
                                try:
                                    piece_str = encoded[offset:offset + PARAGRAPH_BUDGET - trim].decode('utf-8')
                                    break
                                except UnicodeDecodeError:
                                    continue
                            else:
                                piece_str = piece.decode('utf-8', errors='ignore')
                        # Each hard-cut piece = its own chunk (rare path).
                        chunks.append([piece_str])
                        offset += PARAGRAPH_BUDGET
                    continue
                # Per-paragraph accumulation. Track chunk size so we don't blow
                # the 30KB total payload budget too easily.
                if current_bytes and current_bytes + line_bytes_len > PAYLOAD_BUDGET:
                    chunks.append(flush())
                current_chunk.append(line)
                current_bytes += line_bytes_len
            tail = flush()
            if tail:
                chunks.append(tail)
            return chunks

        chunks = _desc_paragraphs(_highlight_attention_lines(desc, 'markdown'))
        if not chunks:
            return [{'title': title_base, 'content': head_paras}]

        # First chunk's paragraphs go into payload 1 with head_paras;
        # subsequent chunks go into payload 2+. Each paragraph = one line on
        # Feishu client (no '\n' rendering dependency).
        payload_chunks: list[list[list[dict]]] = []
        first = chunks[0]
        first_paras = [[{'tag': 'text', 'text': f'📋 {line}'}] for line in first]
        payload_chunks.append(head_paras + first_paras)
        for c in chunks[1:]:
            paras = [[{'tag': 'text', 'text': f'📋 (续) {line}'}] for line in c]
            payload_chunks.append(paras)

        # Compute total payloads once. Append (i/N) marker to payloads > 1.
        results = []
        n = len(payload_chunks)
        for i, paras in enumerate(payload_chunks):
            if n > 1:
                paras = paras + [[{'tag': 'text', 'text': f'({i+1}/{n})'}]]
            results.append({'title': title_base if i == 0 else f'{title_base} ({i+1}/{n})', 'content': paras})

        # Safety: if first payload still > PAYLOAD_BUDGET, fall back to further slicing.
        if _serialize(results[0]['title'], results[0]['content']) > PAYLOAD_BUDGET:
            # Reduce per-chunk size and rebuild (re-trigger the per-line accumulation
            # with smaller PAYLOAD_BUDGET ceiling).
            PAYLOAD_BUDGET = 14000
            chunks = _desc_paragraphs(_highlight_attention_lines(desc, 'markdown'))
            first = chunks[0]
            first_paras = [[{'tag': 'text', 'text': f'📋 {line}'}] for line in first]
            payload_chunks = [head_paras + first_paras]
            for c in chunks[1:]:
                paras = [[{'tag': 'text', 'text': f'📋 (续) {line}'}] for line in c]
                payload_chunks.append(paras)
            results = []
            n = len(payload_chunks)
            for i, paras in enumerate(payload_chunks):
                if n > 1:
                    paras = paras + [[{'tag': 'text', 'text': f'({i+1}/{n})'}]]
                results.append({'title': title_base if i == 0 else f'{title_base} ({i+1}/{n})', 'content': paras})

        return results
