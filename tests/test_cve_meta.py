"""NVD 评分同步：解析优先级、落库、退避、限流与批量选择。"""

from datetime import timedelta

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv('MONITOR_NVD_API_KEY', raising=False)
    monkeypatch.delenv('MONITOR_CVE_LOOKUP', raising=False)


@pytest.fixture
def db(tmp_path, monkeypatch):
    import src.models.database as db_mod

    try:
        db_mod.close_db()
    except Exception:
        pass
    monkeypatch.setattr(db_mod, 'DB_PATH', str(tmp_path / 'nsfocus_monitor.db'))
    if hasattr(db_mod._local, 'conn'):
        monkeypatch.setattr(db_mod._local, 'conn', None)
    db_mod.get_db()

    from src.models import init_all_tables
    init_all_tables()

    from src.models.database import execute
    execute("DELETE FROM content_sources")
    execute(
        "INSERT INTO content_sources (id, name, source_type, is_active) "
        "VALUES (1, 'WAF', 'nsfocus', 1)"
    )
    return db_mod


def _insert_snapshot(sid, status='active', published_at='2026-11-14T00:00:00'):
    from src.models.database import execute
    execute(
        "INSERT INTO snapshots (id, source_id, product_name, version_branch, package_type, "
        "file_name, md5_hash, description_raw, status, source_url, path_id, published_at) "
        "VALUES (?, 1, 'WAF', 'V6.0', 'sys', ?, 'md5', 'desc', ?, 'u', 'p', ?)",
        (sid, f'f{sid}.bin', status, published_at),
    )


def _link(sid, cve_id):
    from src.models.database import execute
    execute(
        "INSERT INTO snapshot_cve (snapshot_id, cve_id, evidence, confidence) "
        "VALUES (?, ?, 'evidence', 0.8)",
        (sid, cve_id),
    )


def _meta(cve_id):
    from src.models.database import query
    rows = query("SELECT * FROM cve_meta WHERE cve_id = ?", (cve_id,))
    return rows[0] if rows else None


def _payload(metrics=None, status='Analyzed', desc='A vulnerability.'):
    return {
        'vulnerabilities': [{
            'cve': {
                'id': 'CVE-2026-0001',
                'vulnStatus': status,
                'published': '2024-01-02T03:04:05.123',
                'lastModified': '2024-02-03T04:05:06.000',
                'descriptions': [{'lang': 'en', 'value': desc}],
                'metrics': metrics or {},
            },
        }],
    }


def _v3(score, severity='HIGH', version='3.1', type_='Primary'):
    return {
        'type': type_,
        'cvssData': {
            'version': version,
            'vectorString': 'CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H',
            'baseScore': score,
            'baseSeverity': severity,
        },
    }


def test_parse_prefers_primary_and_higher_cvss_version():
    from src.core.cve_meta import parse_nvd_payload

    payload = _payload({'cvssMetricV31': [
        _v3(5.0, 'MEDIUM', type_='Secondary'),
        _v3(9.8, 'CRITICAL', type_='Primary'),
    ]})
    parsed = parse_nvd_payload(payload)
    assert parsed['cvss_version'] == '3.1'
    assert parsed['cvss_score'] == 9.8
    assert parsed['severity'] == 'critical'
    assert parsed['published_at'] == '2024-01-02T03:04:05'
    assert parsed['status'] == 'ok'

    v4 = _payload({
        'cvssMetricV31': [_v3(9.8, 'CRITICAL')],
        'cvssMetricV40': [_v3(8.0, 'HIGH', version='4.0')],
    })
    assert parse_nvd_payload(v4)['cvss_version'] == '4.0'


def test_parse_v2_uses_metric_level_severity():
    from src.core.cve_meta import parse_nvd_payload

    payload = _payload({'cvssMetricV2': [{
        'type': 'Primary',
        'cvssData': {'version': '2.0', 'vectorString': 'AV:N/AC:L', 'baseScore': 5.0},
        'baseSeverity': 'MEDIUM',
    }]})
    parsed = parse_nvd_payload(payload)
    assert parsed['cvss_version'] == '2.0'
    assert parsed['cvss_score'] == 5.0
    assert parsed['severity'] == 'medium'


