"""GET /api/metrics 回归：鉴权、时间窗、队列积压、采集耗时、存储与 Prometheus 输出。"""
from datetime import datetime, timedelta

import pytest
from flask import Flask


@pytest.fixture
def client(tmp_path, monkeypatch):
    import src.models.database as db_mod

    db_file = tmp_path / 'nsfocus_monitor.db'
    monkeypatch.setattr(db_mod, 'DB_PATH', str(db_file))
    if hasattr(db_mod._local, 'conn'):
        monkeypatch.setattr(db_mod._local, 'conn', None)
    db_mod.get_db()

    from src.models import init_all_tables
    init_all_tables()

    from src.models.database import execute
    execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'u', 'x')")
    execute("INSERT INTO snapshots (id, source_id, product_name, version_branch, "
            "package_type, package_version, file_name, md5_hash, status) "
            "VALUES (1, 1, 'WAF', 'V6.0', 'sys', 'V6.0.9', 'a.bin', 'md5', 'active')")
    execute("INSERT INTO subscription_rules (id, user_id, name, enabled, digest_mode) "
            "VALUES (3, 1, '周汇总', 1, 'weekly')")

    from src.web.routes import metrics_routes
    app = Flask(__name__)
    app.register_blueprint(metrics_routes.bp)
    return app.test_client()


def _token():
    from src.web.auth import create_token
    return create_token(user_id=1, username='tester')


def _ts(**delta):
    return (datetime.utcnow() - timedelta(**delta)).strftime('%Y-%m-%d %H:%M:%S')


def _delivery(status, channel_type, created_at, sent_at=None, snapshot_id=1):
    from src.models.database import execute
    execute(
        """INSERT INTO delivery_log
           (snapshot_id, rule_id, channel_id, channel_type, delivery_status,
            sent_at, created_at)
           VALUES (?, 3, 1, ?, ?, ?, ?)""",
        (snapshot_id, channel_type, status, sent_at, created_at),
    )


def _seed_deliveries():
    from src.models.database import execute
    execute("INSERT INTO customers (id, name) VALUES (1, 'C')")
    execute("INSERT INTO channels (id, user_id, type, name, config) VALUES (1, 1, 'wecom', 'W', '{}')")

    _delivery('sent', 'wecom', _ts(hours=1), _ts(hours=1))
    _delivery('failed', 'email', _ts(hours=2))
    _delivery('sent', 'dingtalk', _ts(days=10), _ts(days=10))
    # 上线 created_at 前的历史失败：不可按窗口统计，单独计数
    _delivery('failed', 'email', '', None)
    # 历史 sent 行 created_at 为空，回退到 sent_at 仍应计入窗口
    _delivery('sent', 'wecom', '', _ts(hours=3))


def _get(client, query='', headers=None):
    h = {'Authorization': f'Bearer {_token()}'}
    h.update(headers or {})
    resp = client.get(f'/api/metrics{query}', headers=h)
    assert resp.status_code == 200, resp.data[:500]
    return resp.get_json()['data']


