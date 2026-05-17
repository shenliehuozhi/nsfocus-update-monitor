"""Session management routes."""

import time
from flask import Blueprint, request, jsonify, g

from src.web.auth import require_auth
from src.models import user_session

BASE_URL = 'https://update.nsfocus.com'
HEALTH_URL = '/update/listBvsV6/v/bvssys'

bp = Blueprint('sessions', __name__, url_prefix='/api/sessions')


@bp.route('', methods=['GET'])
@require_auth
def list_sessions():
    my_sessions = user_session.get_by_user(g.user_id)
    active = user_session.get_active_sessions()
    expired_count = user_session.get_expired_active_count()

    pool_status = {
        'total': user_session.count_by_status('active') + user_session.count_by_status('expired') + user_session.count_by_status('unknown'),
        'active': user_session.count_by_status('active'),
        'expired': user_session.count_by_status('expired'),
        'active_but_expired': expired_count,
    }

    return jsonify({
        'code': 0,
        'data': {
            'my_sessions': [{
                'id': s['id'], 'status': s['status'],
                'purpose': s.get('purpose', 'collect'),
                'collect_mode': s.get('collect_mode', 'standard'),
                'last_valid': s.get('last_valid'),
                'expires_at': s.get('expires_at'),
                'created_at': s.get('created_at'),
                'last_heartbeat_at': s.get('last_heartbeat_at'),
                'heartbeat_status': s.get('heartbeat_status'),
                'heartbeat_count': s.get('heartbeat_count', 0),
            } for s in my_sessions],
            'pool_status': pool_status
        }
    })


@bp.route('', methods=['POST'])
@require_auth
def create_session():
    data = request.get_json() or {}
    cookie_value = data.get('cookie_value', '').strip()

    if not cookie_value:
        return jsonify({'code': 40001, 'message': '请输入 PHPSESSID'}), 400

    # Validate session by making a test request (immediate heartbeat)
    import requests
    start = time.time()
    try:
        resp = requests.get(
            BASE_URL + HEALTH_URL,
            cookies={'PHPSESSID': cookie_value},
            timeout=10,
            allow_redirects=False
        )
        latency_ms = int((time.time() - start) * 1000)

        # Session expiry: 302 redirect → expired
        if resp.status_code == 302:
            loc = resp.headers.get('Location', '')
            if '/portal/index' in loc:
                return jsonify({'code': 40001, 'message': 'Session 已过期（302 跳转到登录页），请重新登录获取'}), 400

        # 200 OK → session alive (bvssys page doesn't have ser_c_b_con, no need to check page content)
        if resp.status_code != 200:
            return jsonify({'code': 40001, 'message': f'绿盟站点返回异常状态码: {resp.status_code}'}), 400

    except requests.RequestException as e:
        return jsonify({'code': 40001, 'message': f'无法连接绿盟站点: {str(e)}'}), 400

    # Session valid — save it
    purpose = data.get('purpose', 'collect')
    collect_mode = data.get('collect_mode', 'standard')
    if purpose not in ('discover', 'collect'):
        purpose = 'collect'
    if collect_mode not in ('standard', 'vm'):
        collect_mode = 'standard'
    sid = user_session.create(g.user_id, cookie_value, purpose=purpose, collect_mode=collect_mode)
    user_session.update_status(sid, 'active')

    # Record initial heartbeat
    user_session.update_heartbeat(sid, '正常')
    user_session.log_heartbeat(sid, '正常', latency_ms=latency_ms, error_msg='200 OK，session 存活')

    return jsonify({
        'code': 0,
        'data': {
            'id': sid, 'status': 'active',
            'purpose': purpose, 'collect_mode': collect_mode,
            'latency_ms': latency_ms,
            'message': f'Session 验证成功 ({latency_ms}ms)',
        }
    })


@bp.route('/<int:session_id>', methods=['DELETE'])
@require_auth
def delete_session(session_id: int):
    user_session.delete(session_id)
    return jsonify({'code': 0, 'message': '已删除'})


