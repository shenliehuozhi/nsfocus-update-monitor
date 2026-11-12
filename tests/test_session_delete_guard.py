"""Session 删除护栏：最后一个 active 采集 Session 必须二次确认。"""
import pytest
from flask import Flask

from tests.frontend_source import js_source


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
    execute("INSERT INTO users (id, username, password_hash) VALUES (2, 'v', 'x')")

    from src.web.routes import session_routes
    app = Flask(__name__)
    app.register_blueprint(session_routes.bp)
    return app.test_client()


def _token(user_id=1):
    from src.web.auth import create_token
    return create_token(user_id=user_id, username='tester')


def _add_session(status='active', purpose='collect', user_id=1, mode='standard'):
    from src.models.database import execute
    return execute(
        "INSERT INTO user_sessions (user_id, cookie_value, status, purpose, collect_mode) "
        "VALUES (?, 'enc', ?, ?, ?)",
        (user_id, status, purpose, mode),
    )


def _count(session_id):
    from src.models.database import query
    rows = query("SELECT COUNT(*) AS cnt FROM user_sessions WHERE id = ?", (session_id,))
    return rows[0]['cnt']


def _delete(client, session_id, force=False, user_id=1):
    url = f'/api/sessions/{session_id}' + ('?force=1' if force else '')
    return client.delete(url, headers={'Authorization': f'Bearer {_token(user_id)}'})


def _patch(client, session_id, payload, force=False, user_id=1):
    url = f'/api/sessions/{session_id}' + ('?force=1' if force else '')
    return client.patch(
        url,
        json=payload,
        headers={'Authorization': f'Bearer {_token(user_id)}'},
    )


def _purpose(session_id):
    from src.models.database import query
    rows = query("SELECT purpose FROM user_sessions WHERE id = ?", (session_id,))
    return rows[0]['purpose']


def test_last_active_collect_requires_confirmation(client):
    sid = _add_session()
    resp = _delete(client, sid)
    assert resp.status_code == 409
    body = resp.get_json()
    assert body['code'] == 40901
    assert body['data']['requires_confirmation'] is True
    assert '无法采集' in body['message']
    assert _count(sid) == 1

    resp = _delete(client, sid, force=True)
    assert resp.status_code == 200
    assert _count(sid) == 0


def test_other_active_collect_allows_direct_delete(client):
    first = _add_session()
    second = _add_session()
    resp = _delete(client, first)
    assert resp.status_code == 200
    assert _count(first) == 0
    assert _count(second) == 1


def test_expired_collect_and_discover_not_blocked(client):
    expired = _add_session(status='expired')
    discover = _add_session(purpose='discover')

    assert _delete(client, expired).status_code == 200
    assert _delete(client, discover).status_code == 200


def test_list_exposes_active_collect_count(client):
    _add_session()
    _add_session(status='expired')
    resp = client.get('/api/sessions', headers={'Authorization': f'Bearer {_token()}'})
    assert resp.status_code == 200
    pool = resp.get_json()['data']['pool_status']
    assert pool['active'] == 1
    assert pool['active_collect'] == 1


def test_cannot_delete_another_users_session(client):
    sid = _add_session(user_id=2)
    assert _delete(client, sid).status_code == 404
    assert _count(sid) == 1


def test_last_collect_change_to_discover_requires_confirmation(client):
    sid = _add_session()
    resp = _patch(client, sid, {'purpose': 'discover', 'collect_mode': 'standard'})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body['code'] == 40901
    assert body['data']['requires_confirmation'] is True
    assert '无法采集' in body['message']
    assert _purpose(sid) == 'collect'

    resp = _patch(client, sid, {'purpose': 'discover', 'collect_mode': 'standard'}, force=True)
    assert resp.status_code == 200
    assert resp.get_json()['data']['purpose'] == 'discover'
    assert _purpose(sid) == 'discover'


def test_last_collect_mode_change_is_allowed(client):
    sid = _add_session()
    resp = _patch(client, sid, {'collect_mode': 'vm'})
    assert resp.status_code == 200
    assert _purpose(sid) == 'collect'


def test_change_purpose_allowed_when_other_collect_exists(client):
    first = _add_session()
    _add_session()
    resp = _patch(client, first, {'purpose': 'discover'})
    assert resp.status_code == 200
    assert _purpose(first) == 'discover'


def test_cannot_patch_another_users_session(client):
    sid = _add_session(user_id=2)
    resp = _patch(client, sid, {'purpose': 'discover'}, user_id=1)
    assert resp.status_code == 404
    assert _purpose(sid) == 'collect'


def test_session_delete_ui_warns_last_collect():
    source = js_source()
    assert "const lastCollect=s.status==='active'&&s.purpose==='collect'&&pool.active_collect===1;" in source
    assert '删除最后一个可用的采集 Session？' in source
    assert '定时采集和手动采集都会失败' in source
    assert "'/sessions/'+id+(warn?'?force=1':'')" in source
    assert 'r.code===40901' in source


def test_session_type_edit_ui_warns_last_collect():
    source = js_source()
    assert 'data-on-click="editSesType"' in source
    assert 'data-args=\'' in source and 's.purpose,s.collect_mode,lastCollect' in source
    assert '把最后一个采集 Session 改为「发现」？' in source
    assert '修改后将没有可用于采集的 Session' in source
    assert "JSON.stringify({purpose:newP,collect_mode:newM})" in source
    assert 'protocol:newP' not in source
