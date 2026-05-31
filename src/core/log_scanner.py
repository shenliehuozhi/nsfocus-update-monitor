"""Log scanner — periodically scan log files for error keywords.

Scans:
- app.log + app.log.* (rotated backups)
- service_error.log
- access.log (for non-200 requests from 127.0.0.1)

Runs every 30 minutes, skips if collection is running.
"""

import os
import re
import time
import json
from datetime import datetime
from threading import Thread, Event
from typing import Optional

from src.core.logger import get_logger
from src.models.event_log import is_event_enabled, get_config
from src.core.event_handler import emit_log_error

logger = get_logger('log_scanner')

# ── Config ───────────────────────────────────────────────

LOG_DIR = os.getenv('MONITOR_LOG_DIR', '/root/nsfocus-monitor/logs')
SCAN_INTERVAL = 30 * 60  # 30 minutes
TRACEBACK_LINES = 5  # lines to capture after Exception keyword
SCAN_POS_FILE = os.path.join(LOG_DIR, '.log_scanner_positions.json')

# Keyword patterns grouped by error type
ERROR_PATTERNS = {
    'DB错误': [
        re.compile(r'database is lock', re.IGNORECASE),
        re.compile(r'OperationalError', re.IGNORECASE),
        re.compile(r'database is closed', re.IGNORECASE),
        re.compile(r'UNIQUE constraint failed', re.IGNORECASE),
        re.compile(r'FOREIGN KEY constraint failed', re.IGNORECASE),
    ],
    '网络错误': [
        re.compile(r'Connection refused', re.IGNORECASE),
        re.compile(r'Connection timeout', re.IGNORECASE),
        re.compile(r'Read timeout', re.IGNORECASE),
        re.compile(r'HTTP 5\d{2}', re.IGNORECASE),
    ],
    '认证错误': [
        re.compile(r'Session expired', re.IGNORECASE),
        re.compile(r'Login failed', re.IGNORECASE),
        re.compile(r'登录失败', re.IGNORECASE),
        re.compile(r'authentication failed', re.IGNORECASE),
    ],
    '登录失败': [
        # audit_log: login_failed entries written by auth routes
        re.compile(r'login_failed', re.IGNORECASE),
    ],
    '采集错误': [
        re.compile(r'collection failed', re.IGNORECASE),
        re.compile(r'fetch error', re.IGNORECASE),
        re.compile(r'download failed', re.IGNORECASE),
    ],
    '系统错误': [
        re.compile(r'disk full', re.IGNORECASE),
        re.compile(r'No space left', re.IGNORECASE),
        re.compile(r'Permission denied', re.IGNORECASE),
        re.compile(r'MemoryError', re.IGNORECASE),
    ],
    'Python异常': [
        re.compile(r'Exception', re.IGNORECASE),
        re.compile(r'Traceback', re.IGNORECASE),
    ],
}

# Access log pattern: non-200 from 127.0.0.1
ACCESS_LOG_PATTERN = re.compile(
    r'^(?P<time>\S+ \S+ \d+ \d+:\d+:\d+ \d+)\s+127\.0\.0\.1.*(?P<status>[4-5]\d{2})'
)

# Audit log pattern: login_failed audit entries
# Format in audit.log: "2026-05-29T11:07:43 - [LOGIN] failed user_not_found ip=127.0.0.1"
AUDIT_LOGIN_FAILED_PATTERN = re.compile(
    r'\[LOGIN\]\s+((?:bcrypt\s+)?failed)\s+ip=(\S+)'
)


# ── Scanner State ─────────────────────────────────────────

_scanner_thread: Optional[Thread] = None
_stop_event = Event()
_positions: dict = {}  # in-memory cache of persisted positions

def _load_positions() -> dict:
    """Load last scan positions from persistent file."""
    try:
        if os.path.isfile(SCAN_POS_FILE):
            with open(SCAN_POS_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_positions(positions: dict):
    """Save scan positions to persistent file."""
    try:
        with open(SCAN_POS_FILE, 'w') as f:
            json.dump(positions, f)
    except Exception as e:
        logger.warning(f'Failed to save scan positions: {e}')

# ── Login failure tracking (brute-force detection) ──────────────
# Counts login_failed audit events since last successful login.
# After 3 failures, emits a login_bruteforce system event.
_LOGIN_FAIL_COUNT: dict[str, list] = {}   # ip → failure list

# ── Heartbeat log dedup state ─────────────────────────────────────
# Dedup: avoid repeated notifications for the same sid+status
# key = f"sid={sid}|status={status}", value = last_ts
_HB_NOTIFY_KEY: dict = {}

# Heartbeat log line format: ts | sid=N | purpose | collect_mode | status | latency_ms | msg
_HB_LINE_RE = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)'
    r' \| sid=(\d+)'
    r' \| (\w+)'
    r' \| (\w*)'
    r' \| (\w+)'
    r' \| (\d+)ms'
    r' \| (.+)'
)


