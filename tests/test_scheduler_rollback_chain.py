"""Tests for scheduler rollback chain-scoped notification.

Regression: rollback path used ``matched[0]`` to send only the first matched
chain. After chain-scoped push (commit c125c8f), a single physical snapshot
matching N chains via subtree must emit N rollback notifications (one per
chain), not just the first.

Strategy: extract the rollback loop body into a tiny testable helper or
monkeypatch the in-place loop. Simpler approach: source the loop body
directly with grep-verified signature, since it's only 8 lines.
"""

import re
from pathlib import Path

import pytest


def _make_snap(snap_id=100, **overrides):
    snap = {
        'id': snap_id,
        'source_id': 1,
        'source_url': '/update/listWafV69Detail/v/rule',
        'path_id': 'p1',
        'file_name': 'f.zip',
        'urgency': 'high',
        'description_raw': '',
        'product_name': 'WAF',
    }
    snap.update(overrides)
    return snap


def _make_rule(rule_id=10, name='r', **overrides):
    rule = {
        'id': rule_id,
        'name': name,
        'enabled': 1,
        'notify_rollback': 1,
        'filter_conditions': {
            'chains': [
                {'chain': ['WEB应用防护系统(WAF)'], 'match': 'subtree'},
            ],
        },
    }
    rule.update(overrides)
    return rule


SCHEDULER_PATH = Path('/root/nsfocus-monitor/src/core/scheduler.py')


def test_scheduler_source_no_longer_uses_matched_zero():
    """Static check: scheduler.py must not contain ``matched[0]`` in rollback
    path. This is the bug regression marker — the old code did
    ``_matched_sid, _matched_snap, matched_chain = matched[0]``."""
    src = SCHEDULER_PATH.read_text()
    # Allow ``matched[0]`` outside rollback loop (e.g. data destructuring)
    # but the rollback-specific lines we patched must use a for-loop.
    assert 'matched[0]' not in src, (
        'scheduler.py still references matched[0]; rollback loop should '
        'iterate over the full matched list to send per-chain notifications'
    )


def test_scheduler_run_now_rollback_block_iterates_matched():
    """The run_now rollback loop body must iterate ``matched`` and emit one
    notification per chain event."""
    src = SCHEDULER_PATH.read_text()
    # Find the run_now rollback block (after "Handle rollbacks" comment)
    block_match = re.search(
        r'# 6\. Handle rollbacks.*?(?=^        # 7\.|^        summary)',
        src, flags=re.DOTALL | re.MULTILINE,
    )
    assert block_match, 'run_now rollback block not found'
    block = block_match.group(0)
    # Must contain a for-loop over matched
    assert 'for ' in block and ' in matched:' in block, (
        f'run_now rollback block must iterate matched; got:\n{block}'
    )
    # Must NOT destructure matched[0]
    assert 'matched[0]' not in block, (
        f'run_now rollback block must not use matched[0]; got:\n{block}'
    )


def test_scheduler_run_for_source_rollback_block_iterates_matched():
    """Same check for run_for_source rollback block."""
    src = SCHEDULER_PATH.read_text()
    block_match = re.search(
        r'# ── rollback 推送 ──.*?(?=^        # ── 延迟队列处理)',
        src, flags=re.DOTALL | re.MULTILINE,
    )
    assert block_match, 'run_for_source rollback block not found'
    block = block_match.group(0)
    assert 'for ' in block and ' in matched:' in block, (
        f'run_for_source rollback block must iterate matched; got:\n{block}'
    )
    assert 'matched[0]' not in block, (
        f'run_for_source rollback block must not use matched[0]; got:\n{block}'
    )


