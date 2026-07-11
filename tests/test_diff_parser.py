"""Tests for src.diff (pure function parsers).

Covers:
- parse_rules(): IPS description parser (added/updated blocks)
- parse_rules_waf(): WAF description parser (added/updated/deleted blocks)
- diff_rules(): two-package + intermediate-version diff
- compare_versions(): version string ordering
- diff_to_csv_rows(): CSV flattening
- _parse_kv_row(): single-key row extraction
- _slice_block(): sub-block slicing
- parse_rule_packages_from_html(): vendor HTML table parser (multi-table)
"""
import pytest
from src.diff import (
    parse_rules,
    parse_rules_waf,
    diff_rules,
    diff_to_csv_rows,
    compare_versions,
    parse_rule_packages_from_html,
    _parse_kv_row,
    _slice_block,
)


# ============================================================================
# parse_rules (IPS) — added / updated blocks
# ============================================================================

def test_parse_rules_ips_added_block():
    """Extract rules from 新增规则： block (Chinese zh)."""
    desc = (
        "新增规则：\n"
        "1. 攻击[42792]:SpectralViper后门 C2通信\n"
        "2. 攻击[42793]:SQL注入测试规则\n"
        "更新规则：\n"
        "1. 攻击[42790]:已有规则更新\n"
    )
    result = parse_rules(desc)
    assert result['added'] == [
        (42792, 'SpectralViper后门 C2通信'),
        (42793, 'SQL注入测试规则'),
    ]
    assert result['updated'] == [(42790, '已有规则更新')]


def test_parse_rules_ips_updated_block():
    """Extract rules from 更新规则： block only."""
    desc = (
        "更新规则：\n"
        "1. 攻击[42790]:规则A更新\n"
        "2. 攻击[42791]:规则B更新\n"
    )
    result = parse_rules(desc)
    assert result['added'] == []
    assert result['updated'] == [
        (42790, '规则A更新'),
        (42791, '规则B更新'),
    ]


def test_parse_rules_ips_empty_description():
    """Empty description → both blocks empty (not None)."""
    assert parse_rules('') == {'added': [], 'updated': []}
    assert parse_rules(None) == {'added': [], 'updated': []}


def test_parse_rules_ips_english_blocks_discarded():
    """English 'new rules:' block is dropped — frontend only shows Chinese."""
    desc = (
        "new rules:\n"
        "1. threat[42792]:English name here\n"
        "新增规则：\n"
        "1. 攻击[42792]:中文规则\n"
    )
    result = parse_rules(desc)
    # Only the zh block contributes; English is silently dropped
    assert result['added'] == [(42792, '中文规则')]
    assert result['updated'] == []


def test_parse_rules_ips_no_markers():
    """Description with neither 新增规则： nor 更新规则： → empty both."""
    result = parse_rules('这是一段没有规则的描述文本')
    assert result == {'added': [], 'updated': []}


def test_parse_rules_ips_added_with_colon_variant():
    """English colon 'Updated:' inside description does not break zh parser."""
    desc = (
        "新增规则：\n"
        "1. 攻击[42792]:rule one\n"
        "更新规则：\n"
        "1. 攻击[42790]:rule two\n"
        "new rules:\n"
        "1. threat[42792]:english\n"
    )
    result = parse_rules(desc)
    assert len(result['added']) == 1
    assert len(result['updated']) == 1


# ============================================================================
# parse_rules_waf — added / updated / deleted
# ============================================================================

def test_parse_rules_waf_added():
    """Extract 一、新增规则 block (7-digit ID + name)."""
    desc = (
        "一、新增规则\n"
        "27007155 nine_and_on_pichy_sqli 防护NINE_AND_ON相关漏洞\n"
        "27007156 rule_name_two 第二个新增规则\n"
        "二、修改规则\n"
    )
    result = parse_rules_waf(desc)
    assert result['added'] == [
        (27007155, 'nine_and_on_pichy_sqli 防护NINE_AND_ON相关漏洞'),
        (27007156, 'rule_name_two 第二个新增规则'),
    ]
    assert result['updated'] == []
    assert result['deleted'] == []


def test_parse_rules_waf_updated():
    """Extract 二、修改规则 block."""
    desc = (
        "一、新增规则\n"
        "27007155 new_rule 新规则\n"
        "二、修改规则\n"
        "27007001 updated_rule 更新规则\n"
        "三、删除规则\n"
    )
    result = parse_rules_waf(desc)
    assert result['updated'] == [(27007001, 'updated_rule 更新规则')]


