"""GET /api/system/events 过滤回归：severity / since_hours / unacked / 总数。"""
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
    from src.web.routes.system_routes import _ensure_event_ack_column
    assert _ensure_event_ack_column()

    from src.models.database import execute
    execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'u', 'x')")

    from src.web.routes import system_routes
    app = Flask(__name__)
    app.register_blueprint(system_routes.bp)
    return app.test_client()


def _token():
    from src.web.auth import create_token
    return create_token(user_id=1, username='tester')


def _event(severity, created_at, acked_at=None):
    from src.models.database import execute
    execute(
        """INSERT INTO system_event_log
           (event_type, severity, message, created_at, acked_at)
           VALUES ('test', ?, '{}', ?, ?)""",
        (severity, created_at, acked_at),
    )


def _get(client, query):
    resp = client.get(
        '/api/system/events' + query,
        headers={'Authorization': f'Bearer {_token()}'},
    )
    assert resp.status_code == 200, resp.data[:300]
    return resp.get_json()['data']


def test_unacked_filter_and_all_time(client):
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    old = (datetime.utcnow() - timedelta(days=10)).strftime('%Y-%m-%d %H:%M:%S')
    _event('ERROR', old)
    _event('ERROR', now, now)
    _event('WARNING', now)

    all_unacked = _get(client, '?since_hours=0&unacked=1')
    assert all_unacked['total'] == 2
    assert {e['severity'] for e in all_unacked['events']} == {'ERROR', 'WARNING'}

    recent = _get(client, '?since_hours=24')
    assert recent['total'] == 2

    recent_unacked = _get(client, '?since_hours=24&unacked=1')
    assert recent_unacked['total'] == 1
    assert recent_unacked['events'][0]['severity'] == 'WARNING'

    errors = _get(client, '?since_hours=0&severity=ERROR')
    assert errors['total'] == 2


def test_invalid_since_hours_falls_back(client):
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    _event('INFO', now)
    assert _get(client, '?since_hours=abc')['total'] == 1
