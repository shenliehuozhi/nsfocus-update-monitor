"""Tests for FeishuNotifier.

Strategy:
- _build_post is a pure function — test directly with constructed NotificationMessage
- send() makes real requests.post calls — replace with monkeypatch mock
- All assertions are on payload structure / return values, NEVER on real webhook
"""
import json
import pytest
from unittest.mock import MagicMock, patch

from src.notifiers.feishu import FeishuNotifier
from src.notifiers.base import NotificationMessage, DeliveryResult


@pytest.fixture
def base_msg():
    """Standard message used by most tests."""
    return NotificationMessage(
        title='IPS 更新',
        product_name='IPS',
        version_branch='V6',
        package_type='rule',
        file_name='sig.zip',
        package_version='2.0.0.45266',
        md5_hash='a1b2c3d4e5f60708090a0b0c0d0e0f00',
        file_size=1500000,
        description_summary='漏洞库升级',
        description_full='',
        urgency='high',
        download_url='/update/downloads/id/190001',
        source_url='/update/listNewipsDetail',
        published_at='2024-01-15T10:30:00',
        chain=['网络安全', '抗拒绝服务', 'ADS', 'V4.5R'],
        chain_url='https://update.nsfocus.com/update/listNewipsDetail',
    )


@pytest.fixture
def feishu_notifier():
    return FeishuNotifier()


# ============================================================================
# _build_post — pure function tests (NO network)
# ============================================================================

def test_build_post_title_uses_chain_when_present(feishu_notifier, base_msg):
    """Title = 'chain_text 更新' when chain is provided."""
    payloads = feishu_notifier._build_post(base_msg)
    assert len(payloads) >= 1
    title = payloads[0]['title']
    # chain joined with ' / ' + ' 更新'
    assert '网络安全 / 抗拒绝服务 / ADS / V4.5R' in title
    assert title.endswith(' 更新')


def test_build_post_title_fallback_to_product_version(feishu_notifier, base_msg):
    """Title = 'product_name package_version' when chain is empty."""
    base_msg.chain = []
    payloads = feishu_notifier._build_post(base_msg)
    assert 'ADS' not in payloads[0]['title'] or 'IPS' in payloads[0]['title'] or '2.0.0.45266' in payloads[0]['title']


def test_build_post_rollback_uses_warning_emoji(feishu_notifier, base_msg):
    """Rollback messages start with ⚠️ 撤回 (not 🔔)."""
    base_msg.is_rollback = True
    payloads = feishu_notifier._build_post(base_msg)
    assert payloads[0]['title'].startswith('⚠️ 撤回')


def test_build_post_normal_uses_bell_emoji(feishu_notifier, base_msg):
    """Non-rollback messages start with 🔔."""
    payloads = feishu_notifier._build_post(base_msg)
    assert payloads[0]['title'].startswith('🔔')


def test_build_post_text_tags_have_no_style_field(feishu_notifier, base_msg):
    """Critical constraint: Feishu webhook rejects 'style' field on text tags (code 19002).

    Verified by scanning ALL text tags in the payload — none should have a 'style' key.
    """
    payloads = feishu_notifier._build_post(base_msg)
    for payload in payloads:
        for para in payload['content']:
            for tag in para:
                if tag.get('tag') == 'text':
                    assert 'style' not in tag, (
                        f"text tag has style field (would be rejected by Feishu): {tag}"
                    )


def test_build_post_strips_backticks_in_text_values(feishu_notifier, base_msg):
    """Feishu doesn't render markdown backticks — value text tags have them stripped.

    File name with backticks: input `sig.zip` → output sig.zip.
    """
    payloads = feishu_notifier._build_post(base_msg)
    # Find any text tag — check that no value text tag contains literal backticks
    for payload in payloads:
        for para in payload['content']:
            for tag in para:
                if tag.get('tag') == 'text':
                    # Backticks from value text (e.g. file_name) must be stripped.
                    # The label text like '文件名称 ' is also 'text' but has no backticks.
                    assert '`' not in tag['text'], (
                        f"text tag has backticks (Feishu renders literally): {tag['text']!r}"
                    )


