"""Tests for chain-aware delivery_log history queries.

The push history UI / dashboard summary aggregates delivery_log rows. With
chain-scoped pushes, two delivery_log rows for the same physical snapshot
+ rule but different chain_json must display two distinct package_types
(chain-A vs chain-B), not both showing snapshots.package_type which gets
overwritten by URL dedup.

The fix: derive package_type from chain_json[-1] when chain_json is
non-empty; fall back to snapshots.package_type when chain_json is '[]'
(legacy rows written before chain-scoped pipeline).
"""

import json
import sqlite3

import pytest


def _seed_db() -> sqlite3.Connection:
    """Build an in-memory DB with delivery_log + snapshots + subscription_rules
    + customers, seeded for chain-scoped history tests."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')

    conn.executescript("""
    CREATE TABLE snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER,
        product_name TEXT DEFAULT '',
        version_branch TEXT DEFAULT '',
        package_type TEXT DEFAULT '',
        file_name TEXT DEFAULT '',
        package_version TEXT DEFAULT '',
        md5_hash TEXT DEFAULT '',
        file_size INTEGER DEFAULT 0,
        source_url TEXT DEFAULT '',
        path_id TEXT DEFAULT '',
        status TEXT DEFAULT 'active'
    );

    CREATE TABLE subscription_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT DEFAULT '',
        enabled INTEGER DEFAULT 1
    );

    CREATE TABLE customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT DEFAULT '',
        email TEXT DEFAULT '',
        company TEXT DEFAULT ''
    );

    CREATE TABLE channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT DEFAULT 'email',
        name TEXT DEFAULT '',
        is_active INTEGER DEFAULT 1
    );

    CREATE TABLE delivery_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
        rule_id INTEGER REFERENCES subscription_rules(id),
        channel_id INTEGER REFERENCES channels(id),
        channel_type TEXT NOT NULL,
        channel_name TEXT DEFAULT '',
        customer_id INTEGER REFERENCES customers(id),
        delivery_status TEXT DEFAULT 'pending',
        error_message TEXT DEFAULT '',
        sent_at TEXT,
        retry_count INTEGER DEFAULT 0,
        recipient TEXT DEFAULT '',
        sender TEXT DEFAULT '',
        chain_json TEXT DEFAULT '[]'
    );
    """)

    # One physical snapshot shared by two chains
    conn.execute(
        """INSERT INTO snapshots
           (id, product_name, version_branch, package_type, file_name,
            md5_hash, source_url, path_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (100, 'WEB应用防护系统(WAF)', 'WAF V6.0.9',
         'WAF V6.0.9规则升级包',  # snapshots.package_type = chain-A value
         'update_rule.V6.0R09F00.29779937.wcl',
         '1369e0e7a45634a5102549887544ed65',
         '/update/listWafV69Detail/v/rule', 'p1'),
    )

    conn.execute("INSERT INTO subscription_rules (id, name) VALUES (1015, '测试chain')")
    conn.execute("INSERT INTO customers (id, name, email) VALUES (1, '测试客户', 'x@163.com')")
    conn.execute("INSERT INTO channels (id, type, name) VALUES (5, 'email', 'mail')")

    # Two delivery_log rows for the SAME physical snap, but different chain_json
    # (this is what chain-scoped pipeline produces).
    chain_a = ['WEB应用防护系统(WAF)', 'WEB应用防护系统(WAF)列表', 'WAF V6.0.9', 'WAF V6.0.9规则升级包']
    chain_b = ['WEB应用防护系统(WAF)', '信息技术应用创新-WEB应用防护系统(WAF)列表',
               '海光系列HG', '海光系列 V6.0.9', '海光系列 V6.0.9规则升级']

    conn.execute(
        """INSERT INTO delivery_log
           (snapshot_id, rule_id, channel_id, channel_type, channel_name,
            customer_id, delivery_status, sent_at, recipient, chain_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (100, 1015, 5, 'email', 'mail', 1, 'sent', '2026-08-21 09:08:44',
         'x@163.com', json.dumps(chain_a, ensure_ascii=False)),
    )
    conn.execute(
        """INSERT INTO delivery_log
           (snapshot_id, rule_id, channel_id, channel_type, channel_name,
            customer_id, delivery_status, sent_at, recipient, chain_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (100, 1015, 5, 'email', 'mail', 1, 'sent', '2026-08-21 09:08:53',
         'x@163.com', json.dumps(chain_b, ensure_ascii=False)),
    )

    # Third row: legacy delivery_log with chain_json='[]' (written before
    # chain-scoped pipeline). Must fall back to snapshots.package_type.
    conn.execute(
        """INSERT INTO delivery_log
           (id, snapshot_id, rule_id, channel_id, channel_type, channel_name,
            customer_id, delivery_status, sent_at, recipient, chain_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (7563, 100, 1015, 5, 'email', 'mail', 1, 'sent', '2026-08-20 09:37:00',
         'x@163.com', '[]'),
    )

    conn.commit()
    return conn


def test_get_history_derives_package_type_from_chain_json():
    """get_history must return chain-derived package_type so two deliveries
    for the same physical snapshot show two distinct types."""
    from src.models import database as db_mod
    from src.models.subscription import get_history

    conn = _seed_db()
    # Wire get_history to use our in-memory DB instead of the production one.
    orig_query = db_mod.query
    def fake_query(sql, params=()):
        return [dict(zip(r.keys(), r)) for r in conn.execute(sql, params).fetchall()]
    db_mod.query = fake_query
    try:
        rows, _total = get_history(page=1, limit=20, days=7)
    finally:
        db_mod.query = orig_query

    assert len(rows) == 1
    row = rows[0]
    # snap 100 has 3 delivery_log rows (chain-A + chain-B + legacy '[]')
    deliveries = row['deliveries']
    assert len(deliveries) == 3

    # Sort by sent_at DESC (matches production)
    by_chain = {d['chain_json']: d for d in deliveries}

    chain_a_json = json.dumps(['WEB应用防护系统(WAF)', 'WEB应用防护系统(WAF)列表',
                              'WAF V6.0.9', 'WAF V6.0.9规则升级包'],
                             ensure_ascii=False)
    chain_b_json = json.dumps(['WEB应用防护系统(WAF)', '信息技术应用创新-WEB应用防护系统(WAF)列表',
                              '海光系列HG', '海光系列 V6.0.9', '海光系列 V6.0.9规则升级'],
                             ensure_ascii=False)

    # New behavior: get_history attaches a derived package_type to each
    # delivery, based on chain_json[-1]. For chain-A: 'WAF V6.0.9规则升级包'.
    # For chain-B: '海光系列 V6.0.9规则升级'. For legacy '[]': fallback to
    # snapshots.package_type = 'WAF V6.0.9规则升级包'.
    chain_a = by_chain[chain_a_json]
    chain_b = by_chain[chain_b_json]
    legacy = by_chain['[]']

    assert chain_a.get('derived_package_type') == 'WAF V6.0.9规则升级包', (
        f'chain-A should derive WAF V6.0.9规则升级包, got {chain_a.get("derived_package_type")!r}'
    )
    assert chain_b.get('derived_package_type') == '海光系列 V6.0.9规则升级', (
        f'chain-B should derive 海光系列 V6.0.9规则升级, got {chain_b.get("derived_package_type")!r}'
    )
    assert legacy.get('derived_package_type') == 'WAF V6.0.9规则升级包', (
        f'legacy [] should fallback to snapshots.package_type, got {legacy.get("derived_package_type")!r}'
    )


def test_get_history_returns_distinct_package_types_for_two_chains():
    """The two chain-A / chain-B deliveries must have distinct derived
    package_type — this is the user-visible bug fix."""
    from src.models import database as db_mod
    from src.models.subscription import get_history

    conn = _seed_db()
    orig_query = db_mod.query
    def fake_query(sql, params=()):
        return [dict(zip(r.keys(), r)) for r in conn.execute(sql, params).fetchall()]
    db_mod.query = fake_query
    try:
        rows, _total = get_history(page=1, limit=20, days=7)
    finally:
        db_mod.query = orig_query

    deliveries = rows[0]['deliveries']
    # Collect derived_package_type for the 2 chain-scoped rows (non-legacy)
    derived = [d.get('derived_package_type') for d in deliveries
               if d.get('chain_json') and d['chain_json'] != '[]']
    assert len(derived) == 2
    assert derived[0] != derived[1], (
        f'chain-A and chain-B must have distinct derived package types, '
        f'got both equal to {derived[0]!r}'
    )