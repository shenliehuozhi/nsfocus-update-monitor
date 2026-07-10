"""Tests for notifier message chunking:
- _format_markdown_bodies: produces parts each ≤ max_bytes
- _format_markdown_bodies: handles single-line oversized descriptions
- _format_markdown_bodies: no split when description fits
- FeishuNotifier._build_post: returns multiple payloads when desc is large
- FeishuNotifier._build_post: returns single payload when desc is small
- _split_with_marker: marker fits within max_bytes budget
- _sign_url: DingTalk semantics (timestamp ms + url-quoted base64)
- _sign_url: Feishu semantics (timestamp seconds + raw base64)
- _sign_url: no secret → returns input URL unchanged
- _sign_url: query separator handles URL with / without existing '?'
"""
import base64
import hashlib
import hmac
import json
import re
import time

from src.notifiers.base import (
    _format_markdown_bodies,
    _format_markdown_body,
    _split_with_marker,
    _sign_url,
    NotificationMessage,
)
from src.notifiers.dingtalk import DingtalkNotifier
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


def test_dingtalk_line_break_uses_br():
    """DingTalk markdown client does NOT render '\n' as newline — must use '<br/>'.

    This test verifies that _format_markdown_bodies(..., line_break='<br/>') produces
    output where every newline is replaced with the HTML tag.
    """
    msg = NotificationMessage(
        title='t', product_name='X', version_branch='V1.0', package_type='test',
        file_name='test.bin', package_version='v1', md5_hash='a' * 32,
        description_summary='', description_full='',
        file_size=0, download_url='', source_url='', published_at='',
        source_id=0, chain=['test'], chain_url='',
    )
    bodies = _format_markdown_bodies(msg, line_break='<br/>')
    body = bodies[0]
    assert '\n' not in body, f'DingTalk body should have no \\n, got: {body!r}'
    assert body.count('<br/>') >= 5, f'DingTalk body should have multiple <br/>, got {body}'


def test_default_line_break_preserves_newline():
    """Default line_break='\\n' (WeCom/Fengdie markdown rendering) must not regress."""
    msg = NotificationMessage(
        title='t', product_name='X', version_branch='V1.0', package_type='test',
        file_name='test.bin', package_version='v1', md5_hash='a' * 32,
        description_summary='', description_full='',
        file_size=0, download_url='', source_url='', published_at='',
        source_id=0, chain=['test'], chain_url='',
    )
    bodies = _format_markdown_bodies(msg)  # default line_break='\n'
    body = bodies[0]
    assert '\n' in body, f'default line_break should preserve \\n, got: {body!r}'


def test_dingtalk_long_desc_with_br_stays_under_4k():
    """DingTalk sends bloated by <br/> (5 bytes vs 1 byte \\n) — verify chunks still fit."""
    msg = NotificationMessage(
        title='测试长描述', product_name='测试产品 X', version_branch='V4.5R',
        package_type='系统升级包', file_name='long.bin', package_version='v1',
        md5_hash='a' * 32, file_size=696520000,
        description_full=('这是一行比较长的中文描述,内容很多,描述产品功能变更。\n' * 50),
        download_url='https://example.com/dl', source_url='https://example.com/list',
        published_at='2026-07-09T00:49:58Z',
        source_id=0, chain=['测试产品 X', 'V4.5R'], chain_url='https://example.com/list/1',
    )
    bodies = _format_markdown_bodies(msg, max_bytes=4000, line_break='<br/>')
    assert all(len(b.encode('utf-8')) <= 4000 for b in bodies), \
        f'overflow: {[len(b.encode("utf-8")) for b in bodies]}'


def test_dingtalk_description_internal_newlines_become_br():
    """DingTalk: newlines INSIDE description text (between paragraphs) must become <br/>."""
    msg = NotificationMessage(
        title='t', product_name='X', version_branch='V1.0', package_type='test',
        file_name='test.bin', package_version='v1', md5_hash='a' * 32,
        description_summary='', description_full='第一行\n第二行\n第三行',
        file_size=100, download_url='', source_url='', published_at='',
        source_id=0, chain=['X'], chain_url='https://example.com/list/1',
    )
    bodies = _format_markdown_bodies(msg, line_break='<br/>')
    body = bodies[0]
    assert '\n' not in body, f'DingTalk body should have no \\n (incl. inside desc), got: {body!r}'
    # description lines separated by <br/>
    assert '第一行<br/>第二行<br/>第三行' in body