def test_parse_rejected_and_empty_payloads():
    from src.core.cve_meta import parse_nvd_payload

    rejected = parse_nvd_payload(_payload(status='Rejected'))
    assert rejected['status'] == 'rejected'
    assert rejected['severity'] == 'unknown'
    assert parse_nvd_payload({'vulnerabilities': []}) is None
    assert parse_nvd_payload({}) is None
    assert parse_nvd_payload(None) is None


def test_parse_without_metrics_is_ok_but_unscored():
    from src.core.cve_meta import parse_nvd_payload

    parsed = parse_nvd_payload(_payload())
    assert parsed['status'] == 'ok'
    assert parsed['cvss_score'] is None
    assert parsed['severity'] == 'unknown'
    assert parsed['description_en'] == 'A vulnerability.'


def test_sync_stores_score_and_skips_on_next_run(db, monkeypatch):
    import src.core.cve_meta as cm

    _insert_snapshot(1)
    _link(1, 'CVE-2026-0001')
    calls = []

    def fake_fetch(cve_id, timeout=None):
        calls.append(cve_id)
        return 200, _payload({'cvssMetricV31': [_v3(9.8, 'CRITICAL')]})

    monkeypatch.setattr(cm, 'fetch_nvd', fake_fetch)
    result = cm.sync_pending(limit=10)
    assert result['candidates'] == 1
    assert result['selected'] == 1
    assert result['ok'] == 1
    row = _meta('CVE-2026-0001')
    assert row['status'] == 'ok'
    assert row['cvss_score'] == 9.8
    assert row['severity'] == 'critical'
    assert row['cvss_version'] == '3.1'
    assert row['source'] == 'nvd'
    assert row['source_url'] == 'https://nvd.nist.gov/vuln/detail/CVE-2026-0001'
    assert row['next_retry_at'] is None
    assert row['fetched_at']

    again = cm.sync_pending(limit=10)
    assert again['candidates'] == 0
    assert again['selected'] == 0
    assert calls == ['CVE-2026-0001']


def test_sync_404_marks_not_found_and_backs_off(db, monkeypatch):
    import src.core.cve_meta as cm
    from src.core.timeutil import parse_ts

    _insert_snapshot(1)
    _link(1, 'CVE-2026-4040')
    calls = []
    monkeypatch.setattr(cm, 'fetch_nvd',
                        lambda cve_id, timeout=None: (calls.append(cve_id) or (404, None)))

    first = cm.sync_pending(limit=10)
    assert first['not_found'] == 1
    row = _meta('CVE-2026-4040')
    assert row['status'] == 'not_found'
    assert row['retry_count'] == 1
    assert parse_ts(row['next_retry_at']) > parse_ts(row['fetched_at'])

    second = cm.sync_pending(limit=10)
    assert second['candidates'] == 0
    assert len(calls) == 1


def test_sync_network_error_is_retried_after_backoff(db, monkeypatch):
    import src.core.cve_meta as cm

    _insert_snapshot(1)
    _link(1, 'CVE-2026-5000')
    calls = []

    def fail(cve_id, timeout=None):
        calls.append(cve_id)
        raise cm.requests.ConnectionError('boom')

    monkeypatch.setattr(cm, 'fetch_nvd', fail)
    first = cm.sync_pending(limit=10)
    assert first['errors'] == 1
    row = _meta('CVE-2026-5000')
    assert row['status'] == 'error'
    assert row['retry_count'] == 1
    assert row['next_retry_at']

    second = cm.sync_pending(limit=10)
    assert second['selected'] == 0
    assert len(calls) == 1


def test_sync_rate_limit_stops_batch(db, monkeypatch):
    import src.core.cve_meta as cm

    _insert_snapshot(1)
    _link(1, 'CVE-2026-1111')
    _link(1, 'CVE-2026-2222')
    calls = []

    def limited(cve_id, timeout=None):
        calls.append(cve_id)
        return 429, None

    monkeypatch.setattr(cm, 'fetch_nvd', limited)
    result = cm.sync_pending(limit=10)
    assert result['rate_limited'] is True
    assert len(calls) == 1
    assert _meta(calls[0])['status'] == 'error'
    assert _meta(calls[0])['next_retry_at']


