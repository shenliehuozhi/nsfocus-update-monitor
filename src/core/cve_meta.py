"""CVE 评分元数据同步（NVD API 2.0）。

只补齐 ``snapshot_cve`` 中出现且属于活跃快照的 CVE，按需请求 NVD：
- 优先 CVSS v4.0 → v3.1 → v3.0 → v2，指标取 Primary（无则退回第一条）；
- ``status='ok'`` 表示已拿到 NVD 结果（即使暂无 CVSS，也记录发布时间）；
- ``not_found`` / ``error`` 带 ``next_retry_at`` 退避，避免每轮重复打接口；
- 429/403 视为限流，记录一次失败后立即结束本轮，等待下一轮再试；
- 超时、连接错误、5xx、无效 JSON 视为 NVD 临时故障，15 分钟后重试并结束本轮。

限速按 NVD 公开规则：无 key 5 次/30 秒，有 key 50 次/30 秒。每轮批量
默认 5 / 20 条，保证调度线程（max_workers=1）不会被长时间占用。
API key 只从 ``MONITOR_NVD_API_KEY`` 读取，不写日志、不落库。
"""

import os
import threading
import time
from datetime import timedelta

import requests

from src.core.logger import get_logger
from src.core.timeutil import utc_iso, utc_now

logger = get_logger('cve_meta')

NVD_API_URL = 'https://services.nvd.nist.gov/rest/json/cves/2.0'
NVD_DETAIL_URL = 'https://nvd.nist.gov/vuln/detail/'
REQUEST_TIMEOUT = 15
MAX_DESCRIPTION = 2000
SOURCE = 'nvd'

# NVD 公开限速：无 key 5 req/30s，有 key 50 req/30s
MIN_INTERVAL_NO_KEY = 6.0
MIN_INTERVAL_KEY = 0.6
BATCH_LIMIT_NO_KEY = 5
BATCH_LIMIT_KEY = 20

# error 状态退避（分钟），按 retry_count 递增，封顶最后一项
RETRY_BACKOFF_MINUTES = (10, 30, 120, 360, 1440)
NOT_FOUND_RETRY_DAYS = 7
RATE_LIMIT_BACKOFF_MINUTES = 15
# 网络超时、连接错误、HTTP 5xx、无效 JSON 属于 NVD 侧临时故障：
# 只等 15 分钟再试，不按单条 CVE 的退避阶梯升级。
TRANSIENT_RETRY_MINUTES = 15

# CVSS 指标优先级（版本从高到低）
_METRIC_KEYS = (
    ('cvssMetricV40', '4.0'),
    ('cvssMetricV31', '3.1'),
    ('cvssMetricV30', '3.0'),
    ('cvssMetricV2', '2.0'),
)

_VALID_SEVERITIES = ('critical', 'high', 'medium', 'low')

_sync_lock = threading.Lock()
_last_request_at = 0.0

_UPSERT_SQL = """
INSERT INTO cve_meta (
    cve_id, cvss_version, cvss_score, severity, vector, description_en,
    published_at, modified_at, source, source_url, status,
    fetched_at, next_retry_at, retry_count
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(cve_id) DO UPDATE SET
    cvss_version = excluded.cvss_version,
    cvss_score = excluded.cvss_score,
    severity = excluded.severity,
    vector = excluded.vector,
    description_en = excluded.description_en,
    published_at = excluded.published_at,
    modified_at = excluded.modified_at,
    source = excluded.source,
    source_url = excluded.source_url,
    status = excluded.status,
    fetched_at = excluded.fetched_at,
    next_retry_at = excluded.next_retry_at,
    retry_count = excluded.retry_count
"""

# 失败只更新状态与退避字段；已拿到的评分不能因为一次临时失败被清空
_SAVE_FAILURE_SQL = """
INSERT INTO cve_meta (
    cve_id, source, source_url, status, fetched_at, next_retry_at, retry_count
) VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(cve_id) DO UPDATE SET
    source = excluded.source,
    source_url = excluded.source_url,
    status = excluded.status,
    fetched_at = excluded.fetched_at,
    next_retry_at = excluded.next_retry_at,
    retry_count = excluded.retry_count
"""


