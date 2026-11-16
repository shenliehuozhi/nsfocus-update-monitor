"""app.log 与 service_error.log 的同一条 WARNING 只生成一个事件。"""

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

    from src.web.routes import system_routes
    system_routes._log_alert_dedup.clear()

    app = Flask(__name__)
    app.register_blueprint(system_routes.bp)
    yield app.test_client()

    system_routes._log_alert_dedup.clear()


def _ingest(client, log_file, context):
    resp = client.post('/api/system/events/ingest', json={
        'log_file': log_file,
        'error_type': '网络错误',
        'keyword': 'Read timeout',
        'context': context,
        'line_number': 1,
    })
    assert resp.status_code == 200
    return resp.get_json()


def _count():
    from src.models.database import query
    return query(
        "SELECT COUNT(*) AS n FROM system_event_log WHERE event_type = 'log_error'"
    )[0]['n']


def test_same_context_from_two_files_is_deduped(client):
    context = (
        "2026-11-13 23:24:07 [WARNING] monitor.nsfocus: Quick RSAS: "
        "https://update.nsfocus.com/update/listRsasDetail/v/vulweb: "
        "HTTPSConnectionPool(host='update.nsfocus.com', port=443): "
        "Read timed out. (read timeout=5)"
    )
    first = _ingest(client, 'app.log', context)
    second = _ingest(client, 'service_error.log', context)

    assert first.get('skipped') is not True
    assert second.get('skipped') is True
    assert _count() == 1


def test_different_context_is_not_deduped(client):
    first = _ingest(
        client, 'app.log',
        "2026-11-13 23:24:07 [WARNING] monitor.nsfocus: "
        "Quick RSAS: https://update.nsfocus.com/a: Read timed out. (read timeout=5)",
    )
    second = _ingest(
        client, 'app.log',
        "2026-11-13 23:24:12 [WARNING] monitor.nsfocus: "
        "Quick RSAS: https://update.nsfocus.com/b: Read timed out. (read timeout=5)",
    )

    assert first.get('skipped') is not True
    assert second.get('skipped') is not True
    assert _count() == 2
