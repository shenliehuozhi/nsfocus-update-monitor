"""Metrics — machine-readable delivery / queue / collection / storage gauges.

GET /api/metrics?hours=24                → JSON
GET /api/metrics?hours=24&format=prometheus → Prometheus text

Auth: JWT via HttpOnly cookie (browser) or Bearer (API clients). For Prometheus
scraping, set MONITOR_METRICS_TOKEN and send it as `X-Metrics-Token` or
`Authorization: Bearer <token>` (Prometheus `authorization.credentials_file`
natively sends the latter); JWT expires every 24h so it is impractical as a
scrape credential. When the env var is unset, the static-token path is disabled.
"""

import functools
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, current_app, g, jsonify, request

from src.core.logger import get_logger
from src.web.auth import decode_token, request_token

bp = Blueprint('metrics', __name__, url_prefix='/api/metrics')
logger = get_logger('metrics')

_DEFAULT_WINDOW_HOURS = 24
_MAX_WINDOW_HOURS = 24 * 30
_STALE_SOURCE_HOURS = 24


def _auth(f):
    """JWT (HttpOnly cookie or Bearer) or (when configured) static X-Metrics-Token."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        expected = os.getenv('MONITOR_METRICS_TOKEN', '')
        if expected:
            got = request.headers.get('X-Metrics-Token', '')
            if got and secrets.compare_digest(got, expected):
                g.user_id, g.username = 0, 'metrics-token'
                return f(*args, **kwargs)

        token, _source = request_token()
        if token:
            # Prometheus 的 authorization.credentials_file 只会发 Bearer 头，
            # 静态令牌也接受这种形式，避免抓取侧必须依赖反向代理注入自定义头
            if expected and secrets.compare_digest(token, expected):
                g.user_id, g.username = 0, 'metrics-token'
                return f(*args, **kwargs)
            payload = decode_token(token)
            if payload:
                g.user_id = payload['user_id']
                g.username = payload['username']
                return f(*args, **kwargs)
        return jsonify({'code': 40100, 'message': '请先登录'}), 401
    return wrapper


def _window_hours(raw) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_WINDOW_HOURS
    if value < 1 or value > _MAX_WINDOW_HOURS:
        return _DEFAULT_WINDOW_HOURS
    return value


def _utc_cutoff(hours: int) -> str:
    return (datetime.utcnow() - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')


def _age_minutes(ts) -> float | None:
    """Age of a UTC timestamp (SQLite space format or ISO with T/Z) in minutes."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return round(max(0.0, (datetime.utcnow() - dt).total_seconds() / 60), 1)


def _rate(sent: int, failed: int) -> float | None:
    done = sent + failed
    return round(sent / done, 4) if done else None


