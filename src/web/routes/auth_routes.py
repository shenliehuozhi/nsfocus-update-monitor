"""Auth routes."""

from flask import Blueprint, request, jsonify, g
import bcrypt

from src.web.auth import create_token, require_auth
from src.models.user import get_by_username, create_user as create_user_db

bp = Blueprint('auth', __name__, url_prefix='/api/auth')


def _get_client_ip() -> str:
    """Get client IP, respecting X-Forwarded-For if configured behind proxy."""
    return request.headers.get('X-Forwarded-For', request.remote_addr) or ''


def _audit(user_id, action: str, details: dict = None):
    """Log audit entry to file. Ignores errors (best-effort)."""
    try:
        audit = _get_audit_logger()
        ip = _get_client_ip()
        audit.info(f'[{action}] user_id={user_id} ip={ip} details={details or {}}')
    except Exception:
        pass


import logging
import os
from logging.handlers import RotatingFileHandler

_audit_logger = None


def _get_audit_logger():
    """Dedicated logger for security audit events (login attempts, etc).
    Writes to logs/audit.log, separate from app.log, to avoid DB lock issues."""
    global _audit_logger
    if _audit_logger is not None:
        return _audit_logger

    LOG_DIR = os.getenv('MONITOR_LOG_DIR', '/root/nsfocus-monitor/logs')
    audit_log_path = os.path.join(LOG_DIR, 'audit.log')

    _audit_logger = logging.getLogger('audit')
    _audit_logger.setLevel(logging.INFO)
    _audit_logger.propagate = False

    handler = RotatingFileHandler(audit_log_path, maxBytes=10_000_000, backupCount=5)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S'
    ))
    _audit_logger.addHandler(handler)
    return _audit_logger

@bp.route('/login', methods=['POST'])
def login():
    import time as _t; _t0 = _t.time()
    audit = _get_audit_logger()
    client_ip = _get_client_ip()
    audit.info(f'[LOGIN] start ip={client_ip}')

    data = request.get_json() or {}
    username = data.get('username', '')
    password = data.get('password', '')
    audit.info(f'[LOGIN] username={username}')

    if not username or not password:
        return jsonify({'code': 40001, 'message': '请输入用户名和密码'}), 400

    audit.info(f'[LOGIN] calling get_by_username')
    user = get_by_username(username)
    audit.info(f'[LOGIN] get_by_username done, user={"found" if user else "not found"}')

    if not user:
        _audit(0, 'login_failed', {'username': username, 'reason': 'user_not_found'})
        audit.info(f'[LOGIN] failed user_not_found ip={client_ip}')
        return jsonify({'code': 40100, 'message': '用户名或密码错误'}), 401

    audit.info(f'[LOGIN] calling bcrypt.checkpw')
    if not bcrypt.checkpw(password.encode(), user['password_hash'].encode()):
        audit.info(f'[LOGIN] bcrypt failed ip={client_ip} username={username}')
        _audit(user['id'], 'login_failed', {'reason': 'wrong_password'})
        return jsonify({'code': 40100, 'message': '用户名或密码错误'}), 401
    audit.info(f'[LOGIN] bcrypt passed')

    audit.info(f'[LOGIN] success username={username} ip={client_ip}')
    _audit(user['id'], 'login', {'username': user['username']})
    token = create_token(user['id'], user['username'])
    audit.info(f'[LOGIN] token created, total time {_t.time()-_t0:.3f}s')
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

    # Also save to DATA_DIR/initial_password.txt so user can retrieve it from the file
    import os as _os
    _data_dir = os.environ.get('MONITOR_DATA_DIR', '') or _os.path.expanduser('~/.local/share/nsfocus-monitor-data')
    _password_file = _os.path.join(_data_dir, 'initial_password.txt')
    if not _os.path.exists(_password_file):
        try:
            with open(_password_file, 'w') as _f:
                _f.write(raw_password)
        except Exception:
            pass  # non-critical

    _audit(1, 'register', {'username': 'admin', 'mode': 'first_time_setup'})
    return jsonify({
        'code': 0,
        'data': {
            'username': 'admin',
            'password': raw_password,   # only returned here — user must save it
            'message': '初始管理员已创建，密码仅此一次显示，请妥善保存。密码已写入数据目录 initial_password.txt 文件。'
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
