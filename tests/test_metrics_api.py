"""GET /api/metrics 回归：鉴权、时间窗、队列积压、采集耗时、存储与 Prometheus 输出。"""
from datetime import datetime, timedelta, timezone

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
    future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
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

    # Prometheus authorization.credentials_file 只会发 Bearer 头
    bearer_ok = client.get('/api/metrics',
                           headers={'Authorization': 'Bearer scrape-secret'})
    assert bearer_ok.status_code == 200

    bad = client.get('/api/metrics', headers={'X-Metrics-Token': 'nope'})
    assert bad.status_code == 401

    bearer_bad = client.get('/api/metrics',
                            headers={'Authorization': 'Bearer nope'})
    assert bearer_bad.status_code == 401


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


def test_collection_last_run_hydrates_from_db(client, monkeypatch):
    import src.core.scheduler as sched
    from src.models.database import execute

    ts = '2026-11-11T00:00:00.123456'
    execute("INSERT OR REPLACE INTO system_settings (key, value) "
            "VALUES ('last_quick_scan_at', ?)", (ts,))
    monkeypatch.setattr(sched, '_last_quick_run', None)
    monkeypatch.setattr(sched, '_last_full_run', None)

    col = _get(client)['collection']
    assert col['last_quick_run'] == ts
    assert col['last_run'] == ts


def test_collection_live_duration_and_next_run(client, monkeypatch):
    import src.core.scheduler as sched

    monkeypatch.setattr(sched, '_is_running', True)
    monkeypatch.setattr(sched, '_progress', {
        'active': True, 'mode': 'quick', 'phase': 'collecting',
        'started_at': _ts(minutes=2).replace(' ', 'T'),
        'duration_s': 0, 'items_collected': 107,
    })
    monkeypatch.setattr(sched, 'get_job_status', lambda: [
        {'id': 'nsfocus_collect', 'next_run': '2026-11-11T05:00:00Z', 'seconds_until': 100},
    ])

    col = _get(client)['collection']
    assert col['is_running'] is True
    assert col['current']['items_collected'] == 107
    assert col['current']['duration_s'] == pytest.approx(120, abs=5)
    assert col['next_run'] == '2026-11-11T05:00:00Z'


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
    assert 'nsfocus_delivery_undated_failed 1' in body
    assert 'nsfocus_delivery_success_rate{window_hours="24"}' in body


def test_prometheus_is_well_formed(client):
    """每行符合 name{labels} value；每个 family 有且仅有一条 TYPE；无 _total Gauge。"""
    import re

    _seed_deliveries()
    _seed_queue()
    resp = client.get('/api/metrics?format=prometheus',
                      headers={'Authorization': f'Bearer {_token()}'})
    lines = resp.get_data(as_text=True).splitlines()
    sample_re = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{.*\})? (\S+)$')

    types = {}
    seen = set()
    for line in lines:
        if line.startswith('# TYPE '):
            _, _, name, kind = line.split(' ')
            assert name not in types, f'重复 TYPE: {name}'
            assert kind == 'gauge'
            types[name] = kind
            continue
        assert not line.startswith('#'), f'未声明的注释行: {line}'
        m = sample_re.match(line)
        assert m, f'非法样本行: {line}'
        name, labels = m.group(1), m.group(2) or ''
        assert name in types, f'样本缺少 TYPE 声明: {name}'
        assert not name.endswith('_total'), f'窗口统计不应使用 counter 命名: {name}'
        assert (name, labels) not in seen, f'重复序列: {line}'
        seen.add((name, labels))

    assert 'nsfocus_delivery_events' in types
    assert 'nsfocus_delivery_undated_failed' in types


