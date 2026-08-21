"""Tests for router-level chain_json threading.

Scope: route_notifications / _send_immediate / process_delayed_queue must
serialize user_chain into chain_json so that delayed_queue, digest_queue, and
delivery_log dedup across (snapshot_id, rule_id, chain_json) instead of
(snapshot_id, rule_id) only. Same physical file mapped to multiple chains
must produce independent push opportunities.

Design constraint: tests must NEVER trigger real notifications. All
external collaborators (get_snapshot, get_rule, get_rule_channels, get_by_id,
notifiers, enqueue, enqueue_digest, log_delivery, get_due_items,
mark_pushed) are monkeypatched to record calls.
"""

import json
import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_snap(snap_id=100, **overrides):
    snap = {
        'id': snap_id,
        'source_id': 1,
        'source_url': '/update/xxx',
        'path_id': 'p1',
        'file_name': 'f.zip',
        'urgency': 'high',
        'description_raw': '',
        'product_name': 'WAF',
    }
    snap.update(overrides)
    return snap


def _make_rule(rule_id=10, name='r', digest_mode='', **overrides):
    rule = {
        'id': rule_id,
        'name': name,
        'enabled': 1,
        'digest_mode': digest_mode,
        'delay_days': 0,
        'customer_id': None,
        'customer_name': '',
    }
    rule.update(overrides)
    return rule


def _patch_router_collaborators(monkeypatch, **kwargs):
    """Install stub collaborators on src.notifiers.router.

    Defaults: get_snapshot returns a fresh snap per call (matching
    ``snap_id``/kwargs['snap'] when provided); get_rule returns a fresh rule;
    get_rule_channels returns [] unless overridden; get_by_id returns a stub
    channel with type=email. enqueue / enqueue_digest / log_delivery are
    recorded into in-memory lists (drain via .clear()).
    """
    import src.notifiers.router as router_mod
    snap = dict(kwargs.get('snap') or _make_snap())
    snap.setdefault('status', 'active')
    rule = kwargs.get('rule') or _make_rule()
    bindings = kwargs.get('bindings', [])
    channel = kwargs.get('channel') or {
        'id': 5, 'type': 'email', 'is_active': 1, 'name': 'mail',
        'config': {}, 'email_hourly_limit': 0, 'email_daily_limit': 0,
    }
    sent = kwargs.get('sent')  # existing delivery_log rows for dedup test
    enqueued = []
    digest_enqueued = []
    deliveries = []

    monkeypatch.setattr(router_mod, 'get_snapshot', lambda sid: snap)
    monkeypatch.setattr(router_mod, 'get_rule', lambda rid: rule)
    monkeypatch.setattr(router_mod, 'get_rule_channels', lambda rid: bindings)
    monkeypatch.setattr(router_mod, 'get_by_id', lambda cid: channel)
    monkeypatch.setattr(router_mod, '_is_maintenance_mode', lambda: False)
    monkeypatch.setattr(router_mod, '_send_digest_split', lambda *a, **kw: None)

    # Default: get_due_items returns empty
    monkeypatch.setattr(router_mod, 'get_due_items', lambda: [])
    monkeypatch.setattr(router_mod, 'mark_pushed', lambda qid: None)
    monkeypatch.setattr(router_mod, 'cancel_for_snapshot', lambda *a, **kw: None)

    def fake_enqueue(snapshot_id, rule_id, push_after, chain_json='[]'):
        enqueued.append({
            'snapshot_id': snapshot_id,
            'rule_id': rule_id,
            'push_after': push_after,
            'chain_json': chain_json,
        })
        return len(enqueued)

    def fake_enqueue_digest(rule_id, snapshot_id, period_key, chain_json='[]'):
        digest_enqueued.append({
            'rule_id': rule_id,
            'snapshot_id': snapshot_id,
            'period_key': period_key,
            'chain_json': chain_json,
        })
        return len(digest_enqueued)

    def fake_log_delivery(**kw):
        deliveries.append(kw)
        return len(deliveries)

    monkeypatch.setattr(router_mod, 'enqueue', fake_enqueue)
    # enqueue_digest / enqueue / log_delivery are imported lazily inside
    # route_notifications / _send_immediate via `from src.models.subscription
    # import ...`. Patch on the subscription module so the local import
    # picks it up.
    monkeypatch.setattr('src.models.subscription.enqueue_digest', fake_enqueue_digest)
    monkeypatch.setattr('src.models.subscription.enqueue', fake_enqueue)
    monkeypatch.setattr('src.models.subscription.log_delivery', fake_log_delivery)

    # Stub delivery_log dedup query inside _send_immediate
    def fake_query_dedup(sql, params=()):
        if sent is None:
            return []
        # Match only on (snapshot_id, channel_id, rule_id); chain_json is the
        # new dimension — the stub doesn't simulate it (test asserts we don't
        # call query with chain_json in the dedup check).
        return sent

    monkeypatch.setattr(
        'src.models.database.query',
        fake_query_dedup,
        raising=False,
    )

    # Stub notifier.send
    from src.notifiers.base import DeliveryResult
    class _FakeEmail:
        @staticmethod
        def send(msg, cfg):
            return DeliveryResult(
                success=True, channel_type='email', channel_name='mail',
                sender='noreply@x',
            )
    fake_notifiers = dict(router_mod.NOTIFIERS)
    fake_notifiers['email'] = _FakeEmail()
    monkeypatch.setattr(router_mod, 'NOTIFIERS', fake_notifiers)

    # route_notifications does `from src.models.subscription import get_rule`
    # inside the function — patch the subscription module too.
    monkeypatch.setattr('src.models.subscription.get_rule',
                        lambda rid: rule)

    return {
        'enqueued': enqueued,
        'digest_enqueued': digest_enqueued,
        'deliveries': deliveries,
        'snap': snap,
        'rule': rule,
        'bindings': bindings,
        'channel': channel,
    }