# ── Core Logic ────────────────────────────────────────────

def _is_collection_running() -> bool:
    """Check if collection is currently running"""
    from src.models.database import query
    rows = query("SELECT value FROM system_settings WHERE key = 'collection_running'")
    return bool(rows and rows[0]['value'])


def _get_log_files() -> list:
    """Get all log files to scan (app.log and rotated backups, service_error.log)"""
    files = []
    
    # app.log and rotated backups
    for name in os.listdir(LOG_DIR):
        if name.startswith('app.log'):
            filepath = os.path.join(LOG_DIR, name)
            if os.path.isfile(filepath):
                files.append(filepath)
    
    # service_error.log
    service_error = os.path.join(LOG_DIR, 'service_error.log')
    if os.path.isfile(service_error):
        files.append(service_error)
    
    return sorted(set(files))


def _scan_file(filepath: str) -> list:
    """Scan a single file for error patterns. Returns list of (error_type, keyword, context, line_no)"""
    findings = []
    last_pos = _positions.get(filepath, 0)
    
    try:
        file_size = os.path.getsize(filepath)
        
        # If file was rotated (size smaller than last position), start from beginning
        if file_size < last_pos:
            last_pos = 0
        
        with open(filepath, 'r', errors='replace') as f:
            f.seek(last_pos)
            
            # Read new content
            new_content = f.read()
            
            # Update position for next scan
            new_pos = f.tell()
            _positions[filepath] = new_pos
            
            if not new_content.strip():
                return findings
            
            lines = new_content.split('\n')
            
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                
                # Check each error pattern
                for error_type, patterns in ERROR_PATTERNS.items():
                    for pattern in patterns:
                        if pattern.search(line):
                            # Capture context: current line + next few lines if Exception/Traceback
                            context_lines = [line]
                            if error_type == 'Python异常' and i + 1 < len(lines):
                                for j in range(i + 1, min(i + 1 + TRACEBACK_LINES, len(lines))):
                                    context_lines.append(lines[j].strip())
                            
                            findings.append({
                                'error_type': error_type,
                                'keyword': pattern.pattern,
                                'context': '\n'.join(context_lines),
                                'line_number': i + 1,
                                'timestamp': lines[0][:19] if lines else '',
                            })
                            break  # Don't match same line multiple times for same error type
        
        # Update position
        _positions[filepath] = new_pos
        
    except Exception as e:
        logger.warning(f'Error scanning {filepath}: {e}')
    
    return findings


def _scan_audit_log() -> list:
    """Scan audit.log for login failure patterns.
    Tracks per-IP failure counts in memory and emits a bruteforce alert after 3 failures.
    """
    global _LOGIN_FAIL_COUNT

    findings = []
    filepath = os.path.join(LOG_DIR, 'audit.log')
    last_pos = _positions.get(filepath, 0)

    try:
        if not os.path.isfile(filepath):
            return findings

        file_size = os.path.getsize(filepath)
        if file_size < last_pos:
            last_pos = 0

        with open(filepath, 'r', errors='replace') as f:
            f.seek(last_pos)
            new_content = f.read()
            new_pos = f.tell()
            _positions[filepath] = new_pos

            if not new_content.strip():
                return findings

            lines = new_content.strip().split('\n')
            # Scan for login failures
            for line in lines:
                failed_match = AUDIT_LOGIN_FAILED_PATTERN.search(line)

                if failed_match:
                    # reason: 'failed' or 'bcrypt failed'
                    reason = failed_match.group(1)
                    ip = failed_match.group(2)

                    # Increment failure count for this IP
                    if ip not in _LOGIN_FAIL_COUNT:
                        _LOGIN_FAIL_COUNT[ip] = []
                    _LOGIN_FAIL_COUNT[ip].append({'reason': reason, 'ts': line[:19]})

                    count = len(_LOGIN_FAIL_COUNT[ip])
                    if count >= 3:
                        # Emit bruteforce alert
                        try:
                            from src.core.event_handler import emit_login_bruteforce
                            recent = [(e['ts'], {'reason': e['reason'], 'username': ''})
                                      for e in _LOGIN_FAIL_COUNT[ip][-5:]]
                            emit_login_bruteforce(ip, count, recent)
                            # Reset to avoid repeated alerts until more failures
                            _LOGIN_FAIL_COUNT[ip] = _LOGIN_FAIL_COUNT[ip][-2:]
                            logger.warning(f'Login bruteforce detected: ip={ip} count={count}')
                        except Exception as e:
                            logger.error(f'Failed to emit login_bruteforce: {e}', exc_info=True)

    except Exception as e:
        logger.warning(f'Error scanning audit.log', exc_info=True)

    return findings