def test_today_updates_sessions_and_process(client):
    from src.models.database import execute
    execute("INSERT OR IGNORE INTO content_sources (id, name, source_type, is_active) "
            "VALUES (1, 'WAF', 'nsfocus', 1)")
    execute("UPDATE snapshots SET published_at = datetime('now') WHERE id = 1")
    execute("""INSERT INTO snapshots (id, source_id, product_name, version_branch,
               package_type, package_version, file_name, md5_hash, status, published_at)
               VALUES (2, 1, 'IDS', 'V6.0', 'sys', 'V6.0.9', 'b.bin', 'md5-2', 'active',
                       datetime('now', '-10 days'))""")
    execute("""INSERT INTO delivery_log
               (snapshot_id, rule_id, channel_id, channel_type, delivery_status, created_at)
               VALUES (1, 3, 1, 'wecom', 'sent', datetime('now'))""")

    data = _get(client)
    today = data['today']
    assert today['delivery']['total'] == 1
    assert today['delivery']['sent'] == 1
    assert today['delivery']['success_rate'] == 1.0

    updates = data['updates']
    assert updates['window_hours'] == 24
    assert updates['total'] == 1
    assert [(p['name'], p['count']) for p in updates['items']] == [('WAF', 1)]
    assert updates['items'][0]['last_published_at']

    week = _get(client, '?hours=168')['updates']
    assert week['total'] == 1
    assert [p['name'] for p in week['items']] == ['WAF']

    month = _get(client, '?hours=720')['updates']
    assert month['total'] == 2
    assert {p['name']: p['count'] for p in month['items']} == {'IDS': 1, 'WAF': 1}

    assert data['sessions'] == {
        'total': 0, 'active': 0, 'expired': 0, 'unknown': 0,
        'active_but_expired': 0, 'collect_total': 0, 'collect_active': 0,
        'collect_polluted': 0, 'discover_active': 0,
    }
    assert data['process']['started_at']
    assert data['process']['uptime_seconds'] >= 0


def test_today_uses_cst_midnight_and_timestamp_fallback(client, monkeypatch):
    from datetime import timezone
    from src.web.routes import metrics_routes as metrics

    now = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(metrics, 'utc_now', lambda: now)
    _delivery('sent', 'wecom', '2026-11-11T16:00:00', '2026-11-11T16:00:00')
    _delivery('failed', 'email', '2026-11-12 03:59:59')
    _delivery('sent', 'wecom', '', '2026-11-12T08:00:00+08:00')
    _delivery('sent', 'wecom', None, '2026-11-12T04:00:00Z')
    _delivery('failed', 'email', '2026-11-11 15:59:59')
    _delivery('failed', 'email', '')
    _delivery('failed', 'email', '2026-11-12 04:00:01')
    _delivery('failed', 'email', '2026-11-12 16:00:00')

    today = _get(client, '?hours=6')['today']
    assert today == _get(client, '?hours=720')['today']
    assert today['date'] == '2026-11-12'
    assert today['delivery'] == {'total': 4, 'sent': 3, 'failed': 1, 'success_rate': 0.75}


def test_today_products_use_publication_time_and_normalize_latest(client, monkeypatch):
    from datetime import timezone
    from src.models.database import execute
    from src.web.routes import metrics_routes as metrics

    monkeypatch.setattr(metrics, 'utc_now', lambda: datetime(2026, 11, 12, 4, tzinfo=timezone.utc))
    execute("UPDATE snapshots SET published_at='2026-11-11T16:00:00' WHERE id=1")
    rows = [
        ('WAF', '2026-11-12T00:00:00'),
        ('WAF', '2026-11-12 03:00:00'),
        ('IDS', '2026-11-12T11:59:59+08:00'),
        ('Yesterday', '2026-11-11T15:59:59'),
        ('Future', '2026-11-12 04:00:01'),
        ('Invalid', 'not-a-date'),
        ('Undated', ''),
    ]
    for i, (name, ts) in enumerate(rows, start=2):
        execute("""INSERT INTO snapshots
                   (id, source_id, product_name, version_branch, package_type,
                    package_version, file_name, md5_hash, status, published_at)
                   VALUES (?, 1, ?, 'V6', 'sys', 'v1', ?, ?, 'active', ?)""",
                (i, name, f'{i}.bin', str(i), ts))
    today = metrics._today()
    assert (today['product_count'], today['package_count']) == (2, 4)
    assert today['products'] == [
        {'name': 'IDS', 'count': 1, 'last_published_at': '2026-11-12 03:59:59'},
        {'name': 'WAF', 'count': 3, 'last_published_at': '2026-11-12 03:00:00'},
    ]


def test_today_empty_is_not_a_zero_percent_failure(client):
    today = _get(client)['today']
    assert today['delivery'] == {'total': 0, 'sent': 0, 'failed': 0, 'success_rate': None}
    assert today['products'] == []
    assert today['product_count'] == today['package_count'] == 0