def _today() -> dict:
    """当日（CST）推送统计；不随 hours 窗口变化。"""
    from src.models.database import query

    row = query(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN delivery_status='sent' THEN 1 ELSE 0 END) AS sent,
                  SUM(CASE WHEN delivery_status='failed' THEN 1 ELSE 0 END) AS failed
           FROM delivery_log
           WHERE date(datetime(COALESCE(NULLIF(created_at, ''), sent_at)), '+8 hours')
                 = date('now', '+8 hours')"""
    )[0]
    sent = row['sent'] or 0
    failed = row['failed'] or 0
    return {
        'delivery': {
            'total': row['total'] or 0,
            'sent': sent,
            'failed': failed,
            'success_rate': _rate(sent, failed),
        },
    }


def _updates(hours: int) -> dict:
    """窗口内产品更新，按产品大类（snapshots.product_name）聚合。"""
    from src.models.database import query

    hours = max(1, min(hours, 24 * 3650))
    # 采集器写库时已把站点 CST 时间转换为 UTC ISO，按 UTC 当前时间比较
    rows = query(
        """SELECT product_name, COUNT(*) AS count,
                  MAX(published_at) AS last_published_at
           FROM snapshots
           WHERE product_name <> '' AND published_at <> ''
             AND datetime(published_at) >= datetime('now', ?)
           GROUP BY product_name
           ORDER BY last_published_at DESC, product_name""",
        (f'-{hours} hours',),
    )
    items = [{
        'name': r['product_name'],
        'count': r['count'] or 0,
        'last_published_at': r['last_published_at'],
    } for r in rows]
    return {
        'window_hours': hours,
        'total': sum(item['count'] for item in items),
        'items': items,
    }


def _sessions() -> dict:
    from src.models.database import query
    from src.models.user_session import count_by_status, get_expired_active_count

    active = count_by_status('active')
    expired = count_by_status('expired')
    unknown = count_by_status('unknown')
    row = query(
        """SELECT SUM(CASE WHEN purpose='collect' THEN 1 ELSE 0 END) AS collect_total,
                  SUM(CASE WHEN purpose='collect' AND status='active' THEN 1 ELSE 0 END) AS collect_active,
                  SUM(CASE WHEN purpose='collect' AND heartbeat_status='污染' THEN 1 ELSE 0 END) AS collect_polluted,
                  SUM(CASE WHEN purpose='discover' AND status='active' THEN 1 ELSE 0 END) AS discover_active
           FROM user_sessions"""
    )[0]
    return {
        'total': active + expired + unknown,
        'active': active,
        'expired': expired,
        'unknown': unknown,
        'active_but_expired': get_expired_active_count(),
        'collect_total': row['collect_total'] or 0,
        'collect_active': row['collect_active'] or 0,
        'collect_polluted': row['collect_polluted'] or 0,
        'discover_active': row['discover_active'] or 0,
    }


def _customers(hours: int) -> dict:
    """客户维度推送聚合（跟随全局 hours 时间窗）。"""
    from src.models.database import query

    hours = max(0, min(hours, 24 * 3650))
    window = (
        ''
        if hours <= 0
        else f"AND datetime(COALESCE(NULLIF(dl.created_at, ''), dl.sent_at)) "
             f">= datetime('now', '-{hours} hours')"
    )
    rows = query(
        f"""SELECT c.id, c.name, COUNT(dl.id) AS total,
                   SUM(CASE WHEN dl.delivery_status='sent' THEN 1 ELSE 0 END) AS sent,
                   SUM(CASE WHEN dl.delivery_status='failed' THEN 1 ELSE 0 END) AS failed,
                   MAX(dl.sent_at) AS last_sent_at
            FROM customers c
            LEFT JOIN delivery_log dl ON dl.customer_id = c.id {window}
            GROUP BY c.id, c.name
            ORDER BY total DESC, c.name"""
    )
    items = []
    for r in rows:
        item = {
            'id': r['id'],
            'name': r['name'],
            'total': r['total'] or 0,
            'success': r['sent'] or 0,
            'failed': r['failed'] or 0,
            'last_sent_at': r['last_sent_at'],
            'last_product': '',
        }
        if item['last_sent_at']:
            snap = query(
                """SELECT s.product_name FROM delivery_log dl
                   JOIN snapshots s ON dl.snapshot_id = s.id
                   WHERE dl.customer_id = ? AND dl.sent_at IS NOT NULL
                   ORDER BY dl.sent_at DESC LIMIT 1""",
                (r['id'],),
            )
            if snap:
                item['last_product'] = snap[0]['product_name'] or ''
        items.append(item)
    return {'window_hours': hours, 'items': items}


def _process() -> dict:
    """进程启动时间与已运行秒数。"""
    started = None
    try:
        info = current_app.config.get('VERSION_INFO') or {}
        started = info.get('process_start')
    except RuntimeError:
        pass
    if not started:
        try:
            started = datetime.fromtimestamp(
                os.stat('/proc/self').st_ctime, tz=timezone.utc
            ).isoformat()
        except OSError:
            started = datetime.now(timezone.utc).isoformat()
    try:
        dt = datetime.fromisoformat(str(started).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        uptime = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    except ValueError:
        uptime = None
    return {'started_at': started, 'uptime_seconds': uptime}


def _delivery(hours: int) -> dict:
    from src.models.database import query
    cutoff = _utc_cutoff(hours)
    rows = query(
        """SELECT channel_type,
                  COUNT(*) AS total,
                  SUM(CASE WHEN delivery_status='sent' THEN 1 ELSE 0 END) AS sent,
                  SUM(CASE WHEN delivery_status='failed' THEN 1 ELSE 0 END) AS failed,
                  SUM(CASE WHEN delivery_status='pending' THEN 1 ELSE 0 END) AS pending
           FROM delivery_log
           WHERE datetime(COALESCE(NULLIF(created_at, ''), sent_at)) >= datetime(?)
           GROUP BY channel_type
           ORDER BY total DESC""",
        (cutoff,),
    )
    by_channel = []
    for r in rows:
        sent = r['sent'] or 0
        failed = r['failed'] or 0
        by_channel.append({
            'channel_type': r['channel_type'],
            'total': r['total'] or 0,
            'sent': sent,
            'failed': failed,
            'pending': r['pending'] or 0,
            'success_rate': _rate(sent, failed),
        })

    sent = sum(r['sent'] for r in by_channel)
    failed = sum(r['failed'] for r in by_channel)
    undated = query(
        """SELECT COUNT(*) AS c FROM delivery_log
           WHERE delivery_status='failed'
             AND COALESCE(created_at, '')='' AND COALESCE(sent_at, '')=''"""
    )[0]['c'] or 0
    return {
        'total': sum(r['total'] for r in by_channel),
        'sent': sent,
        'failed': failed,
        'pending': sum(r['pending'] for r in by_channel),
        'success_rate': _rate(sent, failed),
        # 失败行 sent_at 为 NULL，created_at 上线前的历史失败没有时间戳
        'undated_failed': undated,
        'by_channel': by_channel,
    }


def _queues() -> dict:
    from src.models.database import query
    # push_after 由 datetime.now() 生成本地时间，不能用 datetime('now') (UTC) 比较
    now_local = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    delayed = query(
        """SELECT COUNT(*) AS pending,
                  SUM(CASE WHEN push_after < ? THEN 1 ELSE 0 END) AS overdue,
                  SUM(CASE WHEN COALESCE(attempts, 0) > 0 THEN 1 ELSE 0 END) AS retrying,
                  MIN(created_at) AS oldest
           FROM delayed_queue WHERE status='pending'""",
        (now_local,),
    )[0]

    digest_rows = query(
        """SELECT dq.rule_id, COALESCE(r.name, '') AS rule_name,
                  COUNT(*) AS pending,
                  SUM(CASE WHEN COALESCE(dq.attempts, 0) > 0 THEN 1 ELSE 0 END) AS retrying,
                  MIN(dq.created_at) AS oldest
           FROM digest_queue dq
           LEFT JOIN subscription_rules r ON r.id = dq.rule_id
           WHERE dq.status='pending'
           GROUP BY dq.rule_id
           ORDER BY oldest ASC
           LIMIT 20"""
    )
    digest_pending = sum(r['pending'] for r in digest_rows)
    digest_retrying = sum(r['retrying'] or 0 for r in digest_rows)
    return {
        'delayed': {
            'pending': delayed['pending'] or 0,
            'overdue': delayed['overdue'] or 0,
            'retrying': delayed['retrying'] or 0,
            'oldest_age_minutes': _age_minutes(delayed['oldest']),
        },
        'digest': {
            'pending': digest_pending,
            'retrying': digest_retrying,
            'by_rule': [{
                'rule_id': r['rule_id'],
                'rule_name': r['rule_name'],
                'pending': r['pending'],
                'oldest_age_minutes': _age_minutes(r['oldest']),
            } for r in digest_rows],
        },
    }


def _collection() -> dict:
    from src.models.database import query
    data = {
        'enabled': None,
        'is_running': None,
        'last_run': None,
        'last_full_run': None,
        'last_quick_run': None,
        'next_run': None,
        'interval_hours': None,
        'current': None,
        'last_duration_s': None,
        'avg_duration_s': None,
        'sources_total': 0,
        'sources_stale': 0,
        'stale_after_hours': _STALE_SOURCE_HOURS,
    }
    try:
        from src.core.scheduler import get_status, get_progress, get_job_status
        st = get_status()
        # 下一次采集取调度任务的实际时间（get_status 的 next_full_scan 是全量扫描，
        # 当前配置下为 None）；没有任务时回落到全量扫描时间
        next_collect = next(
            (j['next_run'] for j in get_job_status() if j['id'] == 'nsfocus_collect'),
            None,
        )
        data.update({
            'enabled': st.get('enabled'),
            'is_running': st.get('is_running'),
            'last_run': st.get('last_run'),
            'last_full_run': st.get('last_full_run'),
            'last_quick_run': st.get('last_quick_run'),
            'next_run': next_collect or st.get('next_full_scan'),
            'interval_hours': st.get('interval_hours'),
        })
        if st.get('is_running'):
            prog = get_progress()
            duration = prog.get('duration_s')
            if not duration and prog.get('started_at'):
                age = _age_minutes(prog.get('started_at'))
                duration = round(age * 60) if age is not None else None
            data['current'] = {
                'mode': prog.get('mode'),
                'phase': prog.get('phase'),
                'duration_s': duration,
                'items_collected': prog.get('items_collected'),
            }
    except Exception as e:
        logger.debug(f'metrics: scheduler status unavailable: {e}')

    summaries = query(
        """SELECT created_at, message FROM system_event_log
           WHERE event_type='collection_summary'
           ORDER BY id DESC LIMIT 20"""
    )
    durations = []
    for row in summaries:
        try:
            value = json.loads(row['message'] or '{}').get('duration_s')
        except (TypeError, ValueError):
            continue
        if isinstance(value, (int, float)):
            durations.append(float(value))
    if durations:
        data['last_duration_s'] = durations[0]
        data['avg_duration_s'] = round(sum(durations) / len(durations), 1)

    stale_cutoff = _utc_cutoff(_STALE_SOURCE_HOURS)
    src = query(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN last_collected_at IS NULL
                            OR datetime(last_collected_at) < datetime(?)
                           THEN 1 ELSE 0 END) AS stale
           FROM content_sources
           WHERE source_type='nsfocus' AND is_active=1""",
        (stale_cutoff,),
    )[0]
    data['sources_total'] = src['total'] or 0
    data['sources_stale'] = src['stale'] or 0
    return data


