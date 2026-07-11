"""Tests for get_new_for_subscription (subscription rule matching).

These tests target the historical bug surface:
  - L253-254 silent return [] when chains missing (legacy rules broken)
  - valid_until expiry (UTC vs local)
  - chain leaf/subtree dispatch
  - urgency / keywords filters
  - path_id-based chain lookup precedence
"""
from unittest.mock import patch
from src.detector.change import (
    get_new_for_subscription,
    _chain_matches,
    is_quiet_time,
    is_window_time,
    compute_next_window_push_time,
    compute_push_time,
)


def test_empty_conditions_matches_all(base_snap):
    """Empty conditions = match all (NOT return []).

    This is the "subscribe to all products" semantic — guard against
    accidentally tightening this in a refactor.
    """
    rule = {'name': 'all', 'filter_conditions': {}}
    items = [base_snap()]
    assert len(get_new_for_subscription(rule, items)) == 1


def test_legacy_products_field_silent_break(base_snap):
    """Historical bug: legacy structure (products/versions/package_types)
    with no `chains` key silently returns [] at L253-254.

    This test DOCUMENTS the current behavior — if you fix the bug, this
    test will fail and you should update it to reflect the new semantic.
    """
    rule = {
        'name': 'legacy',
        'filter_conditions': {'products': ['IPS'], 'versions': ['V6']},
    }
    items = [base_snap()]
    assert get_new_for_subscription(rule, items) == []


def test_urgency_filter_excludes_non_match(stub_chain_lookup, base_snap):
    rule = {
        'name': 'r',
        'filter_conditions': {
            'chains': [{'chain': ['X'], 'match': 'leaf'}],
            'urgency': ['critical'],
        },
    }
    snap = base_snap(urgency='low')
    with patch('src.core.scheduler._get_chain',
               stub_chain_lookup({(1, 'abc123'): ['X']})):
        assert get_new_for_subscription(rule, [snap]) == []


def test_keywords_case_insensitive(stub_chain_lookup, base_snap):
    rule = {
        'name': 'r',
        'filter_conditions': {
            'chains': [{'chain': ['X'], 'match': 'leaf'}],
            'keywords': ['CVE'],
        },
    }
    snap = base_snap(description_raw='fix cve-2026-001')
    with patch('src.core.scheduler._get_chain',
               stub_chain_lookup({(1, 'abc123'): ['X']})):
        result = get_new_for_subscription(rule, [snap])
    assert len(result) == 1


def test_valid_until_expired_returns_empty(base_snap):
    """Expired rule matches nothing — even with empty conditions.

    NOTE: ISO timestamp must be timezone-aware; naive timestamps get parsed
    by datetime.fromisoformat without tzinfo, which makes the comparison
    with datetime.now(timezone.utc) raise TypeError, swallowed by the
    function's except (L203), and the rule silently matches all. This is
    a latent bug — to be fixed in a follow-up. The test uses a UTC-aware
    timestamp to exercise the happy-path expiry check.
    """
    rule = {
        'name': 'expired',
        'filter_conditions': {},
        'valid_until': '2020-01-01T00:00:00+00:00',
    }
    assert get_new_for_subscription(rule, [base_snap()]) == []


def test_valid_until_naive_iso_string_expires(base_snap):
    """Regression: naive ISO timestamps must be treated as UTC.

    Before the fix, naive string '2020-01-01T00:00:00' raised TypeError
    on comparison with tz-aware datetime.now(timezone.utc), the except
    swallowed it, and the rule silently matched everything forever.
    """
    rule = {
        'name': 'naive-expired',
        'filter_conditions': {},
        'valid_until': '2020-01-01T00:00:00',  # naive (no tz suffix)
    }
    assert get_new_for_subscription(rule, [base_snap()]) == []


def test_valid_until_naive_future_matches_all(base_snap):
    """Naive future timestamp must still allow matches."""
    rule = {
        'name': 'naive-future',
        'filter_conditions': {},
        'valid_until': '2099-01-01T00:00:00',  # naive (no tz suffix)
    }
    assert len(get_new_for_subscription(rule, [base_snap()])) == 1


def test_valid_until_future_matches_all(base_snap):
    rule = {
        'name': 'r',
        'filter_conditions': {},
        'valid_until': '2099-01-01T00:00:00',
    }
    assert len(get_new_for_subscription(rule, [base_snap()])) == 1


def test_path_id_used_for_chain_lookup(stub_chain_lookup, base_snap):
    """path_id must take precedence over source_url fallback.

    Guards against regressions where same URL hosts multiple chains.
    """
    rule = {
        'name': 'r',
        'filter_conditions': {
            'chains': [{'chain': ['期望 chain'], 'match': 'leaf'}],
        },
    }
    snap = base_snap(source_url='/same/url', path_id='pid1')
    mapping = {(1, 'pid1'): ['期望 chain']}
    with patch('src.core.scheduler._get_chain', stub_chain_lookup(mapping)):
        result = get_new_for_subscription(rule, [snap])
    assert len(result) == 1