def test_parse_rules_waf_deleted():
    """Extract 三、删除规则 block (WAF-specific, doesn't exist in IPS)."""
    desc = (
        "三、删除规则\n"
        "27006001 deleted_rule_one 第一个删除\n"
        "27006002 deleted_rule_two 第二个删除\n"
    )
    result = parse_rules_waf(desc)
    assert result['deleted'] == [
        (27006001, 'deleted_rule_one 第一个删除'),
        (27006002, 'deleted_rule_two 第二个删除'),
    ]


def test_parse_rules_waf_separator_stripped():
    """Trailing '------' separator line is stripped from name."""
    desc = (
        "一、新增规则\n"
        "27007155 rule_with_separator 规则名\n"
        "--------------------------------\n"
    )
    result = parse_rules_waf(desc)
    # Name should not end with dashes
    rid, name = result['added'][0]
    assert rid == 27007155
    assert not name.endswith('-')
    assert '规则名' in name


def test_parse_rules_waf_wu_marker():
    """Block body = '无' / empty / < 10 chars → no rules."""
    desc = (
        "一、新增规则\n"
        "无\n"
        "二、修改规则\n"
        "27007001 only_updated_rule 已更新\n"
    )
    result = parse_rules_waf(desc)
    assert result['added'] == []
    assert result['updated'] == [(27007001, 'only_updated_rule 已更新')]


def test_parse_rules_waf_empty_description():
    """Empty / None description returns empty three-block dict."""
    assert parse_rules_waf('') == {'added': [], 'updated': [], 'deleted': []}
    assert parse_rules_waf(None) == {'added': [], 'updated': [], 'deleted': []}


# ============================================================================
# diff_rules — net change between two versions
# ============================================================================

def test_diff_rules_basic_new_only():
    """TO has rules not in FROM → pure_new contains them."""
    from_parsed = {'added': [], 'updated': []}
    to_parsed = {'added': [(100, 'rule A')], 'updated': []}
    result = diff_rules('1.0', '2.0', from_parsed, to_parsed)
    assert result['from_version'] == '1.0'
    assert result['to_version'] == '2.0'
    assert result['pure_new'] == [{'id': 100, 'name': 'rule A', 'first_added_in': '2.0'}]
    assert result['pure_updated'] == []
    assert result['pure_deleted'] == []
    assert result['stats']['pure_new_count'] == 1


def test_diff_rules_with_intermediate():
    """Intermediate version rules distribute to pure_new / pure_updated correctly."""
    from_parsed = {'added': [(50, 'old')], 'updated': []}
    inter = ('1.5', {'added': [(60, 'mid')], 'updated': [(50, 'old updated')]})
    to_parsed = {'added': [(70, 'new')], 'updated': []}
    result = diff_rules('1.0', '2.0', from_parsed, to_parsed, [inter])
    # rule 60 (added in 1.5) → pure_new, first_added_in = '1.5'
    # rule 70 (added in 2.0) → pure_new, first_added_in = '2.0'
    pure_new_ids = {e['id']: e['first_added_in'] for e in result['pure_new']}
    assert pure_new_ids == {60: '1.5', 70: '2.0'}
    # rule 50 was in FROM (added there) — already shipped — NOT in pure_updated
    assert 50 not in {e['id'] for e in result['pure_updated']}


def test_diff_rules_pure_deleted():
    """FROM rule not present in any intermediate/TO → pure_deleted."""
    from_parsed = {'added': [(100, 'gone')], 'updated': []}
    to_parsed = {'added': [(200, 'new')], 'updated': []}
    result = diff_rules('1.0', '2.0', from_parsed, to_parsed)
    assert result['pure_deleted'] == [{'id': 100, 'name': 'gone'}]


def test_diff_rules_updated_collected_per_version():
    """A rule updated in multiple intermediate versions records all versions."""
    from_parsed = {'added': [], 'updated': []}
    inter1 = ('1.5', {'added': [(80, 'r')], 'updated': []})
    inter2 = ('1.6', {'added': [], 'updated': [(80, 'r v2')]})
    to_parsed = {'added': [], 'updated': [(80, 'r v3')]}
    result = diff_rules('1.0', '2.0', from_parsed, to_parsed, [inter1, inter2])
    updated_80 = [e for e in result['pure_updated'] if e['id'] == 80]
    assert len(updated_80) == 1
    assert sorted(updated_80[0]['updated_in_versions']) == ['1.6', '2.0']


def test_diff_rules_empty_inputs():
    """Both empty → all counts 0, all lists empty."""
    empty = {'added': [], 'updated': []}
    result = diff_rules('1.0', '2.0', empty, empty)
    assert result['stats']['pure_new_count'] == 0
    assert result['pure_updated'] == []
    assert result['pure_deleted'] == []


