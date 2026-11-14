"""规则库变更解析 + 通知正文不展示规则变更摘要（暂不接入推送）。"""
from src.collectors.nsfocus import parse_description
from src.notifiers.base import (
    NotificationMessage,
    _format_html_body,
    _format_markdown_body,
)


IPS_DESC = (
    "本升级包为入侵防护特征库升级包。\n"
    "新增规则:\n"
    "1. 攻击[31050]:泛微 mobilemode/public.jsp 任意用户登录漏洞\n"
    "2. 攻击[31049]:whatweb 指纹扫描工具\n"
    "更新规则:\n"
    "1. 攻击[30001]:某已有规则更新\n"
)

WAF_DESC = (
    "一、新增规则\n"
    "27007155 nine_and_on_pichy_sqli 防护NINE_AND_ON相关漏洞\n"
    "27007156 path_travel_file_include防护路径穿越漏洞\n"
    "二、修改规则\n"
    "27007001 updated_rule 更新规则\n"
    "三、删除规则\n"
    "27006001 deleted_rule 已删除规则\n"
    "四、其他修改\n"
    "无\n"
    "五、升级建议\n"
    "版本 V6.0.9，升级后重启\n"
)


def test_parse_description_ips_half_width_colon_keeps_names():
    parsed = parse_description(IPS_DESC)
    assert parsed['added'] == ['31050', '31049']
    assert parsed['modified'] == ['30001']
    assert parsed['rule_changes']['added'] == [
        {'id': 31050, 'name': '泛微 mobilemode/public.jsp 任意用户登录漏洞'},
        {'id': 31049, 'name': 'whatweb 指纹扫描工具'},
    ]
    assert parsed['rule_changes']['updated'][0]['id'] == 30001


def test_parse_description_waf_sections_keep_names():
    parsed = parse_description(WAF_DESC)
    assert [e['id'] for e in parsed['rule_changes']['added']] == [27007155, 27007156]
    assert [e['id'] for e in parsed['rule_changes']['updated']] == [27007001]
    assert [e['id'] for e in parsed['rule_changes']['deleted']] == [27006001]
    assert parsed['added'] == ['27007155', '27007156']
    assert parsed['modified'] == ['27007001']
    assert parsed['deleted'] == ['27006001']
    assert parsed['min_sys_version'] == 'V6.0.9'


def test_parse_description_without_rules_unchanged():
    parsed = parse_description('这是一个普通说明，没有规则列表。')
    assert parsed['added'] == []
    assert parsed['modified'] == []
    assert 'rule_changes' not in parsed


def _waf_snap():
    return {
        'product_name': 'WAF',
        'file_name': 'pkg.zip',
        'package_version': 'V6.0.9',
        'md5_hash': 'a' * 32,
        'description_raw': WAF_DESC,
        'description_parsed': parse_description(WAF_DESC),
        'published_at': '2026-11-10T03:22:19',
        'source_url': 'https://update.nsfocus.com/update/list',
    }


def test_notification_templates_exclude_rule_change_summary():
    """规则库变更暂不接入推送：正文不出现额外摘要行，保留原模板。"""
    msg = NotificationMessage.from_snapshot(_waf_snap(), user_chain=['WAF', 'V6.0.9', 'sys'])
    markdown = _format_markdown_body(msg)
    assert '📦 规则变更' not in markdown
    assert '规则变更:' not in markdown

    html = _format_html_body(msg)
    assert '📦 规则变更' not in html
    assert '规则变更:' not in html


def test_from_snapshot_summary_keeps_counts_without_names():
    """IM 摘要保持原模板：只有条数，不带规则名。"""
    msg = NotificationMessage.from_snapshot(_waf_snap(), user_chain=['WAF', 'V6.0.9', 'sys'])
    assert '新增2条规则' in msg.description_summary
    assert '修改1条规则' in msg.description_summary
    assert '删除1条规则' in msg.description_summary
    assert 'nine_and_on_pichy_sqli' not in msg.description_summary
    assert '（' not in msg.description_summary