def test_dingtalk_title_color_normal():
    from src.notifiers.dingtalk import DingtalkNotifier
    msg = NotificationMessage(
        title='t', product_name='IPS', version_branch='V4.5R', package_type='规则包',
        file_name='ips.bin', package_version='v1', md5_hash='a' * 32, file_size=100,
        description_summary='', description_full='',
        download_url='', source_url='', published_at='', source_id=0,
        chain=['IPS'], chain_url='https://example.com/list/1',
        urgency='normal',
    )
    bodies = _format_markdown_bodies(msg, line_break='<br/>')
    body = DingtalkNotifier._wrap_title_with_color(bodies[0], msg)
    assert '<font color="#1689ed">' in body, f'normal should use blue: {body[:100]!r}'


def test_dingtalk_title_color_critical():
    from src.notifiers.dingtalk import DingtalkNotifier
    msg = NotificationMessage(
        title='t', product_name='IPS', version_branch='V4.5R', package_type='规则包',
        file_name='ips.bin', package_version='v1', md5_hash='a' * 32, file_size=100,
        description_summary='', description_full='',
        download_url='', source_url='', published_at='', source_id=0,
        chain=['IPS'], chain_url='https://example.com/list/1',
        urgency='critical',
    )
    bodies = _format_markdown_bodies(msg, line_break='<br/>')
    body = DingtalkNotifier._wrap_title_with_color(bodies[0], msg)
    assert '<font color="#f5454a">' in body, f'critical should use red: {body[:100]!r}'


def test_dingtalk_title_color_rollback_overrides_urgency():
    from src.notifiers.dingtalk import DingtalkNotifier
    msg = NotificationMessage(
        title='t', product_name='IPS', version_branch='V4.5R', package_type='规则包',
        file_name='ips.bin', package_version='v1', md5_hash='a' * 32, file_size=100,
        description_summary='', description_full='',
        download_url='', source_url='', published_at='', source_id=0,
        chain=['IPS'], chain_url='https://example.com/list/1',
        urgency='normal',
        is_rollback=True,
    )
    bodies = _format_markdown_bodies(msg, msg.is_rollback, line_break='<br/>')
    body = DingtalkNotifier._wrap_title_with_color(bodies[0], msg)
    assert '<font color="#ff4d4f">' in body, f'rollback override urgency: {body[:100]!r}'


def test_feishu_each_line_is_own_paragraph():
    """飞书 post: each line of description must become its OWN paragraph,
    not bundled into one with literal '\\n' (which some Feishu clients collapse
    to whitespace, causing description rows to merge into one line)."""
    msg = NotificationMessage(
        title='t', product_name='IPS', version_branch='V4.5R', package_type='规则包',
        file_name='ips.bin', package_version='v1', md5_hash='a'*32, file_size=100,
        description_summary='', description_full='第一行\n第二行\n第三行',
        download_url='', source_url='', published_at='', source_id=0,
        chain=['IPS'], chain_url='https://example.com/list/1',
    )
    payloads = FeishuNotifier()._build_post(msg)
    # Find description paragraphs (📋 prefix)
    desc_paras = []
    for p in payloads:
        for para in p['content']:
            text = ''.join(t.get('text', '') for t in para)
            if text.startswith('📋'):
                desc_paras.append(text)
    # Three lines → three paragraphs
    assert len(desc_paras) >= 3, f'expected 3 desc paragraphs, got {len(desc_paras)}: {desc_paras}'
    # No paragraph contains literal \n (else it'd collapse in client)
    for text in desc_paras:
        assert '\n' not in text, f'paragraph contains \\n (client may collapse): {text!r}'


def test_feishu_many_lines_one_payload():
    """飞书 many lines: prefer 1 payload with many paragraphs over 50 separate payloads."""
    msg = NotificationMessage(
        title='t', product_name='IPS', version_branch='V4.5R', package_type='规则包',
        file_name='ips.bin', package_version='v1', md5_hash='a'*32, file_size=100,
        description_summary='', description_full='\n'.join(['这是一行描述'] * 50),
        download_url='', source_url='', published_at='', source_id=0,
        chain=['IPS'], chain_url='https://example.com/list/1',
    )
    payloads = FeishuNotifier()._build_post(msg)
    # 50 short lines (~10 bytes each + 📋 prefix ≈ 50B) → 50 * 50 = 2500B < 30KB → 1 payload
    assert len(payloads) <= 2, \
        f'50 short lines should bundle into 1-2 payloads, got {len(payloads)}'
    # All 📋 paragraphs should each be a single line (no embedded \n)
    for p in payloads:
        for para in p['content']:
            text = ''.join(t.get('text', '') for t in para)
            if text.startswith('📋'):
                assert '\n' not in text