def lookup_enabled() -> bool:
    """``MONITOR_CVE_LOOKUP=off|0|false|no`` 可关闭外部评分查询。"""
    value = (os.getenv('MONITOR_CVE_LOOKUP', 'auto') or '').strip().lower()
    return value not in ('0', 'off', 'false', 'no', 'disabled')


def _api_key() -> str:
    return (os.getenv('MONITOR_NVD_API_KEY', '') or '').strip()


def default_batch_limit() -> int:
    return BATCH_LIMIT_KEY if _api_key() else BATCH_LIMIT_NO_KEY


def _rate_limit():
    """两次真实请求之间至少间隔 NVD 允许的最小值。"""
    global _last_request_at
    interval = MIN_INTERVAL_KEY if _api_key() else MIN_INTERVAL_NO_KEY
    elapsed = time.monotonic() - _last_request_at
    wait = interval - elapsed
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _headers() -> dict:
    headers = {
        'User-Agent': 'nsfocus-monitor/1.0 cve-sync',
        'Accept': 'application/json',
    }
    key = _api_key()
    if key:
        headers['apiKey'] = key
    return headers


def fetch_nvd(cve_id: str, timeout: int = REQUEST_TIMEOUT) -> tuple:
    """请求单个 CVE；返回 ``(status_code, payload)``。

    网络异常原样抛出，由 ``sync_pending`` 统一记录为 error。
    """
    _rate_limit()
    resp = requests.get(
        NVD_API_URL,
        params={'cveId': cve_id},
        headers=_headers(),
        timeout=timeout,
    )
    try:
        code = resp.status_code
        payload = resp.json() if code == 200 else None
        return code, payload
    finally:
        resp.close()


def _norm_ts(value) -> str:
    if not value:
        return ''
    try:
        return utc_iso(value)
    except (TypeError, ValueError):
        return ''


def _severity_from_score(score) -> str:
    if score is None:
        return 'unknown'
    if score >= 9.0:
        return 'critical'
    if score >= 7.0:
        return 'high'
    if score >= 4.0:
        return 'medium'
    if score > 0:
        return 'low'
    return 'unknown'


def parse_nvd_payload(payload) -> dict | None:
    """解析 NVD 2.0 响应；无漏洞条目返回 None。

    返回值只包含可落库字段，``status`` 为 ``ok`` 或 ``rejected``。
    """
    if not isinstance(payload, dict):
        return None
    vulnerabilities = payload.get('vulnerabilities') or []
    if not vulnerabilities:
        return None
    cve = vulnerabilities[0].get('cve') or {}
    if not cve:
        return None

    descriptions = cve.get('descriptions') or []
    description = next(
        (str(item.get('value') or '') for item in descriptions
         if isinstance(item, dict) and item.get('lang') == 'en'),
        '',
    )[:MAX_DESCRIPTION]
    result = {
        'cvss_version': '',
        'cvss_score': None,
        'severity': 'unknown',
        'vector': '',
        'description_en': description,
        'published_at': _norm_ts(cve.get('published')),
        'modified_at': _norm_ts(cve.get('lastModified')),
        'status': 'ok',
    }
    if str(cve.get('vulnStatus') or '').strip().lower() == 'rejected':
        result['status'] = 'rejected'
        return result

    metrics = cve.get('metrics') or {}
    for key, fallback_version in _METRIC_KEYS:
        entries = metrics.get(key) or []
        if not entries:
            continue
        primary = [m for m in entries if str(m.get('type') or '').lower() == 'primary']
        metric = (primary or entries)[0]
        data = metric.get('cvssData') or {}
        raw_score = data.get('baseScore')
        try:
            score = float(raw_score) if raw_score is not None else None
        except (TypeError, ValueError):
            score = None
        if score is None:
            continue
        severity = str(data.get('baseSeverity') or metric.get('baseSeverity') or '').lower()
        if severity not in _VALID_SEVERITIES:
            severity = _severity_from_score(score)
        result.update({
            'cvss_version': str(data.get('version') or fallback_version),
            'cvss_score': score,
            'severity': severity,
            'vector': str(data.get('vectorString') or '')[:300],
        })
        break
    return result


