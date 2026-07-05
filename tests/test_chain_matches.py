"""Tests for _chain_matches (pure function, no DB)."""
from src.detector.change import _chain_matches


def test_leaf_exact_match():
    assert _chain_matches(
        ['漏洞库', 'Web 漏洞'],
        [{'chain': ['漏洞库', 'Web 漏洞'], 'match': 'leaf'}]
    ) is True


def test_leaf_partial_does_not_match():
    """leaf requires full chain equality; same prefix alone must not match."""
    assert _chain_matches(
        ['漏洞库', 'Web 漏洞', '2026 年'],
        [{'chain': ['漏洞库', 'Web 漏洞'], 'match': 'leaf'}]
    ) is False


def test_subtree_prefix_match():
    """subtree: snap_chain prefixed by rule_chain → match."""
    assert _chain_matches(
        ['漏洞库', 'Web 漏洞', '2026 年'],
        [{'chain': ['漏洞库', 'Web 漏洞'], 'match': 'subtree'}]
    ) is True


def test_empty_snap_chain_does_not_match():
    assert _chain_matches([], [{'chain': ['X'], 'match': 'leaf'}]) is False


def test_empty_rule_chains_matches_all():
    """Empty rule_chains = match-all (subscription "all products" semantics)."""
    assert _chain_matches(['A', 'B'], []) is True


def test_multiple_entries_any_match_wins():
    """If any chain entry matches, the result is True."""
    rule_chains = [
        {'chain': ['A'], 'match': 'leaf'},
        {'chain': ['B', 'C'], 'match': 'subtree'},
    ]
    assert _chain_matches(['B', 'C', 'D'], rule_chains) is True