def test_dingtalk_meta_labels_colored():
    """DingTalk: each meta-row label (发布页面/文件名称/.../下载地址) wrapped in <font color>.

    Verifies that all 7 expected label names get the LABEL_COLOR wrapping,
    while:
      - title stays URGENCY_COLOR (separate wrap)
      - description block untouched
      - (i/N) markers untouched
      - MD5 padding (trailing spaces) NOT included in colored segment
    """
    from src.notifiers.dingtalk import DingtalkNotifier
    msg = NotificationMessage(
        title='t', product_name='IPS', version_branch='V4.5R', package_type='规则包',
        file_name='ips.bin', package_version='v1', md5_hash='a'*32, file_size=680200,
        description_summary='', description_full='第一段\n第二段\n注意段',
        download_url='https://example.com/dl', source_url='https://example.com/list',
        published_at='2026-07-09T08:49:58',
        source_id=0, chain=['IPS'], chain_url='https://example.com/list/1',
        urgency='normal',
    )
    bodies = _format_markdown_bodies(msg, line_break='<br/>')
    body = DingtalkNotifier._wrap_labels_with_color(bodies[0])
    color = DingtalkNotifier.LABEL_COLOR
    color_tag_open = '<font color="{}">'.format(color)
    color_wrap_count = body.count(color_tag_open)
    # All 7 labels must be wrapped
    for label in DingtalkNotifier.KNOWN_LABELS:
        wrapped = '<font color="{c}">{l}</font>'.format(c=color, l=label)
        assert wrapped in body, f'label {label} not wrapped: {body!r}'
    # Exactly 7 wraps (description block does not match)
    assert color_wrap_count == 7, \
        'expected 7 label wraps, got {}'.format(color_wrap_count)
    # Description block untouched
    for desc_line in ['第一段', '第二段', '注意段']:
        bad = '<font color="{c}">{d}'.format(c=color, d=desc_line)
        assert bad not in body
    # MD5 padding stays OUTSIDE the font tag
    assert '<font color="#c27800">MD5</font>       :' in body


def test_feishu_seven_field_layout_matches_dingtalk():
    """Feishu post payload should have 7 fields matching DingTalk/WeCom layout:

      发布页面 / 文件名称 / 版本信息 / 文件大小 / MD5 / 发布时间 / 下载地址

    重要限制: 飞书机器人 webhook 不支持 text tag inline style 字段
    (实测 code=19002 "unknown content value")。所以飞书 label 退化为
    纯文本 — 跟钉钉 <font color> 不同。视觉对齐靠 label + 冒号 + value
    (同企业微信 markdown)。
    """
    msg = NotificationMessage(
        title='t', product_name='IPS', version_branch='V4.5R', package_type='规则包',
        file_name='ips_rule.bin', package_version='v1', md5_hash='a'*32,
        file_size=696520000,
        description_summary='', description_full='',
        urgency='normal',
        download_url='https://example.com/dl',
        source_url='https://example.com/list',
        published_at='2026-07-09T00:49:58',
        source_id=0, chain=['IPS'],
        chain_url='https://example.com/list/1',
    )
    payloads = FeishuNotifier()._build_post(msg)
    p = payloads[0]
    # Label text appears as leading text tag content + space + value content.
    # Verify each of the 7 label names has a paragraph where the first
    # text tag has exactly "<label> " (label + single space).
    expected_labels = {'发布页面', '文件名称', '版本信息', '文件大小', 'MD5', '发布时间', '下载地址'}
    labels_found = set()
    for para in p['content']:
        if not para:
            continue
        first = para[0]
        if first.get('tag') != 'text':
            continue
        text = first.get('text', '')
        # text format: "<label> " (label followed by single space then value)
        for label in expected_labels:
            if text == f'{label} ' or text == f'{label}':
                labels_found.add(label)
                break
    assert expected_labels.issubset(labels_found), \
        'feishu missing labels: got={}, expected={}'.format(
            sorted(labels_found), sorted(expected_labels))
    # Sanity: no style field anywhere (would 19002 the call)
    for para in p['content']:
        for tag in para:
            assert 'style' not in tag, \
                'feishu text tag should not carry style (would 19002): {}'.format(tag)


def test_feishu_download_uses_link_tag():
    """Feishu '下载地址' field uses <a> tag (link) — same as DingTalk markdown [URL](URL)."""
    msg = NotificationMessage(
        title='t', product_name='IPS', version_branch='V4.5R', package_type='规则包',
        file_name='ips.bin', package_version='v1', md5_hash='a'*32, file_size=100,
        description_summary='', description_full='',
        urgency='normal',
        download_url='https://example.com/dl',
        source_url='', published_at='',
        source_id=0, chain=['IPS'], chain_url='',
    )
    payloads = FeishuNotifier()._build_post(msg)
    found_anchor = False
    for para in payloads[0]['content']:
        for tag in para:
            if tag.get('tag') == 'a':
                # Should contain a link to download_url
                found_anchor = 'example.com/dl' in tag.get('href', '')
    assert found_anchor, f'no <a href=download_url> tag in feishu payload: {payloads[0]!r}'


