"""Dashboard API."""

from flask import Blueprint, jsonify, request

from src.web.auth import require_auth

bp = Blueprint('dashboard', __name__, url_prefix='/api')


@bp.route('/dashboard', methods=['GET'])
@require_auth
def get_dashboard():
    from src.models.user_session import count_by_status, get_expired_active_count
    from src.models.snapshot import list_sources as list_src
    from src.models.database import query

    # Session status
    active = count_by_status('active')
    total = active + count_by_status('expired') + count_by_status('unknown')
    active_but_expired = get_expired_active_count()

    # Source health
    sources = list_src('nsfocus')
    source_summary = []
    for s in sources:
        source_summary.append({
            'name': s['name'],
            'health': s.get('health_status', 'unknown'),
            'last_collected': s.get('last_collected_at'),
        })

    # Push stats: today
    today_push = query(
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN delivery_status='sent' THEN 1 ELSE 0 END) as success,
                  SUM(CASE WHEN delivery_status='failed' THEN 1 ELSE 0 END) as failed
           FROM delivery_log WHERE date(sent_at) = date('now')"""
    )
    push_today = {
        'total': today_push[0]['total'] or 0,
        'success': today_push[0]['success'] or 0,
        'failed': today_push[0]['failed'] or 0,
    } if today_push else {'total': 0, 'success': 0, 'failed': 0}

    # Push stats: this week
    week_push = query(
        """SELECT COUNT(*) as total,
                  SUM(CASE WHEN delivery_status='sent' THEN 1 ELSE 0 END) as success,
                  SUM(CASE WHEN delivery_status='failed' THEN 1 ELSE 0 END) as failed
           FROM delivery_log WHERE sent_at >= date('now', '-7 days')"""
    )
    push_week = {
        'total': week_push[0]['total'] or 0,
        'success': week_push[0]['success'] or 0,
        'failed': week_push[0]['failed'] or 0,
    } if week_push else {'total': 0, 'success': 0, 'failed': 0}

    # Total active snapshots
    snap_count = query("SELECT COUNT(*) as cnt FROM snapshots WHERE status='active'")
    total_snapshots = snap_count[0]['cnt'] if snap_count else 0

    # Session detail
    sessions = query(
        "SELECT id, status, last_heartbeat_at, heartbeat_status, heartbeat_count "
        "FROM user_sessions WHERE status='active' ORDER BY last_heartbeat_at DESC LIMIT 3"
    )
    session_detail = [dict(s) for s in sessions]

    # Recent deliveries with time range
    range_days = request.args.get('range', '7')
    range_map = {'1': '1', '7': '7', '30': '30', '365': '365'}
    days = range_map.get(range_days, '7')

    recent = query(
        f"""SELECT dl.sent_at, dl.channel_name, dl.channel_type, dl.delivery_status,
                   c.name as customer_name, s.product_name, s.version_branch,
                   s.package_type, s.file_name, s.package_version, s.urgency,
                   dl.snapshot_id, dl.channel_id, dl.customer_id
            FROM delivery_log dl
            JOIN snapshots s ON dl.snapshot_id = s.id
            LEFT JOIN customers c ON dl.customer_id = c.id
            WHERE dl.sent_at >= date('now', '-{days} days')
            ORDER BY dl.sent_at DESC
            LIMIT 50"""
    )
    recent_deliveries = [dict(r) for r in recent]

    return jsonify({
        'code': 0,
        'data': {
            'session_status': {
                'active': active, 'total': total,
                'active_but_expired': active_but_expired,
            },
            'session_detail': session_detail,
            'sources': source_summary,
            'push_today': push_today,
            'push_week': push_week,
            'total_snapshots': total_snapshots,
            'recent_deliveries': recent_deliveries,
        }
    })