def test_session_counts_include_pollution_and_match_pool_eligibility(client):
    from src.models.database import execute

    for purpose, status, heartbeat in [
        ('collect', 'active', '正常'), ('collect', 'active', '错误'),
        ('collect', '污染', ''), ('collect', 'expired', '污染'),
        ('collect', 'unknown', ''), ('discover', 'active', '正常'),
    ]:
        execute("""INSERT INTO user_sessions
                   (user_id, cookie_value, purpose, status, heartbeat_status, expires_at)
                   VALUES (1, 'test-secret-not-for-metrics', ?, ?, ?, '2020-01-01')""",
                (purpose, status, heartbeat))
    data = _get(client)['sessions']
    assert data == {
        'total': 6, 'active': 3, 'expired': 1, 'unknown': 1,
        'active_but_expired': 1, 'collect_total': 5, 'collect_active': 2,
        'collect_polluted': 2, 'discover_active': 1,
    }
    assert 'test-secret-not-for-metrics' not in str(data)


def test_process_uses_configured_start_and_stable_fallback(client, monkeypatch):
    from datetime import timezone
    from unittest.mock import Mock
    from src.web.routes import metrics_routes as metrics

    start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    client.application.config['VERSION_INFO'] = {'process_start': start}
    process = _get(client)['process']
    assert process['started_at'] == start
    assert process['uptime_seconds'] == pytest.approx(7200, abs=2)
    with monkeypatch.context() as patch:
        patch.setattr(metrics.os, 'stat', Mock(side_effect=OSError))
        assert metrics._process()['started_at'] == metrics._process()['started_at']
    client.application.config['VERSION_INFO']['process_start'] = 'invalid'
    assert _get(client)['process']['uptime_seconds'] is None


def test_customers_follow_hours_window(client):
    from src.models.database import execute
    execute("INSERT INTO customers (id, name) VALUES (1, 'C')")
    execute("INSERT INTO customers (id, name) VALUES (2, 'D')")
    execute("""INSERT INTO delivery_log
               (snapshot_id, customer_id, rule_id, channel_id, channel_type,
                delivery_status, sent_at, created_at)
               VALUES (1, 1, 3, 1, 'wecom', 'sent',
                       datetime('now', '-1 hour'), datetime('now', '-1 hour'))""")
    execute("""INSERT INTO delivery_log
               (snapshot_id, customer_id, rule_id, channel_id, channel_type,
                delivery_status, sent_at, created_at)
               VALUES (1, 1, 3, 1, 'wecom', 'failed',
                       datetime('now', '-10 days'), datetime('now', '-10 days'))""")

    day = _get(client)['customers']
    assert day['window_hours'] == 24
    by_name = {c['name']: c for c in day['items']}
    assert (by_name['C']['total'], by_name['C']['success'], by_name['C']['failed']) == (1, 1, 0)
    assert by_name['D']['total'] == 0

    week = _get(client, '?hours=168')['customers']
    assert week['window_hours'] == 168
    by_name = {c['name']: c for c in week['items']}
    assert by_name['C']['total'] == 1

    month = _get(client, '?hours=720')['customers']
    assert month['window_hours'] == 720
    by_name = {c['name']: c for c in month['items']}
    assert (by_name['C']['total'], by_name['C']['failed']) == (2, 1)

    assert _get(client, '?hours=abc')['customers']['window_hours'] == 24
    assert _get(client, '?hours=999999')['customers']['window_hours'] == 24


def test_scheduler_jobs_keep_known_jobs_when_stopped(monkeypatch):
    import src.core.scheduler as sched
    from src.web.routes.metrics_routes import _SCHEDULER_JOB_IDS, _scheduler_jobs

    monkeypatch.setattr(sched, 'get_job_status', lambda: [])
    jobs = _scheduler_jobs({'enabled': False})

    assert [j['id'] for j in jobs] == list(_SCHEDULER_JOB_IDS)
    assert {j['status'] for j in jobs} == {'stopped'}
    assert all(j['next_run'] is None and j['seconds_until'] is None for j in jobs)