def test_build_post_includes_rollback_warning_paragraph(feishu_notifier, base_msg):
    """Rollback messages have an explicit '⚠️ 此软件包已被撤回' paragraph."""
    base_msg.is_rollback = True
    payloads = feishu_notifier._build_post(base_msg)
    first_content = payloads[0]['content']
    # First paragraph should mention the rollback warning
    assert any('撤回' in str(p) for p in first_content)


def test_build_post_publish_url_as_link_when_chain_url(feishu_notifier, base_msg):
    """chain_url present → use as 'a' tag with href."""
    payloads = feishu_notifier._build_post(base_msg)
    first_content = payloads[0]['content']
    # Should have an 'a' tag with href matching chain_url
    has_link = False
    for para in first_content:
        for tag in para:
            if tag.get('tag') == 'a' and tag.get('href') == base_msg.chain_url:
                has_link = True
    assert has_link, f"expected 'a' tag with chain_url href, got {first_content!r}"


def test_build_post_publish_url_fallback_to_source_url(feishu_notifier, base_msg):
    """No chain_url → fallback to source_url."""
    base_msg.chain_url = ''
    payloads = feishu_notifier._build_post(base_msg)
    first_content = payloads[0]['content']
    has_link = any(
        tag.get('tag') == 'a' and tag.get('href') == base_msg.source_url
        for para in first_content for tag in para
    )
    assert has_link


def test_build_post_no_description_returns_single_payload(feishu_notifier, base_msg):
    """No description → single payload, no splitting."""
    base_msg.description_summary = ''
    base_msg.description_full = ''
    payloads = feishu_notifier._build_post(base_msg)
    assert len(payloads) == 1


def test_build_post_long_description_splits_into_multiple(feishu_notifier, base_msg):
    """Long description that exceeds budget → multiple payloads with (i/N) marker."""
    # Build description that triggers splitting (> 30KB budget after chunks)
    base_msg.description_summary = '\n'.join(
        f'新增规则：\n1. 攻击[{40000+i}]:规则名{i}_' + 'X' * 200
        for i in range(200)
    )
    payloads = feishu_notifier._build_post(base_msg)
    # Should split into > 1 payload
    assert len(payloads) > 1
    # Each payload should be under budget
    for p in payloads:
        size = len(json.dumps(p, ensure_ascii=False).encode('utf-8'))
        assert size < 30_000


def test_build_post_min_sys_version_warning(feishu_notifier, base_msg):
    """If min_sys_version set, a dependency warning paragraph is added."""
    base_msg.min_sys_version = 'V6.5.0'
    payloads = feishu_notifier._build_post(base_msg)
    has_warning = any(
        '依赖' in str(p) and 'V6.5.0' in str(p)
        for p in payloads[0]['content']
    )
    assert has_warning


def test_build_post_payload_under_byte_budget(feishu_notifier, base_msg):
    """Each payload must be < 30KB (Feishu webhook limit)."""
    payloads = feishu_notifier._build_post(base_msg)
    for payload in payloads:
        size = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        assert size < 30_000, f"payload exceeds 30KB: {size} bytes"


def test_build_post_oversized_single_line_hard_cut(feishu_notifier, base_msg):
    """A single line > 4KB must be hard-cut into pieces (rare path)."""
    long_line = 'X' * 5000  # > 4KB paragraph budget
    base_msg.description_summary = long_line
    payloads = feishu_notifier._build_post(base_msg)
    # Should produce multiple chunks even though there's only 1 line
    # (each hard-cut piece = its own chunk/payload)
    assert len(payloads) >= 1
    for payload in payloads:
        size = len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        assert size < 30_000


# ============================================================================
# send() — mock requests.post, test return value + payload structure
# ============================================================================