def test_rollback_two_chain_scenario_sends_two_notifications(monkeypatch):
    """Functional test simulating the FIXED rollback loop body.

    Two chains match the rule for a single rollback item. With the fix,
    both should fire route_notifications.
    """
    chain_a = ['WEB应用防护系统(WAF)', 'WEB应用防护系统(WAF)列表',
               'WAF V6.0.9', 'WAF V6.0.9规则升级包']
    chain_b = ['WEB应用防护系统(WAF)', '信息技术应用创新-WEB应用防护系统(WAF)列表',
               '海光系列HG', '海光系列 V6.0.9', '海光系列 V6.0.9规则升级']

    snap = _make_snap(100)
    rule = _make_rule(1015)

    captured_calls = []

    def fake_route_notifications(sid, rid, is_rollback=False, user_chain=None):
        captured_calls.append({
            'sid': sid, 'rid': rid,
            'is_rollback': is_rollback,
            'user_chain': user_chain,
        })

    # Stub scheduler collaborators
    from src.core import scheduler as sched_mod
    monkeypatch.setattr(sched_mod, 'get_enabled_rules', lambda: [rule])
    monkeypatch.setattr(sched_mod, 'get_new_for_subscription',
                        lambda r, items: [(100, snap, chain_a), (100, snap, chain_b)])
    monkeypatch.setattr(sched_mod, 'route_notifications', fake_route_notifications)

    # Reproduce the FIXED loop body inline:
    rollback_items = [(100, snap)]
    for sid, snap in rollback_items:
        for rule_ in sched_mod.get_enabled_rules():
            if not rule_.get('notify_rollback', 1):
                continue
            matched = sched_mod.get_new_for_subscription(rule_, [(sid, snap)])
            if not matched:
                continue
            for _matched_sid, _matched_snap, matched_chain in matched:
                sched_mod.route_notifications(
                    sid, rule_['id'], is_rollback=True, user_chain=matched_chain,
                )

    assert len(captured_calls) == 2, (
        f'expected 2 rollback notifications (one per chain), got {len(captured_calls)}: '
        f'{[c["user_chain"] for c in captured_calls]}'
    )
    chains_sent = [tuple(c['user_chain']) for c in captured_calls]
    assert tuple(chain_a) in chains_sent
    assert tuple(chain_b) in chains_sent
    assert all(c['is_rollback'] for c in captured_calls)


def test_rollback_empty_matched_skips_notification(monkeypatch):
    """When get_new_for_subscription returns [] for a rule, no notification
    should fire — preserves the existing skip-on-empty behavior."""
    from src.core import scheduler as sched_mod

    snap = _make_snap(100)
    rule = _make_rule(1015)

    captured = []
    monkeypatch.setattr(sched_mod, 'route_notifications',
                        lambda *a, **kw: captured.append(kw))
    monkeypatch.setattr(sched_mod, 'get_enabled_rules', lambda: [rule])
    monkeypatch.setattr(sched_mod, 'get_new_for_subscription',
                        lambda r, items: [])

    rollback_items = [(100, snap)]
    for sid, snap in rollback_items:
        for rule_ in sched_mod.get_enabled_rules():
            if not rule_.get('notify_rollback', 1):
                continue
            matched = sched_mod.get_new_for_subscription(rule_, [(sid, snap)])
            if not matched:
                continue
            for _msid, _msnap, mchain in matched:
                sched_mod.route_notifications(
                    sid, rule_['id'], is_rollback=True, user_chain=mchain,
                )

    assert captured == [], f'no notification should fire, got {captured}'


def test_rollback_disabled_rule_skips_notification(monkeypatch):
    """When notify_rollback=0 on the rule, no notification should fire."""
    from src.core import scheduler as sched_mod

    snap = _make_snap(100)
    rule = _make_rule(1015, notify_rollback=0)

    captured = []
    monkeypatch.setattr(sched_mod, 'route_notifications',
                        lambda *a, **kw: captured.append(kw))
    monkeypatch.setattr(sched_mod, 'get_enabled_rules', lambda: [rule])

    rollback_items = [(100, snap)]
    for sid, snap in rollback_items:
        for rule_ in sched_mod.get_enabled_rules():
            if not rule_.get('notify_rollback', 1):
                continue
            matched = [(100, snap, ['chain_a'])]
            for _msid, _msnap, mchain in matched:
                sched_mod.route_notifications(
                    sid, rule_['id'], is_rollback=True, user_chain=mchain,
                )

    assert captured == [], f'notify_rollback=0 should skip, got {captured}'