# ============================================================================
# Extended coverage — urgency positive, keywords negative + edge cases,
# valid_until invalid format, _chain_matches unit, time/window helpers
# ============================================================================

def test_urgency_filter_includes_match(stub_chain_lookup, base_snap):
    """urgency list contains snap's urgency → included."""
    rule = {
        'name': 'r',
        'filter_conditions': {
            'chains': [{'chain': ['X'], 'match': 'leaf'}],
            'urgency': ['critical', 'high'],
        },
    }
    snap = base_snap(urgency='high')
    with patch('src.core.scheduler._get_chain',
               stub_chain_lookup({(1, 'abc123'): ['X']})):
        result = get_new_for_subscription(rule, [snap])
    assert len(result) == 1


def test_urgency_empty_list_no_filter(stub_chain_lookup, base_snap):
    """Empty urgency list means NO filter — not 'match nothing'."""
    rule = {
        'name': 'r',
        'filter_conditions': {
            'chains': [{'chain': ['X'], 'match': 'leaf'}],
            'urgency': [],
        },
    }
    snap = base_snap(urgency='low')
    with patch('src.core.scheduler._get_chain',
               stub_chain_lookup({(1, 'abc123'): ['X']})):
        result = get_new_for_subscription(rule, [snap])
    assert len(result) == 1


def test_keywords_excludes_non_match(stub_chain_lookup, base_snap):
    """keywords list contains no match in description → excluded."""
    rule = {
        'name': 'r',
        'filter_conditions': {
            'chains': [{'chain': ['X'], 'match': 'leaf'}],
            'keywords': ['SQL注入', 'CVE-2026'],
        },
    }
    snap = base_snap(description_raw='这是一段普通更新,没有特别关键词')
    with patch('src.core.scheduler._get_chain',
               stub_chain_lookup({(1, 'abc123'): ['X']})):
        result = get_new_for_subscription(rule, [snap])
    assert result == []


def test_keywords_empty_list_no_filter(stub_chain_lookup, base_snap):
    """Empty keywords list = no keyword filter (matches all)."""
    rule = {
        'name': 'r',
        'filter_conditions': {
            'chains': [{'chain': ['X'], 'match': 'leaf'}],
            'keywords': [],
        },
    }
    snap = base_snap(description_raw='随便什么描述都行')
    with patch('src.core.scheduler._get_chain',
               stub_chain_lookup({(1, 'abc123'): ['X']})):
        result = get_new_for_subscription(rule, [snap])
    assert len(result) == 1


def test_keywords_chinese_substring(stub_chain_lookup, base_snap):
    """Chinese keywords use substring (lower-case) match."""
    rule = {
        'name': 'r',
        'filter_conditions': {
            'chains': [{'chain': ['X'], 'match': 'leaf'}],
            'keywords': ['SQL注入'],
        },
    }
    snap = base_snap(description_raw='该版本修复了多个SQL注入相关漏洞')
    with patch('src.core.scheduler._get_chain',
               stub_chain_lookup({(1, 'abc123'): ['X']})):
        result = get_new_for_subscription(rule, [snap])
    assert len(result) == 1


def test_valid_until_invalid_format_treated_as_no_expiry(base_snap):
    """Garbage valid_until string → silently ignored (rule matches normally)."""
    rule = {
        'name': 'garbage',
        'filter_conditions': {},
        'valid_until': 'not-a-date-at-all',
    }
    # Garbage string raises ValueError → except swallows → no expiry check
    assert len(get_new_for_subscription(rule, [base_snap()])) == 1


def test_valid_until_with_space_separator(base_snap):
    """valid_until with space separator (not T) still parses.

    Frontend may store '2025-12-31 23:59:59' (space, not T). The parser
    replaces ' ' with 'T' before fromisoformat().
    """
    rule = {
        'name': 'space-sep',
        'filter_conditions': {},
        'valid_until': '2020-01-01 00:00:00',  # past, space-separated
    }
    assert get_new_for_subscription(rule, [base_snap()]) == []


def test_valid_until_with_tz_offset_not_utc(base_snap):
    """Non-UTC tz offset is honored."""
    rule = {
        'name': 'tz-offset',
        'filter_conditions': {},
        'valid_until': '2099-01-01T00:00:00+08:00',  # future in CST
    }
    assert len(get_new_for_subscription(rule, [base_snap()])) == 1