def test_scheduler_jobs_status_running_and_disabled(monkeypatch):
    import src.core.scheduler as sched
    from src.web.routes.metrics_routes import _scheduler_jobs

    monkeypatch.setattr(sched, 'get_job_status', lambda: [{
        'id': 'nsfocus_collect',
        'next_run': '2026-11-11T05:00:00Z',
        'seconds_until': 100,
    }])
    by_id = {j['id']: j for j in _scheduler_jobs({'enabled': True})}

    assert by_id['nsfocus_collect']['status'] == 'running'
    assert by_id['nsfocus_collect']['seconds_until'] == 100
    assert by_id['nsfocus_heartbeat']['status'] == 'disabled'


def test_prometheus_includes_today_and_session_gauges(client):
    _seed_deliveries()
    resp = client.get('/api/metrics?format=prometheus',
                      headers={'Authorization': f'Bearer {_token()}'})
    body = resp.get_data(as_text=True)
    assert 'nsfocus_delivery_today_events ' in body
    assert 'nsfocus_products_updated{window_hours="24"} ' in body
    assert 'nsfocus_sessions{status="active"} ' in body
    assert 'nsfocus_process_uptime_seconds ' in body


def test_prometheus_includes_db_write_gauges(client, monkeypatch):
    import src.models.database as db_mod

    stats = {
        'operations': 120, 'errors': 3, 'timeouts': 2,
        'python_lock_timeouts': 1, 'sqlite_busy_errors': 1,
        'sample_count': 120, 'sample_limit': 1024,
        'wait_budget_seconds': 10.0, 'timeout_rate': 2 / 120,
        'lock_wait_ms': {'p50': 1.5, 'p95': 12.0, 'p99': 30.0},
        'sqlite_ms': {'p50': 2.5, 'p95': 6.0, 'p99': None},
        'total_ms': {'p50': 4.0, 'p95': 18.0, 'p99': 40.0},
    }
    monkeypatch.setattr(db_mod, 'write_stats', lambda: stats)

    assert _get(client)['database'] == stats
    body = client.get('/api/metrics?format=prometheus',
                      headers={'Authorization': f'Bearer {_token()}'}).get_data(as_text=True)
    assert 'nsfocus_db_write_operations 120' in body
    assert 'nsfocus_db_write_errors 3' in body
    assert 'nsfocus_db_write_timeouts{reason="python_lock"} 1' in body
    assert 'nsfocus_db_write_timeouts{reason="sqlite_busy"} 1' in body
    assert f'nsfocus_db_write_timeout_rate {2 / 120}' in body
    assert 'nsfocus_db_write_wait_budget_seconds 10.0' in body
    assert 'nsfocus_db_write_samples{scope="count"} 120' in body
    assert 'nsfocus_db_write_samples{scope="limit"} 1024' in body
    assert 'nsfocus_db_write_lock_wait_ms{quantile="p50"} 1.5' in body
    assert 'nsfocus_db_write_lock_wait_ms{quantile="p99"} 30.0' in body
    assert 'nsfocus_db_write_sqlite_ms{quantile="p95"} 6.0' in body
    assert 'nsfocus_db_write_sqlite_ms{quantile="p99"} NaN' in body
    assert 'nsfocus_db_write_total_ms{quantile="p95"} 18.0' in body


def test_prometheus_db_write_gauges_before_first_write(client, monkeypatch):
    import src.models.database as db_mod

    empty = {
        'operations': 0, 'errors': 0, 'timeouts': 0,
        'python_lock_timeouts': 0, 'sqlite_busy_errors': 0,
        'sample_count': 0, 'sample_limit': 1024,
        'wait_budget_seconds': 10.0, 'timeout_rate': 0.0,
        'lock_wait_ms': {'p50': None, 'p95': None, 'p99': None},
        'sqlite_ms': {'p50': None, 'p95': None, 'p99': None},
        'total_ms': {'p50': None, 'p95': None, 'p99': None},
    }
    monkeypatch.setattr(db_mod, 'write_stats', lambda: empty)

    body = client.get('/api/metrics?format=prometheus',
                      headers={'Authorization': f'Bearer {_token()}'}).get_data(as_text=True)
    assert 'nsfocus_db_write_operations 0' in body
    assert 'nsfocus_db_write_timeout_rate 0.0' in body
    for stage in ('lock_wait', 'sqlite', 'total'):
        for quantile in ('p50', 'p95', 'p99'):
            assert f'nsfocus_db_write_{stage}_ms{{quantile="{quantile}"}} NaN' in body