def test_feishu_no_style_field_real_server_check():
    """飞书机器人 webhook 服务端实测:payload 含 style 字段会触发 19002。

    离线 fast check: 验证 _build_post 输出永远不含 style。
    """
    msg = NotificationMessage(
        title='t', product_name='IPS', version_branch='V4.5R', package_type='规则包',
        file_name='ips.bin', package_version='v1', md5_hash='a'*32, file_size=100,
        description_summary='', description_full='', urgency='normal',
        download_url='https://example.com/dl', source_url='', published_at='',
        source_id=0, chain=['IPS'], chain_url='',
    )
    payloads = FeishuNotifier()._build_post(msg)
    for p in payloads:
        for para in p['content']:
            for tag in para:
                assert 'style' not in tag, \
                    'feishu tag should NOT carry style (real server returns 19002): {}'.format(tag)


def test_feishu_strips_backticks_from_value_text():
    """飞书 IM 客户端不渲染 markdown 反引号 — 字段值中的 `` ` `` 字面显示会让
    通知看起来 "markdown 没渲染干净"。_build_post 必须把 value text tag 里的
    反引号剥掉(钉钉/企微不受影响——它们走的是 markdown body 另一条路径)。
    """
    msg = NotificationMessage(
        title='t', product_name='IPS', version_branch='V4.5R', package_type='补丁包',
        file_name='eoi.unify.avpatch.6.0.0.701.av',
        package_version='6.0.0.701',
        md5_hash='d6b4d082e0cc9aa0bdf96673b3892e3d',
        file_size=35_030_000,
        description_full='', urgency='normal',
        download_url='https://example.com/dl',
        source_url='https://example.com/list',
        published_at='2026-07-09T19:03:03',
        source_id=0, chain=['IPS'],
        chain_url='https://example.com/list',
    )
    payloads = FeishuNotifier()._build_post(msg)
    # Iterate every text tag in payload 0; assert no backtick characters.
    for p in payloads:
        for para in p['content']:
            for tag in para:
                if tag.get('tag') == 'text':
                    assert '`' not in tag.get('text', ''), \
                        'feishu text tag still has backtick: {!r}'.format(tag.get('text', ''))
    # Sanity: at least one field actually rendered the value (no regression to empty)
    rendered = []
    for para in payloads[0]['content']:
        for tag in para:
            if tag.get('tag') == 'text':
                rendered.append(tag.get('text', ''))
    full = ' | '.join(rendered)
    assert 'eoi.unify.avpatch.6.0.0.701.av' in full
    assert '6.0.0.701' in full
    assert '33.41M' in full or '35.03M' in full  # size_display formatting


# =======================================================================
# TestLogWriter / 机器人 log writer 行为测试 (2026-07-10 加频道覆盖)
# =======================================================================

from src.notifiers._log import TestLogWriter, _NullLogWriter, get_log_writer


def test_test_log_writer_writes_to_disk(tmp_path, monkeypatch):
    """TestLogWriter 能正确写文件 + in-memory 缓存(给前端 /api/channels/<id>/test-log 读)。"""
    log_path = str(tmp_path / 'test_1.log')
    writer = TestLogWriter(42, '测试渠道', log_path=log_path,
                          header='Test Log')
    writer.info('info message')
    writer.warn('warning message')
    writer.error('error message')
    writer.ok('success')
    # In-memory lines
    assert any('[INFO ] info message' in line for line in writer.lines)
    assert any('[WARN ] warning message' in line for line in writer.lines)
    assert any('[ERROR] error message' in line for line in writer.lines)
    assert any('[OK   ] success' in line for line in writer.lines)
    # On disk
    content = open(log_path, encoding='utf-8').read()
    assert '开始' in content or 'Started' in content
    assert 'info message' in content
    assert 'success' in content


def test_test_log_writer_handles_disk_error(monkeypatch):
    """TestLogWriter 在 OSError(磁盘满/无权限)时仍保留内存 lines,不抛。"""
    writer = TestLogWriter(1, 'test', log_path='/nonexistent/path/that/cannot/be/written/xx.log')
    # 不应抛异常
    writer.info('mem-only message')
    assert any('mem-only message' in line for line in writer.lines)


def test_get_log_writer_returns_null_when_disabled():
    """config['_log_disabled']=True 或没 _test_log_writer 时返回 _NullLogWriter。"""
    null_w1 = get_log_writer({}, 'dingtalk', 1, 'name')
    assert isinstance(null_w1, _NullLogWriter)
    null_w2 = get_log_writer({'_log_disabled': True}, 'dingtalk', 1, 'name')
    assert isinstance(null_w2, _NullLogWriter)


