"""Session management routes."""

import time
from flask import Blueprint, request, jsonify, g

from src.web.auth import require_auth
from src.models import user_session

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
            'https://update.nsfocus.com/update/wafIndex',
            cookies={'PHPSESSID': cookie_value},
            timeout=10,
            allow_redirects=False
        )
        latency_ms = int((time.time() - start) * 1000)

        # Check for redirect to login (session expired)
        if resp.status_code == 302:
            loc = resp.headers.get('Location', '')
            if '/portal/index' in loc or '/portal' in loc:
                return jsonify({'code': 40001, 'message': 'Session 已过期，请重新登录获取'}), 400

        if resp.status_code == 200:
            if 'ser_c_b_con' not in resp.text and '登录' in resp.text:
                return jsonify({'code': 40001, 'message': 'Session 无效（返回了登录页），请重新获取'}), 400
            if 'ser_c_b_con' not in resp.text:
                return jsonify({'code': 40001, 'message': '无法确认 Session 有效性，请确认已登录并复制正确的 PHPSESSID'}), 400
        else:
            return jsonify({'code': 40001, 'message': f'绿盟站点返回异常状态码: {resp.status_code}'}), 400

    except requests.RequestException as e:
        return jsonify({'code': 40001, 'message': f'无法连接绿盟站点: {str(e)}'}), 400

    # Session valid — save it
    sid = user_session.create(g.user_id, cookie_value)
    user_session.update_status(sid, 'active')

    # Record initial heartbeat
    user_session.update_heartbeat(sid, 'ok')
    user_session.log_heartbeat(sid, 'ok', latency_ms=latency_ms)

    return jsonify({
        'code': 0,
        'data': {
            'id': sid, 'status': 'active',
            'latency_ms': latency_ms,
            'message': f'Session 验证成功 ({latency_ms}ms)',
        }
    })


@bp.route('/<int:session_id>', methods=['DELETE'])
@require_auth
def delete_session(session_id: int):
    user_session.delete(session_id)
    return jsonify({'code': 0, 'message': '已删除'})


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
    """Re-validate a stored session with an immediate heartbeat."""
    from src.collectors.nsfocus import NsfocusCollector, SessionExpiredError

    sessions = user_session.get_by_user(g.user_id)
    target = next((s for s in sessions if s['id'] == session_id), None)
    if not target:
        return jsonify({'code': 40400, 'message': 'Session 不存在'}), 404

    # Need the decrypted cookie value
    active_list = user_session.get_active_sessions()
    active_target = next((s for s in active_list if s['id'] == session_id), None)

    if not active_target:
        # Session is not active, can't test
        return jsonify({'code': 40001, 'message': '只能验证状态为 active 的 Session'}), 400

    collector = NsfocusCollector()
    collector._set_cookie(active_target['cookie_value'])

    start = time.time()
    try:
        collector._fetch('/update/wafIndex')
        latency_ms = int((time.time() - start) * 1000)

        user_session.update_status(session_id, 'active')
        user_session.update_heartbeat(session_id, 'ok')
        user_session.log_heartbeat(session_id, 'ok', latency_ms=latency_ms)

        return jsonify({
            'code': 0,
            'data': {'status': 'active', 'latency_ms': latency_ms},
            'message': f'验证通过 ({latency_ms}ms)',
        })

    except SessionExpiredError:
        user_session.update_status(session_id, 'expired')
        user_session.update_heartbeat(session_id, 'expired')
        user_session.log_heartbeat(session_id, 'expired', error_msg='Session expired')

        return jsonify({
            'code': 40001,
            'message': 'Session 已过期',
        }), 400

    except Exception as e:
        user_session.update_heartbeat(session_id, 'error')
        user_session.log_heartbeat(session_id, 'error', error_msg=str(e)[:200])

        return jsonify({
            'code': 40001,
            'message': f'验证失败: {str(e)[:100]}',
        }), 400
