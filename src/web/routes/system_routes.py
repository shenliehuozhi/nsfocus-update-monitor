"""System routes: log viewer, log level control, manual collection trigger."""

import os
import glob
from flask import Blueprint, request, jsonify, g

from src.web.auth import require_auth

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
    if mode not in ('delta', 'full'):
        return {'code': 400, 'message': 'mode must be delta or full'}, 400

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