# ============================================================================
# _chain_matches — direct unit tests (no scheduler mocking needed)
# ============================================================================

def test_chain_matches_empty_snap_returns_false():
    """Empty snap_chain → False (cannot match anything)."""
    assert _chain_matches([], [{'chain': ['X'], 'match': 'leaf'}]) is False


def test_chain_matches_empty_rule_chains_returns_true():
    """Empty rule_chains → True (no chain condition = match all)."""
    assert _chain_matches(['A', 'B'], []) is True


def test_chain_matches_leaf_exact():
    """leaf mode requires exact match."""
    assert _chain_matches(['A', 'B'], [{'chain': ['A', 'B'], 'match': 'leaf'}]) is True
    assert _chain_matches(['A', 'B', 'C'], [{'chain': ['A', 'B'], 'match': 'leaf'}]) is False


def test_chain_matches_subtree_prefix():
    """subtree mode: snap_chain starts with rule_chain."""
    assert _chain_matches(['A', 'B', 'C'], [{'chain': ['A'], 'match': 'subtree'}]) is True
    assert _chain_matches(['A', 'B', 'C'], [{'chain': ['A', 'B'], 'match': 'subtree'}]) is True
    assert _chain_matches(['A', 'B', 'C'], [{'chain': ['A', 'B', 'C', 'D'], 'match': 'subtree'}]) is False


def test_chain_matches_default_mode_is_leaf():
    """Missing 'match' key defaults to leaf mode."""
    # No 'match' key → defaults to 'leaf'
    assert _chain_matches(['X'], [{'chain': ['X']}]) is True
    assert _chain_matches(['X', 'Y'], [{'chain': ['X']}]) is False


def test_chain_matches_multiple_entries_any_match():
    """Multiple chain entries: any one matching is enough."""
    chains = [
        {'chain': ['A'], 'match': 'leaf'},
        {'chain': ['B'], 'match': 'leaf'},
    ]
    assert _chain_matches(['B'], chains) is True
    assert _chain_matches(['A'], chains) is True
    assert _chain_matches(['C'], chains) is False


def test_chain_matches_empty_rule_chain_entry_skipped():
    """A chain entry with empty chain list is skipped (not auto-match)."""
    chains = [{'chain': [], 'match': 'leaf'}, {'chain': ['X'], 'match': 'leaf'}]
    assert _chain_matches(['X'], chains) is True
    # If only the empty entry exists, no match
    assert _chain_matches(['X'], [{'chain': [], 'match': 'leaf'}]) is False


def test_chain_matches_unknown_mode_returns_false():
    """Unknown match mode → False for that entry (falls through to others)."""
    chains = [{'chain': ['X'], 'match': 'unknown'}]
    assert _chain_matches(['X'], chains) is False


# ============================================================================
# is_quiet_time / is_window_time — time-window helpers
# ============================================================================

def test_is_quiet_time_no_config_returns_false():
    """Missing quiet_start/quiet_end → not in quiet time."""
    assert is_quiet_time({}) is False
    assert is_quiet_time({'quiet_start': '22:00'}) is False
    assert is_quiet_time({'quiet_end': '08:00'}) is False


def test_is_quiet_time_within_range():
    """When now is between start and end (same-day), returns True if rule specifies so."""
    # We can't directly control datetime.now() — but we can test the branch logic
    # by examining behavior with extreme values that always evaluate to True or False.
    rule = {'quiet_start': '00:00', 'quiet_end': '23:59'}  # covers all of today
    assert is_quiet_time(rule) is True


def test_is_window_time_no_config_returns_true():
    """No window_config → always OK to send."""
    assert is_window_time({}) is True
    assert is_window_time({'window_config': None}) is True
    assert is_window_time({'window_config': {}}) is True


def test_is_window_time_inside_simple_range():
    """Wide window covering all day → True."""
    rule = {'window_config': {'start': '00:00', 'end': '23:59', 'days': []}}
    assert is_window_time(rule) is True


def test_compute_push_time_zero_delay_returns_nowish():
    """delay_days <= 0 → timestamp ≈ now."""
    from datetime import datetime
    result = compute_push_time(0)
    result_dt = datetime.strptime(result, '%Y-%m-%d %H:%M:%S')
    delta = abs((datetime.now() - result_dt).total_seconds())
    # Within 5 seconds
    assert delta < 5


def test_compute_push_time_positive_delay_returns_future():
    """delay_days > 0 → future timestamp."""
    from datetime import datetime, timedelta
    result = compute_push_time(1)
    result_dt = datetime.strptime(result, '%Y-%m-%d %H:%M:%S')
    # 1 day from now
    expected = datetime.now() + timedelta(days=1)
    delta = abs((expected - result_dt).total_seconds())
    assert delta < 5