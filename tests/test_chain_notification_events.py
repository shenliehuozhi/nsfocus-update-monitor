"""Regression tests for chain-scoped package events."""

from unittest.mock import patch

from src.collectors.base import UnifiedContentItem
from src.detector import change
from src.detector.change import get_new_for_subscription


def _item(*, version="V1", package_type="pkg-a"):
    return UnifiedContentItem(
        source_id=1,
        source_type="nsfocus",
        product_name="WAF",
        version_branch=version,
        package_type=package_type,
        file_name="rule.wcl",
        md5_hash="same-md5",
        source_url="https://update.nsfocus.com/update/rule",
        path_id="url-only-path",
    )


def test_run_detection_emits_one_new_chain_event_per_chain():
    """Two chain items for one physical file produce two chain events."""
    snap = {
        "id": 42,
        "source_id": 1,
        "source_url": "https://update.nsfocus.com/update/rule",
        "path_id": "url-only-path",
        "file_name": "rule.wcl",
        "first_seen_at": "2026-08-21 00:00:00",
        "last_seen_at": "2026-08-21 00:00:00",
    }
    chain_a = ["WAF", "V1", "pkg-a"]
    chain_b = ["WAF", "V1", "pkg-b"]

    with patch.object(change.snap_db, "save_snapshot", return_value=42), \
         patch.object(change.snap_db, "get_snapshot", return_value=snap), \
         patch("src.core.scheduler._get_chain", return_value=[chain_a, chain_b]):
        result = change.run_detection(1, [_item(), _item(package_type="pkg-b")])

    assert len(result.new_items) == 1
    assert result.new_chain_items == [
        (42, snap, chain_a),
        (42, snap, chain_b),
    ]


def test_run_detection_deduplicates_same_physical_file_before_saving(monkeypatch):
    """A shared URL must save/update the physical snapshot only once."""
    saved = []

    def save_snapshot(snap):
        saved.append(snap)
        return 42

    snap = {
        "id": 42,
        "source_id": 1,
        "source_url": "https://update.nsfocus.com/update/rule",
        "path_id": "url-only-path",
        "file_name": "rule.wcl",
        "first_seen_at": "2026-08-21 00:00:00",
        "last_seen_at": "2026-08-21 00:00:00",
    }
    monkeypatch.setattr(change.snap_db, "save_snapshot", save_snapshot)
    monkeypatch.setattr(change.snap_db, "get_snapshot", lambda sid: snap)
    with patch("src.core.scheduler._get_chain", return_value=[["WAF", "V1", "pkg-a"]]):
        result = change.run_detection(1, [_item(), _item(package_type="pkg-b")])

    assert len(saved) == 1
    assert len(result.new_chain_items) == 1


def test_run_detection_keeps_existing_result_fields():
    snap = {
        "id": 42,
        "source_id": 1,
        "source_url": "https://update.nsfocus.com/update/rule",
        "path_id": "url-only-path",
        "file_name": "rule.wcl",
        "first_seen_at": "2026-08-21 00:00:00",
        "last_seen_at": "2026-08-21 00:00:01",
    }
    with patch.object(change.snap_db, "save_snapshot", return_value=42), \
         patch.object(change.snap_db, "get_snapshot", return_value=snap), \
         patch("src.core.scheduler._get_chain", return_value=[["WAF", "V1", "pkg-a"]]):
        result = change.run_detection(1, [_item()])
    assert result.new_items == []
    assert result.unchanged_count == 1
    assert result.new_chain_items == []


def test_subtree_subscription_returns_one_event_per_shared_chain(stub_chain_lookup, base_snap):
    """A root subtree must retain every matching chain event."""
    rule = {
        'name': 'root-subtree',
        'filter_conditions': {
            'chains': [{'chain': ['ROOT'], 'match': 'subtree'}],
        },
    }
    _sid, snap = base_snap()
    chain_a = ['ROOT', 'A']
    chain_b = ['ROOT', 'B']
    with patch('src.core.scheduler._get_chain', stub_chain_lookup({
        (1, 'abc123'): [chain_a, chain_b],
    })):
        result = get_new_for_subscription(rule, [(100, snap)])

    assert result == [(100, snap, chain_a), (100, snap, chain_b)]


def test_leaf_subscription_uses_chain_from_chain_event(base_snap):
    """A 3-tuple event is matched directly, not by the first URL chain."""
    rule = {
        'name': 'leaf-b',
        'filter_conditions': {
            'chains': [{'chain': ['ROOT', 'B'], 'match': 'leaf'}],
        },
    }
    _sid, snap = base_snap()
    chain_a = ['ROOT', 'A']
    chain_b = ['ROOT', 'B']

    result = get_new_for_subscription(
        rule,
        [(100, snap, chain_a), (100, snap, chain_b)],
    )

    assert result == [(100, snap, chain_b)]