def _pending_rows(threshold: str, limit: int, force: bool) -> list:
    from src.models.database import query

    condition = '' if force else """
              AND (m.cve_id IS NULL OR (
                    m.status IN ('pending', 'error', 'not_found')
                    AND (m.next_retry_at IS NULL OR m.next_retry_at <= ?)
                  ))"""
    params = (limit,) if force else (threshold, limit)
    return query(f"""
        SELECT sc.cve_id,
               MAX(COALESCE(NULLIF(s.published_at, ''),
                            replace(s.first_seen_at, ' ', 'T'))) AS latest_at,
               COUNT(*) AS links
        FROM snapshot_cve sc
        JOIN snapshots s ON s.id = sc.snapshot_id
        LEFT JOIN cve_meta m ON m.cve_id = sc.cve_id
        WHERE s.status = 'active'{condition}
        GROUP BY sc.cve_id
        ORDER BY MIN(m.cve_id IS NULL) DESC, latest_at DESC, links DESC, sc.cve_id
        LIMIT ?
    """, params)


def _pending_count(threshold: str, force: bool) -> int:
    from src.models.database import query

    condition = '' if force else """
              AND (m.cve_id IS NULL OR (
                    m.status IN ('pending', 'error', 'not_found')
                    AND (m.next_retry_at IS NULL OR m.next_retry_at <= ?)
                  ))"""
    params = () if force else (threshold,)
    rows = query(f"""
        SELECT COUNT(DISTINCT sc.cve_id) AS n
        FROM snapshot_cve sc
        JOIN snapshots s ON s.id = sc.snapshot_id
        LEFT JOIN cve_meta m ON m.cve_id = sc.cve_id
        WHERE s.status = 'active'{condition}
    """, params)
    return rows[0]['n'] if rows else 0


def _existing_retry_count(cve_id: str) -> int:
    from src.models.database import query

    rows = query("SELECT retry_count FROM cve_meta WHERE cve_id = ?", (cve_id,))
    try:
        return int(rows[0]['retry_count'] or 0) if rows else 0
    except (TypeError, ValueError):
        return 0


def _save(cve_id: str, record: dict, status: str, next_retry_at,
          retry_count: int = 0):
    from src.models.database import execute

    execute(_UPSERT_SQL, (
        cve_id,
        record.get('cvss_version') or '',
        record.get('cvss_score'),
        record.get('severity') or 'unknown',
        record.get('vector') or '',
        record.get('description_en') or '',
        record.get('published_at') or '',
        record.get('modified_at') or '',
        SOURCE,
        NVD_DETAIL_URL + cve_id,
        status,
        utc_iso(),
        next_retry_at,
        retry_count,
    ))


def _save_failure(cve_id: str, status: str, next_retry_at, retry_count: int):
    from src.models.database import execute

    execute(_SAVE_FAILURE_SQL, (
        cve_id,
        SOURCE,
        NVD_DETAIL_URL + cve_id,
        status,
        utc_iso(),
        next_retry_at,
        retry_count,
    ))


def _backoff(retry_count: int) -> timedelta:
    index = min(max(retry_count, 0), len(RETRY_BACKOFF_MINUTES) - 1)
    return timedelta(minutes=RETRY_BACKOFF_MINUTES[index])