# ---------------------------------------------------------------------------
# tests: chain_json must flow through route_notifications
# ---------------------------------------------------------------------------

def test_route_immediate_writes_chain_json_to_delivery_log(monkeypatch):
    """Same physical snap + rule, different user_chain → two distinct
    delivery_log rows, each carrying its own chain_json."""
    from src.notifiers.router import route_notifications

    snap = _make_snap(100)
    rule = _make_rule(10)
    bindings = [{'channel_id': 5, 'customer_id': 1}]
    state = _patch_router_collaborators(
        monkeypatch, snap=snap, rule=rule, bindings=bindings,
    )

    chain_a = ['WAF', 'WAF-V6', 'WAF-V6-RULE']
    chain_b = ['WAF', '海光', 'WAF-V6-RULE']

    route_notifications(100, 10, user_chain=chain_a)
    route_notifications(100, 10, user_chain=chain_b)

    assert len(state['deliveries']) == 2
    assert state['deliveries'][0]['chain_json'] == json.dumps(chain_a, ensure_ascii=False)
    assert state['deliveries'][1]['chain_json'] == json.dumps(chain_b, ensure_ascii=False)


def test_route_delayed_enqueue_writes_chain_json(monkeypatch):
    """Delayed path: enqueue must record chain_json matching user_chain."""
    from src.notifiers.router import route_notifications

    snap = _make_snap(101)
    rule = _make_rule(11, delay_days=1)
    bindings = [{'channel_id': 5, 'customer_id': 1}]
    state = _patch_router_collaborators(
        monkeypatch, snap=snap, rule=rule, bindings=bindings,
    )

    chain_a = ['WAF', 'WAF-V6', 'rule']
    chain_b = ['WAF', '海光', 'rule']

    route_notifications(101, 11, user_chain=chain_a)
    route_notifications(101, 11, user_chain=chain_b)

    assert len(state['enqueued']) == 2
    assert state['enqueued'][0]['chain_json'] == json.dumps(chain_a, ensure_ascii=False)
    assert state['enqueued'][1]['chain_json'] == json.dumps(chain_b, ensure_ascii=False)


def test_route_digest_enqueue_writes_chain_json(monkeypatch):
    """Digest mode: enqueue_digest must record chain_json."""
    from src.notifiers.router import route_notifications

    snap = _make_snap(102)
    rule = _make_rule(12, digest_mode='monthly')
    bindings = [{'channel_id': 5, 'customer_id': 1}]
    state = _patch_router_collaborators(
        monkeypatch, snap=snap, rule=rule, bindings=bindings,
    )

    chain_a = ['WAF', 'WAF-V6', 'rule']
    chain_b = ['WAF', '海光', 'rule']

    route_notifications(102, 12, user_chain=chain_a)
    route_notifications(102, 12, user_chain=chain_b)

    assert len(state['digest_enqueued']) == 2
    assert state['digest_enqueued'][0]['chain_json'] == json.dumps(chain_a, ensure_ascii=False)
    assert state['digest_enqueued'][1]['chain_json'] == json.dumps(chain_b, ensure_ascii=False)


def test_delivery_log_dedup_query_includes_chain_json(monkeypatch):
    """The dedup check inside _send_immediate must include chain_json in
    its WHERE clause so (snap, rule, chain-A) and (snap, rule, chain-B) are
    treated as different push opportunities."""
    from src.notifiers.router import _send_immediate
    import src.notifiers.router as router_mod

    snap = _make_snap(103)
    rule = _make_rule(13)
    bindings = [{'channel_id': 5, 'customer_id': 1}]
    captured_queries = []

    state = _patch_router_collaborators(
        monkeypatch, snap=snap, rule=rule, bindings=bindings,
    )

    # Capture the actual query sent to the dedup check
    def fake_query(sql, params=()):
        captured_queries.append({'sql': sql, 'params': params})
        # For first chain-A call: nothing exists yet, allow push
        # For chain-B: also nothing → allow push
        return []
    monkeypatch.setattr('src.models.database.query', fake_query, raising=False)

    chain_a = ['WAF', 'rule']
    chain_b = ['WAF', '海光', 'rule']

    _send_immediate(snap, rule, user_chain=chain_a)
    _send_immediate(snap, rule, user_chain=chain_b)

    # Find the dedup queries (filter out others)
    dedup_qs = [q for q in captured_queries if 'delivery_log' in q['sql']]
    assert len(dedup_qs) >= 2
    # chain_json column must be referenced
    for q in dedup_qs:
        assert 'chain_json' in q['sql'], (
            f'delivery_log dedup query must include chain_json, got: {q["sql"]}'
        )
        # chain_json param must appear in params
        assert any(isinstance(p, str) and (p.startswith('[') or p == '[]') for p in q['params']), (
            f'delivery_log dedup params must include chain_json, got: {q["params"]}'
        )


