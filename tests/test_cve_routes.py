"""CVE 接口：窗口口径、跨设备聚合、筛选、分页与鉴权。"""

from datetime import datetime, timedelta

import pytest
from flask import Flask


def _iso(days_ago: int) -> str:
    return (datetime.utcnow() - timedelta(days=days_ago)).strftime('%Y-%m-%dT%H:%M:%S')


def _space_now() -> str:
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')


@pytest.fixture
def client(tmp_path, monkeypatch):
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
        "VALUES (1, 'WAF', 'nsfocus', 1), (2, 'IDS', 'nsfocus', 1)"
    )
    # id, source_id, product, pkg_type, published, status
    snapshots = [
        (1, 1, 'WAF', '系统升级包', _iso(2), 'active'),
        (2, 2, 'IDS', '系统升级包', _iso(2), 'active'),
        (3, 1, 'WAF', '规则升级包', _iso(3), 'active'),
        (4, 1, 'WAF', '系统升级包', _iso(100), 'active'),
        (5, 1, 'WAF', '系统升级包', _iso(1), 'superseded'),
        (6, 1, 'WAF', '系统升级包', '', 'active'),
    ]
    for sid, src, product, pkg, published, status in snapshots:
        execute(
            "INSERT INTO snapshots (id, source_id, product_name, version_branch, package_type, "
            "file_name, md5_hash, description_raw, status, published_at, first_seen_at, "
            "source_url, path_id) "
            "VALUES (?, ?, ?, 'V6.0', ?, ?, ?, 'desc', ?, ?, ?, 'u', 'p')",
            (sid, src, product, pkg, f'pkg-{sid}.bin', f'md5-{sid}',
             status, published, _space_now() if sid == 6 else _iso(2)),
        )
    links = [(1, 'CVE-2026-1111'), (2, 'CVE-2026-1111'), (3, 'CVE-2026-2222'),
             (4, 'CVE-2026-2222'), (5, 'CVE-2026-3333'), (6, 'CVE-2026-4444'),
             (1, 'CVE-2026-5555')]
    for sid, cve in links:
        execute(
            "INSERT INTO snapshot_cve (snapshot_id, cve_id, evidence, confidence) "
            "VALUES (?, ?, 'evidence', 0.9)",
            (sid, cve),
        )
    metas = [
        ('CVE-2026-1111', '3.1', 9.8, 'critical', 'ok',
         'Remote code execution in an example product.',
         '示例产品远程代码执行漏洞', '示例产品存在远程代码执行漏洞，攻击者可执行任意命令。',
         'CNVD-2026-10001'),
        ('CVE-2026-2222', '3.1', 5.3, 'medium', 'ok',
         'Information disclosure in an example component.',
         '', '', ''),
        ('CVE-2026-3333', '3.1', 3.1, 'low', 'ok',
         'Minor denial of service in an example service.',
         '示例服务拒绝服务漏洞', '', 'CNVD-2026-10002'),
        ('CVE-2026-5555', '3.1', 7.5, 'high', 'rejected',
         'Rejected reason: not a valid vulnerability.',
         '', '', ''),
    ]
    for cve, ver, score, sev, status, description, name_zh, description_zh, cnvd_id in metas:
        execute(
            "INSERT INTO cve_meta (cve_id, cvss_version, cvss_score, severity, status, "
            "description_en, name_zh, description_zh, cnvd_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cve, ver, score, sev, status, description, name_zh, description_zh, cnvd_id),
        )

    from src.web.routes import cve_routes
    app = Flask(__name__)
    app.register_blueprint(cve_routes.bp)
    return app.test_client()


def _auth():
    from src.web.auth import create_token
    return {'Authorization': 'Bearer ' + create_token(1, 'tester')}


def _get(client, path, **params):
    return client.get('/api/cve/' + path, query_string=params, headers=_auth())


def test_summary_window_and_totals(client):
    r = _get(client, 'summary', days=30)
    assert r.status_code == 200
    data = r.get_json()['data']
    assert data['window_days'] == 30
    assert data['totals'] == {
        'links': 4, 'snapshots': 4, 'distinct_cves': 3,
        'scored_cves': 2, 'unscored_cves': 1, 'cnvd_cves': 1,
    }
    assert data['by_severity'] == {'critical': 1, 'high': 0, 'medium': 1, 'low': 0, 'unknown': 1}


def test_summary_aggregates_same_cve_across_devices(client):
    data = _get(client, 'summary', days=30).get_json()['data']
    top = {row['cve_id']: row for row in data['top_cves']}
    assert top['CVE-2026-1111']['device_count'] == 2
    assert top['CVE-2026-1111']['devices'] == ['IDS', 'WAF']
    assert top['CVE-2026-1111']['pkg_kinds'] == ['sys']
    assert top['CVE-2026-1111']['severity'] == 'critical'
    assert top['CVE-2026-1111']['description'] == 'Remote code execution in an example product.'
    assert top['CVE-2026-1111']['name_zh'] == '示例产品远程代码执行漏洞'
    assert top['CVE-2026-1111']['description_zh'] == '示例产品存在远程代码执行漏洞，攻击者可执行任意命令。'
    assert top['CVE-2026-1111']['cnvd_id'] == 'CNVD-2026-10001'
    assert top['CVE-2026-1111']['cnvd_url'] == (
        'https://www.cnvd.org.cn/flaw/show/CNVD-2026-10001')
    assert top['CVE-2026-2222']['name_zh'] == ''
    assert top['CVE-2026-2222']['cnvd_id'] == ''
    assert data['totals']['cnvd_cves'] == 1
    assert data['top_cves'][0]['cve_id'] == 'CVE-2026-1111'


