"""Auth routes."""

from flask import Blueprint, request, jsonify, g
import bcrypt

from src.web.auth import create_token, require_auth
from src.models.user import get_by_username, create_user as create_user_db

bp = Blueprint('auth', __name__, url_prefix='/api/auth')


def _audit(user_id, action: str, details: dict = None):
    """Log audit entry. Ignores errors (best-effort)."""
    try:
        from src.models.audit import log
        ip = request.headers.get('X-Forwarded-For', request.remote_addr) or ''
        log(user_id, action, details or {}, ip)
    except Exception:
        pass


@bp.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    username = data.get('username', '')
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'code': 40001, 'message': '请输入用户名和密码'}), 400

    user = get_by_username(username)
    if not user:
        _audit(0, 'login_failed', {'username': username, 'reason': 'user_not_found'})
        return jsonify({'code': 40100, 'message': '用户名或密码错误'}), 401

    if not bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
        _audit(user['id'], 'login_failed', {'reason': 'wrong_password'})
        return jsonify({'code': 40100, 'message': '用户名或密码错误'}), 401

    token = create_token(user['id'], user['username'])
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
    data = request.get_json() or {}
    username = data.get('username', '')
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'code': 40001, 'message': '请输入用户名和密码'}), 400

    if len(password) < 6:
        return jsonify({'code': 40001, 'message': '密码至少6位'}), 400

    existing = get_by_username(username)
    if existing:
        return jsonify({'code': 40001, 'message': '用户名已存在'}), 400

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    uid = create_user_db(username, pw_hash)
    token = create_token(uid, username)
    _audit(uid, 'register', {'username': username})

    return jsonify({
        'code': 0,
        'data': {'token': token, 'user': {'id': uid, 'username': username}}
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
