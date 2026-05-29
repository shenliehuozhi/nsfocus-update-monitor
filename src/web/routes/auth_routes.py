"""Auth routes."""

from flask import Blueprint, request, jsonify, g
import bcrypt

from src.web.auth import create_token, require_auth
from src.models.user import get_by_username, create_user as create_user_db, is_ip_banned, record_login_failure, clear_login_failure

bp = Blueprint('auth', __name__, url_prefix='/api/auth')


def _get_client_ip() -> str:
    """Get client IP, respecting X-Forwarded-For if configured behind proxy."""
    return request.headers.get('X-Forwarded-For', request.remote_addr) or ''


def _audit(user_id, action: str, details: dict = None):
    """Log audit entry. Ignores errors (best-effort)."""
    try:
        from src.models.audit import log
        ip = _get_client_ip()
        log(user_id, action, details or {}, ip)
    except Exception:
        pass


import logging
_logger = logging.getLogger('monitor.auth')

@bp.route('/login', methods=['POST'])
def login():
    import time as _t; _t0 = _t.time(); _logger.info(f'[LOGIN] start')
    client_ip = _get_client_ip()
    _logger.info(f'[LOGIN] ip={client_ip}')

    # Check if IP is banned (brute-force protection)
    _logger.info(f'[LOGIN] checking is_ip_banned')
    if is_ip_banned(client_ip):
        return jsonify({'code': 42900, 'message': '登录尝试次数过多，请15分钟后再试'}), 429
    _logger.info(f'[LOGIN] is_ip_banned passed, took {_t.time()-_t0:.3f}s')

    data = request.get_json() or {}
    username = data.get('username', '')
    password = data.get('password', '')
    _logger.info(f'[LOGIN] username={username}')

    if not username or not password:
        return jsonify({'code': 40001, 'message': '请输入用户名和密码'}), 400

    _logger.info(f'[LOGIN] calling get_by_username')
    user = get_by_username(username)
    _logger.info(f'[LOGIN] get_by_username done, user={"found" if user else "not found"}')

    if not user:
        _audit(0, 'login_failed', {'username': username, 'reason': 'user_not_found'})
        record_login_failure(client_ip)
        return jsonify({'code': 40100, 'message': '用户名或密码错误'}), 401

    _logger.info(f'[LOGIN] calling bcrypt.checkpw')
    if not bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
        _logger.info(f'[LOGIN] bcrypt failed')
        _audit(user['id'], 'login_failed', {'reason': 'wrong_password'})
        record_login_failure(client_ip)
        return jsonify({'code': 40100, 'message': '用户名或密码错误'}), 401
    _logger.info(f'[LOGIN] bcrypt passed, took {_t.time()-_t0:.3f}s')

    # Login successful — clear failure record (best-effort, non-blocking)
    try:
        clear_login_failure(client_ip)
    except Exception as e:
        _logger.warning(f'[LOGIN] clear_login_failure failed: {e}')
    _logger.info(f'[LOGIN] creating token')
    token = create_token(user['id'], user['username'])
    _logger.info(f'[LOGIN] token created, total time {_t.time()-_t0:.3f}s')
    _audit(user['id'], 'login', {'username': user['username']})
    return jsonify({
        'code': 0,
        'data': {
            'token': token,
            'user': {'id': user['id'], 'username': user['username'], 'is_admin': bool(user['is_admin'])}
        }
    })


@bp.route('/register', methods=['POST'])
def register():
    """First-time setup: create admin account when no users exist.
    Password is auto-generated and returned in the response — save it on first login.
    Subsequent calls return 403 (registration closed after first user is created)."""
    import secrets, bcrypt
    from src.models.user import list_users, create_user

    users = list_users()
    if users:
        return jsonify({'code': 40300, 'message': '注册功能已关闭'}), 403

    # First-time setup: auto-generate admin password
    raw_password = secrets.token_urlsafe(12)  # 16 chars, alphanumeric
    password_hash = bcrypt.hashpw(raw_password.encode(), bcrypt.gensalt()).decode()

    create_user('admin', password_hash, is_admin=True)

    _audit(1, 'register', {'username': 'admin', 'mode': 'first_time_setup'})
    return jsonify({
        'code': 0,
        'data': {
            'username': 'admin',
            'password': raw_password,   # only returned here — user must save it
            'message': '初始管理员已创建，密码仅此一次显示，请妥善保存'
        }
    })


@bp.route('/me', methods=['GET'])
@require_auth
def me():
    from src.models.user import get_by_id
    user = get_by_id(g.user_id)
    if not user:
        return jsonify({'code': 40400, 'message': '用户不存在'}), 404
    return jsonify({
        'code': 0,
        'data': {'id': user['id'], 'username': user['username'], 'is_admin': bool(user['is_admin'])}
    })


@bp.route('/password', methods=['PUT'])
@require_auth
def change_password():
    """Change current user's password. Requires old password verification."""
    data = request.get_json() or {}
    old_pw = data.get('old_password', '')
    new_pw = data.get('new_password', '')

    if not old_pw or not new_pw:
        return jsonify({'code': 40001, 'message': '请输入旧密码和新密码'}), 400
    if len(new_pw) < 4:
        return jsonify({'code': 40001, 'message': '新密码至少4位'}), 400
    if old_pw == new_pw:
        return jsonify({'code': 40001, 'message': '新密码不能与旧密码相同'}), 400

    from src.models.user import get_by_id, update_password
    user = get_by_id(g.user_id)
    if not user:
        return jsonify({'code': 40400, 'message': '用户不存在'}), 404

    if not bcrypt.checkpw(old_pw.encode(), user['password_hash'].encode()):
        _audit(g.user_id, 'password_change_failed', {'reason': 'wrong_old_password'})
        return jsonify({'code': 40100, 'message': '旧密码错误'}), 401

    new_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
    update_password(g.user_id, new_hash)
    _audit(g.user_id, 'password_changed', {})
    return jsonify({'code': 0, 'message': '密码已修改'})
