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


def _run_counting(conn, **kwargs):
    """同上，另外返回一次调用发出的 SQL 语句条数（用来盯住 N+1 回归）。"""
    from src.models import database as db_mod
    from src.models.subscription import get_history

    orig = db_mod.query
    counter = {'n': 0}

    def fake_query(sql, params=()):
        counter['n'] += 1
        return [dict(zip(r.keys(), r)) for r in conn.execute(sql, params).fetchall()]
    db_mod.query = fake_query
    try:
        rows, total = get_history(page=1, limit=20, **kwargs)
    finally:
        db_mod.query = orig
    return rows, total, counter['n']


def test_rule_filter_restricts_both_levels():
    conn = _seed_db()
    rows, total = _run(conn, rule_id=1)
    assert total == 1, 'total 是包数(distinct snapshot), 规则1 的 2 条交付同属 1 个包'
    assert len(rows) == 1  # 同一个 snapshot 只出一行
    assert [d['customer_id'] for d in rows[0]['deliveries']] == [11, 10]


def test_total_counts_snapshots_not_deliveries():
    """total 语义 (2026-11-09 P1): COUNT(DISTINCT s.id) = 页数基数, 不是交付行数。
    同一包推 4 次(2 客户 × 2 规则)仍然只有 1 页 1 行。"""
    conn = _seed_db()
    rows, total = _run(conn)
    assert total == 1
    assert len(rows) == 1
    assert len(rows[0]['deliveries']) == 4


def test_customer_and_rule_combine():
    conn = _seed_db()
    rows, total = _run(conn, rule_id=2, customer_id=11)
    assert total == 1
    assert len(rows[0]['deliveries']) == 1
    assert rows[0]['deliveries'][0]['customer_id'] == 11


def test_no_filter_returns_all_deliveries():
    conn = _seed_db()
    rows, total = _run(conn)
    assert total == 1
    assert len(rows[0]['deliveries']) == 4


def _add_second_snapshot(conn):
    """再加一个快照 + 两条交付,用来验证分组不串台。"""
    conn.execute("INSERT INTO snapshots (id, product_name, package_type, file_name)"
                 " VALUES (201, 'WAF', '规则', 'other.zip')")
    for rule_id, cust_id, sent_at in ((1, 10, '2026-11-02 05:00:00'),
                                      (2, 11, '2026-11-02 06:00:00')):
        conn.execute(
            "INSERT INTO delivery_log (snapshot_id, rule_id, channel_id, channel_type,"
            " channel_name, customer_id, delivery_status, sent_at, recipient)"
            " VALUES (201, ?, 5, 'email', 'mail', ?, 'sent', ?, 'y@163.com')",
            (rule_id, cust_id, sent_at))
    conn.commit()


def test_deliveries_grouped_per_snapshot():
    conn = _seed_db()
    _add_second_snapshot(conn)
    rows, total = _run(conn)
    assert total == 2
    by_id = {r['id']: r['deliveries'] for r in rows}
    assert len(by_id[200]) == 4 and len(by_id[201]) == 2
    assert all(d['snapshot_id'] == 200 for d in by_id[200])
    # 每个快照内部仍按 sent_at 倒序
    assert [d['sent_at'] for d in by_id[201]] == sorted(
        [d['sent_at'] for d in by_id[201]], reverse=True)


def test_rule_filter_survives_batching():
    conn = _seed_db()
    _add_second_snapshot(conn)
    rows, total = _run(conn, rule_id=1)
    # 规则1: 快照200 两条(客户10/11) + 快照201 一条(客户10) → 3 条交付 / 2 个包
    assert total == 2
    assert {r['id']: len(r['deliveries']) for r in rows} == {200: 2, 201: 1}


def test_deliveries_query_is_batched_not_per_row():
    """回归防线: deliveries 必须一次批量取,SQL 条数不随快照数增长。

    2026-11-09 前是 N+1 (count + rows + 每行一条 deliveries = 2 + N)。
    """
    conn = _seed_db()
    _add_second_snapshot(conn)
    _rows, _total, n_sql = _run_counting(conn)
    assert n_sql == 3, f'期望 3 条 SQL (count/rows/deliveries), 实际 {n_sql}'