def test_maintenance_and_site_metrics(client, monkeypatch):
    from src.models.database import execute
    import src.core.site_monitor as sm

    execute("INSERT OR REPLACE INTO system_settings (key, value) "
            "VALUES ('maintenance_mode', '1')")
    monkeypatch.setattr(sm, 'get_status', lambda: {
        'url': 'https://update.nsfocus.com/',
        'status': 'down',
        'http_status': 503,
        'latency_ms': 1234,
        'checked_at': _ts(minutes=2),
        'error': 'HTTP 503',
        'consecutive_failures': 2,
        'last_ok_at': _ts(hours=1),
        'last_fail_at': _ts(minutes=2),
    })

    data = _get(client)
    assert data['maintenance'] == {'enabled': True}
    assert data['site']['status'] == 'down'
    assert data['site']['consecutive_failures'] == 2

    body = client.get('/api/metrics?format=prometheus',
                      headers={'Authorization': f'Bearer {_token()}'}).get_data(as_text=True)
    assert 'nsfocus_maintenance_mode 1' in body
    assert 'nsfocus_site_up{url="https://update.nsfocus.com/"} 0' in body
    assert 'nsfocus_site_http_status{url="https://update.nsfocus.com/"} 503' in body
    assert 'nsfocus_site_latency_ms{url="https://update.nsfocus.com/"} 1234' in body


def test_maintenance_defaults_off_and_site_unknown(client):
    data = _get(client)
    assert data['maintenance'] == {'enabled': False}
    assert data['site']['status'] == 'unknown'
    assert data['site']['url'] == 'https://update.nsfocus.com/'
    assert data['site']['history'] == []
    assert data['site']['history_summary']['samples'] == 0
    assert data['site']['history_hours'] == 24


def test_site_history_follows_hours_window(client):
    from datetime import timezone
    from src.models.site_probe import record

    def iso(**delta):
        return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat(timespec='seconds')

    record({'status': 'up', 'latency_ms': 100, 'http_status': 200,
            'checked_at': iso(hours=1), 'error': ''})
    record({'status': 'down', 'latency_ms': 500, 'http_status': 503,
            'checked_at': iso(hours=2), 'error': 'HTTP 503'})
    record({'status': 'up', 'latency_ms': 300, 'http_status': 200,
            'checked_at': iso(days=10), 'error': ''})

    site = _get(client)['site']
    assert site['history_hours'] == 24
    assert site['history_summary'] == {
        'samples': 2, 'failures': 1, 'avg_ms': 100, 'max_ms': 100,
    }
    assert [p['s'] for p in site['history']] == ['down', 'up']

    month = _get(client, '?hours=720')['site']
    assert month['history_hours'] == 720
    assert month['history_summary']['samples'] == 3
    assert month['history_summary']['max_ms'] == 300


def test_prometheus_omits_site_history(client):
    from src.web.routes.metrics_routes import collect_metrics

    assert 'history' not in collect_metrics(24, include_history=False)['site']
    body = client.get('/api/metrics?format=prometheus',
                      headers={'Authorization': f'Bearer {_token()}'}).get_data(as_text=True)
    assert 'nsfocus_site_up{' in body


def test_prometheus_escapes_label_values(client):
    from src.web.routes.metrics_routes import collect_metrics, render_prometheus

    data = collect_metrics(24)
    data['queues']['digest']['by_rule'] = [{
        'rule_id': 1, 'rule_name': 'a"b\nc', 'pending': 2, 'oldest_age_minutes': 1,
    }]
    body = render_prometheus(data)
    assert 'rule_name="a\\"b\\nc"' in body


def test_log_delivery_stamps_created_at_for_failed_rows(client):
    from src.models.subscription import log_delivery
    from src.models.database import query

    log_delivery(snapshot_id=1, channel_id=1, channel_type='email',
                 status='failed', error='boom', rule_id=3)
    row = query("SELECT created_at, sent_at, delivery_status FROM delivery_log ORDER BY id DESC LIMIT 1")[0]
    assert row['delivery_status'] == 'failed'
    assert row['sent_at'] is None
    assert row['created_at']
