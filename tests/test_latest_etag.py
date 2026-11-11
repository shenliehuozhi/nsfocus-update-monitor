"""GET /api/latest/snapshots 条件请求：ETag 随 DB 文件戳变化，未变更时回 304。"""

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
    # init_all_tables seeds bundled products from data/initial_sources.json;
    # clear them so the fixture's ids and counts are deterministic.
    execute("DELETE FROM content_sources")
    execute(
        "INSERT INTO content_sources (id, name, source_type, is_active, package_type) "
        "VALUES (1, 'WAF', 'nsfocus', 1, '{\"types\":[],\"paths\":[],\"modes\":{}}')"
    )
    execute(
        "INSERT INTO snapshots (id, source_id, product_name, version_branch, package_type, "
        "package_version, file_name, md5_hash, status) "
        "VALUES (1, 1, 'WAF', 'V6.0', 'sys', 'V6.0.9', 'a.bin', 'md5', 'active')"
    )

    from src.web.routes import api_routes
    app = Flask(__name__)
    app.register_blueprint(api_routes.bp_latest)
    return app.test_client()


def _auth():
    from src.web.auth import create_token
    return {'Authorization': 'Bearer ' + create_token(1, 'tester')}


def _get(client, extra=None):
    headers = _auth()
    headers.update(extra or {})
    return client.get('/api/latest/snapshots', headers=headers)


def test_etag_and_revalidate(client):
    r = _get(client)
    assert r.status_code == 200
    etag = r.headers.get('ETag')
    assert etag
    assert r.headers['Cache-Control'] == 'private, no-cache'
    assert r.get_json()['data']['1']['snapshots'][0]['file_name'] == 'a.bin'

    r2 = _get(client, {'If-None-Match': etag})
    assert r2.status_code == 304
    assert r2.get_data() == b''
    assert r2.headers.get('ETag') == etag
    assert r2.headers['Cache-Control'] == 'private, no-cache'


def test_etag_changes_after_write(client):
    etag = _get(client).headers['ETag']

    from src.models.database import execute
    execute(
        "INSERT INTO snapshots (id, source_id, product_name, version_branch, package_type, "
        "package_version, file_name, md5_hash, status) "
        "VALUES (2, 1, 'WAF', 'V6.0', 'sys', 'V6.0.10', 'b.bin', 'md5b', 'active')"
    )

    r = _get(client, {'If-None-Match': etag})
    assert r.status_code == 200
    assert r.headers['ETag'] != etag
    names = [s['file_name'] for s in r.get_json()['data']['1']['snapshots']]
    assert 'b.bin' in names


def test_etag_empty_source_map(client):
    from src.models.database import execute
    execute("UPDATE content_sources SET is_active = 0")

    r = _get(client)
    assert r.status_code == 200
    assert r.headers.get('ETag')
    assert r.get_json() == {'code': 0, 'data': {}}

    assert _get(client, {'If-None-Match': r.headers['ETag']}).status_code == 304
