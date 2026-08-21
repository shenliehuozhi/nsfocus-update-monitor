"""Chain identity persistence tests.

These tests cover the two queue identities and the delivery log. They use
in-memory SQLite only, so the real notification service is never touched.
"""

import json
import sqlite3

import pytest

from src.models.subscription import create_tables, enqueue, enqueue_digest, log_delivery


def _table_sql(name: str, *, delayed=False, digest=False) -> str:
    if name == 'delivery_log':
        return """
        CREATE TABLE delivery_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            rule_id INTEGER,
            channel_id INTEGER,
            channel_type TEXT NOT NULL,
            channel_name TEXT DEFAULT '',
            customer_id INTEGER,
            delivery_status TEXT DEFAULT 'pending',
            error_message TEXT DEFAULT '',
            sent_at TEXT,
            retry_count INTEGER DEFAULT 0,
            recipient TEXT DEFAULT '',
            sender TEXT DEFAULT ''
        )
        """
    if delayed:
        return """
        CREATE TABLE delayed_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL,
            rule_id INTEGER NOT NULL,
            push_after TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'pending',
            cancelled_reason TEXT DEFAULT '',
            pushed_at TEXT
        )
        """
    if digest:
        return """
        CREATE TABLE digest_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER NOT NULL,
            snapshot_id INTEGER NOT NULL,
            period_key TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    raise AssertionError(name)


def _old_db() -> sqlite3.Connection:
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute(_table_sql('delivery_log'))
    conn.execute(_table_sql('delayed', delayed=True))
    conn.execute(_table_sql('digest', digest=True))
    create_tables(conn)
    return conn


def test_chain_json_is_added_to_new_queue_and_delivery_tables():
    conn = sqlite3.connect(':memory:')
    create_tables(conn)

    for table in ('delivery_log', 'delayed_queue', 'digest_queue'):
        cols = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
        assert 'chain_json' in cols


def test_chain_json_is_added_to_existing_tables_without_breaking_data():
    conn = _old_db()
    conn.execute(
        "INSERT INTO delayed_queue (snapshot_id, rule_id, push_after) VALUES (1, 2, 'soon')"
    )
    conn.execute(
        "INSERT INTO digest_queue (rule_id, snapshot_id, period_key) VALUES (2, 1, '2026-08')"
    )

    for table in ('delivery_log', 'delayed_queue', 'digest_queue'):
        cols = {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}
        assert 'chain_json' in cols
    assert conn.execute('SELECT snapshot_id FROM delayed_queue').fetchone()[0] == 1
    assert conn.execute('SELECT snapshot_id FROM digest_queue').fetchone()[0] == 1


def test_enqueue_dedupes_by_chain_not_only_snapshot_and_rule():
    conn = _old_db()
    from src.models import database as db_mod
    old_query = db_mod.query
    old_execute = db_mod.execute
    try:
        db_mod.query = lambda sql, params=(): [dict(zip([c for c in r.keys()], r)) for r in conn.execute(sql, params).fetchall()]
        db_mod.execute = lambda sql, params=(): conn.execute(sql, params).lastrowid
        first = enqueue(1, 2, 'soon', chain_json='["A", "B"]')
        duplicate = enqueue(1, 2, 'soon', chain_json='["A", "B"]')
        second_chain = enqueue(1, 2, 'soon', chain_json='["A", "C"]')
    finally:
        db_mod.query = old_query
        db_mod.execute = old_execute

    assert first == duplicate
    assert second_chain != first


def test_digest_dedupes_by_chain_not_only_snapshot_and_rule():
    conn = _old_db()
    from src.models import database as db_mod
    old_query = db_mod.query
    old_execute = db_mod.execute
    try:
        db_mod.query = lambda sql, params=(): [dict(zip([c for c in r.keys()], r)) for r in conn.execute(sql, params).fetchall()]
        db_mod.execute = lambda sql, params=(): conn.execute(sql, params).lastrowid
        first = enqueue_digest(2, 1, '2026-08', chain_json='["A", "B"]')
        duplicate = enqueue_digest(2, 1, '2026-08', chain_json='["A", "B"]')
        second_chain = enqueue_digest(2, 1, '2026-08', chain_json='["A", "C"]')
    finally:
        db_mod.query = old_query
        db_mod.execute = old_execute

    assert first == duplicate
    assert second_chain != first


def test_delivery_log_accepts_chain_json():
    conn = _old_db()
    from src.models import database as db_mod
    old_execute = db_mod.execute
    db_mod.execute = lambda sql, params=(): conn.execute(sql, params).lastrowid
    chain = ['A', 'B']
    delivery_id = log_delivery(
        snapshot_id=1,
        rule_id=2,
        channel_id=3,
        channel_type='email',
        chain_json=json.dumps(chain, ensure_ascii=False),
    )

    row = conn.execute(
        'SELECT chain_json FROM delivery_log WHERE id=?', (delivery_id,)
    ).fetchone()
    db_mod.execute = old_execute
    assert json.loads(row[0]) == chain