def test_summary_by_device_and_package_type(client):
    data = _get(client, 'summary', days=30).get_json()['data']
    by_device = {row['product_name']: row for row in data['by_device']}
    assert by_device['WAF']['cves'] == 3
    assert by_device['WAF']['sys_links'] == 2
    assert by_device['WAF']['rule_links'] == 1
    by_pkg = {row['package_type']: row for row in data['by_package_type']}
    assert by_pkg['系统升级包']['links'] == 3
    assert by_pkg['规则升级包']['pkg_kind'] == 'rule'


def test_summary_trend_covers_window_and_zero_fills(client):
    data = _get(client, 'summary', days=30).get_json()['data']
    assert len(data['trend']) >= 30
    sums = {sev: sum(point[sev] for point in data['trend']) for sev in
            ('critical', 'high', 'medium', 'low', 'unknown')}
    assert sums['critical'] == 1
    assert sums['medium'] == 1
    assert sums['unknown'] == 1
    assert sums['high'] == 0


def test_list_keeps_one_row_per_snapshot_link(client):
    r = _get(client, 'list', days=30)
    data = r.get_json()['data']
    assert data['total'] == 4
    assert len(data['items']) == 4
    assert [item['cve_id'] for item in data['items']] == [
        'CVE-2026-1111', 'CVE-2026-1111', 'CVE-2026-2222', 'CVE-2026-4444',
    ]
    assert {item['product_name'] for item in data['items'] if item['cve_id'] == 'CVE-2026-1111'} == {'WAF', 'IDS'}
    assert all(item['description'] == 'Remote code execution in an example product.'
               for item in data['items'] if item['cve_id'] == 'CVE-2026-1111')
    first = next(item for item in data['items'] if item['cve_id'] == 'CVE-2026-1111')
    assert first['name_zh'] == '示例产品远程代码执行漏洞'
    assert first['description_zh'] == '示例产品存在远程代码执行漏洞，攻击者可执行任意命令。'
    assert first['cnvd_id'] == 'CNVD-2026-10001'
    assert first['cnvd_url'] == 'https://www.cnvd.org.cn/flaw/show/CNVD-2026-10001'


def test_list_excludes_rejected_and_out_of_window(client):
    data = _get(client, 'list', days=30, q='5555').get_json()['data']
    assert data['total'] == 0
    data = _get(client, 'list', days=30, q='3333').get_json()['data']
    assert data['total'] == 0


def test_list_severity_product_and_package_type_filters(client):
    data = _get(client, 'list', days=30, severity='unknown').get_json()['data']
    assert [item['cve_id'] for item in data['items']] == ['CVE-2026-4444']
    data = _get(client, 'list', days=30, product='WAF').get_json()['data']
    assert data['total'] == 3
    data = _get(client, 'list', days=30, package_type='规则升级包').get_json()['data']
    assert [item['cve_id'] for item in data['items']] == ['CVE-2026-2222']


def test_list_search_matches_cve_and_file_name(client):
    assert _get(client, 'list', days=30, q='2222').get_json()['data']['total'] == 1
    assert _get(client, 'list', days=30, q='pkg-6.bin').get_json()['data']['total'] == 1
    assert _get(client, 'list', days=30, q='remote code').get_json()['data']['total'] == 2
    assert _get(client, 'list', days=30, q='远程代码').get_json()['data']['total'] == 2
    assert _get(client, 'list', days=30, q='CNVD-2026-10001').get_json()['data']['total'] == 2
    assert _get(client, 'list', days=30, q='%').get_json()['data']['total'] == 0


def test_list_pagination(client):
    data = _get(client, 'list', days=30, page=2, limit=2).get_json()['data']
    assert data['total'] == 4
    assert len(data['items']) == 2
    assert data['page'] == 2


def test_days_are_clamped(client):
    assert _get(client, 'summary', days=9999).get_json()['data']['window_days'] == 365
    assert _get(client, 'summary', days=0).get_json()['data']['window_days'] == 1


def test_invalid_severity_rejected(client):
    r = _get(client, 'list', severity='urgent')
    assert r.status_code == 400
    assert r.get_json()['code'] == 1


def test_auth_required(client):
    r = client.get('/api/cve/summary')
    assert r.status_code == 401


def test_empty_tables_return_zeros(client):
    from src.models.database import execute
    execute("DELETE FROM snapshot_cve")
    execute("DELETE FROM cve_meta")

    data = _get(client, 'summary', days=30).get_json()['data']
    assert data['totals']['links'] == 0
    assert data['totals']['distinct_cves'] == 0
    assert data['top_cves'] == []
    assert data['by_device'] == []
    assert data['by_severity'] == {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'unknown': 0}
