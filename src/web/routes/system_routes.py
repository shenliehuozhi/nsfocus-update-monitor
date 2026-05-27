"""System routes: log viewer, log level control, manual collection trigger."""

from src.core.logger import get_log_dir
import glob
import json
import os
import sqlite3
import threading
from datetime import datetime
from flask import Blueprint, request, jsonify, g

from src.web.auth import require_auth
from src.collectors.nsfocus import PRODUCTS

bp = Blueprint('system', __name__, url_prefix='/api/system')
_refresh_pool = None  # ThreadPoolExecutor for pkg-type refresh, lazy init
_auto_discover_state = None  # {active, phase, progress, total, log_lines, result}
_auto_discover_lock = None
_confirm_state = None  # {active, phase, log_lines, result, error}
_confirm_lock = None

# ── Temp file for discover result persistence ─────────────────────────
import threading
# Compute project root from this module's location (src/web/routes/)
# Need 3 levels up to reach project root from src/web/routes/
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
_PENDING_FILE = os.path.join(_PROJECT_ROOT, 'data', 'discover_pending.json')

# ── Dedicated discover/confirm log file ──────────────────────────────
_disc_log_file = None

def _disc_log_path():
    global _disc_log_file
    if _disc_log_file is None:
        _disc_log_file = os.path.join(_PROJECT_ROOT, 'data', 'discover.log')
    return _disc_log_file

