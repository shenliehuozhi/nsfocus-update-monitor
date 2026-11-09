"""推送历史过滤条件（customer_id / rule_id）。

feat: 2026-11-09 推送历史新增「订阅规则」过滤，与客户过滤走同一套
dl.* 条件，且必须同时下推到每个 snapshot 的 deliveries 子查询，否则
列表行会把别的规则/客户的交付记录也列出来。
"""

import sqlite3


def _seed_db() -> sqlite3.Connection:
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript("""
    CREATE TABLE snapshots (
        id INTEGER PRIMARY KEY, product_name TEXT DEFAULT '',
        package_type TEXT DEFAULT '', file_name TEXT DEFAULT '',
        package_version TEXT DEFAULT '', md5_hash TEXT DEFAULT '',
        source_url TEXT DEFAULT '', path_id TEXT DEFAULT '',
        status TEXT DEFAULT 'active', published_at TEXT DEFAULT ''
    );
    CREATE TABLE subscription_rules (id INTEGER PRIMARY KEY, name TEXT DEFAULT '');
    CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT DEFAULT '',
                            email TEXT DEFAULT '', company TEXT DEFAULT '');
    CREATE TABLE channels (id INTEGER PRIMARY KEY, type TEXT DEFAULT 'email',
                           name TEXT DEFAULT '', is_active INTEGER DEFAULT 1);
    CREATE TABLE delivery_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
        rule_id INTEGER REFERENCES subscription_rules(id),
        channel_id INTEGER REFERENCES channels(id),
        channel_type TEXT NOT NULL, channel_name TEXT DEFAULT '',
        customer_id INTEGER REFERENCES customers(id),
        delivery_status TEXT DEFAULT 'pending', error_message TEXT DEFAULT '',
        sent_at TEXT, retry_count INTEGER DEFAULT 0,
        recipient TEXT DEFAULT '', sender TEXT DEFAULT '', chain_json TEXT DEFAULT '[]'
    );
    """)

    conn.execute("INSERT INTO snapshots (id, product_name, package_type, file_name)"
                 " VALUES (200, 'IPS', '规则', 'sig.zip')")
    conn.execute("INSERT INTO subscription_rules (id, name) VALUES (1, '规则A'), (2, '规则B')")
    conn.execute("INSERT INTO customers (id, name) VALUES (10, '客户甲'), (11, '客户乙')")
    conn.execute("INSERT INTO channels (id, type, name) VALUES (5, 'email', 'mail')")

    rows = [
        # (rule, customer, sent_at)
        (1, 10, '2026-11-01 01:00:00'),
        (1, 11, '2026-11-01 02:00:00'),
        (2, 10, '2026-11-01 03:00:00'),
        (2, 11, '2026-11-01 04:00:00'),
    ]
    for rule_id, cust_id, sent_at in rows:
        conn.execute(
            "INSERT INTO delivery_log (snapshot_id, rule_id, channel_id, channel_type,"
            " channel_name, customer_id, delivery_status, sent_at, recipient)"
            " VALUES (200, ?, 5, 'email', 'mail', ?, 'sent', ?, 'x@163.com')",
            (rule_id, cust_id, sent_at))
    conn.commit()
    return conn


def _run(conn, **kwargs):
    from src.models import database as db_mod
    from src.models.subscription import get_history

    orig = db_mod.query
    def fake_query(sql, params=()):
        return [dict(zip(r.keys(), r)) for r in conn.execute(sql, params).fetchall()]
    db_mod.query = fake_query
    try:
        return get_history(page=1, limit=20, **kwargs)
    finally:
        db_mod.query = orig


def test_rule_filter_restricts_both_levels():
    conn = _seed_db()
    rows, total = _run(conn, rule_id=1)
    assert total == 2
    assert len(rows) == 1  # 同一个 snapshot 只出一行
    assert [d['customer_id'] for d in rows[0]['deliveries']] == [11, 10]


def test_customer_and_rule_combine():
    conn = _seed_db()
    rows, total = _run(conn, rule_id=2, customer_id=11)
    assert total == 1
    assert len(rows[0]['deliveries']) == 1
    assert rows[0]['deliveries'][0]['customer_id'] == 11


def test_no_filter_returns_all_deliveries():
    conn = _seed_db()
    rows, total = _run(conn)
    assert total == 4
    assert len(rows[0]['deliveries']) == 4