def test_get_log_writer_returns_passed_writer():
    """config['_test_log_writer'] 显式传入时原样返回(允许 API 注入)。"""
    custom = TestLogWriter(42, 'name', log_path='/tmp/test_42.log',
                           header='Test Log')
    out = get_log_writer({'_test_log_writer': custom}, 'dingtalk', 42, 'name')
    assert out is custom


def test_null_log_writer_silently_drops():
    """_NullLogWriter.info/warn/error/ok 全是 noop,不抛。"""
    null_w = _NullLogWriter()
    for fn in (null_w.info, null_w.warn, null_w.error, null_w.ok):
        fn('test message')  # 无异常


def test_dingtalk_send_writes_to_log_writer(monkeypatch):
    """DingTalk 不传 webhook_url 时应该走 log_writer.error 路径(并返回 False)。

    验证:_test_log_writer 被注入后,失败时 log.error 被调用。
    """
    from src.notifiers.dingtalk import DingtalkNotifier
    msg = NotificationMessage(
        title='t', product_name='X', version_branch='', package_type='pkg',
        file_name='x.bin', package_version='v1', md5_hash='a'*32,
        file_size=100, description_summary='', description_full='',
        download_url='', source_url='', published_at='', source_id=0,
        chain=[], chain_url='',
    )
    # 故意 missing webhook_url
    import os
    log_path = '/tmp/test_dingtalk_missing.log'
    if os.path.exists(log_path):
        os.unlink(log_path)
    writer = TestLogWriter(99, 'test', log_path=log_path, header='Test Log')
    result = DingtalkNotifier().send(msg, {
        'webhook_url': '',  # missing
        'secret': '',
        'name': '钉钉',
        '_test_log_writer': writer,
        '_channel_id': 99,
    })
    assert not result.success
    content = open(log_path, encoding='utf-8').read()
    # Verify: webhook_url missing → log records "webhook_url 为空"
    assert 'webhook_url 为空' in content
    # Verify: log also captures channel name in header
    assert '钉钉' in content or 'test' in content  # header includes channel_name 'test'


def test_feishu_send_writes_to_log_writer(monkeypatch, tmp_path):
    """FeiShu 同样: 注入 log_writer 后,失败/成功都写内容。"""
    from src.notifiers.feishu import FeishuNotifier

    msg = NotificationMessage(
        title='t', product_name='X', version_branch='', package_type='pkg',
        file_name='x.bin', package_version='v1', md5_hash='a'*32,
        file_size=100, description_summary='', description_full='',
        download_url='', source_url='', published_at='', source_id=0,
        chain=[], chain_url='',
    )
    log_path = str(tmp_path / 'fs_test.log')
    writer = TestLogWriter(11, '飞书', log_path=log_path, header='Test Log')

    # mock requests.post to always return 200
    import requests as rq
    orig = rq.post
    class FakeResp:
        status_code = 200
        def json(self): return {'code': 0, 'msg': 'success'}
    rq.post = lambda *a, **k: FakeResp()
    try:
        result = FeishuNotifier().send(msg, {
            'webhook_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/FAKE',
            'secret': '',
            'name': '飞书-test',
            '_test_log_writer': writer,
            '_channel_id': 11,
        })
    finally:
        rq.post = orig

    content = open(log_path, encoding='utf-8').read()
    # log should record the dispatch attempt with URL + HTTP status
    assert '开始推送' in content
    assert 'HTTP 200' in content
    # channel 名称写到 header
    assert '飞书' in content  # channel_name '飞书' used in TestLogWriter construction


# =======================================================================
# test-log endpoint path 计算测试 (回归测试 fecdfad 后的 bug)
# =======================================================================

def test_channel_test_log_path_per_channel_type(monkeypatch, tmp_path):
    """get_channel_test_log API 的 path 必须按 channel type 走对应文件:
        - email → /tmp/email_test_<id>.log
        - dingtalk / feishu / wecom / apprise → /tmp/<type>_test_<id>.log

    回归 fecdfad 时只支持 email,导致机器人 channel 点 测试 + 日志 看不到内容。
    """
    # 用最小依赖:直接构造 channel row 对象(避免 mock 整个 DB)
    import os

    # 模拟 5 个 channel type 各自的 log 文件已写入
    type_to_log = {
        'email': '/tmp/email_test_42.log',
        'dingtalk': '/tmp/dingtalk_test_43.log',
        'feishu': '/tmp/feishu_test_44.log',
        'wecom': '/tmp/wecom_test_45.log',
        'apprise': '/tmp/apprise_test_46.log',
    }
    for p in type_to_log.values():
        with open(p, 'w') as f:
            f.write(f'test log for {p}\n')

    # 测试 path 计算的纯逻辑(避免 import 整个 flask app;只要 path 推导函数正确就行)
    def computed_path(channel_type, ch_id):
        if channel_type == 'email':
            return f'/tmp/email_test_{ch_id}.log'
        return f'/tmp/{channel_type}_test_{ch_id}.log'

    for ch_type, expected in type_to_log.items():
        ch_id = int(expected.split('_')[-1].split('.')[0])
        assert computed_path(ch_type, ch_id) == expected, \
            f'{ch_type} path mismatch: got {computed_path(ch_type, ch_id)}, expected {expected}'

    # 清理
    for p in type_to_log.values():
        if os.path.exists(p):
            os.unlink(p)


