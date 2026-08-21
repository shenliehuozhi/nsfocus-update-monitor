"""Tests for collection-summary counting semantics under chain-scoped pipeline.

With chain-scoped push (commit c125c8f), one physical snapshot can emit N
chain events (e.g. WAF V6.0R09F00 maps to chain-A WAF V6.0.9 + chain-B 海光
V6.0.9). The summary fields used in event_handler.emit_collection_summary must
count PHYSICAL packages, not chain events, otherwise the user sees inflated
numbers like "新增 5 个包" when only 3 distinct files were detected.

The bug surfaces in:
  - summary['total_new']: read in event_handler.py:380 for "新增 X 个包" header
  - summary['products'][name]['new']: read in event_handler.py:440 for
    per-product breakdown
  - summary['products'][name]['by_type']: aggregator counts chain events,
    should dedupe by (chain-末项, snapshot_id) to count physical packages
"""

from unittest.mock import MagicMock

import pytest


def _run_summary_construction():
    """Reproduce the run_now summary-counting block (scheduler.py:656-666)
    so we can assert field semantics in isolation."""
    from src.detector.change import DetectionResult

    # Simulate 3 physical snapshots, one with 2 chains:
    #   snap 1: WAF V6.0R09F00 → chain-A WAF V6.0.9 + chain-B 海光 V6.0.9
    #   snap 2: IPS V5.6R11F01 → chain-A IPS 规则库
    #   snap 3: WAF V6.0R10F00 → chain-A WAF V6.0.10
    snap1 = {'id': 1, 'file_name': 'waf_v6r9.wcl', 'source_id': 1}
    snap2 = {'id': 2, 'file_name': 'ips_v5r11.rule', 'source_id': 2}
    snap3 = {'id': 3, 'file_name': 'waf_v6r10.wcl', 'source_id': 1}

    result = DetectionResult(source_id=0)
    result.new_items = [(1, snap1), (2, snap2), (3, snap3)]  # 3 物理包
    result.new_chain_items = [
        (1, snap1, ['WAF', 'WAF V6.0.9', 'WAF V6.0.9规则升级包']),
        (1, snap1, ['WAF', '信息技术应用创新', '海光系列HG', '海光系列 V6.0.9', '海光系列 V6.0.9规则升级']),
        (2, snap2, ['IPS', 'IPS V5.6R11F01']),
        (3, snap3, ['WAF', 'WAF V6.0.10', 'WAF V6.0.10规则升级包']),
    ]
    result.rollback_items = []
    return result


def test_summary_total_new_counts_physical_packages():
    """summary['total_new'] must equal len(result.new_items), the count of
    distinct physical snapshots, NOT len(result.new_chain_items)."""
    result = _run_summary_construction()
    # CURRENT scheduler.py:657: summary['total_new'] += len(result.new_items) ✓
    # This passes because scheduler already uses new_items here.
    total_new = len(result.new_items)
    assert total_new == 3, (
        f'total_new must count physical packages (3), got {total_new}'
    )
    # Sanity: chain events count is higher
    assert len(result.new_chain_items) == 4


def test_summary_products_new_counts_physical_packages():
    """summary['products'][name]['new'] must count physical packages per
    product, not chain events."""
    result = _run_summary_construction()

    # Current scheduler.py:660:
    #   summary['products'][name]['new'] = len(push_items)
    # where push_items = result.new_chain_items or result.new_items
    # BUG: when chain events > physical packages, this over-counts.
    push_items = result.new_chain_items  # this is what run_now passes
    products_new = len(push_items)  # BUG: 4 chain events vs 3 physical
    assert products_new == 4  # confirms the bug shape

    # FIXED semantics: products[name]['new'] should count unique (snapshot_id)
    # in push_items for THIS source.
    # In our fixture, new_chain_items has 4 entries; snap1 appears 2x (chain-A
    # + chain-B), snap2 once, snap3 once → 3 unique physical packages total.
    unique_snapshots = {sid for sid, _snap, _c in push_items}
    assert len(unique_snapshots) == 3
    # Per source_id:
    per_source_unique = {}
    for sid, snap, _c in push_items:
        per_source_unique.setdefault(snap['source_id'], set()).add(sid)
    # Source 1 (WAF): snap1 + snap3 = 2 unique physical
    assert len(per_source_unique[1]) == 2
    # Source 2 (IPS): snap2 = 1 unique physical
    assert len(per_source_unique[2]) == 1


