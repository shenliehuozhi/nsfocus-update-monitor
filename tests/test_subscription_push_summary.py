"""订阅规则命中统计接口测试：时间窗、按用户隔离与失败投递计入命中。"""
import pytest
from flask import Flask


@pytest.fixture
def client(tmp_path, monkeypatch):
    import src.models.database as db_mod

    monkeypatch.setattr(db_mod, 'DB_PATH', str(tmp_path / 'nsfocus_monitor.db'))
    if hasattr(db_mod._local, 'conn'):
        monkeypatch.setattr(db_mod._local, 'conn', None)
    db_mod.get_db()

    from src.models import init_all_tables
    init_all_tables()

    from src.models.database import execute
    execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'u1', 'x')")
    execute("INSERT INTO users (id, username, password_hash) VALUES (2, 'u2', 'x')")
    execute("INSERT INTO subscription_rules (id, user_id, name) VALUES (1, 1, '规则A')")
    execute("INSERT INTO subscription_rules (id, user_id, name) VALUES (2, 1, '规则B')")
    execute("INSERT INTO subscription_rules (id, user_id, name) VALUES (3, 2, '他人规则')")
    for sid in (1, 2, 3):
        execute(
            "INSERT INTO snapshots (id, source_id, product_name, version_branch, package_type, "
            "package_version, file_name, md5_hash, published_at, status) "
            "VALUES (?, 1, 'WAF', 'WAF V6.0.9', '系统升级包', 'V6.0R09F00H02', "
            "?, ?, '2026-11-01T00:00:00', 'active')",
            (sid, f's{sid}.bin', f'm{sid}'),
        )

    def add_delivery(snapshot_id, rule_id, status, when):
        execute(
            "INSERT INTO delivery_log (snapshot_id, rule_id, channel_type, "
            "delivery_status, sent_at, created_at) "
            f"VALUES (?, ?, 'email', ?, {when}, {when})",
            (snapshot_id, rule_id, status),
        )

    # 规则 1：两个包、三次投递（失败也计入命中）
    add_delivery(1, 1, 'sent', "datetime('now', '-1 day')")
    add_delivery(1, 1, 'failed', "datetime('now', '-2 days')")
    add_delivery(2, 1, 'sent', "datetime('now', '-3 days')")
    # 规则 2：只有很久以前的一次命中
    add_delivery(3, 2, 'sent', "'2000-01-01T00:00:00'")
    # 他人规则的近期命中不能出现在当前用户结果中
    add_delivery(3, 3, 'sent', "datetime('now', '-1 day')")

    from src.web.routes.api_routes import bp_subscriptions
    app = Flask(__name__)
    app.register_blueprint(bp_subscriptions)
    return app.test_client()


def _get(client, days=30):
    from src.web.auth import create_token
    token = create_token(user_id=1, username='tester')
    return client.get(f'/api/subscriptions/push-summary?days={days}',
                      headers={'Authorization': f'Bearer {token}'})


def test_push_summary_requires_auth(client):
    assert client.get('/api/subscriptions/push-summary').status_code == 401


def test_push_summary_counts_packages_and_deliveries(client):
    resp = _get(client, 30)
    assert resp.status_code == 200
    items = {i['id']: i for i in resp.get_json()['data']['items']}

    rule1 = items[1]
    assert (rule1['packages'], rule1['deliveries']) == (2, 3)
    assert (rule1['sent'], rule1['failed']) == (2, 1)
    assert rule1['last_at']

    # 近 30 天未命中，但全部历史里有一次
    assert items[2]['packages'] == 0
    assert items[2]['deliveries'] == 0
    all_items = {i['id']: i for i in _get(client, 0).get_json()['data']['items']}
    assert all_items[2]['packages'] == 1

    # 按当前用户隔离
    assert 3 not in items
