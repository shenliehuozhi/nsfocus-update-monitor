"""飞书机器人通知器."""

import json
import requests

from src.notifiers.base import BaseNotifier, NotificationMessage, DeliveryResult, _sign_url


class FeishuNotifier(BaseNotifier):
    channel_type = 'feishu'

    def send(self, message: NotificationMessage, config: dict) -> DeliveryResult:
        webhook_url = config.get('webhook_url', '')
        secret = config.get('secret', '')
        if not webhook_url:
            return DeliveryResult(False, 'feishu', '', 'Missing webhook_url')

        # 飞书机器人后台 3 选 1 安全设置:签名校验 / 自定义关键词 / IP 白名单
        # 仅当后台启用了「签名校验」且本项目填了 secret 时,才能成功推送。
        # 算法见 _sign_url (base.py) — 通用加签,本渠道参数:timestamp 秒 + base64 raw (不 quote)。
        url = _sign_url(webhook_url, secret, timestamp_unit='s', url_quote=False)

        # 飞书使用富文本 (post) 格式 — 一次可能返回多个 payload(长描述分多条)
        payloads = self._build_post(message)
        name = config.get('name', '')

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
                if result.get('code') == 0 or result.get('StatusCode') == 0:
                    continue
                return DeliveryResult(False, 'feishu', name,
                                      f"Feishu error [{i+1}/{len(payloads)}]: {result.get('msg', 'unknown')}")
            except Exception as e:
                return DeliveryResult(False, 'feishu', name, str(e))

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

        Strategy:
          1. Build the standard payload once (base paragraphs + product info).
          2. Serialize and measure bytes.
          3. If the JSON exceeds 30000 bytes OR any single paragraph text
             exceeds 3800 bytes (defensive margin against 4KB feishu tag
             limit), split the description paragraph into multiple smaller
             paragraphs and route them into a subsequent payload with a
             "(2/N)" / "(i/N)" marker paragraph.
        """
        from src.notifiers.base import _highlight_attention_lines

        title_base = f'{"⚠️ 撤回" if msg.is_rollback else "🔔"} {msg.product_name} {msg.package_version}'

        head_paras = []
        if msg.is_rollback:
            head_paras.append([{'tag': 'text', 'text': '⚠️ 此软件包已被撤回，请暂缓升级'}])
        head_paras += [
            [{'tag': 'text', 'text': f'产品: {msg.product_name}'}],
            [{'tag': 'text', 'text': f'版本: {msg.version_branch}'}],
            [{'tag': 'text', 'text': f'类型: {msg.package_type}'}],
            [{'tag': 'text', 'text': f'文件: {msg.file_name}'}],
            [{'tag': 'text', 'text': f'大小: {msg.size_display}'}],
        ]
        if msg.min_sys_version:
            head_paras.append([{'tag': 'text', 'text': f'⚠️ 依赖: 系统版本 ≥ {msg.min_sys_version}'}])
        if msg.download_url:
            head_paras.append([{'tag': 'text', 'text': ''}, {'tag': 'a', 'text': '📥 下载升级包', 'href': msg.download_url}])

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
