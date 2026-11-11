"""会话凭证：HttpOnly Cookie + CSRF 双提交。

浏览器不再持有可被 JS 读取的 token（localStorage 里只有升级前的迁移副本）；
Cookie 会被浏览器自动携带，所以非安全方法必须回传 X-CSRF-Token。
脚本/API 客户端继续用 Authorization: Bearer（不要求 CSRF）。
"""
import bcrypt
import pytest
from flask import Flask, jsonify


@pytest.fixture
def app(tmp_path, monkeypatch):
    import src.models.database as db_mod

    monkeypatch.setattr(db_mod, 'DB_PATH', str(tmp_path / 'nsfocus_monitor.db'))
    if hasattr(db_mod._local, 'conn'):
        monkeypatch.setattr(db_mod._local, 'conn', None)
    db_mod.get_db()

    from src.models import init_all_tables
    init_all_tables()
    from src.models.user import create_user
    create_user('admin', bcrypt.hashpw(b'secret', bcrypt.gensalt(rounds=4)).decode(),
                is_admin=True)

    from src.web.auth import require_auth
    from src.web.routes import auth_routes, metrics_routes

    app = Flask(__name__)
    app.register_blueprint(auth_routes.bp)
    app.register_blueprint(metrics_routes.bp)

    @app.post('/api/_test/mutate')
    @require_auth
    def _mutate():
        return jsonify({'code': 0})

    @app.get('/api/_test/read')
    @require_auth
    def _read():
        return jsonify({'code': 0})

    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, password='secret'):
    return client.post('/api/auth/login', json={'username': 'admin', 'password': password})


def _csrf(client):
    cookie = client.get_cookie('nsm_csrf')
    return cookie.value if cookie else ''


def _set_cookie_headers(resp):
    return resp.headers.getlist('Set-Cookie')


def test_login_issues_httponly_session_cookie(client):
    resp = _login(client)
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload['code'] == 0
    assert payload['data']['user']['username'] == 'admin'
    # 凭证不再出现在响应体里，避免 JS/日志拿到可转存的 token
    assert 'token' not in payload['data']

    cookies = '\n'.join(_set_cookie_headers(resp))
    assert 'nsm_session=' in cookies and 'HttpOnly' in cookies
    assert 'nsm_csrf=' in cookies
    assert 'SameSite=Lax' in cookies and 'Path=/' in cookies
    # CSRF Cookie 必须能被前端读取（不能 HttpOnly）
    csrf_lines = [c for c in _set_cookie_headers(resp) if c.startswith('nsm_csrf=')]
    assert csrf_lines and 'HttpOnly' not in csrf_lines[0]


def test_failed_login_sets_no_cookie(client):
    resp = _login(client, password='wrong')
    assert resp.status_code == 401
    assert not _set_cookie_headers(resp)


def test_cookie_authenticates_without_authorization_header(client):
    _login(client)
    resp = client.get('/api/auth/me')
    assert resp.status_code == 200
    assert resp.get_json()['data']['username'] == 'admin'


def test_unsafe_method_from_cookie_requires_csrf(client):
    _login(client)

    missing = client.post('/api/_test/mutate')
    assert missing.status_code == 403
    assert 'CSRF' in missing.get_json()['message']

    wrong = client.post('/api/_test/mutate', headers={'X-CSRF-Token': 'nope'})
    assert wrong.status_code == 403

    ok = client.post('/api/_test/mutate', headers={'X-CSRF-Token': _csrf(client)})
    assert ok.status_code == 200

    # GET 是安全方法，不需要 CSRF
    assert client.get('/api/_test/read').status_code == 200


def test_bearer_header_still_works_without_csrf(client):
    from src.web.auth import create_token
    token = create_token(1, 'admin')
    resp = client.post('/api/_test/mutate', headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 200


def test_logout_clears_cookies(client):
    _login(client)
    assert client.get('/api/auth/me').status_code == 200

    resp = client.post('/api/auth/logout', headers={'X-CSRF-Token': _csrf(client)})
    assert resp.status_code == 200
    assert client.get('/api/auth/me').status_code == 401


def test_bearer_me_upgrades_legacy_session_to_cookie(client):
    from src.web.auth import create_token
    token = create_token(1, 'admin')
    resp = client.get('/api/auth/me', headers={'Authorization': f'Bearer {token}'})
    assert resp.status_code == 200
    cookies = '\n'.join(_set_cookie_headers(resp))
    assert 'nsm_session=' in cookies and 'HttpOnly' in cookies


def test_metrics_accepts_session_cookie(client):
    _login(client)
    resp = client.get('/api/metrics')
    assert resp.status_code == 200
    assert resp.get_json()['code'] == 0
