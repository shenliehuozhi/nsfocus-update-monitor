"""Tests for src.models.database.sync_table_columns.

背景:2026-07-24 用户报 schema 升级时 `create_rule` 报
`table subscription_rules has no column named template`,
`log_delivery` 报 `delivery_log has no column named sender`。
根因:代码加了新 INSERT 列,但 SCHEMA / ALTER 兜底都漏了,
导致全新部署和老 DB 升级两个场景都炸。

这套测试覆盖方案 B(自动同步列),保证以后再加列时:
1. 全新部署跑 init_all_tables → schema 含全部 EXPECTED 列
2. 老 DB(真的缺列) 跑 init_all_tables → sync 自动 ALTER 补齐
3. 老数据不丢
4. 跑多次幂等(不会重复加同名列)

绝不 import scheduler / notifier / app(只测 schema 层)。
"""

import os
import sqlite3
import pytest


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """全新部署:空白目录 + 空白 DB,跑 init_all_tables。预插 1 个 user。"""
    import src.models.database as db_mod

    db_file = tmp_path / 'fresh_nsfocus_monitor.db'
    monkeypatch.setattr(db_mod, 'DB_PATH', str(db_file))
    # 重置 thread-local connection,确保 DB_PATH 切换生效
    if hasattr(db_mod._local, 'conn'):
        monkeypatch.setattr(db_mod._local, 'conn', None)
    db_mod.get_db()

    from src.models import init_all_tables
    init_all_tables()

    # 预插 1 个 user,这样 create_rule / log_delivery 测试不依赖外部数据
    con = sqlite3.connect(str(db_file))
    con.execute("INSERT INTO users (username, password_hash) VALUES ('test', 'x')")
    con.commit()
    con.close()

    return str(db_file)


@pytest.fixture
def old_db_missing_template_and_sender(tmp_path, monkeypatch):
    """模拟老 DB:subscription_rules 没有 template 列,delivery_log 没有 sender 列。
    这是用户报错瞬间的 schema 状态(2026-07-24 10:05)。
    """
    import src.models.database as db_mod

    db_file = tmp_path / 'old_nsfocus_monitor.db'
    monkeypatch.setattr(db_mod, 'DB_PATH', str(db_file))
    if hasattr(db_mod._local, 'conn'):
        monkeypatch.setattr(db_mod._local, 'conn', None)
    db_mod.get_db()

    # 用裸 sqlite3 建老 schema,跟报错时实际生产 DB 一致
    con = sqlite3.connect(str(db_file))
    con.executescript("""
        CREATE TABLE subscription_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            filter_conditions TEXT DEFAULT '{}',
            delay_hours INTEGER DEFAULT 0,
            delay_strategy TEXT DEFAULT 'reset',
            min_interval_hours INTEGER DEFAULT 0,
            quiet_start TEXT DEFAULT '',
            quiet_end TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            customer_id INTEGER DEFAULT 0,
            valid_until TEXT DEFAULT '',
            digest_mode TEXT DEFAULT '',
            digest_config TEXT DEFAULT '{}',
            notify_rollback INTEGER DEFAULT 1,
            customer_emails TEXT DEFAULT '',
            window_config TEXT DEFAULT '{}',
            delay_days INTEGER DEFAULT 0
            -- 故意缺: template (本次 bug 主角)
        );
        INSERT INTO subscription_rules (user_id, name, delay_hours, delay_days)
            VALUES (1, '老规则_绿盟waf', 72, 0);
        INSERT INTO subscription_rules (user_id, name, delay_hours, delay_days)
            VALUES (1, '老规则_rsas', 24, 0);

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
            retry_count INTEGER DEFAULT 0
            -- 故意缺: recipient, sender (本次 bug 主角)
        );
        INSERT INTO delivery_log (snapshot_id, rule_id, channel_type, channel_name)
            VALUES (1, 1, 'feishu', '老渠道');

        CREATE TABLE system_event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL
            -- 故意缺: severity, product_name, source_url, rule_id,
            --         channel_id, channel_type, customer_id, is_rollback, message
        );

        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            password_hash TEXT
        );
        INSERT INTO users (username, password_hash) VALUES ('u1', 'x');
    """)
    con.commit()
    con.close()

    return str(db_file)


# ─────────────────────────────────────────────────────────────
# A) 全新部署
# ─────────────────────────────────────────────────────────────

def test_fresh_deploy_schema_has_all_expected_columns(fresh_db):
    """全新部署后,subscription_rules / delivery_log / system_event_log
    都必须含 EXPECTED 列 — 否则 create_rule / log_delivery / log_event
    会触发 no-such-column 错误。"""
    from src.models.subscription import (
        EXPECTED_SUBSCRIPTION_RULES_COLUMNS,
        EXPECTED_DELIVERY_LOG_COLUMNS,
    )
    from src.models.event_log import EXPECTED_SYSTEM_EVENT_LOG_COLUMNS

    con = sqlite3.connect(fresh_db)
    for table, expected in [
        ('subscription_rules', EXPECTED_SUBSCRIPTION_RULES_COLUMNS),
        ('delivery_log', EXPECTED_DELIVERY_LOG_COLUMNS),
        ('system_event_log', EXPECTED_SYSTEM_EVENT_LOG_COLUMNS),
    ]:
        cols = {r[1] for r in con.execute(f'PRAGMA table_info({table})').fetchall()}
        missing = [c for c, _, _ in expected if c not in cols]
        assert not missing, f'{table} 缺列: {missing} (CREATE TABLE 没声明)'