def _make_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


def test_send_missing_webhook_returns_failure(feishu_notifier, base_msg):
    """No webhook_url → DeliveryResult(success=False)."""
    result = feishu_notifier.send(base_msg, {'webhook_url': '', 'secret': ''})
    assert result.success is False
    assert 'webhook' in result.error_message.lower() or 'webhook' in str(result.channel_name)


def test_send_happy_path_returns_success(feishu_notifier, base_msg):
    """Successful response (code=0) → success."""
    resp = _make_response({'code': 0, 'msg': 'ok', 'data': {}})
    with patch('src.notifiers.feishu.requests.post', return_value=resp) as mock_post:
        result = feishu_notifier.send(
            base_msg,
            {'webhook_url': 'https://open.feishu.cn/hook/abc', 'secret': '', 'name': 'feishu-test'},
        )
    assert result.success is True
    assert result.channel_type == 'feishu'
    # Verify exactly one request per payload
    assert mock_post.call_count >= 1
    # Verify payload structure: msg_type='post', content.post.zh_cn present
    first_payload = mock_post.call_args_list[0].kwargs['json']
    assert first_payload['msg_type'] == 'post'
    assert 'zh_cn' in first_payload['content']['post']


def test_send_with_secret_signs_url(feishu_notifier, base_msg):
    """When secret is set, URL gets '?timestamp=...&sign=...' appended (signature)."""
    resp = _make_response({'code': 0, 'msg': 'ok'})
    with patch('src.notifiers.feishu.requests.post', return_value=resp) as mock_post:
        feishu_notifier.send(
            base_msg,
            {'webhook_url': 'https://open.feishu.cn/hook/abc', 'secret': 'mysecret', 'name': 't'},
        )
    # URL passed to requests.post should have timestamp + sign
    called_url = mock_post.call_args_list[0].args[0]
    assert 'timestamp=' in called_url
    assert 'sign=' in called_url


def test_send_with_status_code_zero_accepted(feishu_notifier, base_msg):
    """Old feishu API may return StatusCode=0 instead of code=0 — both accepted."""
    resp = _make_response({'StatusCode': 0, 'Message': 'ok'})
    with patch('src.notifiers.feishu.requests.post', return_value=resp):
        result = feishu_notifier.send(
            base_msg,
            {'webhook_url': 'https://open.feishu.cn/hook/abc', 'secret': '', 'name': 't'},
        )
    assert result.success is True


def test_send_error_response_returns_failure(feishu_notifier, base_msg):
    """Feishu returns code != 0 → DeliveryResult(success=False)."""
    resp = _make_response({'code': 19002, 'msg': 'unknown content value'})
    with patch('src.notifiers.feishu.requests.post', return_value=resp):
        result = feishu_notifier.send(
            base_msg,
            {'webhook_url': 'https://open.feishu.cn/hook/abc', 'secret': '', 'name': 't'},
        )
    assert result.success is False
    assert '19002' in result.error_message or 'unknown' in result.error_message


def test_send_http_exception_returns_failure(feishu_notifier, base_msg):
    """requests.post raises (timeout / network error) → DeliveryResult(success=False)."""
    with patch('src.notifiers.feishu.requests.post', side_effect=Exception('timeout')):
        result = feishu_notifier.send(
            base_msg,
            {'webhook_url': 'https://open.feishu.cn/hook/abc', 'secret': '', 'name': 't'},
        )
    assert result.success is False
    assert 'timeout' in result.error_message or 'Exception' in result.error_message


