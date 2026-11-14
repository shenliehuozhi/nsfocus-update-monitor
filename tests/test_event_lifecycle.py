"""事件生命周期：open → acked → resolved、自愈 resolve、保留期清理。"""
from datetime import datetime, timedelta

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
    execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'u', 'x')")

    from src.web.routes import system_routes
    app = Flask(__name__)
    app.register_blueprint(system_routes.bp)
    return app.test_client()


def _token():
    from src.web.auth import create_token
    return create_token(user_id=1, username='tester')


def _headers():
    return {'Authorization': f'Bearer {_token()}'}


def _insert(severity='ERROR', created_at=None, acked_at=None, resolved_at=None,
            resolved_by=None, event_type='test', message='{}'):
    from src.models.database import execute
    if created_at is None:
        created_at = datetime.utcnow().isoformat()
    return execute(
        """INSERT INTO system_event_log
           (event_type, severity, message, created_at, acked_at,
            resolved_at, resolved_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (event_type, severity, message, created_at, acked_at,
         resolved_at, resolved_by),
    )


def _row(event_id):
    from src.models.database import query
    return query("SELECT * FROM system_event_log WHERE id = ?", (event_id,))[0]


def _get(client, query):
    resp = client.get('/api/system/events' + query, headers=_headers())
    assert resp.status_code == 200, resp.data[:300]
    return resp.get_json()['data']


def _post(client, path, payload=None):
    resp = client.post(path, json=payload or {}, headers=_headers())
    assert resp.status_code == 200, resp.data[:300]
    return resp.get_json()


def test_state_filter_open_acked_resolved(client):
    open_id = _insert('ERROR')
    acked_id = _insert('WARNING', acked_at=datetime.utcnow().isoformat())
    resolved_id = _insert('ERROR', acked_at=datetime.utcnow().isoformat(),
                          resolved_at=datetime.utcnow().isoformat(),
                          resolved_by='user:1')

    data = _get(client, '?since_hours=0')
    states = {e['id']: e['state'] for e in data['events']}
    assert states[open_id] == 'open'
    assert states[acked_id] == 'acked'
    assert states[resolved_id] == 'resolved'

    assert {e['id'] for e in _get(client, '?since_hours=0&state=open')['events']} == {open_id}
    assert {e['id'] for e in _get(client, '?since_hours=0&state=acked')['events']} == {acked_id}
    assert {e['id'] for e in _get(client, '?since_hours=0&state=resolved')['events']} == {resolved_id}


def test_resolved_events_are_not_unacked(client):
    resolved_id = _insert('ERROR', acked_at=datetime.utcnow().isoformat(),
                          resolved_at=datetime.utcnow().isoformat(),
                          resolved_by='auto')
    open_id = _insert('ERROR')

    unacked = _get(client, '?since_hours=0&unacked=1')
    assert [e['id'] for e in unacked['events']] == [open_id]
    assert unacked['unread_counts']['unread_total'] == 1

    data = _get(client, '?since_hours=0&unacked=1')
    assert all(e['id'] != resolved_id for e in data['events'])


def test_resolve_endpoint_marks_acked_and_resolved(client):
    event_id = _insert('ERROR')

    body = _post(client, f'/api/system/events/{event_id}/resolve')
    assert body['code'] == 0
    assert body['data']['resolved'] == 1

    row = _row(event_id)
    assert row['resolved_at'] is not None
    assert row['acked_at'] is not None, '已解决必须同时视为已读'
    assert row['resolved_by'] == 'user:1'

    # 幂等：再次 resolve 不重复计数
    assert _post(client, f'/api/system/events/{event_id}/resolve')['data']['resolved'] == 0


def test_ack_does_not_touch_resolved(client):
    event_id = _insert('ERROR', resolved_at=datetime.utcnow().isoformat(),
                       acked_at=datetime.utcnow().isoformat(),
                       resolved_by='auto')
    before = _row(event_id)['acked_at']

    _post(client, f'/api/system/events/{event_id}/ack')
    assert _row(event_id)['acked_at'] == before


def test_resolve_all_respects_severity_and_time(client):
    now = datetime.utcnow().isoformat()
    old = (datetime.utcnow() - timedelta(days=10)).isoformat()
    recent_error = _insert('ERROR', created_at=now)
    old_error = _insert('ERROR', created_at=old)
    recent_warning = _insert('WARNING', created_at=now)

    body = _post(client, '/api/system/events/resolve-all',
                 {'severity': 'ERROR', 'since_hours': 24})
    assert body['data']['resolved'] == 1

    assert _row(recent_error)['resolved_at'] is not None
    assert _row(old_error)['resolved_at'] is None
    assert _row(recent_warning)['resolved_at'] is None


def test_resolve_all_skips_already_resolved(client):
    event_id = _insert('ERROR', resolved_at=datetime.utcnow().isoformat(),
                       acked_at=datetime.utcnow().isoformat(),
                       resolved_by='user:1')
    body = _post(client, '/api/system/events/resolve-all', {'since_hours': 0})
    assert body['data']['resolved'] == 0
    assert _row(event_id)['resolved_by'] == 'user:1'


def test_cleanup_old_events_retention(client):
    from src.models.event_log import cleanup_old_events

    now = datetime.utcnow()
    old_181 = (now - timedelta(days=181)).isoformat()
    old_366 = (now - timedelta(days=366)).isoformat()
    recent = (now - timedelta(days=1)).isoformat()

    resolved_181 = _insert('ERROR', created_at=old_181,
                           acked_at=old_181, resolved_at=old_181,
                           resolved_by='user:1')
    info_181 = _insert('INFO', created_at=old_181)
    unresolved_error_181 = _insert('ERROR', created_at=old_181)
    unresolved_error_366 = _insert('ERROR', created_at=old_366)
    resolved_recent = _insert('ERROR', created_at=recent,
                              acked_at=recent, resolved_at=recent,
                              resolved_by='auto')

    removed = cleanup_old_events(resolved_days=180, info_days=180,
                                 unresolved_days=365)
    assert removed == 3

    from src.models.database import query
    ids = {r['id'] for r in query("SELECT id FROM system_event_log")}
    assert resolved_181 not in ids
    assert info_181 not in ids
    assert unresolved_error_366 not in ids
    assert unresolved_error_181 in ids, '未解决告警保留 365 天'
    assert resolved_recent in ids


def test_db_health_auto_resolves_db_lock_events(client):
    from src.core.db_health import check

    db_error = _insert(
        'CRITICAL', event_type='log_error',
        message='{"error_type": "DB错误", "keyword": "database is locked"}')
    other_error = _insert(
        'WARNING', event_type='log_error',
        message='{"error_type": "网络错误", "keyword": "timeout"}')

    result = check()
    assert result['ok'] is True

    assert _row(db_error)['resolved_at'] is not None
    assert _row(db_error)['resolved_by'] == 'auto'
    assert _row(other_error)['resolved_at'] is None, '只自动解决 DB 锁相关事件'


def test_db_health_skips_when_local_write_queue_busy(client, caplog):
    import logging
    import threading

    from src.core.db_health import check
    from src.models import database as db_mod

    db_error = _insert(
        'CRITICAL', event_type='log_error',
        message='{"error_type": "DB错误", "keyword": "database is locked"}')
    held, release = threading.Event(), threading.Event()

    def hold_queue():
        with db_mod._write_lock:
            held.set()
            release.wait(5)

    worker = threading.Thread(target=hold_queue, daemon=True)
    worker.start()
    try:
        assert held.wait(5)
        with caplog.at_level(logging.DEBUG, logger='db_health'):
            result = check(timeout=0.05)
    finally:
        release.set()
        worker.join(5)

    assert result == {'ok': False, 'skipped': True,
                      'error': 'local write queue busy'}
    assert _row(db_error)['resolved_at'] is None, '队列繁忙不能误判恢复'
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    assert any('skipped' in r.message for r in caplog.records)


def test_db_health_reports_sqlite_lock_and_keeps_event_open(client):
    import sqlite3

    from src.core.db_health import check
    from src.models import database as db_mod

    db_error = _insert(
        'CRITICAL', event_type='log_error',
        message='{"error_type": "DB错误", "keyword": "database is locked"}')
    blocker = sqlite3.connect(db_mod.DB_PATH, timeout=5, isolation_level=None)
    try:
        blocker.execute('BEGIN IMMEDIATE')
        result = check(timeout=0.05)
    finally:
        blocker.execute('ROLLBACK')
        blocker.close()

    assert result['ok'] is False
    assert result['skipped'] is False
    assert 'locked' in result['error'].lower()
    assert _row(db_error)['resolved_at'] is None


def test_db_health_probe_excluded_from_write_stats(client):
    from src.core.db_health import check
    from src.models import database as db_mod

    before = db_mod.write_stats()
    result = check()
    after = db_mod.write_stats()

    assert result['ok'] is True
    assert result['skipped'] is False
    assert after['operations'] == before['operations']
    assert after['sample_count'] == before['sample_count']


def test_db_health_job_registered_even_when_scheduler_disabled(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler
    import src.core.scheduler as sched

    settings = {'scheduler_enabled': '0', 'collect_interval': '4',
                'heartbeat_enabled': '0'}
    monkeypatch.setattr(sched, '_get_setting',
                        lambda key, default='': settings.get(key, default))
    for name in ('_smart_collect', '_session_heartbeat', '_digest_check',
                 '_run_db_cleanup', '_channel_health_check',
                 '_site_availability_check', '_run_db_backup', '_run_db_health'):
        monkeypatch.setattr(sched, name, lambda *a, **k: None)

    s = BackgroundScheduler(max_workers=1)
    s.start()
    monkeypatch.setattr(sched, '_scheduler', s)
    try:
        sched.refresh_scheduler_jobs()
        job = s.get_job('nsfocus_db_health')
        assert job is not None
        assert job.trigger.interval == timedelta(minutes=10)
    finally:
        s.shutdown(wait=False)