def _scan_heartbeat_log() -> list:
    """Scan heartbeat.log for session status changes and emit notifications.

    Reads the rolling heartbeat log file. For each line, checks:
    - If status='正常' and heartbeat_log_notify_on_success='1': send notification
    - If status in ('过期','污染','错误') and heartbeat_log_notify_on_failure='1': send notification

    Deduplication: only notifies on first occurrence of each sid+status combination
    since the log file is already rolling (max 10 lines, rewrites same sid entries).
    """
    global _HB_NOTIFY_KEY

    findings = []
    hb_log_path = os.path.join(LOG_DIR, 'heartbeat.log')
    last_pos = _positions.get(hb_log_path, 0)

    try:
        if not os.path.isfile(hb_log_path):
            return findings

        file_size = os.path.getsize(hb_log_path)
        if file_size < last_pos:
            last_pos = 0

        with open(hb_log_path, 'r', errors='replace') as f:
            f.seek(last_pos)
            new_content = f.read()
            new_pos = f.tell()
            _positions[hb_log_path] = new_pos

            if not new_content.strip():
                return findings

            lines = new_content.strip().split('\n')

            # Read system settings for notification toggles
            notify_on_success = _get_setting('heartbeat_log_notify_on_success', '0') == '1'
            notify_on_failure = _get_setting('heartbeat_log_notify_on_failure', '1') == '1'

            for line in lines:
                m = _HB_LINE_RE.match(line.strip())
                if not m:
                    continue

                ts, sid, purpose, collect_mode, status, latency_ms, msg = m.groups()
                dedup_key = f'sid={sid}|status={status}'

                # Deduplication: skip if already notified for this combination at same or later ts
                if dedup_key in _HB_NOTIFY_KEY and _HB_NOTIFY_KEY[dedup_key] >= ts:
                    continue

                should_notify = (
                    (status == '正常' and notify_on_success) or
                    (status in ('过期', '污染', '错误') and notify_on_failure)
                )

                if not should_notify:
                    continue

                _HB_NOTIFY_KEY[dedup_key] = ts

                purpose_cn = '采集' if purpose == 'collect' else '探测'
                collect_tag = f'/[{collect_mode}]' if collect_mode else ''

                if status == '正常':
                    title = '【Session 心跳成功】'
                    suggest = f'Session {sid} ({purpose_cn}{collect_tag}) 连接正常，延迟 {latency_ms}ms'
                elif status == '过期':
                    title = '【Session 失效】'
                    suggest = f'Session {sid} ({purpose_cn}{collect_tag}) 已过期，请重新登录更新 cookie'
                elif status == '污染':
                    title = '【Session 污染】'
                    suggest = f'Session {sid} ({purpose_cn}{collect_tag}) 上下文被污染，请重新登录'
                else:
                    title = f'【Session 心跳失败】'
                    suggest = f'Session {sid} ({purpose_cn}{collect_tag}) 心跳错误：{msg}'

                findings.append({
                    'error_type': f'hb_{status}',
                    'keyword': f'sid={sid} {status}',
                    'context': f'{ts} {title}\n{suggest}',
                    'line_number': 0,
                    'timestamp': ts,
                })

    except Exception as e:
        logger.warning(f'Error scanning heartbeat.log', exc_info=True)

    return findings


def _get_setting(key: str, default: str = '') -> str:
    """Read a system_settings value."""
    try:
        from src.models.database import query
        rows = query("SELECT value FROM system_settings WHERE key = ?", (key,))
        return rows[0]['value'] if rows else default
    except Exception:
        return default


def _scan_access_log() -> list:
    """Scan access.log for non-200 requests from 127.0.0.1"""
    findings = []
    filepath = os.path.join(LOG_DIR, 'access.log')
    last_pos = _positions.get(filepath, 0)
    
    try:
        if not os.path.isfile(filepath):
            return findings
        
        file_size = os.path.getsize(filepath)
        if file_size < last_pos:
            last_pos = 0
        
        with open(filepath, 'r', errors='replace') as f:
            f.seek(last_pos)
            new_content = f.read()
            new_pos = f.tell()
            _positions[filepath] = new_pos
            
            if not new_content.strip():
                return findings
            
            lines = new_content.split('\n')
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                match = ACCESS_LOG_PATTERN.search(line)
                if match:
                    findings.append({
                        'error_type': 'HTTP错误',
                        'keyword': f"HTTP {match.group('status')}",
                        'context': line,
                        'line_number': 0,
                        'timestamp': match.group('time')[:19] if match.group('time') else '',
                    })
        
        _positions[filepath] = new_pos
        
    except Exception as e:
        logger.warning(f'Error scanning access.log: {e}')
    
    return findings