def test_fresh_deploy_create_rule_works(fresh_db):
    """全新部署后,直接调 create_rule 必须成功(不会再报 no-such-column)。"""
    from src.models import subscription
    import src.models.database as db_mod

    user_id = db_mod.query('SELECT id FROM users LIMIT 1')[0]['id']
    rule_id = subscription.create_rule(user_id, name='新规则', template='brief')
    assert rule_id > 0
    row = db_mod.query('SELECT template FROM subscription_rules WHERE id=?', (rule_id,))[0]
    assert row['template'] == 'brief'


def test_fresh_deploy_log_delivery_works(fresh_db):
    """全新部署后,log_delivery 必须能写 sender/recipient 列。"""
    from src.models import subscription
    import src.models.database as db_mod

    delivery_id = subscription.log_delivery(
        snapshot_id=1, channel_id=1, channel_type='feishu',
        channel_name='cn', customer_id=1, status='pending',
        rule_id=1, recipient='r@example.com', sender='s@example.com',
    )
    assert delivery_id > 0
    row = db_mod.query(
        'SELECT sender, recipient FROM delivery_log WHERE id=?', (delivery_id,)
    )[0]
    assert row['sender'] == 's@example.com'
    assert row['recipient'] == 'r@example.com'


def test_fresh_deploy_log_event_works(fresh_db):
    """全新部署后,log_event 必须能写 10 列。"""
    from src.models import event_log as elog
    import src.models.database as db_mod

    elog.log_event(
        'push_success', 'INFO', product_name='p1', source_url='http://x',
        rule_id=1, channel_id=1, channel_type='feishu',
        customer_id=1, is_rollback=0, message='{"k":"v"}',
    )
    row = db_mod.query('SELECT event_type, channel_type FROM system_event_log ORDER BY id DESC LIMIT 1')[0]
    assert row['event_type'] == 'push_success'
    assert row['channel_type'] == 'feishu'


# ─────────────────────────────────────────────────────────────
# B) 老 DB 升级
# ─────────────────────────────────────────────────────────────

def test_old_db_sync_adds_missing_columns(old_db_missing_template_and_sender):
    """老 DB 缺 template / sender / recipient / 多个 event 列 →
    跑 init_all_tables → sync 自动 ALTER 补齐。"""
    from src.models import init_all_tables
    init_all_tables()  # 必须幂等,可重复调用

    con = sqlite3.connect(old_db_missing_template_and_sender)

    sub_cols = {r[1] for r in con.execute('PRAGMA table_info(subscription_rules)').fetchall()}
    assert 'template' in sub_cols, '老 DB 升级后 subscription_rules 缺 template'

    dl_cols = {r[1] for r in con.execute('PRAGMA table_info(delivery_log)').fetchall()}
    assert 'sender' in dl_cols, '老 DB 升级后 delivery_log 缺 sender'
    assert 'recipient' in dl_cols, '老 DB 升级后 delivery_log 缺 recipient'

    sel_cols = {r[1] for r in con.execute('PRAGMA table_info(system_event_log)').fetchall()}
    assert 'severity' in sel_cols
    assert 'channel_type' in sel_cols
    assert 'message' in sel_cols


def test_old_db_sync_preserves_existing_data(old_db_missing_template_and_sender):
    """老数据必须保留:subscription_rules 2 行 / delivery_log 1 行不能丢。"""
    from src.models import init_all_tables
    init_all_tables()

    con = sqlite3.connect(old_db_missing_template_and_sender)
    assert con.execute('SELECT COUNT(*) FROM subscription_rules').fetchone()[0] == 2
    assert con.execute('SELECT COUNT(*) FROM delivery_log').fetchone()[0] == 1

    # 老规则 name 必须保留
    names = [r[0] for r in con.execute('SELECT name FROM subscription_rules ORDER BY id').fetchall()]
    assert names == ['老规则_绿盟waf', '老规则_rsas']


def test_old_db_sync_bake_in_defaults(old_db_missing_template_and_sender):
    """老行的新列必须有 DEFAULT 值(template='full', sender='', recipient='')。"""
    from src.models import init_all_tables
    init_all_tables()

    con = sqlite3.connect(old_db_missing_template_and_sender)
    rules = con.execute('SELECT name, template FROM subscription_rules ORDER BY id').fetchall()
    for name, template in rules:
        assert template == 'full', f'老规则 {name} 的 template 应默认为 full,实际 {template!r}'

    dl = con.execute('SELECT channel_name, sender, recipient FROM delivery_log').fetchone()
    assert dl[1] == ''
    assert dl[2] == ''