def test_process_delayed_queue_reads_chain_json_from_row(monkeypatch):
    """process_delayed_queue must read chain_json from delayed_queue row
    and pass it to _send_immediate as user_chain (so the chain-scoped push
    message uses the same chain that was originally enqueued)."""
    import src.notifiers.router as router_mod

    chain_a = ['WAF', 'rule']
    chain_b = ['WAF', '海光', 'rule']

    due_items = [
        {'id': 1, 'snapshot_id': 100, 'rule_id': 10, 'chain_json': json.dumps(chain_a, ensure_ascii=False)},
        {'id': 2, 'snapshot_id': 100, 'rule_id': 10, 'chain_json': json.dumps(chain_b, ensure_ascii=False)},
    ]
    snap = _make_snap(100, status='active')

    user_chains_seen = []

    real_send = router_mod._send_immediate

    def fake_send_immediate(snap_arg, rule_arg, is_rollback=False, user_chain=None):
        user_chains_seen.append(user_chain)
        return None

    monkeypatch.setattr(router_mod, '_is_maintenance_mode', lambda: False)
    monkeypatch.setattr(router_mod, 'get_due_items', lambda: due_items)
    monkeypatch.setattr(router_mod, 'mark_pushed', lambda qid: None)
    monkeypatch.setattr(router_mod, 'get_snapshot', lambda sid: snap)
    monkeypatch.setattr(router_mod, 'get_rule', lambda rid: _make_rule(rid))
    # Bypass quiet/window checks
    monkeypatch.setattr(router_mod, 'is_quiet_time', lambda r: False)
    monkeypatch.setattr(router_mod, 'is_window_time', lambda r: True)
    monkeypatch.setattr(router_mod, '_send_immediate', fake_send_immediate)

    router_mod.process_delayed_queue()

    assert len(user_chains_seen) == 2
    assert user_chains_seen[0] == chain_a
    assert user_chains_seen[1] == chain_b


def test_process_delayed_queue_does_not_reference_undefined_user_chain(monkeypatch):
    """Regression: process_delayed_queue used `user_chain` without defining
    it. Must read chain_json from each row instead."""
    import src.notifiers.router as router_mod
    import inspect

    src = inspect.getsource(router_mod.process_delayed_queue)
    # The bare identifier `user_chain` (not `_send_immediate(user_chain=...)`)
    # must not appear as a NameError candidate
    # i.e. the body must NOT have a line like `_send_immediate(..., user_chain=user_chain)`
    # without it being bound locally.
    # Simpler check: function must reference chain_json or a local var:
    assert 'chain_json' in src, (
        "process_delayed_queue must read chain_json from delayed_queue row"
    )


# ---------------------------------------------------------------------------
# tests: push summary accumulator keyed by chain
# ---------------------------------------------------------------------------

def test_push_summary_accumulator_keyed_by_chain_json(monkeypatch):
    """_send_immediate accumulates push summary under key (rule, channel,
    chain_json) so chain-A and chain-B report separately."""
    import src.notifiers.router as router_mod

    snap = _make_snap(200)
    rule = _make_rule(20)
    bindings = [{'channel_id': 5, 'customer_id': 1}]
    state = _patch_router_collaborators(
        monkeypatch, snap=snap, rule=rule, bindings=bindings,
    )

    chain_a = ['WAF', 'rule']
    chain_b = ['WAF', '海光', 'rule']

    router_mod._push_summary_accumulator.clear()

    router_mod._send_immediate(snap, rule, user_chain=chain_a)
    router_mod._send_immediate(snap, rule, user_chain=chain_b)

    acc = router_mod._push_summary_accumulator
    # Two distinct chain_json keys
    assert len(acc) == 2, (
        f'expected 2 summary entries (one per chain), got {len(acc)}: '
        f'{list(acc.keys())}'
    )
    # Both keys carry chain_json
    keys_have_chain = all(
        isinstance(k, tuple) and len(k) == 3 and isinstance(k[2], str)
        for k in acc.keys()
    )
    assert keys_have_chain, (
        f'accumulator keys must be (rule_id, channel_id, chain_json), got: '
        f'{list(acc.keys())}'
    )
    chain_jsons = {k[2] for k in acc.keys()}
    assert json.dumps(chain_a, ensure_ascii=False) in chain_jsons
    assert json.dumps(chain_b, ensure_ascii=False) in chain_jsons