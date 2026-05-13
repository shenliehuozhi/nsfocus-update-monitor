"""System routes: log viewer, log level control, manual collection trigger."""
from src.core.logger import get_log_dir
import glob
import os
from flask import Blueprint, request, jsonify, g

from src.web.auth import require_auth
from src.collectors.nsfocus import PRODUCTS

bp = Blueprint('system', __name__, url_prefix='/api/system')


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
                  display_name=display_name)

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
    """Auto-discover all products from the NSFOCUS update index page.

    Compares discovered products with DB to classify each as:
      - added:    in discovered, not in DB
      - removed:  in DB, not in discovered (gone from website)
      - unchanged: in both and unchanged
    """
    from src.models.snapshot import discover_products_from_index, list_sources
    try:
        discovered = discover_products_from_index()
        existing = {s['entry_url']: s for s in list_sources()}

        discovered_urls = {p['entry_url'] for p in discovered}
        existing_urls = set(existing.keys())
        db_urls = set()

        added, unchanged, removed = [], [], []
        for p in discovered:
            url = p['entry_url']
            db_urls.add(url)
            if url not in existing_urls:
                added.append({**p, 'status': 'added'})
            else:
                unchanged.append({**p, 'status': 'unchanged'})

        for url, s in existing.items():
            if url not in db_urls:
                removed.append({
                    'name': s['name'],
                    'entry_url': url,
                    'display_name': s.get('display_name', s['name']),
                    'status': 'removed',
                    'is_active': s.get('is_active', 1),
                })

        _audit('product_discover', {
            'added': len(added), 'unchanged': len(unchanged), 'removed': len(removed)
        })
        return {
            'code': 0,
            'message': f'新增 {len(added)} | 下线 {len(removed)} | 未变 {len(unchanged)}',
            'data': {
                'added': added,
                'unchanged': unchanged,
                'removed': removed,
                'total': len(discovered),
            },
        }
    except Exception as e:
        import traceback
        return {'code': 500, 'message': f'自动发现失败: {str(e)}'}, 500


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
        if not name or not entry_url:
            continue
        strategy = _auto_detect_strategy(entry_url)
        sid = upsert_source(name, 'nsfocus', entry_url, strategy,
                           created_by=g.user_id, display_name=display_name,
                           is_active=False, is_manual=False)
        saved.append({'id': sid, 'name': name, 'entry_url': entry_url,
                      'strategy': strategy, 'display_name': display_name})

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