def _do_scan() -> int:
    """Perform one scan cycle. Returns number of errors found.
    
    Sends alerts via HTTP POST to /api/events/ingest (DB-independent path)
    so they bypass the main app's DB lock. Falls back to emit_log_error if
    the HTTP endpoint is unavailable.
    """
    if not is_event_enabled('log_error'):
        return 0
    
    logger.info('Starting log scan...')
    total_errors = 0
    
    # Collect all findings first
    all_findings = []
    
    # Scan regular log files
    log_files = _get_log_files()
    for filepath in log_files:
        findings = _scan_file(filepath)
        for finding in findings:
            all_findings.append({
                'log_file': os.path.basename(filepath),
                **finding,
            })
    
    # Scan access log
    access_findings = _scan_access_log()
    for finding in access_findings:
        all_findings.append({
            'log_file': 'access.log',
            **finding,
        })

    # Scan audit log for login failures
    try:
        audit_findings = _scan_audit_log()
        for finding in audit_findings:
            all_findings.append({
                'log_file': 'audit.log',
                **finding,
            })
    except Exception as e:
        logger.warning(f'audit log scan failed: {e}')

    # Scan heartbeat.log for session status notifications
    try:
        hb_findings = _scan_heartbeat_log()
        for finding in hb_findings:
            all_findings.append({
                'log_file': 'heartbeat.log',
                **finding,
            })
    except Exception as e:
        logger.warning(f'heartbeat log scan failed: {e}')

    # Send each finding via DB-independent HTTP callback
    for finding in all_findings:
        _send_alert_via_http(finding)
        total_errors += 1
    
    logger.info(f'Log scan complete: {total_errors} errors found')
    _save_positions(_positions)
    return total_errors


def _send_alert_via_http(finding: dict):
    """Send alert via HTTP POST to /api/events/ingest (DB-independent).
    
    Falls back to emit_log_error() if the HTTP endpoint is unreachable.
    """
    import requests as _requests

    try:
        resp = _requests.post(
            'http://127.0.0.1:9999/api/system/events/ingest',
            json={
                'log_file': finding.get('log_file', ''),
                'error_type': finding.get('error_type', ''),
                'keyword': finding.get('keyword', ''),
                'context': finding.get('context', ''),
                'line_number': finding.get('line_number'),
            },
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get('skipped'):
                logger.debug(f"Log alert deduped: {finding.get('keyword')}")
            else:
                logger.info(f"Log alert sent: [{finding.get('error_type')}] {finding.get('keyword')}")
        else:
            logger.warning(f"Log alert HTTP {resp.status_code}: {resp.text[:100]}")
    except Exception:
        # Fallback to DB path if HTTP endpoint unavailable
        emit_log_error(
            log_file=finding.get('log_file', ''),
            error_type=finding.get('error_type', ''),
            keyword=finding.get('keyword', ''),
            context=finding.get('context', ''),
            line_number=finding.get('line_number'),
        )


def _scan_loop():
    """Main scan loop — runs independently of collection state."""
    logger.info(f'Log scanner started, interval={SCAN_INTERVAL}s')

    while not _stop_event.wait(SCAN_INTERVAL):
        try:
            _do_scan()
        except Exception as e:
            logger.error(f'Error in scan loop: {e}')


# ── Public API ────────────────────────────────────────────

def start():
    """Start the log scanner in a background thread"""
    global _scanner_thread, _stop_event, _positions
    
    if _scanner_thread and _scanner_thread.is_alive():
        logger.warning('Log scanner already running')
        return
    
    _stop_event.clear()
    _positions = _load_positions()
    _scanner_thread = Thread(target=_scan_loop, daemon=True, name='log_scanner')
    _scanner_thread.start()
    logger.info('Log scanner thread started')


def stop():
    """Stop the log scanner"""
    global _stop_event, _scanner_thread
    
    _stop_event.set()
    if _scanner_thread:
        _scanner_thread.join(timeout=5)
    logger.info('Log scanner stopped')


def run_once() -> int:
    """Run a single scan (for manual trigger). Returns error count."""
    return _do_scan()