def _record_error(cve_id: str, reason: str, transient: bool = False) -> str:
    retry_count = _existing_retry_count(cve_id) + 1
    if transient:
        next_retry = utc_iso(utc_now() + timedelta(minutes=TRANSIENT_RETRY_MINUTES))
    else:
        next_retry = utc_iso(utc_now() + _backoff(retry_count - 1))
    logger.warning('[CVE-META] %s fetch failed: %s (retry #%d at %s)',
                   cve_id, reason, retry_count, next_retry)
    _save_failure(cve_id, 'error', next_retry, retry_count)
    return next_retry


def sync_pending(limit: int | None = None, force: bool = False) -> dict:
    """补齐缺失/到期的 CVE 评分；返回本轮统计。

    单条网络异常记录为 ``error`` 并继续；限流/5xx 提前结束本轮。
    """
    summary = {
        'skipped': False,
        'reason': '',
        'candidates': 0,
        'selected': 0,
        'fetched': 0,
        'ok': 0,
        'not_found': 0,
        'rejected': 0,
        'errors': 0,
        'rate_limited': False,
        'elapsed_s': 0.0,
    }
    if not lookup_enabled():
        summary.update({'skipped': True, 'reason': 'MONITOR_CVE_LOOKUP disabled'})
        return summary

    batch = default_batch_limit() if limit is None else max(1, int(limit))
    if not _sync_lock.acquire(blocking=False):
        summary.update({'skipped': True, 'reason': 'sync already running'})
        return summary

    started = time.monotonic()
    try:
        threshold = utc_iso()
        summary['candidates'] = _pending_count(threshold, force)
        rows = _pending_rows(threshold, batch, force)
        summary['selected'] = len(rows)
        for row in rows:
            cve_id = row['cve_id']
            try:
                code, payload = fetch_nvd(cve_id)
            except requests.RequestException as e:
                # 超时/连接错误是 NVD 侧临时故障，本轮停止，避免连续打接口
                summary['errors'] += 1
                _record_error(cve_id, str(e)[:200] or e.__class__.__name__,
                              transient=True)
                break
            except Exception as e:
                summary['errors'] += 1
                _record_error(cve_id, str(e)[:200] or e.__class__.__name__)
                continue

            if code in (403, 429):
                summary['errors'] += 1
                summary['rate_limited'] = True
                retry_count = _existing_retry_count(cve_id) + 1
                next_retry = utc_iso(utc_now() + timedelta(minutes=RATE_LIMIT_BACKOFF_MINUTES))
                logger.warning('[CVE-META] NVD rate limited (HTTP %s), stop batch; '
                               'retry after %s', code, next_retry)
                _save_failure(cve_id, 'error', next_retry, retry_count)
                break

            if code == 404:
                summary['fetched'] += 1
                summary['not_found'] += 1
                retry_count = _existing_retry_count(cve_id) + 1
                next_retry = utc_iso(utc_now() + timedelta(days=NOT_FOUND_RETRY_DAYS))
                _save_failure(cve_id, 'not_found', next_retry, retry_count)
                continue

            if code >= 500:
                summary['errors'] += 1
                _record_error(cve_id, f'HTTP {code}', transient=True)
                break
            if code != 200:
                summary['errors'] += 1
                _record_error(cve_id, f'HTTP {code}')
                continue

            if payload is None:
                summary['errors'] += 1
                _record_error(cve_id, 'invalid JSON response', transient=True)
                break

            parsed = parse_nvd_payload(payload)
            summary['fetched'] += 1
            if parsed is None:
                summary['not_found'] += 1
                retry_count = _existing_retry_count(cve_id) + 1
                next_retry = utc_iso(utc_now() + timedelta(days=NOT_FOUND_RETRY_DAYS))
                _save_failure(cve_id, 'not_found', next_retry, retry_count)
                continue
            if parsed['status'] == 'rejected':
                summary['rejected'] += 1
                _save(cve_id, parsed, 'rejected', None, 0)
                continue
            summary['ok'] += 1
            _save(cve_id, parsed, 'ok', None, 0)
        return summary
    finally:
        summary['elapsed_s'] = round(time.monotonic() - started, 3)
        _sync_lock.release()
