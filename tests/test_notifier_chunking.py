"""Tests for notifier message chunking:
- _format_markdown_bodies: produces parts each ≤ max_bytes
- _format_markdown_bodies: handles single-line oversized descriptions
- _format_markdown_bodies: no split when description fits
- FeishuNotifier._build_post: returns multiple payloads when desc is large
- FeishuNotifier._build_post: returns single payload when desc is small
- _split_with_marker: marker fits within max_bytes budget
"""
import json

from src.notifiers.base import (
    _format_markdown_bodies,
    _format_markdown_body,
    _split_with_marker,
    NotificationMessage,
)
from src.notifiers.feishu import FeishuNotifier


def make_msg(description: str = '') -> NotificationMessage:
    """Build a NotificationMessage with optional long description."""
    return NotificationMessage(
        title='测试产品 ABC 升级',
        product_name='测试产品 ABC',
        version_branch='V1.0R01',
        package_type='系统升级包',
        file_name='test_update.bin',
        package_version='V1.0R01F01',
        md5_hash='d0abc505819b0c22b88a13c46e33d4b1',
        file_size=696_520_000,
        description_summary=description,
        description_full=description,
        download_url='https://example.com/download/123',
        source_url='https://example.com/listDetail/abc/123',
        published_at='2026-07-09T00:49:58Z',
        chain=['测试产品 ABC', '分类', 'V1.0R01'],
        chain_url='https://example.com/listEspcL/123',
    )


def test_markdown_bodies_no_split_when_short():
    msg = make_msg('简短描述,不到 100 字符')
    bodies = _format_markdown_bodies(msg, max_bytes=4000)
    assert len(bodies) == 1
    assert len(bodies[0].encode('utf-8')) <= 4000


def test_markdown_bodies_split_when_large():
    # 3 行 × 100 char ≈ 4KB
    desc = ('这是一段非常非常长的中文描述用来测试分块' * 50 + '\n') * 4
    msg = make_msg(desc)
    bodies = _format_markdown_bodies(msg, max_bytes=4000)
    assert len(bodies) >= 2
    for b in bodies:
        assert len(b.encode('utf-8')) <= 4000, f'part too big: {len(b.encode("utf-8"))}'
    # pagination marker
    assert all(f'({i+1}/{len(bodies)})' in b for i, b in enumerate(bodies))


def test_markdown_bodies_handles_single_oversized_line():
    # Single line with no newlines, 8KB total
    desc = 'X' * 8000
    msg = make_msg(desc)
    bodies = _format_markdown_bodies(msg, max_bytes=4000)
    assert len(bodies) >= 2
    for b in bodies:
        assert len(b.encode('utf-8')) <= 4000


def test_markdown_bodies_empty_description():
    msg = make_msg('')
    msg.description_full = ''
    msg.description_summary = ''
    bodies = _format_markdown_bodies(msg, max_bytes=4000)
    assert len(bodies) == 1
    assert len(bodies[0].encode('utf-8')) <= 4000


def test_split_with_marker_short_passthrough():
    text = 'small content here'
    parts = _split_with_marker(text, max_bytes=4000)
    assert parts == [text]


def test_split_with_marker_marker_fits_budget():
    text = '\n'.join(f'line {i} ' + 'X' * 100 for i in range(100))
    parts = _split_with_marker(text, max_bytes=4000)
    assert len(parts) >= 2
    for p in parts:
        assert len(p.encode('utf-8')) <= 4000


def test_feishu_short_desc_single_payload():
    msg = make_msg('简短描述')
    payloads = FeishuNotifier()._build_post(msg)
    assert isinstance(payloads, list)
    assert len(payloads) == 1
    p = payloads[0]
    serialized = json.dumps(p, ensure_ascii=False).encode('utf-8')
    # Single paragraph text under 4KB feishu hard limit
    for para in p['content']:
        for t in para:
            text_bytes = len(t.get('text', '').encode('utf-8'))
            assert text_bytes <= 4096, f'paragraph text {text_bytes}B > 4KB'
    # Total reasonable
    assert len(serialized) < 30000


def test_feishu_large_desc_multi_payload():
    # ~8KB of description
    desc = '中文描述内容非常长用来测试分块' * 200
    msg = make_msg(desc)
    payloads = FeishuNotifier()._build_post(msg)
    assert isinstance(payloads, list)
    assert len(payloads) >= 2
    for p in payloads:
        # Each paragraph text must be under 4KB feishu hard limit
        for para in p['content']:
            for t in para:
                text_bytes = len(t.get('text', '').encode('utf-8'))
                assert text_bytes <= 4096, f'paragraph text {text_bytes}B > 4KB'
        # Marker present in multi-payload mode
        if len(payloads) > 1:
            joined = '\n'.join(t.get('text', '') for para in p['content'] for t in para)
            assert f'(1/{len(payloads)})' in joined or f'(2/{len(payloads)})' in joined or f'(3/{len(payloads)})' in joined


def test_feishu_marker_in_all_parts():
    desc = 'X' * 6000
    msg = make_msg(desc)
    payloads = FeishuNotifier()._build_post(msg)
    n = len(payloads)
    assert n >= 2
    for i, p in enumerate(payloads):
        joined = '\n'.join(t.get('text', '') for para in p['content'] for t in para)
        assert f'({i+1}/{n})' in joined, f'part {i+1}/{n} missing marker: {joined[:80]!r}'