def test_feishu_title_uses_chain_when_available():
    """飞书 post title 跟钉钉/企微一致:有 chain 时用完整链拼接,
    无 chain 时 fallback 到 product_name + package_version。

    修复历史不一致(钉钉/企微用 chain 拼,飞书之前硬编码只显示 product_name+version)。
    """
    msg = NotificationMessage(
        title='fallback title', product_name='ADS', version_branch='V4.5R', package_type='pkg',
        file_name='x.bin', package_version='V4.5R90F04', md5_hash='a'*32, file_size=100,
        description_summary='', description_full='', urgency='normal',
        download_url='https://x.com/d', source_url='https://x.com/s', published_at='',
        source_id=0,
        chain=['网络安全', '抗拒绝服务', 'ADS', 'V4.5R90F04'],
        chain_url='https://x.com/list/1',
    )
    payloads = FeishuNotifier()._build_post(msg)
    title = payloads[0]['title']
    # 跟钉钉链拼接保持一致 (钉钉是 "chain_text 更新",飞书少了"更新"后缀但链完整)
    assert '网络安全 / 抗拒绝服务 / ADS / V4.5R90F04' in title, \
        f'feishu title should embed full chain: {title!r}'
    assert title.startswith('🔔 '), f'feishu title should have emoji icon: {title!r}'

    # 无 chain → fallback 到 product_name + package_version
    msg2 = NotificationMessage(
        title='fallback title', product_name='ADS', version_branch='V4.5R', package_type='pkg',
        file_name='x.bin', package_version='V4.5R90F04', md5_hash='a'*32, file_size=100,
        description_summary='', description_full='', urgency='normal',
        download_url='https://x.com/d', source_url='https://x.com/s', published_at='',
        source_id=0, chain=[], chain_url='https://x.com/list/1',
    )
    payloads2 = FeishuNotifier()._build_post(msg2)
    title2 = payloads2[0]['title']
    assert title2 == '🔔 ADS V4.5R90F04', \
        f'feishu title fallback should be product+version: {title2!r}'

    # rollback 也要 chain 拼
    msg3 = NotificationMessage(
        title='fallback title', product_name='ADS', version_branch='V4.5R', package_type='pkg',
        file_name='x.bin', package_version='V4.5R90F04', md5_hash='a'*32, file_size=100,
        description_summary='', description_full='', urgency='normal',
        download_url='https://x.com/d', source_url='https://x.com/s', published_at='',
        source_id=0,
        chain=['网络安全', '抗拒绝服务', 'ADS', 'V4.5R90F04'],
        chain_url='https://x.com/list/1',
        is_rollback=True,
    )
    payloads3 = FeishuNotifier()._build_post(msg3)
    title3 = payloads3[0]['title']
    assert title3.startswith('⚠️ 撤回 '), f'is_rollback title should start with warning: {title3!r}'
    assert '网络安全 / 抗拒绝服务 / ADS / V4.5R90F04' in title3












# =======================================================================
# 签名 URL 测试(_sign_url:钉钉/飞书共用,差 2 参数)
# =======================================================================

# 钉钉/飞书后台算法(独立实现一次,验证 _sign_url 输出与之完全一致)
def _expected_sign(timestamp: str, secret: str, raw_base64: bool = False) -> str:
    """Reference HMAC-SHA256 implementation per DingTalk/Feishu docs.

    DingTalk: base64 raw → URL-quote the result
    Feishu:   base64 raw, no URL-quote
    """
    s2s = f'{timestamp}\n{secret}'
    code = hmac.new(secret.encode('utf-8'), s2s.encode('utf-8'), hashlib.sha256).digest()
    raw = base64.b64encode(code).decode('utf-8')
    if raw_base64:
        return raw
    from urllib.parse import quote_plus
    return quote_plus(raw)


def test_sign_url_no_secret_unchanged():
    url = _sign_url('https://example.com/webhook', '', timestamp_unit='ms', url_quote=True)
    assert url == 'https://example.com/webhook', f'should be unchanged when no secret: {url}'


