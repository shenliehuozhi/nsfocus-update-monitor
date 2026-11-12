"""客户月度服务报告接口测试：鉴权、月份边界与汇总。"""
import json

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
    execute(
        "INSERT INTO customers (id, name, owned_products) VALUES (1, 'C1', ?)",
        (json.dumps([
            {'product': 'WAF', 'version': 'V6.0.8'},
            {'product': 'IPS', 'version': '5.6.11'},
            {'product': 'RSAS', 'version': ''},
            {'product': 'NOT_MONITORED', 'version': '1.0'},
        ], ensure_ascii=False),),
    )
    execute(
        "INSERT INTO snapshots (id, source_id, product_name, version_branch, package_type, "
        "package_version, file_name, md5_hash, published_at, status) "
        "VALUES (1, 1, 'WAF', 'WAF V6.0.9', '系统升级包', 'V6.0R09F00H02', "
        "'w.bin', 'm1', '2026-09-03T09:24:13', 'active')"
    )
    execute(
        "INSERT INTO snapshots (id, source_id, product_name, version_branch, package_type, "
        "package_version, file_name, md5_hash, published_at, status) "
        "VALUES (2, 1, 'IPS', '海光U系列（V5.6.11）', '引擎升级包', '5.6.11.9.10901.441', "
        "'i.bin', 'm2', '2026-04-20T08:25:09', 'active')"
    )
    execute(
        "INSERT INTO snapshots (id, source_id, product_name, version_branch, package_type, "
        "package_version, file_name, md5_hash, published_at, status) "
        "VALUES (3, 1, 'RSAS', 'V6.0R04F05', '系统升级包', 'V6.0R04F05SP04', "
        "'r.bin', 'm3', '2026-10-30T02:32:00', 'active')"
    )
    # 2026-10 与 2026-11 的推送流水:时间统一按 UTC 存,月份按 UTC+8 划分。
    for row in (
        # Nov 2 01:00 CST
        (1, 1, 'sent', '2026-11-01T17:00:00', '2026-11-01 17:00:00'),
        # Oct 31 23:30 CST → 属于 10 月
        (2, 1, 'sent', '2026-10-31T15:30:00', '2026-10-31 15:30:00'),
        # created_at 为空时回退 sent_at;Nov 10 11:00 CST
        (3, 2, 'failed', '2026-11-10T03:00:00', ''),
        # 边界:Oct 31 16:00 UTC == Nov 1 00:00 CST → 属于 11 月
        (4, 3, 'sent', '2026-10-31T16:00:00', '2026-10-31 16:00:00'),
    ):
        execute(
            "INSERT INTO delivery_log (id, snapshot_id, channel_type, customer_id, "
            "delivery_status, sent_at, created_at) VALUES (?, ?, 'email', 1, ?, ?, ?)",
            row,
        )

    from src.web.routes.api_routes import bp_customers
    app = Flask(__name__)
    app.register_blueprint(bp_customers)
    return app.test_client()


def _token():
    from src.web.auth import create_token
    return create_token(user_id=1, username='tester')


def _report(client, month=None, customer_id=1):
    url = f'/api/customers/{customer_id}/service-report'
    if month:
        url += f'?month={month}'
    resp = client.get(url, headers={'Authorization': f'Bearer {_token()}'})
    return resp


def test_service_report_requires_auth(client):
    assert client.get('/api/customers/1/service-report').status_code == 401


def test_service_report_month_boundary_utc_plus_8(client):
    resp = _report(client, '2026-11')
    assert resp.status_code == 200, resp.data[:500]
    data = resp.get_json()['data']

    assert data['customer']['name'] == 'C1'
    assert data['month'] == '2026-11'
    assert data['month_label'] == '2026年11月'
    assert data['summary'] == {
        'total': 3, 'success': 2, 'failed': 1, 'success_rate': 66.7,
    }
    assert data['truncated'] is False

    products = [d['product_name'] for d in data['deliveries']]
    assert products == ['IPS', 'WAF', 'RSAS']
    assert data['deliveries'][0]['status'] == 'failed'
    # created_at 为空时回退 sent_at
    assert data['deliveries'][0]['sent_at'] == '2026-11-10T03:00:00'


def test_service_report_previous_month(client):
    data = _report(client, '2026-10').get_json()['data']
    assert data['summary'] == {
        'total': 1, 'success': 1, 'failed': 0, 'success_rate': 100.0,
    }
    assert [d['product_name'] for d in data['deliveries']] == ['WAF']


def test_service_report_defaults_to_current_cst_month(client):
    from datetime import datetime, timedelta, timezone
    expected = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m')
    data = _report(client).get_json()['data']
    assert data['month'] == expected


def test_service_report_rejects_bad_month_and_missing_customer(client):
    assert _report(client, '2026-13').status_code == 400
    assert _report(client, '2026-1').status_code == 400
    assert _report(client, '2026-11', customer_id=999).status_code == 404
