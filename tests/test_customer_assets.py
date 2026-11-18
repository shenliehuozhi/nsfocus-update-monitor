"""客户资产台账（owned_products）规整测试。"""
from src.models.customer import normalize_assets


# ============================================================================
# normalize_assets — 兼容旧字符串 / 新 dict
# ============================================================================

def test_normalize_legacy_product_strings():
    assert normalize_assets('["WEB应用防护系统(WAF)", "IPS"]') == [
        {'product': 'WEB应用防护系统(WAF)', 'version': ''},
        {'product': 'IPS', 'version': ''},
    ]


def test_normalize_legacy_product_version_string():
    assert normalize_assets(['WAF:V6.0.9']) == [
        {'product': 'WAF', 'version': 'V6.0.9'},
    ]


def test_normalize_new_dict_entries():
    raw = [{'product': 'WAF', 'version': 'V6.0.9'}, {'product': 'IPS'}]
    assert normalize_assets(raw) == [
        {'product': 'WAF', 'version': 'V6.0.9'},
        {'product': 'IPS', 'version': ''},
    ]


def test_normalize_drops_blank_version_when_product_has_versions():
    raw = [{'product': 'WAF', 'version': ''}, {'product': 'WAF', 'version': 'V6.0.9'}]
    assert normalize_assets(raw) == [{'product': 'WAF', 'version': 'V6.0.9'}]


def test_normalize_keeps_multiple_versions_of_same_product():
    raw = [
        {'product': 'WAF', 'version': 'V6.0.9'},
        {'product': 'WAF', 'version': 'V6.0.8'},
        {'product': 'WAF', 'version': 'V6.0.9'},
    ]
    assert normalize_assets(raw) == [
        {'product': 'WAF', 'version': 'V6.0.9'},
        {'product': 'WAF', 'version': 'V6.0.8'},
    ]
    # 旧字符串格式同样允许同产品多版本
    assert normalize_assets(['WAF:V6.0.9', 'WAF:V6.0.8']) == [
        {'product': 'WAF', 'version': 'V6.0.9'},
        {'product': 'WAF', 'version': 'V6.0.8'},
    ]


def test_normalize_skips_empty_and_bad_input():
    assert normalize_assets(None) == []
    assert normalize_assets('') == []
    assert normalize_assets([{'product': '  '}, '', None]) == []
    assert normalize_assets({'product': 'WAF'}) == []
