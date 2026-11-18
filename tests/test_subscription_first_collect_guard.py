"""首采护栏：完成一轮采集前不允许创建/启用订阅规则，避免存量包被批量推送。"""
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
    execute("INSERT INTO customers (id, name) VALUES (1, '测试客户')")

    from src.web.routes.api_routes import bp_settings, bp_subscriptions
    app = Flask(__name__)
    app.register_blueprint(bp_subscriptions)
    app.register_blueprint(bp_settings)
    return app.test_client()


def _headers():
    from src.web.auth import create_token
    token = create_token(user_id=1, username='tester')
    return {'Authorization': f'Bearer {token}'}


def _create_rule(client):
    return client.post('/api/subscriptions', json={'name': '规则A', 'customer_id': 1},
                       headers=_headers())


def _seed_snapshot():
    from src.models.database import execute
    execute(
        "INSERT INTO snapshots (source_id, product_name, version_branch, package_type, "
        "file_name, md5_hash, status) VALUES (1, 'WAF', 'V6.0', 'sys', 'a.bin', 'md5', 'active')"
    )


def _seed_completion_marker():
    from src.models.database import execute
    execute(
        "INSERT OR REPLACE INTO system_settings (key, value, updated_at) "
        "VALUES ('last_quick_scan_at', '2026-11-18T10:00:00', datetime('now'))"
    )


def test_create_rule_blocked_before_first_collection(client):
    resp = _create_rule(client)
    assert resp.status_code == 409
    body = resp.get_json()
    assert '首次采集' in body['message']

    from src.models.database import query
    assert query("SELECT id FROM subscription_rules") == []


def test_create_rule_allowed_after_snapshot_baseline(client):
    _seed_snapshot()
    resp = _create_rule(client)
    assert resp.status_code == 200
    assert resp.get_json()['code'] == 0


def test_create_rule_allowed_with_completion_marker(client):
    _seed_completion_marker()
    resp = _create_rule(client)
    assert resp.status_code == 200
    assert resp.get_json()['code'] == 0


def test_toggle_enable_blocked_before_first_collection(client):
    from src.models.database import execute
    execute("INSERT INTO subscription_rules (id, user_id, name, enabled) VALUES (7, 1, '旧规则', 0)")

    resp = client.post('/api/subscriptions/7/toggle', headers=_headers())
    assert resp.status_code == 409
    assert '首次采集' in resp.get_json()['message']

    resp = client.post('/api/subscriptions/7/toggle', json={'enabled': 0}, headers=_headers())
    assert resp.status_code == 409

    # 已有规则的停用操作不应被拦
    execute("UPDATE subscription_rules SET enabled = 1 WHERE id = 7")
    resp = client.post('/api/subscriptions/7/toggle', headers=_headers())
    assert resp.status_code == 200
    assert resp.get_json()['data']['enabled'] is False


def test_scheduler_status_exposes_baseline_flag(client):
    def flag():
        resp = client.get('/api/settings/scheduler', headers=_headers())
        return resp.get_json()['data']['collection_baseline_ready']

    assert flag() is False
    _seed_snapshot()
    assert flag() is True
