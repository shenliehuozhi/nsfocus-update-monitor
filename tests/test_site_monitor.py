"""update.nsfocus.com 可用性探测：HTTP 判定、状态持久化、切换事件与调度注册。"""
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    import src.models.database as db_mod

    monkeypatch.setattr(db_mod, 'DB_PATH', str(tmp_path / 'site.db'))
    if hasattr(db_mod._local, 'conn'):
        monkeypatch.setattr(db_mod._local, 'conn', None)
    db_mod.get_db()
    from src.models import init_all_tables
    init_all_tables()
    return db_mod


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code
        self.closed = False

    def close(self):
        self.closed = True


def _mock_get(monkeypatch, result):
    import src.core.site_monitor as sm

    def fake_get(*args, **kwargs):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(sm.requests, 'get', fake_get)


def test_probe_up_records_latency_and_closes_response(monkeypatch):
    import src.core.site_monitor as sm

    resp = _Response(200)
    _mock_get(monkeypatch, resp)
    result = sm.probe()

    assert result['status'] == 'up'
    assert result['http_status'] == 200
    assert result['latency_ms'] >= 0
    assert result['error'] == ''
    assert resp.closed is True


@pytest.mark.parametrize('code,status', [(301, 'up'), (404, 'up'), (500, 'down'), (503, 'down')])
def test_probe_http_status_threshold(monkeypatch, code, status):
    import src.core.site_monitor as sm

    _mock_get(monkeypatch, _Response(code))
    assert sm.probe()['status'] == status


def test_probe_connection_error_is_down(monkeypatch):
    import src.core.site_monitor as sm

    _mock_get(monkeypatch, sm.requests.ConnectionError('name resolution failed'))
    result = sm.probe()

    assert result['status'] == 'down'
    assert result['http_status'] is None
    assert 'name resolution failed' in result['error']


def test_run_check_persists_and_counts_consecutive_failures(monkeypatch, db):
    import src.core.site_monitor as sm

    transitions = []
    monkeypatch.setattr(sm, '_emit_transition',
                        lambda prev, state: transitions.append((prev, state['status'])))

    _mock_get(monkeypatch, _Response(200))
    state = sm.run_check()
    assert state['status'] == 'up'
    assert state['consecutive_failures'] == 0
    assert state['last_ok_at'] and state['last_fail_at'] is None
    assert sm.get_status()['status'] == 'up'
    assert transitions == []

    _mock_get(monkeypatch, _Response(503))
    state = sm.run_check()
    assert state['status'] == 'down'
    assert state['consecutive_failures'] == 1
    assert state['last_fail_at']
    assert transitions == [('up', 'down')]

    state = sm.run_check()
    assert state['consecutive_failures'] == 2
    assert transitions == [('up', 'down')]  # 持续故障不重复告警

    _mock_get(monkeypatch, _Response(200))
    state = sm.run_check()
    assert state['status'] == 'up'
    assert state['consecutive_failures'] == 0
    assert transitions[-1] == ('down', 'up')


def test_run_check_appends_history(monkeypatch, db):
    import src.core.site_monitor as sm
    from src.models.site_probe import history

    _mock_get(monkeypatch, _Response(200))
    sm.run_check()

    points, summary = history(24)
    assert summary['samples'] == 1
    assert points[0]['s'] == 'up'
    assert points[0]['ms'] is not None


def test_transition_writes_system_event(monkeypatch, db):
    import src.core.site_monitor as sm
    from src.models.database import query

    _mock_get(monkeypatch, _Response(503))
    sm.run_check()

    rows = query("SELECT event_type, severity, product_name, message FROM system_event_log")
    assert len(rows) == 1
    assert rows[0]['event_type'] == 'site_unavailable'
    assert rows[0]['severity'] == 'ERROR'
    assert rows[0]['product_name'] == 'update.nsfocus.com'
    assert 'HTTP 503' in rows[0]['message']


def test_recovery_auto_resolves_site_unavailable(monkeypatch, db):
    import src.core.site_monitor as sm
    from src.models.database import query

    _mock_get(monkeypatch, _Response(503))
    sm.run_check()
    row = query("SELECT resolved_at FROM system_event_log "
                "WHERE event_type='site_unavailable'")[0]
    assert row['resolved_at'] is None

    _mock_get(monkeypatch, _Response(200))
    sm.run_check()
    row = query("SELECT resolved_at, resolved_by FROM system_event_log "
                "WHERE event_type='site_unavailable'")[0]
    assert row['resolved_at'] is not None
    assert row['resolved_by'] == 'auto'


def test_get_status_defaults_when_never_checked(db):
    import src.core.site_monitor as sm

    state = sm.get_status()
    assert state['status'] == 'unknown'
    assert state['url'] == sm.SITE_URL
    assert state['consecutive_failures'] == 0


def test_job_registered_even_when_scheduler_disabled(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler
    import src.core.scheduler as sched

    settings = {'scheduler_enabled': '0', 'collect_interval': '4',
                'heartbeat_enabled': '0'}
    monkeypatch.setattr(sched, '_get_setting',
                        lambda key, default='': settings.get(key, default))
    for name in ('_smart_collect', '_session_heartbeat', '_digest_check',
                 '_run_db_cleanup', '_channel_health_check', '_site_availability_check'):
        monkeypatch.setattr(sched, name, lambda *a, **k: None)

    s = BackgroundScheduler(max_workers=1)
    s.start()
    monkeypatch.setattr(sched, '_scheduler', s)
    try:
        sched.refresh_scheduler_jobs()
        job = s.get_job('nsfocus_site_check')
        assert job is not None
        assert job.trigger.interval == timedelta(minutes=5)
        assert job.next_run_time.tzinfo is not None
        assert job.next_run_time > datetime.now(timezone.utc)
        assert s.get_job('nsfocus_collect') is None

        settings['scheduler_enabled'] = '1'
        sched.refresh_scheduler_jobs()
        assert s.get_job('nsfocus_site_check') is not None
    finally:
        s.shutdown(wait=False)