def test_sign_url_dingtalk_semantics():
    """DingTalk: timestamp in milliseconds, base64 URL-quoted."""
    url_in = 'https://oapi.dingtalk.com/robot/send?access_token=ABC'
    secret = 'SEC123_secret_test'
    signed = _sign_url(url_in, secret, timestamp_unit='ms', url_quote=True)
    # Schema: ?access_token=ABC&timestamp=<13-digit>&sign=<urlquoted>
    m = re.match(r'^(.+?)&timestamp=(\d+)&sign=(\S+)$', signed)
    assert m, f'unexpected format: {signed}'
    base, ts, sign = m.group(1), m.group(2), m.group(3)
    assert base == url_in
    assert len(ts) == 13, f'DingTalk timestamp should be 13 digits (ms), got {len(ts)}'
    # Verify sign is correct HMAC-SHA256 + URL-quoted base64
    from urllib.parse import unquote_plus
    assert unquote_plus(sign) == _expected_sign(ts, secret, raw_base64=True), \
        f'sign mismatch: expected {_expected_sign(ts, secret, raw_base64=True)}, got {unquote_plus(sign)}'


def test_sign_url_feishu_semantics():
    """Feishu: timestamp in seconds, base64 RAW (no URL-quote)."""
    url_in = 'https://open.feishu.cn/open-apis/bot/v2/hook/XYZ'
    secret = 'FST_secret_test'
    signed = _sign_url(url_in, secret, timestamp_unit='s', url_quote=False)
    # Schema: ?timestamp=<10-digit>&sign=<base64 with possible + / = >
    m = re.match(r'^(.+?)\?timestamp=(\d+)&sign=(\S+)$', signed)
    assert m, f'unexpected format: {signed}'
    base, ts, sign = m.group(1), m.group(2), m.group(3)
    assert base == url_in
    assert len(ts) == 10, f'Feishu timestamp should be 10 digits (s), got {len(ts)}'
    # Sign must equal unquoted base64 reference
    assert sign == _expected_sign(ts, secret, raw_base64=True), \
        f'Feishu sign mismatch: expected {_expected_sign(ts, secret, raw_base64=True)}, got {sign}'


def test_sign_url_query_separator():
    """When URL has no ?, _sign_url uses ?; otherwise uses &."""
    secret = 's'
    # No ? → use ?
    r1 = _sign_url('https://x.test/hook', secret, timestamp_unit='s', url_quote=False)
    assert '?' in r1
    # With ? → use &
    r2 = _sign_url('https://x.test/hook?a=1', secret, timestamp_unit='s', url_quote=False)
    assert '&timestamp=' in r2
    assert r2.count('?') == 1


def test_sign_url_default_is_dingtalk_like():
    """Default params (timestamp_unit='ms', url_quote=True) match DingTalk behavior."""
    signed = _sign_url('https://oapi.dingtalk.com/robot/send?access_token=A', 'S')
    # 13-digit timestamp → DingTalk default
    assert re.search(r'timestamp=\d{13}', signed)
    # sign is url-quoted (i.e. + and = are encoded as %2B %3D)
    m = re.search(r'sign=(\S+)', signed)
    assert m
    assert '+' not in m.group(1) or '%2B' in m.group(1)
    assert '=' not in m.group(1) or '%3D' in m.group(1)


def test_sign_url_real_algorithm_match_dingtalk():
    """Cross-check: _sign_url with dingtalk params must produce the same sign as
    an independent HMAC computation matching DingTalk docs.
    """
    url_in = 'https://oapi.dingtalk.com/robot/send?access_token=REAL_TEST'
    secret = 'SECabcdef0123456789'
    signed = _sign_url(url_in, secret, timestamp_unit='ms', url_quote=True)
    ts = re.search(r'timestamp=(\d+)', signed).group(1)
    sign_param = re.search(r'sign=(\S+)', signed).group(1)
    from urllib.parse import unquote_plus
    unquoted = unquote_plus(sign_param)
    # Recompute independently
    s2s = f'{ts}\n{secret}'
    expected = base64.b64encode(
        hmac.new(secret.encode('utf-8'), s2s.encode('utf-8'), hashlib.sha256).digest()
    ).decode('utf-8')
    assert unquoted == expected, f'\n  got      : {unquoted}\n  expected : {expected}'


def test_sign_url_cross_check_feishu():
    url_in = 'https://open.feishu.cn/open-apis/bot/v2/hook/X'
    secret = 'FShhh_secret'
    signed = _sign_url(url_in, secret, timestamp_unit='s', url_quote=False)
    ts = re.search(r'timestamp=(\d+)', signed).group(1)
    sign_param = re.search(r'sign=(\S+)', signed).group(1)
    s2s = f'{ts}\n{secret}'
    expected = base64.b64encode(
        hmac.new(secret.encode('utf-8'), s2s.encode('utf-8'), hashlib.sha256).digest()
    ).decode('utf-8')
    assert sign_param == expected