def test_sync_force_refetches_ok_rows(db, monkeypatch):
    import src.core.cve_meta as cm

    _insert_snapshot(1)
    _link(1, 'CVE-2026-0001')
    calls = []

    def fake_fetch(cve_id, timeout=None):
        calls.append(cve_id)
        return 200, _payload({'cvssMetricV31': [_v3(7.5, 'HIGH')]})

    monkeypatch.setattr(cm, 'fetch_nvd', fake_fetch)
    cm.sync_pending(limit=10)
    result = cm.sync_pending(limit=10, force=True)
    assert result['selected'] == 1
    assert len(calls) == 2
    assert _meta('CVE-2026-0001')['cvss_score'] == 7.5


def test_sync_failure_keeps_existing_score(db, monkeypatch):
    import src.core.cve_meta as cm

    _insert_snapshot(1)
    _link(1, 'CVE-2026-0001')
    monkeypatch.setattr(cm, 'fetch_nvd',
                        lambda cve_id, timeout=None: (200, _payload(
                            {'cvssMetricV31': [_v3(9.8, 'CRITICAL')]})))
    cm.sync_pending(limit=10)

    def fail(cve_id, timeout=None):
        raise cm.requests.Timeout('timeout')

    monkeypatch.setattr(cm, 'fetch_nvd', fail)
    cm.sync_pending(limit=10, force=True)
    row = _meta('CVE-2026-0001')
    assert row['status'] == 'error'
    assert row['cvss_score'] == 9.8
    assert row['severity'] == 'critical'


def test_sync_respects_batch_limit_and_active_snapshots(db, monkeypatch):
    import src.core.cve_meta as cm

    _insert_snapshot(1)
    _insert_snapshot(2, status='withdrawn')
    for i in range(1, 6):
        _link(1, f'CVE-2026-100{i}')
    _link(2, 'CVE-2026-9999')
    calls = []
    monkeypatch.setattr(cm, 'fetch_nvd',
                        lambda cve_id, timeout=None: (calls.append(cve_id) or (404, None)))

    result = cm.sync_pending(limit=2)
    assert result['candidates'] == 5
    assert result['selected'] == 2
    assert len(calls) == 2
    assert 'CVE-2026-9999' not in calls


def test_sync_disabled_by_env_makes_no_requests(db, monkeypatch):
    import src.core.cve_meta as cm

    _insert_snapshot(1)
    _link(1, 'CVE-2026-0001')
    monkeypatch.setenv('MONITOR_CVE_LOOKUP', 'off')
    calls = []
    monkeypatch.setattr(cm, 'fetch_nvd',
                        lambda cve_id, timeout=None: (calls.append(cve_id) or (200, _payload())))

    result = cm.sync_pending()
    assert result['skipped'] is True
    assert calls == []


def test_headers_include_key_only_when_configured(monkeypatch):
    import src.core.cve_meta as cm

    assert 'apiKey' not in cm._headers()
    monkeypatch.setenv('MONITOR_NVD_API_KEY', 'secret-key')
    assert cm._headers()['apiKey'] == 'secret-key'
    assert cm.default_batch_limit() == cm.BATCH_LIMIT_KEY


def test_rate_limit_waits_between_requests(monkeypatch):
    import src.core.cve_meta as cm

    sleeps = []
    now = {'t': 100.0}
    monkeypatch.setattr(cm.time, 'monotonic', lambda: now['t'])
    monkeypatch.setattr(cm.time, 'sleep', lambda s: sleeps.append(s))
    monkeypatch.setattr(cm, '_last_request_at', 99.5)
    monkeypatch.setattr(cm, 'MIN_INTERVAL_NO_KEY', 1.0)

    cm._rate_limit()
    assert sleeps and abs(sleeps[0] - 0.5) < 1e-6
    now['t'] = 100.4
    cm._rate_limit()
    assert len(sleeps) == 2
    assert abs(sleeps[1] - 0.6) < 1e-6
    now['t'] = 101.4
    cm._rate_limit()
    assert len(sleeps) == 2