def _disc_log(msg, prefix='[auto_discover]'):
    """Append a timestamped line to data/discover.log."""
    try:
        import datetime as _dt
        ts = _dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = f'{ts} {prefix} {msg}\n'
        with open(_disc_log_path(), 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception:
        pass

def _load_pending():
    """Load pending discover result from temp file, or None."""
    try:
        import json as _json
        if os.path.exists(_PENDING_FILE):
            with open(_PENDING_FILE, 'r', encoding='utf-8') as f:
                d = _json.load(f)
            if isinstance(d, dict) and 'result' in d:
                return d
    except Exception:
        pass
    return None

def _save_pending(result):
    """Write discover result to temp file for service-restart resilience."""
    try:
        import json as _json
        os.makedirs(os.path.dirname(_PENDING_FILE), exist_ok=True)
        with open(_PENDING_FILE, 'w', encoding='utf-8') as f:
            _json.dump({'saved_at': __import__('datetime').datetime.utcnow().isoformat() + 'Z',
                        'result': result}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _clear_pending():
    """Remove pending file after successful confirm or discard."""
    try:
        if os.path.exists(_PENDING_FILE):
            os.remove(_PENDING_FILE)
    except Exception:
        pass

# ── Service-startup recovery ─────────────────────────────────────────
_auto_discover_lock = threading.Lock()
_pending = _load_pending()
if _pending:
    _auto_discover_state = {
        'active': False,
        'phase': 'done',
        'result': _pending['result'],
        'progress': 0,
        'total': 0,
        'log_lines': ['[已从临时文件恢复发现结果，服务重启前未执行确认]'],
        'is_stale': True,  # mark as stale so UI can show banner
    }
    _disc_log('service started: restored pending result from temp file', '[auto_discover]')


def _get_refresh_pool():
    global _refresh_pool
    if _refresh_pool is None:
        from concurrent.futures import ThreadPoolExecutor
        _refresh_pool = ThreadPoolExecutor(max_workers=1)
    return _refresh_pool


def _audit(action: str, details: dict = None):
    """Log audit entry (best-effort)."""
    try:
        from src.models.audit import log
        ip = request.headers.get('X-Forwarded-For', request.remote_addr) or ''
        log(g.user_id, action, details or {}, ip)
    except Exception:
        pass


# ── Log file listing ──────────────────────────────────────────────

@bp.route('/log-files', methods=['GET'])
@require_auth
def list_log_files():
    """List available log files with size and modification time."""
    from src.core.logger import get_log_dir
    import os
    log_dir = get_log_dir()
    files = []
    patterns = ['app.log*', 'access.log*']
    for pat in patterns:
        for path in sorted(glob.glob(os.path.join(log_dir, pat))):
            try:
                st = os.stat(path)
                files.append({
                    'name': os.path.basename(path),
                    'size': st.st_size,
                    'size_human': _fmt_size(st.st_size),
                    'modified': _fmt_time(st.st_mtime),
                })
            except OSError:
                pass
    return {'code': 0, 'data': {'files': files, 'log_dir': log_dir}}


# ── Log tail ──────────────────────────────────────────────────────

@bp.route('/logs', methods=['GET'])
@require_auth
def tail_logs():
    """Tail the last N lines of a log file, optionally filtered by level.
    
    Query params:
        file   - log file name (default: app.log)
        lines  - number of lines (default: 200, max: 1000)
        level  - filter level: DEBUG, INFO, WARNING, ERROR (default: all)
    """
    from src.core.logger import get_log_dir
    filename = request.args.get('file', 'app.log')
    n = min(int(request.args.get('lines', 200)), 1000)
    level_filter = request.args.get('level', '').upper()

    # Security: only allow *.log files
    if not filename.endswith('.log') or '..' in filename or '/' in filename:
        return {'code': 400, 'message': 'Invalid filename'}, 400

    filepath = os.path.join(get_log_dir(), filename)
    if not os.path.exists(filepath):
        return {'code': 404, 'message': f'Log file not found: {filename}'}, 404

    # Read last N lines efficiently (seek from end)
    lines = _tail_file(filepath, n)

    # Filter by level if requested
    if level_filter in ('DEBUG', 'INFO', 'WARNING', 'ERROR'):
        lines = [l for l in lines if f'[{level_filter}]' in l]

    # Newest first for display
    lines.reverse()

    # Get current log level for display
    from src.core.logger import get_current_level
    current_level = get_current_level()

    return {
        'code': 0,
        'data': {
            'file': filename,
            'lines': lines,
            'total': len(lines),
            'requested': n,
            'level': level_filter or 'ALL',
            'current_log_level': current_level,
        }
    }


# ── Log level control ─────────────────────────────────────────────

@bp.route('/log-level', methods=['GET', 'POST'])
@require_auth
def control_log_level():
    """GET: return current log level. POST: set log level.
    
    POST body: {"level": "DEBUG", "auto_restore_minutes": 30}
    """
    from src.core.logger import get_current_level, set_log_level

    if request.method == 'GET':
        return {'code': 0, 'data': {'level': get_current_level()}}

    body = request.get_json(silent=True) or {}
    level = body.get('level', 'INFO').upper()
    if level not in ('DEBUG', 'INFO', 'WARNING', 'ERROR'):
        return {'code': 400, 'message': 'Invalid level. Use: DEBUG, INFO, WARNING, ERROR'}, 400

    auto_restore = int(body.get('auto_restore_minutes', 30))
    new_level = set_log_level(level, auto_restore_minutes=auto_restore)
    _audit('log_level_change', {'level': new_level, 'auto_restore': auto_restore})

    return {
        'code': 0,
        'data': {
            'level': new_level,
            'auto_restore_minutes': auto_restore if new_level == 'DEBUG' else None,
        },
        'message': f'Log level set to {new_level}' +
                   (f' (auto-restore INFO in {auto_restore}min)' if new_level == 'DEBUG' else ''),
    }


# ── Manual collection trigger ─────────────────────────────────────

import threading

_collect_thread = None


@bp.route('/collect', methods=['POST'])
@require_auth
def trigger_collect():
    """Manually trigger collection. Runs in background, returns immediately.
    
    Body: {"mode": "delta"|"full"}  (default: "delta")
    Poll /collect/progress for status updates.
    """
    global _collect_thread
    from src.core.scheduler import run_now, _is_running

    if _is_running:
        return {'code': 409, 'message': '采集正在进行中，请等待完成'}, 409

    body = request.get_json(silent=True) or {}
    mode = body.get('mode', 'delta')
    if mode not in ('delta', 'full', 'quick'):
        return {'code': 400, 'message': 'mode must be delta, full, or quick'}, 400

    _audit('manual_collect', {'mode': mode})

    # Run in background thread
    def _bg_run():
        from src.core.scheduler import run_now
        run_now(mode=mode)

    _collect_thread = threading.Thread(target=_bg_run, daemon=True)
    _collect_thread.start()

    return {
        'code': 0,
        'message': f'{mode} 采集已触发，请轮询 /collect/progress 查看进度',
        'data': {'mode': mode},
    }


@bp.route('/collect/progress', methods=['GET'])
@require_auth
def collect_progress():
    """Get real-time collection progress."""
    from src.core.scheduler import get_progress, get_status as sched_status
    progress = get_progress()
    status = sched_status()
    return {
        'code': 0,
        'data': {
            'progress': progress,
            'scheduler': status,
        }
    }


@bp.route('/collect/status', methods=['GET'])
@require_auth
def collect_status():
    """Get current scheduler/collector status."""
    from src.core.scheduler import get_status as sched_status
    return {'code': 0, 'data': sched_status()}


# ── Rate Limit Admin ──────────────────────────────────────────────

@bp.route('/rate-limits', methods=['GET'])
@require_auth
def list_rate_limits():
    """List all currently banned keys with remaining time."""
    from src.core.rate_limiter import get_all_bans
    bans = get_all_bans()
    return {'code': 0, 'data': {'bans': bans, 'total': len(bans)}}


@bp.route('/rate-limits/reset', methods=['POST'])
@require_auth
def reset_rate_limit():
    """Reset rate limit ban for a specific key, or all keys.

    Body: {"key": "email@example.com"}  — reset specific key
          {}                             — reset all
    """
    data = request.get_json() or {}
    key = data.get('key', None)
    from src.core.rate_limiter import clear_ban
    affected = clear_ban(key)
    _audit('rate_limit_reset', {'key': key, 'affected': affected})
    if key:
        return {'code': 0, 'message': f'已重置 {key} 的频率限制'}
    else:
        return {'code': 0, 'message': f'已重置全部频率限制 ({affected} 条)'}


@bp.route('/health', methods=['GET'])
@require_auth
def health_check():
    """一站式运维健康视图：Session状态 / 采集结果 / 队列积压 / 限流 / 异常摘要"""
    from src.models.user_session import count_by_status, get_expired_active_count
    from src.models.database import query
    from src.core.scheduler import get_status as sched_status
    from src.core.rate_limiter import get_all_bans
    import logging, re
    from datetime import datetime, timedelta

    # ── 1. Session 状态 ─────────────────────────────────────
    active = count_by_status('active')
    total_sessions = active + count_by_status('expired') + count_by_status('unknown')
    expired_active = get_expired_active_count()

    session_detail = query(
        "SELECT id, status, heartbeat_status, last_heartbeat_at, heartbeat_count "
        "FROM user_sessions ORDER BY last_heartbeat_at DESC LIMIT 5"
    )

    # ── 2. 调度器状态 ───────────────────────────────────────
    sch = sched_status()
    scheduler = {
        'enabled': sch.get('enabled', False),
        'is_running': sch.get('is_running', False),
        'current_mode': sch.get('current_mode', ''),
        'last_run': sch.get('last_run'),
        'last_full_run': sch.get('last_full_run'),
        'next_run': sch.get('next_run'),
        'interval_hours': sch.get('interval_hours', 4),
    }

    # ── 3. 推送队列积压（delayed_queue pending 数量）────────
    queue_rows = query(
        "SELECT COUNT(*) as cnt FROM delayed_queue WHERE status = 'pending'"
    )
    queue_pending = queue_rows[0]['cnt'] if queue_rows else 0

    queue_overdue = query(
        "SELECT COUNT(*) as cnt FROM delayed_queue "
        "WHERE status = 'pending' AND push_after < datetime('now')"
    )
    queue_overdue_count = queue_overdue[0]['cnt'] if queue_overdue else 0

    # ── 4. 限流状态 ──────────────────────────────────────────
    bans = get_all_bans()

    # email_rate_counters 当前使用量（今日桶）
    from datetime import datetime as dt
    today_bucket = dt.utcnow().strftime('%Y-%m-%d')
    email_counts = query(
        "SELECT key, count FROM email_rate_counters WHERE bucket = ?",
        (today_bucket,)
    )
    email_rates = [{'key': r['key'], 'count': r['count']} for r in email_counts]

    # ── 5. 采集健康状态（各产品最近采集情况）──────────────
    # 查各产品最后一次采集时间和健康状态
    product_health = query("""
        SELECT s.name, s.id,
               s.last_collected_at,
               s.health_status,
               s.is_active,
               COUNT(snap.id) as snap_count
        FROM snapshots s
        LEFT JOIN snapshots snap ON snap.source_id = s.id AND snap.status = 'active'
        WHERE s.source_type = 'nsfocus'
        GROUP BY s.id
        ORDER BY s.last_collected_at DESC NULLS LAST
        LIMIT 20
    """)
    product_health_list = [dict(row) for row in product_health] if product_health else []

    # 计算成功率
    push_success_rate = 0
    if push_today['total'] > 0:
        push_success_rate = round(push_today['success'] / push_today['total'] * 100)

    # ── 5. 采集异常摘要（从 app.log 聚合最近2小时的 WARNING/ERROR）────────
    异常日志 = []
    try:
        log_dir = get_log_dir()
        log_file = os.path.join(log_dir, 'app.log')
        if os.path.exists(log_file):
            cutoff = datetime.utcnow() - timedelta(hours=2)
            # 读最后 500 行做采样，避免全文件扫描
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            recent = lines[-500:] if len(lines) > 500 else lines
            for line in recent:
                m = re.match(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^ ]*) (ERROR|WARNING)', line)
                if not m:
                    continue
                try:
                    t = datetime.fromisoformat(m.group(1))
                    if t < cutoff:
                        continue
                except Exception:
                    pass
                level = m.group(2)
                # 提取关键异常信息
                msg = line.strip()
                if '关键字段' in msg or '字段缺失' in msg or 'empty' in msg.lower() or 'null' in msg.lower():
                    kind = 'field_missing'
                elif '采集失败' in msg or 'Collection failed' in msg:
                    kind = 'collection_failed'
                elif 'Session' in msg and ('expired' in msg or 'exhausted' in msg):
                    kind = 'session_error'
                elif '解析' in msg or 'parse' in msg.lower():
                    kind = 'parse_error'
                else:
                    kind = 'other'
                异常日志.append({'level': level, 'kind': kind, 'msg': msg[-200:]})
    except Exception:
        pass

    # 今日推送统计
    today_push = query(
        "SELECT COUNT(*) as total, "
        "SUM(CASE WHEN delivery_status='sent' THEN 1 ELSE 0 END) as success, "
        "SUM(CASE WHEN delivery_status='failed' THEN 1 ELSE 0 END) as failed "
        "FROM delivery_log WHERE date(sent_at) = date('now')"
    )
    push_today = {
        'total': today_push[0]['total'] or 0,
        'success': today_push[0]['success'] or 0,
        'failed': today_push[0]['failed'] or 0,
    } if today_push else {'total': 0, 'success': 0, 'failed': 0}

    # 活跃快照数
    snap_count = query("SELECT COUNT(*) as cnt FROM snapshots WHERE status='active'")
    active_snapshots = snap_count[0]['cnt'] if snap_count else 0

    return {'code': 0, 'data': {
        'session': {
            'active': active,
            'total': total_sessions,
            'expired_active': expired_active,   # 活跃但已过期（保活失败）
            'detail': [dict(s) for s in session_detail],
        },
        'scheduler': scheduler,
        'queue': {
            'pending': queue_pending,
            'overdue': queue_overdue_count,      # 应发未发（超过 push_after 时间）
        },
        'rate_limits': {
            'bans': bans,                         # 当前被封禁的 key
            'email_rates': email_rates,           # 今日邮件计数
        },
        'push_today': push_today,
        'push_success_rate': push_success_rate,
        'active_snapshots': active_snapshots,
        'product_health': product_health_list,
        '异常日志': 异常日志[-20:],              # 最近2小时内最多20条
    }}


# ── Product Management ────────────────────────────────────────

VALID_STRATEGIES = ('standard', 'recursive')


@bp.route('/products', methods=['GET'])
@require_auth
def list_products():
    """List all nsfocus products from DB with full metadata."""
    from src.models.snapshot import list_sources
    products = list_sources('nsfocus')
    return {
        'code': 0,
        'data': {
            'products': [_product_safe(p) for p in products],
            'total': len(products),
            # Also include the hardcoded fallback PRODUCTS for reference
            'builtin_products': [p['name'] for p in products if p.get('is_active')],
        }
    }


@bp.route('/products', methods=['POST'])
@require_auth
def create_product():
    """Manually register a new product.

    Body: {"name": "WAF", "entry_url": "/update/wafIndex", "strategy": "standard"}
    """
    body = request.get_json() or {}
    name = (body.get('name') or '').strip()
    entry_url = (body.get('entry_url') or '').strip()
    strategy = body.get('strategy', 'standard')
    category = body.get('category', '安全')
    display_name = (body.get('display_name') or '').strip() or name

    if not name:
        return {'code': 400, 'message': '产品名称不能为空'}, 400
    if not entry_url:
        return {'code': 400, 'message': '入口URL不能为空'}, 400
    if not entry_url.startswith('/'):
        return {'code': 400, 'message': '入口URL必须以 / 开头'}, 400
    if strategy not in VALID_STRATEGIES:
        return {'code': 400, 'message': f'strategy 必须是 {VALID_STRATEGIES} 之一'}, 400

    from src.models.snapshot import upsert_source, get_source_by_name
    # Check duplicate
    existing = get_source_by_name(name)
    if existing:
        return {'code': 409, 'message': f'产品「{name}」已存在，请使用编辑功能'}, 409

    source_id = upsert_source(name, 'nsfocus', entry_url, strategy, created_by=g.user_id, category=category, display_name=display_name, is_active=True, is_manual=True)
    _audit('product_create', {'name': name, 'entry_url': entry_url, 'strategy': strategy})
    return {'code': 0, 'message': f'产品「{name}」已添加', 'data': {'id': source_id}}, 201


@bp.route('/products/<int:source_id>', methods=['GET'])
@require_auth
def get_product(source_id: int):
    """Get a single product by id."""
    from src.models.snapshot import get_source
    src = get_source(source_id)
    if not src:
        return {'code': 404, 'message': '产品不存在'}, 404
    return {'code': 0, 'data': _product_safe(src)}


@bp.route('/products/<int:source_id>', methods=['PUT'])
@require_auth
def update_product(source_id: int):
    """Update a product (name, entry_url, strategy, is_active, category).

    Body: partial — any of the fields above.
    """
    from src.models.snapshot import get_source, update_source

    src = get_source(source_id)
    if not src:
        return {'code': 404, 'message': '产品不存在'}, 404

    body = request.get_json() or {}
    # Auto-discovered products (is_manual=0) can only change strategy
    is_manual = bool(src.get('is_manual', 1))
    if not is_manual:
        if body.get('name') is not None or body.get('entry_url') is not None:
            return {'code': 403, 'message': '自动发现的产品仅允许修改采集策略'}, 403

    name = body.get('name')
    entry_url = body.get('entry_url')
    strategy = body.get('strategy')
    is_active = body.get('is_active')
    category = body.get('category')
    display_name = body.get('display_name')
    package_type = body.get('package_type')
    force_type = body.get('force_type')
    package_type_discovered = body.get('package_type_discovered')
    package_type_changed = body.get('package_type_changed')

    if name is not None and not name.strip():
        return {'code': 400, 'message': '产品名称不能为空'}, 400
    if entry_url is not None:
        if not entry_url.startswith('/'):
            return {'code': 400, 'message': '入口URL必须以 / 开头'}, 400
        entry_url = entry_url.strip()
    if strategy is not None and strategy not in VALID_STRATEGIES:
        return {'code': 400, 'message': f'strategy 必须是 {VALID_STRATEGIES} 之一'}, 400

    update_source(source_id,
                  name=name.strip() if name else None,
                  entry_url=entry_url,
                  strategy=strategy,
                  is_active=bool(is_active) if is_active is not None else None,
                  category=category,
                  display_name=display_name,
                  package_type=package_type,
                  force_type=force_type,
                  package_type_discovered=package_type_discovered if package_type_discovered is not None else None,
                  package_type_changed=int(package_type_changed) if package_type_changed is not None else None)
    # 产品信息变更后失效 chain 缓存
    from src.core.scheduler import invalidate_chain_cache
    invalidate_chain_cache()

    _audit('product_update', {'source_id': source_id, 'fields': list(body.keys())})
    return {'code': 0, 'message': '已更新'}


@bp.route('/products/<int:source_id>', methods=['DELETE'])
@require_auth
def delete_product(source_id: int):
    """Delete a product and all its snapshots."""
    from src.models.snapshot import get_source, delete_source
    src = get_source(source_id)
    if not src:
        return {'code': 404, 'message': '产品不存在'}, 404

    # Check if any snapshots reference this source in active subscription rules
    from src.models.database import query
    refs = query(
        "SELECT COUNT(*) AS cnt FROM snapshots WHERE source_id = ? AND status = 'active'",
        (source_id,)
    )
    # Log warning if active snapshots exist (but still allow deletion)
    if refs and refs[0]['cnt'] > 0:
        import logging
        logging.warning(f"Deleting product {source_id} which has {refs[0]['cnt']} active snapshots")

    name = src['name']
    delete_source(source_id)
    _audit('product_delete', {'source_id': source_id, 'name': name})
    return {'code': 0, 'message': f'产品「{name}」已删除'}


@bp.route('/products/discover', methods=['POST'])
@require_auth
def discover_products():
    """Auto-discover: scan products + discover package types + compare changes.

    Two-stage flow:
    1. Stage 1 (products): scan index → product-level added/removed (immediate, no DB write)
    2. Stage 2 (pkg-types): for each product call discover_package_types → diff
       → package-type added/deleted/modified (held in memory, no DB write)

    Client polls GET /products/discover/status for progress.
    After review, client calls POST /products/discover/confirm to apply.
    """
    global _auto_discover_state, _auto_discover_lock
    import threading, json
    from src.models.snapshot import list_sources, discover_products_from_index
    from src.models.user_session import get_active_sessions, get_active_sessions_by_purpose
    from src.collectors.nsfocus import NsfocusCollector
    from src.core.logger import get_logger as _get_logger
    logger = _get_logger('system_routes')

    # Lazy-init lock
    if _auto_discover_lock is None:
        import threading
        _auto_discover_lock = threading.Lock()

    with _auto_discover_lock:
        if _auto_discover_state and _auto_discover_state.get('active'):
            return {'code': 409, 'message': '自动发现已在运行中'}, 409

        sessions = get_active_sessions_by_purpose('discover')
        if not sessions:
            return {'code': 400, 'message': '无发现用途 Session，请先添加"发现"类型的 PHPSESSID'}, 400
        cookie = sessions[0]['cookie_value']

        _auto_discover_state = {
            'active': True,
            'phase': 'scanning_products',   # scanning_products | discovering_pkg_types | done | error
            'progress': 0,
            'total': 0,
            'current': '',
            'log_lines': [],
            'result': None,   # set when done/error
        }

    def _log(msg):
        with _auto_discover_lock:
            if _auto_discover_state:
                _auto_discover_state['log_lines'].append(str(msg))
        _disc_log(msg, '[auto_discover]')

    def _run():
        global _auto_discover_state, _auto_discover_lock
        collector = NsfocusCollector()
        collector._set_cookie(cookie)

        try:
            # ── Stage 1: product-level scan ─────────────────────────
            _log('开始扫描产品列表...')
            with _auto_discover_lock:
                _auto_discover_state['phase'] = 'scanning_products'
                _auto_discover_state['current'] = '扫描产品列表'

            discovered = discover_products_from_index()
            existing_map = {s['entry_url']: s for s in list_sources()}
            disc_urls = {p['entry_url'] for p in discovered}

            prod_added, prod_unchanged, prod_removed = [], [], []
            for p in discovered:
                url = p['entry_url']
                if url not in existing_map:
                    prod_added.append({**p, 'status': 'added'})
                else:
                    prod_unchanged.append({**p, **existing_map[url], 'status': 'unchanged'})

            for url, s in existing_map.items():
                if url not in disc_urls:
                    prod_removed.append({
                        'name': s['name'],
                        'entry_url': url,
                        'display_name': s.get('display_name', s['name']),
                        'status': 'removed',
                    })

            _log(f'产品扫描完成: 新增={len(prod_added)} 移除={len(prod_removed)} 未变={len(prod_unchanged)}')

            # ── Stage 2: package-type discovery for ALL products ─────
            all_products = prod_added + prod_unchanged
            total = len(all_products)

            with _auto_discover_lock:
                _auto_discover_state['phase'] = 'discovering_pkg_types'
                _auto_discover_state['total'] = total

            pkg_changes = {}   # {source_id or temp_id: {added_paths, deleted_paths, modified_paths}}

            # For unchanged products, fetch current stored package_type for diff
            unchanged_map = {p['entry_url']: p for p in prod_unchanged}

            for idx, p in enumerate(all_products):
                source_id = p.get('id')
                # If source exists in DB, get its current package_type
                current_pkg = None
                if source_id:
                    from src.models.snapshot import get_source
                    src = get_source(source_id)
                    if src and src.get('package_type'):
                        try:
                            current_pkg = json.loads(src['package_type'])
                        except Exception:
                            pass

                # Discover new package types
                _log(f'[{idx+1}/{total}] 发现包类型: {p["name"]}')
                with _auto_discover_lock:
                    _auto_discover_state['progress'] = idx + 1
                    _auto_discover_state['current'] = p['name']

                if source_id:
                    # Wrap log_fn to capture every log line from nested discovery calls
                    captured_lines = []
                    def capture_log(msg):
                        if msg:
                            captured_lines.append(str(msg))
                            _log(msg)
                    def on_progress(phase, current, total):
                        _log(f'  → 版本 {current}/{total}: {phase}')
                    discovered_pkg = collector.discover_package_types(source_id, cookie, log_fn=capture_log, progress_fn=on_progress)
                else:
                    # New product — no source_id yet, can't call discover_package_types
                    # We'll insert the product first (in confirm step) then set package_type
                    discovered_pkg = {'types': [], 'paths': [], 'modes': {}}

                # Diff
                diff = NsfocusCollector.diff_package_types(current_pkg, discovered_pkg)
                if diff['added_paths'] or diff['deleted_paths'] or diff['modified_paths']:
                    pkg_changes[p.get('id') or p['entry_url']] = {
                        **diff,
                        'product_name': p['name'],
                        'entry_url': p['entry_url'],
                    }

            _log(f'包类型发现完成，共 {len(pkg_changes)} 个产品有变更')

            result = {
                'products': {
                    'added': prod_added,
                    'removed': prod_removed,
                    'unchanged': prod_unchanged,
                    'total': len(discovered),
                },
                'pkg_changes': pkg_changes,
            }

            with _auto_discover_lock:
                _auto_discover_state['result'] = result
                _auto_discover_state['phase'] = 'done'
                _auto_discover_state['active'] = False

            _save_pending(result)
            _log('自动发现完成，请确认变更')

        except Exception as e:
            import traceback
            _disc_log(f'ERROR: {e}', '[auto_discover]')
            _log(f'错误: {e}')
            with _auto_discover_lock:
                if _auto_discover_state:
                    _auto_discover_state['phase'] = 'error'
                    _auto_discover_state['active'] = False
                    _auto_discover_state['result'] = {'error': str(e)}
            _clear_pending()

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return {'code': 0, 'message': '自动发现已启动，请轮询状态'}


@bp.route('/products/discover/status', methods=['GET'])
@require_auth
def discover_products_status():
    """Poll auto-discover progress. Returns current state + log_lines."""
    global _auto_discover_state, _auto_discover_lock
    if _auto_discover_lock is None:
        # Service just started: check pending file for stale result
        pending = _load_pending()
        if pending and 'result' in pending:
            return {'code': 0, 'data': {'active': False, 'phase': 'done', 'result': pending['result'], 'is_stale': True}}
        return {'code': 0, 'data': {'active': False}}
    with _auto_discover_lock:
        if not _auto_discover_state:
            # Memory empty: check pending file for stale result
            pending = _load_pending()
            if pending and 'result' in pending:
                return {'code': 0, 'data': {'active': False, 'phase': 'done', 'result': pending['result'], 'is_stale': True}}
            return {'code': 0, 'data': {'active': False}}
        state = dict(_auto_discover_state)
    # Don't expose lock in response
    state.pop('_lock', None)
    return {'code': 0, 'data': state}


@bp.route('/products/discover/confirm', methods=['POST'])
@require_auth
def discover_products_confirm():
    """Apply the discovered changes via background thread + SSE log stream.
    POST /products/discover/confirm           → starts background task, returns immediately
    GET  /products/discover/confirm/status    → SSE stream of progress/log_lines
    """
    global _auto_discover_state, _auto_discover_lock, _confirm_state, _confirm_lock
    from src.models.snapshot import upsert_source, delete_source, get_source_by_url, get_source, update_source
    from src.models.user_session import get_active_sessions, get_active_sessions_by_purpose
    from src.core.logger import get_logger as _get_logger
    import threading, json

    logger = _get_logger('system_routes')

    # Try memory first, then fall back to temp file (service-restart resilience)
    result = None
    stale = False
    if _auto_discover_lock is not None:
        with _auto_discover_lock:
            if _auto_discover_state and _auto_discover_state.get('result') and \
               'error' not in _auto_discover_state.get('result', {}):
                result = _auto_discover_state['result']

    if result is None:
        pending = _load_pending()
        if pending and 'error' not in pending.get('result', {}):
            result = pending['result']
            stale = True
            _disc_log('restored result from pending file (stale=True)', '[confirm]')
        else:
            return {'code': 400, 'message': '无待确认的发现结果，请先运行自动发现'}, 400

    if 'error' in result:
        return {'code': 400, 'message': f'上次发现有错误: {result["error"]}'}, 400

    body = request.get_json() or {}
    prod_added = body.get('products', {}).get('added', result['products']['added'])
    prod_removed = body.get('products', {}).get('removed', result['products']['removed'])
    pkg_changes = body.get('pkg_changes', result['pkg_changes'])

    # Lazy-init locks + state
    if _confirm_lock is None:
        _confirm_lock = threading.Lock()
    if _confirm_state is None:
        _confirm_state = {'active': False, 'phase': '', 'log_lines': [], 'result': None, 'error': None}

    with _confirm_lock:
        if _confirm_state.get('active'):
            return {'code': 409, 'message': '确认应用任务已在运行中'}, 409
        _confirm_state.update({
            'active': True, 'phase': 'applying',
            'log_lines': [], 'result': None, 'error': None,
        })

    def _log(msg):
        with _confirm_lock:
            if _confirm_state:
                _confirm_state['log_lines'].append(str(msg))
        _disc_log(msg, '[confirm]')

    # Capture user_id before entering background thread (g is not accessible there)
    user_id = g.user_id

    def _run():
        global _confirm_state, _confirm_lock
        saved_prods, deleted_prods = [], []
        pkg_updated_prods = []
        import traceback as _tb
        _disc_log(f'thread started: user_id={user_id}, prod_added={len(prod_added)}, prod_removed={len(prod_removed)}, pkg_changes={len(pkg_changes) if pkg_changes else "None"}', '[confirm]')

        def _audit_in_thread(action: str, details: dict = None):
            """Thread-safe audit (no g/request access)."""
            try:
                from src.models.audit import log
                log(user_id, action, details or {}, '')
            except Exception:
                pass

        try:
            # ── Insert added products ────────────────────────────────
            for p in prod_added:
                name = p.get('name', '').strip()
                entry_url = p.get('entry_url', '').strip()
                display_name = p.get('display_name', name)
                if not name or not entry_url:
                    continue
                strategy = _auto_detect_strategy(entry_url)
                sid = upsert_source(name, 'nsfocus', entry_url, strategy,
                                   created_by=user_id, display_name=display_name,
                                   is_active=False, is_manual=False)
                saved_prods.append({'id': sid, 'name': name, 'entry_url': entry_url})
                _log(f'[confirm] 新增产品: {name} (id={sid})')

            # ── Remove deleted products ──────────────────────────────
            for p in prod_removed:
                entry_url = p.get('entry_url', '').strip()
                if not entry_url:
                    continue
                src = get_source_by_url(entry_url)
                if src:
                    delete_source(src['id'])
                    deleted_prods.append(entry_url)
                    _log(f'[confirm] 删除产品: {entry_url}')

            # ── Update package_type for all products with changes ────
            sessions = get_active_sessions()
            if not sessions:
                raise Exception('无有效会话')

            cookie = sessions[0]['cookie_value']
            from src.collectors.nsfocus import NsfocusCollector
            collector = NsfocusCollector()
            collector._set_cookie(cookie)

            total = len(pkg_changes)
            for idx, (key, change) in enumerate(pkg_changes.items()):
                product_name = change.get('product_name')
                entry_url = change.get('entry_url')
                if not entry_url:
                    continue
                src = get_source_by_url(entry_url)
                if not src:
                    continue
                source_id = src['id']

                _log(f'[confirm] [{idx+1}/{total}] 更新包类型: {product_name}')
                with _confirm_lock:
                    _confirm_state['phase'] = f'updating_pkg ({idx+1}/{total})'

                captured_lines = []
                def capture_log(msg):
                    if msg:
                        captured_lines.append(str(msg))
                        _log(f'  {msg}')
                def on_progress(phase, current, total_p):
                    _log(f'  → 版本 {current}/{total_p}: {phase}')

                discovered = collector.discover_package_types(
                    source_id, cookie,
                    log_fn=capture_log,
                    progress_fn=on_progress
                )
                new_pkg_json = json.dumps({
                    'types': discovered.get('types', []),
                    'paths': discovered.get('paths', []),
                    'modes': discovered.get('modes', {}),
                })
                update_source(source_id,
                    package_type=new_pkg_json,
                    package_type_discovered=new_pkg_json,
                    package_type_changed=1 if (change['added_paths'] or change['deleted_paths'] or change['modified_paths']) else 0)
                # 产品路径变更后失效 chain 缓存，确保订阅条件能匹配新路径
                from src.core.scheduler import invalidate_chain_cache
                invalidate_chain_cache()
                pkg_updated_prods.append(product_name)

            _audit_in_thread('product_discover_save', {
                'added': len(saved_prods), 'removed': len(deleted_prods),
                'pkg_updated': len(pkg_updated_prods),
            })

            _disc_log(f'[confirm] ✅ 应用完成: 新增产品{len(saved_prods)} 删除{len(deleted_prods)} 包类型更新{len(pkg_updated_prods)}', '[confirm]')
            with _confirm_lock:
                _confirm_state['result'] = {
                    'saved': saved_prods, 'deleted': deleted_prods,
                    'pkg_updated': pkg_updated_prods,
                }
                _confirm_state['active'] = False
                _confirm_state['phase'] = 'done'
            _log('✅ 确认应用完成')
            _clear_pending()
            # Clear in-memory state so service restart doesn't restore stale pending
            global _auto_discover_state
            _auto_discover_state = {'active': False, 'phase': 'cleared', 'result': None, 'log_lines': [], 'is_stale': False}

        except Exception as e:
            import traceback
            _disc_log(f'ERROR: {e}', '[confirm]')
            _log(f'❌ 错误: {e}')
            with _confirm_lock:
                _confirm_state['active'] = False
                _confirm_state['phase'] = 'error'
                _confirm_state['error'] = str(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return {'code': 0, 'message': '确认应用已启动，请轮询状态'}


@bp.route('/products/discover/confirm', methods=['DELETE'])
@require_auth
def discover_confirm_discard():
    """Discard pending discover result and clear temp file."""
    global _pending
    _clear_pending()
    # Clear in-memory state so subsequent status calls return empty
    if _auto_discover_lock:
        with _auto_discover_lock:
            if _auto_discover_state:
                _auto_discover_state.update({'active': False, 'phase': 'done', 'error': 'discarded'})
                _auto_discover_state.pop('result', None)
    _pending = None
    if _confirm_lock:
        with _confirm_lock:
            if _confirm_state:
                _confirm_state.update({'active': False, 'phase': 'done', 'error': 'discarded'})
    _disc_log('discarded pending discover result', '[confirm]')
    return {'code': 0, 'message': '已丢弃'}


@bp.route('/products/discover/confirm/status', methods=['GET'])
@require_auth
def discover_confirm_status():
    """SSE stream for confirm progress."""
    from flask import Response, stream_with_context
    import json, time

    # Lazy-init like POST endpoint does
    global _confirm_lock
    if _confirm_lock is None:
        import threading
        _confirm_lock = threading.Lock()

    def generate():
        last_count = 0
        for _ in range(600):  # 600 * 0.5s = 5min max
            time.sleep(0.5)
            with _confirm_lock:
                state = dict(_confirm_state) if _confirm_state else {}
            yield f"data: {json.dumps(state)}\n\n"
            log_count = len(state.get('log_lines', []))
            if log_count > last_count:
                last_count = log_count
            if not state.get('active') or state.get('phase') in ('done', 'error'):
                break
        with _confirm_lock:
            final = dict(_confirm_state) if _confirm_state else {}
        yield f"data: {json.dumps(final)}\n\n"
        yield "event: close\ndata: {}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


@bp.route('/products/discover/save', methods=['PUT'])
@require_auth
def save_discovered_products():
    """Save discovered products: insert added, remove deleted, ignore unchanged."""
    from src.models.snapshot import upsert_source, delete_source
    body = request.get_json() or {}
    added = body.get('added', [])
    removed = body.get('removed', [])

    saved, deleted = [], []
    for p in added:
        name = p.get('name', '').strip()
        entry_url = p.get('entry_url', '').strip()
        display_name = p.get('display_name', name)
        pkg_type = p.get('package_type')  # may be a JSON string or list
        if not name or not entry_url:
            continue
        strategy = _auto_detect_strategy(entry_url)
        sid = upsert_source(name, 'nsfocus', entry_url, strategy,
                           created_by=g.user_id, display_name=display_name,
                           is_active=False, is_manual=False, package_type=pkg_type)
        saved.append({'id': sid, 'name': name, 'entry_url': entry_url,
                      'strategy': strategy, 'display_name': display_name,
                      'package_type': pkg_type})

    for p in removed:
        entry_url = p.get('entry_url', '').strip()
        if not entry_url:
            continue
        from src.models.snapshot import get_source_by_url
        src = get_source_by_url(entry_url)
        if src:
            delete_source(src['id'])
            deleted.append(entry_url)

    _audit('product_discover_save', {'added': len(saved), 'removed': len(deleted)})
    return {'code': 0, 'message': f'新增 {len(saved)} | 删除 {len(deleted)}',
            'data': {'saved': saved, 'deleted': deleted}}


@bp.route('/products/discover-pkg-types', methods=['POST'])
@require_auth
def discover_pkg_types_batch():
    """批量为指定产品发现包类型（轻量采集，只访问首页+版本页）。

    Body: {"source_ids": [1,2,3]}
    Returns: {"code":0,"data":{"updated":N,"failed":[]}}
    """
    import json
    body = request.get_json() or {}
    source_ids = body.get('source_ids', [])
    if not source_ids:
        return {'code': 0, 'data': {'updated': 0, 'failed': []}}

    from src.collectors.nsfocus import discover_package_types
    from src.models.snapshot import update_source

    session = _get_session_cookie()
    updated, failed = 0, []

    for sid in source_ids:
        src = get_source(sid)
        if not src or not src.get('entry_url'):
            failed.append(sid)
            continue
        try:
            types_dict = discover_package_types(sid, session)
            if types_dict:
                pkg_json = json.dumps(types_dict)
                update_source(sid, package_type=pkg_json, package_type_discovered=pkg_json)
                updated += 1
                # 包类型刷新后失效 chain 缓存
                from src.core.scheduler import invalidate_chain_cache
                invalidate_chain_cache()
            else:
                failed.append(sid)
        except Exception:
            failed.append(sid)

    return {'code': 0, 'data': {'updated': updated, 'failed': failed}}


@bp.route('/products/<int:source_id>/refresh-pkg-types', methods=['GET'])
def sse_pkg_refresh_progress(source_id: int):
    """SSE stream for package-type refresh progress of a single product.

    Auth: token via query param ?token=xxx (for EventSource compatibility).
    Returns server-sent events with log lines as they are produced.
    """
    from flask import request, Response, stream_with_context
    from src.web.auth import decode_token

    # Auth from query param (SSE can't send headers)
    token = request.args.get('token', '')
    payload = decode_token(token) if token else None
    if not payload:
        return {'code': 401, 'message': '请先登录'}, 401

    from src.core.scheduler import get_pkg_refresh_progress, _pkg_refresh_state, _pkg_refresh_lock

    def generate():
        import json, time
        # Send initial state
        with _pkg_refresh_lock:
            state = dict(_pkg_refresh_state)
        yield f"data: {json.dumps(state)}\n\n"

        last_log_count = len(state.get('log_lines', []))

        # Poll until done or error (max 90s)
        for _ in range(180):  # 180 * 0.5s = 90s
            time.sleep(0.5)
            with _pkg_refresh_lock:
                s = dict(_pkg_refresh_state)
            yield f"data: {json.dumps(s)}\n\n"
            if not s.get('active'):
                break
            # If new logs appeared, send immediately
            if len(s.get('log_lines', [])) > last_log_count:
                last_log_count = len(s.get('log_lines', []))
            # If done, send final and stop
            if s.get('phase') in ('done', 'error'):
                break

        # Final state
        with _pkg_refresh_lock:
            s = dict(_pkg_refresh_state)
        yield f"data: {json.dumps(s)}\n\n"
        yield "event: close\ndata: {}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )


@bp.route('/products/<int:source_id>/refresh-pkg-status', methods=['GET'])
@require_auth
def get_pkg_refresh_status(source_id: int):
    """返回当前包类型刷新的 JSON 状态（供前端轮询）。"""
    from src.core.scheduler import get_pkg_refresh_progress
    state = get_pkg_refresh_progress()
    return {'code': 0, 'data': state}


@bp.route('/products/<int:source_id>/refresh-pkg-type', methods=['POST'])
@require_auth
def trigger_pkg_refresh(source_id: int):
    """触发单个产品包类型后台刷新，返回是否成功启动。"""
    from src.core.scheduler import refresh_pkg_type_single, _pkg_refresh_state, _pkg_refresh_lock
    from src.models.user_session import get_active_sessions, get_active_sessions_by_purpose
    from concurrent.futures import ThreadPoolExecutor

    # 检查是否已有任务在运行
    with _pkg_refresh_lock:
        if _pkg_refresh_state.get('active') and _pkg_refresh_state.get('source_id') == source_id:
            return {'code': 409, 'message': '该产品正在刷新中'}, 409

    sessions = get_active_sessions_by_purpose('discover')
    if not sessions:
        return {'code': 400, 'message': '无发现用途 Session，请先添加"发现"类型的 PHPSESSID'}, 400
    cookie = sessions[0]['cookie_value']

    # 启动后台任务
    _get_refresh_pool().submit(refresh_pkg_type_single, source_id, cookie)

    return {'code': 0, 'message': '刷新任务已启动', 'data': {'source_id': source_id}}


def _auto_detect_strategy(entry_url: str) -> str:
    """Auto-detect standard vs recursive based on URL pattern heuristics."""
    path = entry_url.lower()
    # Recursive products tend to have specific URL patterns
    recursive_patterns = ('aurora', 'listnf', 'nf', 'rsa')
    for pat in recursive_patterns:
        if pat in path:
            return 'recursive'
    return 'standard'


@bp.route('/products/<int:source_id>/collect', methods=['POST'])
@require_auth
def collect_single_product(source_id: int):
    """Trigger collection for a single product (runs in background).

    Body: {"mode": "quick"}  — mode: quick or full
    """
    from src.models.snapshot import get_source
    from src.core.scheduler import run_now

    src = get_source(source_id)
    if not src:
        return {'code': 404, 'message': '产品不存在'}, 404

    body = request.get_json() or {}
    mode = body.get('mode', 'quick')
    if mode not in ('quick', 'full'):
        return {'code': 400, 'message': 'mode must be quick or full'}, 400

    # Run in background — just trigger and return
    def _bg():
        run_now(mode=mode)

    import threading
    t = threading.Thread(target=_bg, daemon=True)
    t.start()

    _audit('product_collect', {'source_id': source_id, 'name': src['name'], 'mode': mode})
    return {
        'code': 0,
        'message': f'「{src["name"]}」采集已触发 ({mode})',
        'data': {'source_id': source_id, 'mode': mode},
    }


def _product_safe(p: dict) -> dict:
    """Strip internal config from product dict for API response."""
    return {
        'id': p['id'],
        'name': p['name'],
        'display_name': p.get('display_name') or p['name'],
        'entry_url': p.get('entry_url', ''),
        'strategy': p.get('strategy', 'standard'),
        'category': p.get('category', ''),
        'is_active': bool(p.get('is_active', 1)),
        'is_manual': bool(p.get('is_manual', 1)),
        'package_type': p.get('package_type') or '',
        'force_type': p.get('force_type') or '',
        'health_status': p.get('health_status', 'unknown'),
        'last_collected_at': p.get('last_collected_at'),
        'created_at': p.get('created_at'),
    }


# ── Helpers ───────────────────────────────────────────────────────

def _tail_file(filepath: str, n: int) -> list:
    """Read last N lines of a file efficiently."""
    with open(filepath, 'rb') as f:
        f.seek(0, 2)  # end
        size = f.tell()
        if size == 0:
            return []

        # Estimate: read last N*200 bytes (avg line ~200 chars)
        chunk_size = min(size, max(n * 200, 4096))
        f.seek(max(0, size - chunk_size))
        data = f.read()

    lines = data.decode('utf-8', errors='replace').splitlines()
    return lines[-n:]


def _fmt_size(size: int) -> str:
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'


def _fmt_time(ts: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')


# ── Log Scan Alert Ingestion (DB-independent, for log_scanner) ─────────────────

# In-memory ring buffer: remember last-N alerts so we don't spam duplicates
# Key: (log_file, keyword, error_type) → last_seen timestamp
_log_alert_dedup: dict[tuple, float] = {}
_dedup_lock = threading.Lock()
_DEDUP_TTL = 300  # 5 minutes — don't re-alert for same error within 5 min


@bp.route('/events/ingest', methods=['POST'])
def ingest_log_alert():
    """HTTP callback for log_scanner to report critical errors.
    
    This endpoint is intentionally DB-independent — it reads channel config
    directly from the channel DB file (bypassing the main app's DB connection
    pool) so it works even when the main DB is locked.

    Request body:
      {
        "log_file": "app.log",
        "error_type": "DB错误",
        "keyword": "database is lock",
        "context": "...",
        "line_number": 1234
      }

    Response: 200 OK or 202 Accepted (even if notification fails, log is noted)
    """
    try:
        body = request.get_json(force=True)
    except Exception:
        return jsonify({'code': 1, 'message': 'invalid JSON'}), 400

    log_file = body.get('log_file', '')
    error_type = body.get('error_type', '')
    keyword = body.get('keyword', '')
    context = body.get('context', '')
    line_number = body.get('line_number')

    # Deduplicate: don't re-alert for same (file+type+keyword) within 5 min
    key = (log_file, error_type, keyword)
    now = datetime.utcnow().timestamp()
    with _dedup_lock:
        last_seen = _log_alert_dedup.get(key, 0)
        if now - last_seen < _DEDUP_TTL:
            return jsonify({'code': 0, 'message': 'deduped', 'skipped': True}), 200
        _log_alert_dedup[key] = now

    cst_now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    message_text = '\n'.join([
        "【日志异常检测】",
        f"扫描时间：{cst_now}",
        f"异常类型：{error_type}",
        f"关键词：{keyword}",
        f"日志文件：{log_file}",
        "",
        "上下文：",
        context[:500] if context else '—',
    ])

    # ── Direct channel lookup without going through main DB execute() ──
    # Read channel config directly from DB file (avoids connection pool lock)
    channel = _load_notify_channel_direct()
    if not channel:
        return jsonify({'code': 2, 'message': 'no channel configured'}), 200

    webhook_url = channel.get('config', {}).get('webhook_url', '')
    if not webhook_url:
        return jsonify({'code': 3, 'message': 'no webhook URL'}), 200

    # ── Try to also record to system_event_log ──
    # (best-effort, won't block the notification if DB is locked)
    _write_event_log_safely(log_file, error_type, keyword, context, line_number)

    # ── Send notification ──
    try:
        import requests as _requests
        payload = {'msgtype': 'markdown', 'markdown': {'content': message_text}}
        resp = _requests.post(webhook_url, json=payload, timeout=10)
        result = resp.json()
        if result.get('errcode') == 0:
            return jsonify({'code': 0, 'message': 'notified'}), 200
        else:
            return jsonify({'code': 4, 'message': f"wechat error: {result.get('errmsg')}", 'skipped': False}), 200
    except Exception as e:
        return jsonify({'code': 5, 'message': f'notification failed: {e}', 'skipped': False}), 200


def _load_notify_channel_direct() -> dict | None:
    """Load the configured notify channel by reading the DB file directly.
    
    This bypasses the app's main DB connection to avoid lock contention
    when the main DB is in a bad state.
    """
    from src.models.database import DB_PATH
    
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        # Get channel_id from system_event_config
        cfg_row = conn.execute(
            "SELECT channel_id FROM system_event_config LIMIT 1"
        ).fetchone()
        if not cfg_row or not cfg_row['channel_id']:
            conn.close()
            return None
        channel_id = cfg_row['channel_id']
        # Get channel config
        chan_row = conn.execute(
            "SELECT * FROM notification_channels WHERE id = ?", (channel_id,)
        ).fetchone()
        conn.close()
        if not chan_row:
            return None
        return dict(chan_row)
    except Exception:
        return None


def _write_event_log_safely(log_file, error_type, keyword, context, line_number):
    """Best-effort write to system_event_log. Failures are silently ignored."""
    from src.models.database import DB_PATH
    import json as _json

    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
        msg = _json.dumps({
            'log_file': log_file,
            'error_type': error_type,
            'keyword': keyword,
            'context': context,
            'line_number': line_number,
        }, ensure_ascii=False)
        conn.execute(
            """INSERT INTO system_event_log
               (event_type, severity, message)
               VALUES ('log_error', 'CRITICAL', ?)""",
            (msg,)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # Best-effort only