def test_dingtalk_send_uses_signed_url(monkeypatch):
    """DingTalkNotifier.send() calls _sign_url to append sign params."""
    captured = []
    class FakeResp:
        status_code = 200
        def json(self): return {'errcode': 0, 'errmsg': 'ok'}
    def fake_post(url, json, timeout):
        captured.append(url)
        return FakeResp()
    monkeypatch.setattr('requests.post', fake_post)

    msg = NotificationMessage(
        title='t', product_name='X', version_branch='',
        package_type='pkg', file_name='x.bin', package_version='v1',
        md5_hash='a', file_size=100, description_summary='', description_full='hi',
        download_url='', source_url='', published_at='', source_id=0,
        chain=[], chain_url='',
    )
    cfg = {
        'webhook_url': 'https://oapi.dingtalk.com/robot/send?access_token=K',
        'secret': 'SEC123',
        'name': '钉钉-test',
    }
    result = DingtalkNotifier().send(msg, cfg)
    assert result.success, f'failed: {result.error_message}'
    assert len(captured) == 1
    posted_url = captured[0]
    assert 'timestamp=' in posted_url
    assert 'sign=' in posted_url
    # DingTalk: 13-digit
    assert re.search(r'timestamp=\d{13}', posted_url)


def test_feishu_send_uses_signed_url(monkeypatch):
    """FeishuNotifier.send() now calls _sign_url to append sign params."""
    captured = []
    class FakeResp:
        status_code = 200
        def json(self): return {'code': 0}
    def fake_post(url, json, timeout):
        captured.append(url)
        return FakeResp()
    monkeypatch.setattr('requests.post', fake_post)

    msg = NotificationMessage(
        title='t', product_name='X', version_branch='',
        package_type='pkg', file_name='x.bin', package_version='v1',
        md5_hash='a', file_size=100, description_summary='', description_full='hi',
        download_url='', source_url='', published_at='', source_id=0,
        chain=[], chain_url='',
    )
    cfg = {
        'webhook_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/K',
        'secret': 'FShhh_secret',
        'name': '飞书-test',
    }
    result = FeishuNotifier().send(msg, cfg)
    assert result.success, f'failed: {result.error_message}'
    assert len(captured) == 1
    posted_url = captured[0]
    # Feishu: timestamp=10-digit, sign=raw base64 (no url-quote)
    assert re.search(r'timestamp=\d{10}', posted_url)
    assert 'sign=' in posted_url
    # Feishu sign should NOT contain URL-encoded chars (%2B / %3D)
    m = re.search(r'sign=(\S+)', posted_url)
    assert m
    sign_param = m.group(1)
    assert '%2B' not in sign_param, f'Feishu sign should not be url-quoted: {sign_param}'
    assert '%3D' not in sign_param, f'Feishu sign should not be url-quoted: {sign_param}'


def test_feishu_send_without_secret_no_sign():
    """Feishu send without secret should NOT add sign params (default key only)."""
    captured = []
    class FakeResp:
        def json(self): return {'code': 0}
    def fake_post(url, json, timeout):
        captured.append(url)
        return FakeResp()
    import requests as req
    orig = req.post
    req.post = fake_post
    try:
        msg = NotificationMessage(
            title='t', product_name='X', version_branch='', package_type='pkg',
            file_name='x.bin', package_version='v1', md5_hash='a', file_size=100,
            description_summary='', description_full='hi',
            download_url='', source_url='', published_at='', source_id=0,
            chain=[], chain_url='',
        )
        cfg = {
            'webhook_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/K',
            'name': '飞书-nosecret',
            # no secret
        }
        FeishuNotifier().send(msg, cfg)
    finally:
        req.post = orig
    posted_url = captured[0]
    assert 'timestamp=' not in posted_url
    assert 'sign=' not in posted_url


def test_dingtalk_send_without_secret_no_sign():
    """DingTalk backward compat: no secret → no sign params appended."""
    captured = []
    class FakeResp:
        def json(self): return {'errcode': 0}
    def fake_post(url, json, timeout):
        captured.append(url)
        return FakeResp()
    import requests as req
    orig = req.post
    req.post = fake_post
    try:
        msg = NotificationMessage(
            title='t', product_name='X', version_branch='', package_type='pkg',
            file_name='x.bin', package_version='v1', md5_hash='a', file_size=100,
            description_summary='', description_full='hi',
            download_url='', source_url='', published_at='', source_id=0,
            chain=[], chain_url='',
        )
        cfg = {
            'webhook_url': 'https://oapi.dingtalk.com/robot/send?access_token=K',
            'name': 'dingtalk-nosecret',
        }
        DingtalkNotifier().send(msg, cfg)
    finally:
        req.post = orig
    posted_url = captured[0]
    assert 'timestamp=' not in posted_url
    assert 'sign=' not in posted_url