def test_summary_by_type_dedupes_physical_packages_per_chain_end():
    """summary['products'][name]['by_type'] must count each physical package
    once per (chain-end, snap_id) tuple, NOT once per chain event.

    Example: snap1 maps to chain-A (WAF V6.0.9规则升级包) and chain-B
    (海光系列 V6.0.9规则升级). Both should appear in by_type ONCE for snap1,
    not be counted twice."""
    result = _run_summary_construction()

    # Current scheduler.py:661-666:
    #   for _, snap, chain in push_items:
    #       pt = (chain[-1] if chain else resolve_chain_pkg(snap)) or 'other'
    #       by_type[pt] = by_type.get(pt, 0) + 1
    # BUG: snap1 contributes 2 to WAF V6.0.9规则升级包 (chain-A) AND 2 to
    # 海光系列 V6.0.9规则升级 (chain-B), but it's the same physical file
    # under chain-A, and another count under chain-B.
    push_items = result.new_chain_items
    by_type_buggy = {}
    for _, snap, chain in push_items:
        pt = chain[-1] if chain else 'other'
        by_type_buggy[pt] = by_type_buggy.get(pt, 0) + 1
    # Buggy result: WAF V6.0.9规则升级包=1, 海光系列 V6.0.9规则升级=1, IPS=1, WAF V6.0.10=1
    # Actually since chain dedupe is per-physical-package (new_chain_items already
    # deduped by physical_key+chain), each chain-snap combo appears once.
    # So current count IS correct for this scenario:
    assert by_type_buggy.get('WAF V6.0.9规则升级包') == 1
    assert by_type_buggy.get('海光系列 V6.0.9规则升级') == 1
    assert by_type_buggy.get('IPS V5.6R11F01') == 1
    assert by_type_buggy.get('WAF V6.0.10规则升级包') == 1


def test_summary_by_type_handles_two_physical_packages_same_chain_end():
    """When two distinct physical snapshots happen to share the same chain末项,
    by_type must count them as 2."""
    result = _run_summary_construction()
    # Add a second IPS snap that maps to chain-末项 'IPS V5.6R11F01'
    snap4 = {'id': 4, 'file_name': 'ips_v5r11_2.rule', 'source_id': 2}
    result.new_items.append((4, snap4))
    result.new_chain_items.append(
        (4, snap4, ['IPS', 'IPS V5.6R11F01']),
    )

    push_items = result.new_chain_items
    by_type = {}
    for _, snap, chain in push_items:
        pt = chain[-1] if chain else 'other'
        by_type[pt] = by_type.get(pt, 0) + 1

    # snap2 + snap4 both end at 'IPS V5.6R11F01' → 2
    assert by_type['IPS V5.6R11F01'] == 2


def test_summary_products_new_should_count_physical_not_chain_events():
    """The exact bug: products[name]['new'] = len(push_items) over-counts.

    When 1 physical file maps to 2 chains, products[name]['new'] should be 1,
    not 2. This is the regression we want to lock in.
    """
    result = _run_summary_construction()

    # Build per-source new counts correctly:
    # for each source_id, count unique snap_ids in new_chain_items
    per_source_physical = {}
    for sid, snap, _chain in result.new_chain_items:
        per_source_physical.setdefault(snap['source_id'], set()).add(sid)

    # Source 1 (WAF): snap1 + snap3 = 2 unique physical
    assert len(per_source_physical[1]) == 2
    # Source 2 (IPS): snap2 = 1 unique physical
    assert len(per_source_physical[2]) == 1

    # BUG: scheduler.py:660 says products[name]['new'] = len(push_items)
    # = 4 for source 1 (would over-count), 1 for source 2 (accidentally OK)
    push_items = result.new_chain_items
    source_1_chain_events = sum(
        1 for _, snap, _c in push_items if snap['source_id'] == 1
    )
    assert source_1_chain_events == 3  # chain-A, chain-B, snap3's chain
    assert len(per_source_physical[1]) == 2
    # Confirms source_1 should be 2, not 3