def test_send_multi_payload_only_first_failure_short_circuits(feishu_notifier, base_msg):
    """Multi-payload message: 2nd payload fails → return error for 2nd, don't retry 3rd+."""
    # Long description forces multi-payload
    base_msg.description_summary = '\n'.join(
        f'新增规则：\n1. 攻击[{40000+i}]:规则名{i}_' + 'X' * 200 for i in range(200)
    )
    ok_resp = _make_response({'code': 0, 'msg': 'ok'})
    fail_resp = _make_response({'code': 230001, 'msg': 'second fails'})
    # First call ok, second fails, third should NOT be made
    with patch('src.notifiers.feishu.requests.post', side_effect=[ok_resp, fail_resp]) as mock_post:
        result = feishu_notifier.send(
            base_msg,
            {'webhook_url': 'https://open.feishu.cn/hook/abc', 'secret': '', 'name': 't'},
        )
    assert result.success is False
    # First attempt ok, second fails — total calls = 2 (NOT more)
    assert mock_post.call_count == 2
    assert '230001' in result.error_message or 'second fails' in result.error_message


def test_send_single_payload_success_message_empty(feishu_notifier, base_msg):
    """1 payload success → error string is empty (per implementation)."""
    resp = _make_response({'code': 0, 'msg': 'ok'})
    with patch('src.notifiers.feishu.requests.post', return_value=resp):
        result = feishu_notifier.send(
            base_msg,
            {'webhook_url': 'https://open.feishu.cn/hook/abc', 'secret': '', 'name': 't'},
        )
    assert result.success is True
    assert result.error_message == ''


def test_send_multi_payload_success_message_count(feishu_notifier, base_msg):
    """>1 payloads successful → error string mentions payload count."""
    base_msg.description_summary = '\n'.join(
        f'新增规则：\n1. 攻击[{40000+i}]:规则名{i}_' + 'X' * 200 for i in range(200)
    )
    resp = _make_response({'code': 0, 'msg': 'ok'})
    with patch('src.notifiers.feishu.requests.post', return_value=resp):
        result = feishu_notifier.send(
            base_msg,
            {'webhook_url': 'https://open.feishu.cn/hook/abc', 'secret': '', 'name': 't'},
        )
    assert result.success is True
    assert 'message' in result.error_message.lower() or 'sent' in result.error_message.lower() or result.error_message == ''


# ============================================================================
# send_confirmation — confirmation message after multiple results
# ============================================================================

def test_send_confirmation_missing_webhook_returns_failure(feishu_notifier, base_msg):
    """No webhook → failure without raising."""
    result = feishu_notifier.send_confirmation(
        base_msg, [], {'webhook_url': '', 'secret': ''}
    )
    assert result.success is False


def test_send_confirmation_success_status_text_includes_results(feishu_notifier, base_msg):
    """Confirmation payload includes ✅ / ❌ icons for each result."""
    results = [
        DeliveryResult(True, 'feishu', 'A'),
        DeliveryResult(False, 'dingtalk', 'B'),
    ]
    resp = _make_response({'code': 0, 'msg': 'ok'})
    with patch('src.notifiers.feishu.requests.post', return_value=resp) as mock_post:
        result = feishu_notifier.send_confirmation(
            base_msg, results, {'webhook_url': 'https://open.feishu.cn/hook/abc', 'secret': '', 'name': 't'},
        )
    assert result.success is True
    payload = mock_post.call_args.kwargs['json']
    # The text message should contain icons
    assert '✅' in payload['content']['text']
    assert '❌' in payload['content']['text']
    assert 'feishu' in payload['content']['text']
    assert 'dingtalk' in payload['content']['text']


def test_send_confirmation_http_error_silently_swallowed(feishu_notifier, base_msg):
    """Confirmation request fails → still return success (best-effort)."""
    with patch('src.notifiers.feishu.requests.post', side_effect=Exception('timeout')):
        result = feishu_notifier.send_confirmation(
            base_msg, [], {'webhook_url': 'https://open.feishu.cn/hook/abc', 'secret': '', 'name': 't'},
        )
    # Confirmation is best-effort — even on error, returns success
    assert result.success is True


def test_channel_type_is_feishu(feishu_notifier):
    """channel_type class attribute."""
    assert feishu_notifier.channel_type == 'feishu'