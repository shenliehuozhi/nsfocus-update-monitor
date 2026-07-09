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
                resp = requests.post(webhook_url, json=payload, timeout=10)
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

        # Build one chunk of description, splitting at paragraph budget.
        def _desc_chunks(text: str) -> list[str]:
            out = []
            cur = ''
            for line in text.split('\n'):
                cand = cur + ('\n' if cur else '') + line
                if len(cand.encode('utf-8')) > PARAGRAPH_BUDGET:
                    if cur:
                        out.append(cur)
                    # If single line itself exceeds budget, hard-cut at last newline.
                    if len(line.encode('utf-8')) > PARAGRAPH_BUDGET:
                        # Hard split, biased to keep whole lines.
                        encoded = line.encode('utf-8')
                        offset = 0
                        while offset < len(encoded):
                            piece = encoded[offset:offset + PARAGRAPH_BUDGET]
                            try:
                                out.append(piece.decode('utf-8'))
                            except UnicodeDecodeError:
                                # back off to last clean boundary
                                for trim in range(1, 4):
                                    try:
                                        out.append(encoded[offset:offset + PARAGRAPH_BUDGET - trim].decode('utf-8'))
                                        break
                                    except UnicodeDecodeError:
                                        continue
                                else:
                                    out.append(piece.decode('utf-8', errors='ignore'))
                            offset += PARAGRAPH_BUDGET
                    else:
                        cur = line
                else:
                    cur = cand
            if cur:
                out.append(cur)
            return out

        chunks = _desc_chunks(_highlight_attention_lines(desc, 'markdown'))
        if not chunks:
            return [{'title': title_base, 'content': head_paras}]

        # First chunk goes into payload 1 with head_paras; subsequent chunks go into payload 2+.
        payload_chunks: list[list[list[dict]]] = []
        payload_chunks.append(head_paras + [[{'tag': 'text', 'text': f'📋 {chunks[0]}'}]])
        for c in chunks[1:]:
            payload_chunks.append([[{'tag': 'text', 'text': f'📋 (续) {c}'}]])

        # Compute total payloads once. Append (i/N) marker to payloads > 1.
        results = []
        n = len(payload_chunks)
        for i, paras in enumerate(payload_chunks):
            if n > 1:
                paras = paras + [[{'tag': 'text', 'text': f'({i+1}/{n})'}]]
            results.append({'title': title_base if i == 0 else f'{title_base} ({i+1}/{n})', 'content': paras})

        # Safety: if first payload still > PAYLOAD_BUDGET, fall back to further slicing.
        if _serialize(results[0]['title'], results[0]['content']) > PAYLOAD_BUDGET:
            # Reduce per-chunk size and rebuild.
            PARAGRAPH_BUDGET = 1800
            chunks = _desc_chunks(_highlight_attention_lines(desc, 'markdown'))
            payload_chunks = [head_paras + [[{'tag': 'text', 'text': f'📋 {chunks[0]}'}]]]
            for c in chunks[1:]:
                payload_chunks.append([[{'tag': 'text', 'text': f'📋 (续) {c}'}]])
            results = []
            n = len(payload_chunks)
            for i, paras in enumerate(payload_chunks):
                if n > 1:
                    paras = paras + [[{'tag': 'text', 'text': f'({i+1}/{n})'}]]
                results.append({'title': title_base if i == 0 else f'{title_base} ({i+1}/{n})', 'content': paras})

        return results