def _rules() -> dict:
    from src.models.database import query
    row = query(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) AS enabled,
                  SUM(CASE WHEN enabled=1 AND COALESCE(digest_mode, '')!='' THEN 1 ELSE 0 END) AS digest_enabled
           FROM subscription_rules"""
    )[0]
    return {
        'total': row['total'] or 0,
        'enabled': row['enabled'] or 0,
        'digest_enabled': row['digest_enabled'] or 0,
    }


def _maintenance() -> dict:
    """维护模式：开启后所有订阅推送被抑制，采集照常。"""
    from src.models.database import query
    enabled = False
    try:
        rows = query("SELECT value FROM system_settings WHERE key = 'maintenance_mode'")
        enabled = bool(rows) and str(rows[0]['value']) == '1'
    except Exception as e:
        logger.debug(f'metrics: maintenance mode unavailable: {e}')
    return {'enabled': enabled}


def _site(hours: int = 24, include_history: bool = True) -> dict:
    """update.nsfocus.com 最近一次可用性探测结果与延时历史。"""
    try:
        from src.core.site_monitor import get_status
        site = get_status()
    except Exception as e:
        logger.debug(f'metrics: site status unavailable: {e}')
        site = {
            'url': 'https://update.nsfocus.com/',
            'status': 'unknown',
            'http_status': None,
            'latency_ms': None,
            'checked_at': None,
            'error': '',
            'consecutive_failures': 0,
            'last_ok_at': None,
            'last_fail_at': None,
        }
    if include_history:
        points, summary = [], {'samples': 0, 'failures': 0, 'avg_ms': None,
                               'max_ms': None}
        try:
            from src.models.site_probe import history
            points, summary = history(hours)
        except Exception as e:
            logger.debug(f'metrics: site history unavailable: {e}')
        site['history'] = points
        site['history_summary'] = summary
        site['history_hours'] = hours
    return site


def _events(hours: int) -> dict:
    from src.models.database import query
    rows = query(
        """SELECT severity, COUNT(*) AS c FROM system_event_log
           WHERE datetime(created_at) >= datetime(?)
           GROUP BY severity""",
        (_utc_cutoff(hours),),
    )
    by_severity = {r['severity']: r['c'] for r in rows}
    # acked_at 由 system_routes._ensure_acked_at 懒迁移，老库/测试库里可能还没有
    cols = {r['name'] for r in query("PRAGMA table_info(system_event_log)")}
    acked_filter = 'acked_at IS NULL AND ' if 'acked_at' in cols else ''
    unacked = query(
        f"""SELECT severity, COUNT(*) AS c FROM system_event_log
            WHERE {acked_filter}severity IN ('ERROR', 'CRITICAL', 'WARNING')
            GROUP BY severity"""
    )
    return {
        'window_total': sum(by_severity.values()),
        'by_severity': by_severity,
        'unacked': {r['severity']: r['c'] for r in unacked},
    }


def _storage() -> dict:
    from src.models import database as db_mod
    from src.models.database import query
    try:
        db_size = os.path.getsize(db_mod.DB_PATH)
    except (OSError, TypeError):
        db_size = None
    snap = query(
        """SELECT COUNT(*) AS total,
                  SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active
           FROM snapshots"""
    )[0]
    return {
        'db_size_bytes': db_size,
        'snapshots_total': snap['total'] or 0,
        'snapshots_active': snap['active'] or 0,
        'delivery_log_rows': query("SELECT COUNT(*) AS c FROM delivery_log")[0]['c'] or 0,
        'digest_queue_rows': query("SELECT COUNT(*) AS c FROM digest_queue")[0]['c'] or 0,
        'system_event_log_rows': query("SELECT COUNT(*) AS c FROM system_event_log")[0]['c'] or 0,
    }


_SCHEDULER_JOB_IDS = (
    'nsfocus_collect',
    'nsfocus_heartbeat',
    'nsfocus_digest',
    'nsfocus_delivery_queue',
    'nsfocus_db_cleanup',
    'nsfocus_channel_health',
    'nsfocus_site_check',
)


def _scheduler_jobs(collection: dict | None = None) -> list:
    """Known jobs always returned so the page can show running/stopped per job."""
    try:
        from src.core.scheduler import get_job_status
        jobs = get_job_status()
    except Exception as e:
        logger.debug(f'metrics: scheduler jobs unavailable: {e}')
        jobs = []

    by_id = {job['id']: job for job in jobs}
    enabled = (collection or {}).get('enabled')
    result = []
    for job_id in _SCHEDULER_JOB_IDS:
        job = by_id.get(job_id)
        if job:
            result.append({**job, 'status': 'running'})
        else:
            result.append({
                'id': job_id,
                'next_run': None,
                'seconds_until': None,
                'status': 'stopped' if enabled is False else 'disabled',
            })
    for job in jobs:
        if job['id'] not in _SCHEDULER_JOB_IDS:
            result.append({**job, 'status': 'running'})
    return result


def collect_metrics(hours: int, include_history: bool = True) -> dict:
    collection = _collection()
    return {
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'window_hours': hours,
        'delivery': _delivery(hours),
        'today': _today(),
        'updates': _updates(hours),
        'customers': _customers(hours),
        'sessions': _sessions(),
        'process': _process(),
        'queues': _queues(),
        'collection': collection,
        'rules': _rules(),
        'events': _events(hours),
        'storage': _storage(),
        'maintenance': _maintenance(),
        'site': _site(hours, include_history),
        'scheduler_jobs': _scheduler_jobs(collection),
    }


# ── Prometheus exposition ────────────────────────────────────

def _label(key: str, value) -> str:
    escaped = (str(value).replace('\\', '\\\\')
               .replace('\n', '\\n').replace('"', '\\"'))
    return f'{key}="{escaped}"'


def _fmt(value) -> str:
    if value is None:
        return 'NaN'
    if isinstance(value, bool):
        return '1' if value else '0'
    return str(value)


def render_prometheus(data: dict) -> str:
    lines = []
    typed = set()

    def gauge(name: str, value, labels: dict | None = None):
        # 窗口统计是 Gauge，不是单调 Counter：名字不带 _total，并显式声明类型
        if name not in typed:
            lines.append(f'# TYPE {name} gauge')
            typed.add(name)
        if labels:
            lbl = '{' + ','.join(_label(k, v) for k, v in labels.items()) + '}'
        else:
            lbl = ''
        lines.append(f'{name}{lbl} {_fmt(value)}')

    window = {'window_hours': data['window_hours']}
    delivery = data['delivery']
    gauge('nsfocus_delivery_events', delivery['total'], {**window})
    gauge('nsfocus_delivery_success_rate', delivery['success_rate'], {**window})
    gauge('nsfocus_delivery_undated_failed', delivery['undated_failed'])
    for ch in delivery['by_channel']:
        tags = {'channel_type': ch['channel_type'], 'window_hours': data['window_hours']}
        gauge('nsfocus_delivery_channel_events', ch['total'], tags)
        gauge('nsfocus_delivery_channel_sent', ch['sent'], tags)
        gauge('nsfocus_delivery_channel_failed', ch['failed'], tags)

    today = data['today']
    gauge('nsfocus_delivery_today_events', today['delivery']['total'])
    gauge('nsfocus_delivery_today_sent', today['delivery']['sent'])
    gauge('nsfocus_delivery_today_failed', today['delivery']['failed'])
    gauge('nsfocus_products_updated', data['updates']['total'], {**window})

    sessions = data['sessions']
    gauge('nsfocus_sessions', sessions['active'], {'status': 'active'})
    gauge('nsfocus_sessions', sessions['expired'], {'status': 'expired'})
    gauge('nsfocus_sessions', sessions['active_but_expired'], {'status': 'active_but_expired'})
    gauge('nsfocus_process_uptime_seconds', data['process']['uptime_seconds'])

    delayed = data['queues']['delayed']
    digest = data['queues']['digest']
    gauge('nsfocus_queue_pending', delayed['pending'], {'queue': 'delayed'})
    gauge('nsfocus_queue_pending', digest['pending'], {'queue': 'digest'})
    gauge('nsfocus_queue_retrying', delayed['retrying'], {'queue': 'delayed'})
    gauge('nsfocus_queue_retrying', digest['retrying'], {'queue': 'digest'})
    gauge('nsfocus_queue_overdue', delayed['overdue'], {'queue': 'delayed'})
    gauge('nsfocus_queue_oldest_age_minutes', delayed['oldest_age_minutes'], {'queue': 'delayed'})
    for r in digest['by_rule']:
        gauge('nsfocus_digest_pending', r['pending'],
              {'rule_id': r['rule_id'], 'rule_name': r['rule_name']})

    col = data['collection']
    gauge('nsfocus_collection_enabled', col['enabled'])
    gauge('nsfocus_collection_running', col['is_running'])
    gauge('nsfocus_collection_last_duration_seconds', col['last_duration_s'])
    gauge('nsfocus_collection_avg_duration_seconds', col['avg_duration_s'])
    gauge('nsfocus_collection_last_run_age_minutes', _age_minutes(col['last_run']))
    gauge('nsfocus_collection_sources', col['sources_total'])
    gauge('nsfocus_collection_sources_stale', col['sources_stale'])

    for severity, count in data['events']['unacked'].items():
        gauge('nsfocus_unacked_events', count, {'severity': severity})

    storage = data['storage']
    gauge('nsfocus_db_size_bytes', storage['db_size_bytes'])
    gauge('nsfocus_snapshots', storage['snapshots_active'], {'status': 'active'})
    gauge('nsfocus_snapshots', storage['snapshots_total'], {'status': 'total'})
    gauge('nsfocus_delivery_log_rows', storage['delivery_log_rows'])

    gauge('nsfocus_maintenance_mode', data['maintenance']['enabled'])

    site = data['site']
    site_up = None if site['status'] == 'unknown' else (1 if site['status'] == 'up' else 0)
    site_tags = {'url': site['url']}
    gauge('nsfocus_site_up', site_up, site_tags)
    gauge('nsfocus_site_http_status', site['http_status'], site_tags)
    gauge('nsfocus_site_latency_ms', site['latency_ms'], site_tags)
    gauge('nsfocus_site_consecutive_failures', site['consecutive_failures'], site_tags)
    gauge('nsfocus_site_last_check_age_minutes', _age_minutes(site['checked_at']), site_tags)

    for job in data['scheduler_jobs']:
        gauge('nsfocus_scheduler_job_seconds_until', job['seconds_until'], {'job': job['id']})

    return '\n'.join(lines) + '\n'


@bp.route('', methods=['GET'])
@_auth
def metrics():
    hours = _window_hours(request.args.get('hours'))
    prometheus = request.args.get('format') == 'prometheus'
    data = collect_metrics(hours, include_history=not prometheus)
    if prometheus:
        return Response(render_prometheus(data), mimetype='text/plain; version=0.0.4')
    return jsonify({'code': 0, 'data': data})