def _seed_queue():
    from src.models.database import execute
    dig_old = _ts(minutes=90)
    execute("""INSERT INTO digest_queue (rule_id, snapshot_id, period_key, status, created_at)
               VALUES (3, 1, '2026-W45', 'pending', ?)""", (dig_old,))
    execute("""INSERT INTO digest_queue (rule_id, snapshot_id, period_key, status, created_at)
               VALUES (3, 1, '2026-W44', 'sent', ?)""", (_ts(days=7),))
    future = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
    past = (datetime.now() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
    execute("""INSERT INTO delayed_queue (snapshot_id, rule_id, push_after, status)
               VALUES (1, 3, ?, 'pending')""", (future,))
    execute("""INSERT INTO delayed_queue (snapshot_id, rule_id, push_after, status)
               VALUES (1, 3, ?, 'pending')""", (past,))
    execute("""INSERT INTO delayed_queue (snapshot_id, rule_id, push_after, status)
               VALUES (1, 3, ?, 'cancelled')""", (past,))


def _seed_events():
    from src.models.database import execute
    for duration, minutes in ((80, 60), (100, 30)):
        execute("""INSERT INTO system_event_log (event_type, severity, message, created_at)
                   VALUES ('collection_summary', 'INFO', ?, ?)""",
                (f'{{"duration_s": {duration}}}', _ts(minutes=minutes)))
    execute("""INSERT INTO system_event_log (event_type, severity, created_at)
               VALUES ('log_error', 'WARNING', ?)""", (_ts(hours=1),))


def test_requires_auth(client):
    resp = client.get('/api/metrics')
    assert resp.status_code == 401
    assert resp.get_json()['code'] == 40100


def test_static_metrics_token(client, monkeypatch):
    monkeypatch.setenv('MONITOR_METRICS_TOKEN', 'scrape-secret')

    ok = client.get('/api/metrics', headers={'X-Metrics-Token': 'scrape-secret'})
    assert ok.status_code == 200

    bad = client.get('/api/metrics', headers={'X-Metrics-Token': 'nope'})
    assert bad.status_code == 401


def test_jwt_still_works_when_static_token_configured(client, monkeypatch):
    monkeypatch.setenv('MONITOR_METRICS_TOKEN', 'scrape-secret')
    data = _get(client)
    assert data['window_hours'] == 24


def test_delivery_window_and_success_rate(client):
    _seed_deliveries()

    day = _get(client, '?hours=24')['delivery']
    assert (day['sent'], day['failed'], day['total']) == (2, 1, 3)
    assert day['success_rate'] == pytest.approx(round(2 / 3, 4))
    assert day['undated_failed'] == 1

    by_type = {c['channel_type']: c for c in day['by_channel']}
    assert by_type['wecom']['sent'] == 2
    assert by_type['email']['failed'] == 1
    assert 'dingtalk' not in by_type

    month = _get(client, '?hours=720')['delivery']
    assert (month['sent'], month['failed'], month['total']) == (3, 1, 4)
    assert {c['channel_type'] for c in month['by_channel']} == {'wecom', 'email', 'dingtalk'}


def test_invalid_window_falls_back_to_default(client):
    assert _get(client, '?hours=abc')['window_hours'] == 24
    assert _get(client, '?hours=0')['window_hours'] == 24
    assert _get(client, '?hours=999999')['window_hours'] == 24


def test_queue_backlog(client):
    _seed_queue()
    queues = _get(client)['queues']

    assert queues['digest']['pending'] == 1
    rule = queues['digest']['by_rule'][0]
    assert rule['rule_id'] == 3
    assert rule['rule_name'] == '周汇总'
    assert rule['pending'] == 1
    assert rule['oldest_age_minutes'] == pytest.approx(90, abs=2)

    assert queues['delayed']['pending'] == 2
    assert queues['delayed']['overdue'] == 1
    assert queues['delayed']['oldest_age_minutes'] is not None


def test_collection_duration_and_events(client):
    _seed_events()
    data = _get(client)

    assert data['collection']['last_duration_s'] == 100
    assert data['collection']['avg_duration_s'] == 90
    assert data['collection']['sources_total'] >= 1

    assert data['events']['by_severity']['WARNING'] == 1
    assert data['events']['by_severity']['INFO'] == 2
    assert data['events']['unacked']['WARNING'] == 1


def test_rules_and_storage(client):
    data = _get(client)
    assert data['rules']['total'] == 1
    assert data['rules']['enabled'] == 1
    assert data['rules']['digest_enabled'] == 1
    assert data['storage']['db_size_bytes'] > 0
    assert data['storage']['snapshots_total'] == 1
    assert data['storage']['snapshots_active'] == 1
    assert data['storage']['delivery_log_rows'] == 0


def test_prometheus_format(client):
    _seed_deliveries()
    _seed_queue()
    resp = client.get('/api/metrics?format=prometheus',
                      headers={'Authorization': f'Bearer {_token()}'})
    assert resp.status_code == 200
    assert resp.mimetype.startswith('text/plain')
    body = resp.get_data(as_text=True)
    assert 'nsfocus_queue_pending{queue="digest"} 1' in body
    assert 'nsfocus_delivery_undated_failed_total 1' in body
    assert 'nsfocus_delivery_success_rate{window_hours="24"}' in body


def test_log_delivery_stamps_created_at_for_failed_rows(client):
    from src.models.subscription import log_delivery
    from src.models.database import query

    log_delivery(snapshot_id=1, channel_id=1, channel_type='email',
                 status='failed', error='boom', rule_id=3)
    row = query("SELECT created_at, sent_at, delivery_status FROM delivery_log ORDER BY id DESC LIMIT 1")[0]
    assert row['delivery_status'] == 'failed'
    assert row['sent_at'] is None
    assert row['created_at']
