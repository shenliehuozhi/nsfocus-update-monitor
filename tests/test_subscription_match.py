"""Tests for get_new_for_subscription (subscription rule matching).

These tests target the historical bug surface:
  - L253-254 silent return [] when chains missing (legacy rules broken)
  - valid_until expiry (UTC vs local)
  - chain leaf/subtree dispatch
  - urgency / keywords filters
  - path_id-based chain lookup precedence
"""
from unittest.mock import patch
from src.detector.change import get_new_for_subscription


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