def test_diff_rules_stats_counts():
    """Stats reflect parsed counts, not pure counts."""
    from_parsed = {'added': [(1, 'a'), (2, 'b')], 'updated': [(3, 'c')]}
    to_parsed = {'added': [(10, 'x')], 'updated': [(11, 'y'), (12, 'z')]}
    result = diff_rules('1.0', '2.0', from_parsed, to_parsed)
    assert result['stats']['from_added_count'] == 2
    assert result['stats']['from_updated_count'] == 1
    assert result['stats']['to_added_count'] == 1
    assert result['stats']['to_updated_count'] == 2


# ============================================================================
# compare_versions — version string ordering
# ============================================================================

def test_compare_versions_less():
    assert compare_versions('2.0.0.45102', '2.0.0.45209') == -1


def test_compare_versions_greater():
    assert compare_versions('2.0.0.45209', '2.0.0.45102') == 1


def test_compare_versions_equal():
    assert compare_versions('1.2.3', '1.2.3') == 0


def test_compare_versions_different_lengths():
    """Shorter version padded with zeros — '1.2' < '1.2.1'."""
    assert compare_versions('1.2', '1.2.1') == -1
    assert compare_versions('1.2.1', '1.2') == 1


def test_compare_versions_empty_strings():
    """Empty strings compare equal; ordering vs non-empty is an implementation
    detail (currently pads empty to zero-segments, so '1.0' < '' by Python
    list comparison). Pin to documented behavior."""
    assert compare_versions('', '') == 0


# ============================================================================
# diff_to_csv_rows — CSV flattening
# ============================================================================

def test_diff_to_csv_rows_new():
    diff = {'pure_new': [{'id': 1, 'name': 'new_rule', 'first_added_in': '2.0'}], 'pure_updated': [], 'pure_deleted': []}
    rows = diff_to_csv_rows(diff)
    assert len(rows) == 1
    assert rows[0]['类型'] == '新增'
    assert rows[0]['规则号'] == 1
    assert rows[0]['规则名称'] == 'new_rule'
    assert rows[0]['首次新增于'] == '2.0'


def test_diff_to_csv_rows_updated_expands_per_version():
    """Updated rule with N versions → N rows."""
    diff = {
        'pure_new': [],
        'pure_updated': [{'id': 5, 'name': 'rule X', 'updated_in_versions': ['1.5', '2.0']}],
        'pure_deleted': [],
    }
    rows = diff_to_csv_rows(diff)
    assert len(rows) == 2
    versions = sorted(r['更新于'] for r in rows)
    assert versions == ['1.5', '2.0']
    for r in rows:
        assert r['类型'] == '更新'
        assert r['规则号'] == 5


def test_diff_to_csv_rows_deleted():
    diff = {'pure_new': [], 'pure_updated': [], 'pure_deleted': [{'id': 99, 'name': 'gone'}]}
    rows = diff_to_csv_rows(diff)
    assert len(rows) == 1
    assert rows[0]['类型'] == '删除'
    assert rows[0]['规则号'] == 99


def test_diff_to_csv_rows_empty():
    """Empty diff → empty rows."""
    assert diff_to_csv_rows({'pure_new': [], 'pure_updated': [], 'pure_deleted': []}) == []


# ============================================================================
# _parse_kv_row — internal kv-row parser
# ============================================================================

def test_parse_kv_row_file_name():
    text = "名称：eoi.unify.allrulepatch.ips.2.0.0.45209.rule"
    result = _parse_kv_row(text)
    assert result.get('file_name') == 'eoi.unify.allrulepatch.ips.2.0.0.45209.rule'


def test_parse_kv_row_md5_normalized_lowercase():
    """MD5 is normalized to lowercase."""
    text = "MD5：A1B2C3D4E5F60708090A0B0C0D0E0F00"
    result = _parse_kv_row(text)
    assert result.get('md5_hash') == 'a1b2c3d4e5f60708090a0b0c0d0e0f00'


def test_parse_kv_row_package_version():
    text = "版本：2.0.0.45209"
    result = _parse_kv_row(text)
    assert result.get('package_version') == '2.0.0.45209'


def test_parse_kv_row_no_match():
    """Non-kv text returns empty dict (not error)."""
    assert _parse_kv_row('just some random text') == {}


# ============================================================================
# _slice_block — internal block slicer
# ============================================================================

def test_slice_block_basic():
    text = "START hello world END"
    assert _slice_block(text, 'START', ['END']) == ' hello world '


def test_slice_block_missing_start():
    """Missing start marker → empty string (not None)."""
    assert _slice_block('nothing here', 'START', ['END']) == ''


def test_slice_block_ends_at_first_marker():
    """Ends at the first end marker encountered."""
    text = "BEGIN first end_here SECOND later END"
    result = _slice_block(text, 'BEGIN', ['end_here', 'END'])
    # Should stop at 'end_here', not 'END'
    assert 'end_here' not in result
    assert 'first' in result