def test_old_db_sync_delay_hours_to_delay_days(old_db_missing_template_and_sender):
    """老 schema 有 delay_hours 列 → 跑完 init_all_tables,
    delay_days 应该是 delay_hours / 24 的整数。"""
    from src.models import init_all_tables
    init_all_tables()

    con = sqlite3.connect(old_db_missing_template_and_sender)
    rows = con.execute(
        'SELECT name, delay_hours, delay_days FROM subscription_rules ORDER BY id'
    ).fetchall()
    assert rows[0][1] == 72 and rows[0][2] == 3, f'row1: delay_hours={rows[0][1]} delay_days={rows[0][2]}'
    assert rows[1][1] == 24 and rows[1][2] == 1, f'row2: delay_hours={rows[1][1]} delay_days={rows[1][2]}'


def test_old_db_sync_idempotent(old_db_missing_template_and_sender):
    """跑 init_all_tables 多次,schema 必须稳定(不能再加同名列,否则 ALTER 报错)。"""
    from src.models import init_all_tables

    con = sqlite3.connect(old_db_missing_template_and_sender)

    # 第一次跑应该补齐列
    init_all_tables()
    after_first = sorted(r[1] for r in con.execute('PRAGMA table_info(subscription_rules)').fetchall())

    # 第二次/第三次跑应该 noop,schema 不变
    init_all_tables()
    init_all_tables()
    after_more = sorted(r[1] for r in con.execute('PRAGMA table_info(subscription_rules)').fetchall())

    assert after_first == after_more, (
        f'幂等失败: 第二次之后列变了\n{after_first}\n→\n{after_more}'
    )

    # delivery_log 同样
    init_all_tables()
    dl_first = sorted(r[1] for r in con.execute('PRAGMA table_info(delivery_log)').fetchall())
    init_all_tables()
    dl_more = sorted(r[1] for r in con.execute('PRAGMA table_info(delivery_log)').fetchall())
    assert dl_first == dl_more


def test_old_db_sync_then_create_rule_works(old_db_missing_template_and_sender):
    """老 DB 升级后,新规则创建必须成功 — 这是用户最初报错的核心场景。"""
    from src.models import init_all_tables
    from src.models import subscription
    import src.models.database as db_mod

    init_all_tables()

    user_id = db_mod.query('SELECT id FROM users LIMIT 1')[0]['id']
    rule_id = subscription.create_rule(user_id, name='升级后新规则', template='brief')
    assert rule_id > 0
    row = db_mod.query('SELECT template FROM subscription_rules WHERE id=?', (rule_id,))[0]
    assert row['template'] == 'brief'


def test_old_db_sync_then_log_delivery_works(old_db_missing_template_and_sender):
    """老 DB 升级后,log_delivery 必须成功(不再报 no-such-column sender)。"""
    from src.models import init_all_tables
    from src.models import subscription
    import src.models.database as db_mod

    init_all_tables()

    delivery_id = subscription.log_delivery(
        snapshot_id=1, channel_id=1, channel_type='feishu',
        channel_name='cn', customer_id=1, status='pending',
        rule_id=1, recipient='r', sender='s@example.com',
    )
    assert delivery_id > 0
    row = db_mod.query('SELECT sender FROM delivery_log WHERE id=?', (delivery_id,))[0]
    assert row['sender'] == 's@example.com'


# ─────────────────────────────────────────────────────────────
# sync_table_columns 单元测试
# ─────────────────────────────────────────────────────────────

def test_sync_table_columns_noop_when_already_in_sync(fresh_db):
    """已经同步的 DB,再调 sync_table_columns 应该是 no-op(不打印 warning)。"""
    import src.models.database as db_mod
    from src.models.subscription import EXPECTED_SUBSCRIPTION_RULES_COLUMNS

    # fresh_db 跑过 init_all_tables,所有 EXPECTED 列都在
    con = sqlite3.connect(fresh_db)
    before = sorted(r[1] for r in con.execute('PRAGMA table_info(subscription_rules)').fetchall())

    db_mod.sync_table_columns(db_mod.get_db(), 'subscription_rules', EXPECTED_SUBSCRIPTION_RULES_COLUMNS)

    after = sorted(r[1] for r in con.execute('PRAGMA table_info(subscription_rules)').fetchall())
    assert before == after


def test_sync_table_columns_handles_missing_table(tmp_path, monkeypatch):
    """sync_table_columns 遇到表不存在(PRAGMA 报错),不能 raise,要 warn 跳过。"""
    import src.models.database as db_mod
    db_file = tmp_path / 'no_table.db'
    monkeypatch.setattr(db_mod, 'DB_PATH', str(db_file))
    if hasattr(db_mod._local, 'conn'):
        monkeypatch.setattr(db_mod._local, 'conn', None)
    db_mod.get_db()

    # 必须不抛异常
    db_mod.sync_table_columns(
        db_mod.get_db(), 'nonexistent_table',
        [('col1', 'TEXT', "''")]
    )