def test_cve_jobs_registered_even_when_scheduler_disabled(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler
    import src.core.scheduler as sched

    settings = {'scheduler_enabled': '0', 'collect_interval': '4',
                'heartbeat_enabled': '0'}
    monkeypatch.setattr(sched, '_get_setting',
                        lambda key, default='': settings.get(key, default))
    for name in ('_smart_collect', '_session_heartbeat', '_digest_check',
                 '_run_db_cleanup', '_channel_health_check', '_site_availability_check',
                 '_run_db_health', '_run_db_backup', '_run_cve_extract',
                 '_run_cve_extract_full', '_run_cve_meta_sync', '_run_cnvd_import'):
        monkeypatch.setattr(sched, name, lambda *a, **k: None)

    s = BackgroundScheduler(max_workers=1)
    s.start()
    monkeypatch.setattr(sched, '_scheduler', s)
    try:
        sched.refresh_scheduler_jobs()
        extract = s.get_job('nsfocus_cve_extract')
        meta = s.get_job('nsfocus_cve_meta')
        full = s.get_job('nsfocus_cve_extract_full')
        cnvd = s.get_job('nsfocus_cnvd_import')
        assert extract is not None and extract.trigger.interval == timedelta(minutes=10)
        assert meta is not None and meta.trigger.interval == timedelta(minutes=5)
        assert full is not None
        assert cnvd is not None and cnvd.trigger.interval == timedelta(minutes=60)
        assert s.get_job('nsfocus_collect') is None
    finally:
        s.shutdown(wait=False)


def test_cleanup_removes_orphan_cve_rows(db, monkeypatch):
    import src.core.scheduler as sched
    from src.models.database import execute, query

    _insert_snapshot(1)
    _link(1, 'CVE-2026-0001')
    execute(
        "INSERT INTO cve_scan_state (snapshot_id, desc_hash, desc_len, cve_count) "
        "VALUES (1, 'hash', 4, 1)"
    )
    execute("DELETE FROM snapshots WHERE id = 1")
    monkeypatch.setattr('src.core.event_handler.emit_cleanup_summary',
                        lambda summary: None)

    sched._run_db_cleanup()

    assert query("SELECT COUNT(*) AS c FROM snapshot_cve")[0]['c'] == 0
    assert query("SELECT COUNT(*) AS c FROM cve_scan_state")[0]['c'] == 0


def test_sync_5xx_stops_batch_and_uses_short_retry(db, monkeypatch):
    import src.core.cve_meta as cm
    from src.core.timeutil import parse_ts

    _insert_snapshot(1)
    _link(1, 'CVE-2026-3001')
    _link(1, 'CVE-2026-3002')
    calls = []

    monkeypatch.setattr(cm, 'fetch_nvd',
                        lambda cve_id, timeout=None: (calls.append(cve_id) or (503, None)))

    result = cm.sync_pending(limit=10)
    assert result['errors'] == 1
    assert len(calls) == 1
    row = _meta(calls[0])
    assert row['status'] == 'error'
    delta = parse_ts(row['next_retry_at']) - parse_ts(row['fetched_at'])
    assert timedelta(minutes=14) <= delta <= timedelta(minutes=16)


def test_sync_network_timeout_stops_batch(db, monkeypatch):
    import src.core.cve_meta as cm

    _insert_snapshot(1)
    _link(1, 'CVE-2026-4001')
    _link(1, 'CVE-2026-4002')
    calls = []

    def timeout(cve_id, timeout=None):
        calls.append(cve_id)
        raise cm.requests.Timeout('read timeout=15')

    monkeypatch.setattr(cm, 'fetch_nvd', timeout)
    result = cm.sync_pending(limit=10)
    assert result['errors'] == 1
    assert len(calls) == 1
    assert _meta(calls[0])['status'] == 'error'


def test_sync_invalid_json_is_transient_not_not_found(db, monkeypatch):
    import src.core.cve_meta as cm

    _insert_snapshot(1)
    _link(1, 'CVE-2026-6001')
    monkeypatch.setattr(cm, 'fetch_nvd', lambda cve_id, timeout=None: (200, None))

    result = cm.sync_pending(limit=10)
    assert result['errors'] == 1
    assert result['not_found'] == 0
    row = _meta('CVE-2026-6001')
    assert row['status'] == 'error'
    assert row['next_retry_at']