@bp.route('/<int:session_id>', methods=['PATCH'])
@require_auth
def update_session(session_id: int):
    """Update session purpose and/or collect_mode."""
    data = request.get_json() or {}
    purpose = data.get('purpose')
    collect_mode = data.get('collect_mode')

    if not purpose and not collect_mode:
        return jsonify({'code': 40001, 'message': '至少需要提供 purpose 或 collect_mode'}), 400

    if purpose not in ('discover', 'collect', None):
        return jsonify({'code': 40001, 'message': 'purpose 必须是 discover 或 collect'}), 400
    if collect_mode not in ('standard', 'vm', None):
        return jsonify({'code': 40001, 'message': 'collect_mode 必须是 standard 或 vm'}), 400

    # Resolve final values (keep existing if not provided)
    sessions = user_session.get_by_user(g.user_id)
    target = next((s for s in sessions if s['id'] == session_id), None)
    if not target:
        return jsonify({'code': 40400, 'message': 'Session 不存在'}), 404

    final_purpose = purpose if purpose else target.get('purpose', 'collect')
    final_mode = collect_mode if collect_mode else target.get('collect_mode', 'standard')

    user_session.update_purpose_mode(session_id, final_purpose, final_mode)
    return jsonify({'code': 0, 'message': '已更新', 'data': {'purpose': final_purpose, 'collect_mode': final_mode}})


@bp.route('/<int:session_id>/heartbeat', methods=['GET'])
@require_auth
def heartbeat_history(session_id: int):
    """Get heartbeat history for a session."""
    history = user_session.get_heartbeat_history(session_id, limit=20)
    return jsonify({
        'code': 0,
        'data': {
            'session_id': session_id,
            'history': [dict(h) for h in history],
        }
    })


@bp.route('/<int:session_id>/validate', methods=['POST'])
@require_auth
def validate_session(session_id: int):
    """Re-validate a stored session with an immediate heartbeat.
    Supports both active and expired sessions — if expired and still valid,
    it will be restored to active status.
    """
    import requests
    from src.core.crypto import decrypt

    sessions = user_session.get_by_user(g.user_id)
    target = next((s for s in sessions if s['id'] == session_id), None)
    if not target:
        return jsonify({'code': 40400, 'message': 'Session 不存在'}), 404

    # Get decrypted cookie (get_by_user returns encrypted, need to decrypt)
    cookie_value = decrypt(target['cookie_value'])

    start = time.time()
    try:
        resp = requests.get(
            BASE_URL + HEALTH_URL,
            cookies={'PHPSESSID': cookie_value},
            timeout=10,
            allow_redirects=False
        )
        latency_ms = int((time.time() - start) * 1000)

        # Session expiry: 302 redirect → expired
        if resp.status_code == 302:
            loc = resp.headers.get('Location', '')
            if '/portal/index' in loc:
                user_session.update_status(session_id, 'expired')
                user_session.update_heartbeat(session_id, '过期')
                user_session.log_heartbeat(session_id, '过期', error_msg=f'302 跳转 {loc}，session 已失效')
                return jsonify({'code': 40001, 'message': 'Session 已过期（302 跳转到登录页）'}), 400

        # 200 OK → session alive
        if resp.status_code != 200:
            user_session.update_heartbeat(session_id, '错误')
            user_session.log_heartbeat(session_id, '错误', error_msg=f'HTTP {resp.status_code}')
            return jsonify({'code': 40001, 'message': f'绿盟站点返回异常状态码: {resp.status_code}'}), 400

    except requests.RequestException as e:
        user_session.update_heartbeat(session_id, '错误')
        user_session.log_heartbeat(session_id, '错误', error_msg=str(e)[:200])
        return jsonify({
            'code': 40001,
            'message': f'无法连接绿盟站点: {str(e)[:100]}',
        }), 400

    # Session still valid — restore to active if it was expired
    user_session.update_status(session_id, 'active')
    user_session.update_heartbeat(session_id, '正常')
    user_session.log_heartbeat(session_id, '正常', latency_ms=latency_ms, error_msg='200 OK，session 存活')

    return jsonify({
        'code': 0,
        'data': {'status': 'active', 'latency_ms': latency_ms},
        'message': f'验证通过 ({latency_ms}ms)',
    })