def test_slice_block_empty_end_markers():
    """No end markers → slice to end of text."""
    text = "START all the way to end"
    result = _slice_block(text, 'START', [])
    assert 'all the way' in result


# ============================================================================
# parse_rule_packages_from_html — multi-table HTML parser
# ============================================================================

SAMPLE_HTML_SINGLE_PKG = """
<html><body>
<table>
  <tr><td>名称：eoi.unify.allrulepatch.ips.2.0.0.45209.rule</td></tr>
  <tr><td>版本：2.0.0.45209</td></tr>
  <tr><td>MD5：a1b2c3d4e5f60708090a0b0c0d0e0f00</td></tr>
  <tr><td>大小：95.43M</td></tr>
  <tr><td>发布时间：2024-01-15 10:30:00</td></tr>
  <tr><td>描述：
    新增规则：
    1. 攻击[42792]:SpectralViper后门 C2通信
    This upgrade contains new rules for IPS.
  </td></tr>
  <tr><td><a href="/update/downloads/id/190001">下载</a></td></tr>
</table>
</body></html>
"""


def test_parse_html_single_package():
    """Single table → one package, all fields populated."""
    pkgs = parse_rule_packages_from_html(SAMPLE_HTML_SINGLE_PKG, 'http://x/list')
    assert len(pkgs) == 1
    p = pkgs[0]
    assert p['package_version'] == '2.0.0.45209'
    assert p['file_name'] == 'eoi.unify.allrulepatch.ips.2.0.0.45209.rule'
    assert p['md5_hash'] == 'a1b2c3d4e5f60708090a0b0c0d0e0f00'
    assert p['file_size_raw'] == '95.43M'
    assert p['download_url'] == '/update/downloads/id/190001'
    assert p['page_url'] == 'http://x/list'
    assert p['table_index'] == 0
    # description_zh ends BEFORE the English 'This upgrade' marker
    assert 'This upgrade' not in p['description_zh']
    assert '新增规则' in p['description_zh']


def test_parse_html_multiple_tables():
    """Multiple <table> blocks → multiple packages (the bug fix for vendor history pages)."""
    html = SAMPLE_HTML_SINGLE_PKG.replace('2.0.0.45209', '2.0.0.45209').replace(
        'a1b2c3d4e5f60708090a0b0c0d0e0f00', 'aaaa1111aaaa1111aaaa1111aaaa1111'
    ).replace(
        '95.43M', '95.43M'
    )
    html += """
<table>
  <tr><td>名称：eoi.unify.allrulepatch.ips.2.0.0.45102.rule</td></tr>
  <tr><td>版本：2.0.0.45102</td></tr>
  <tr><td>MD5：bbbb2222bbbb2222bbbb2222bbbb2222</td></tr>
  <tr><td>大小：92.10M</td></tr>
  <tr><td>发布时间：2024-01-10 09:00:00</td></tr>
  <tr><td>描述：无</td></tr>
  <tr><td><a href="/update/downloads/id/190000">下载</a></td></tr>
</table>
"""
    pkgs = parse_rule_packages_from_html(html, 'http://x/list')
    assert len(pkgs) == 2
    versions = sorted(p['package_version'] for p in pkgs)
    assert versions == ['2.0.0.45102', '2.0.0.45209']


def test_parse_html_empty_html():
    """Empty / None HTML → empty list."""
    assert parse_rule_packages_from_html('', 'http://x') == []
    assert parse_rule_packages_from_html(None, 'http://x') == []


def test_parse_html_no_tables():
    """HTML without <table> → empty list (not crash)."""
    assert parse_rule_packages_from_html('<html><body>hello</body></html>', 'http://x') == []


def test_parse_html_dedup_by_file_name_md5():
    """Vendor pages sometimes repeat rows — dedup by (file_name, md5)."""
    # Build two identical tables
    html = SAMPLE_HTML_SINGLE_PKG + SAMPLE_HTML_SINGLE_PKG
    pkgs = parse_rule_packages_from_html(html, 'http://x/list')
    assert len(pkgs) == 1, 'duplicate table rows should be deduplicated'


def test_parse_html_table_without_required_fields_skipped():
    """Table missing file_name OR md5 → package not finalized (skipped)."""
    html = """
<html><body>
<table>
  <tr><td>描述：无</td></tr>
</table>
</body></html>
"""
    pkgs = parse_rule_packages_from_html(html, 'http://x')
    assert pkgs == []


def test_parse_html_description_zh_excludes_english():
    """description_zh stops at 'This upgrade' / 'new rules:' markers."""
    pkgs = parse_rule_packages_from_html(SAMPLE_HTML_SINGLE_PKG, 'http://x')
    p = pkgs[0]
    assert 'This upgrade' in p['description_raw']
    assert 'This upgrade' not in p['description_zh']