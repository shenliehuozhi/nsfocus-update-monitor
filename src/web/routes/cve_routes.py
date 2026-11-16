"""CVE 情报接口：仪表盘卡片聚合与明细列表。

口径：
- 只统计 ``status='active'`` 的快照；时间窗口按 ``published_at``，
  缺失时回退 ``first_seen_at``。
- 同一 CVE 跨产品/升级包会出现多行；summary 按 CVE 聚合，list 保留逐行。
- ``cve_meta.status='rejected'`` 的 CVE 不参与统计。
- 「关联 CVE」包含规则包的历史防护覆盖，不等同于本次修复。
"""

from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from src.core.timeutil import utc_iso
from src.web.auth import require_auth

bp = Blueprint('cve', __name__, url_prefix='/api/cve')

SEVERITIES = ('critical', 'high', 'medium', 'low', 'unknown')
MAX_DAYS = 365
MAX_LIMIT = 200

# published_at 为 ISO('T')；first_seen_at 为 'YYYY-MM-DD HH:MM:SS'
_RELEASED_AT = "COALESCE(NULLIF(s.published_at, ''), replace(s.first_seen_at, ' ', 'T'))"


def _window_cte() -> str:
    return f"""
        WITH cve_window AS (
            SELECT
                sc.snapshot_id,
                sc.cve_id,
                sc.evidence,
                sc.confidence,
                s.product_name,
                s.package_type,
                s.version_branch,
                s.file_name,
                s.package_version,
                s.source_url,
                {_RELEASED_AT} AS released_at,
                COALESCE(NULLIF(m.severity, ''), 'unknown') AS severity,
                m.cvss_score AS cvss_score,
                m.cvss_version AS cvss_version,
                COALESCE(m.description_en, '') AS description_en,
                COALESCE(m.name_zh, '') AS name_zh,
                COALESCE(m.description_zh, '') AS description_zh,
                COALESCE(m.cnvd_id, '') AS cnvd_id,
                COALESCE(m.status, 'pending') AS meta_status,
                CASE
                    WHEN s.package_type LIKE '%规则%' OR lower(s.package_type) LIKE '%rule%'
                    THEN 'rule' ELSE 'sys'
                END AS pkg_kind
            FROM snapshot_cve sc
            JOIN snapshots s ON s.id = sc.snapshot_id
            LEFT JOIN cve_meta m ON m.cve_id = sc.cve_id
            WHERE s.status = 'active'
              AND {_RELEASED_AT} >= ?
              AND COALESCE(m.status, 'pending') != 'rejected'
        )
    """


def _severity_rank_sql() -> str:
    return (
        "CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3 "
        "WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END"
    )


def _parse_days() -> int:
    value = request.args.get('days', 30, type=int)
    if not isinstance(value, int):
        value = 30
    return max(1, min(MAX_DAYS, value))


def _window_start(days: int) -> tuple:
    now = datetime.utcnow()
    started = now - timedelta(days=days)
    return now, utc_iso(started), started


def _escape_like(value: str) -> str:
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def _cnvd_url(cnvd_id: str) -> str:
    return f'https://www.cnvd.org.cn/flaw/show/{cnvd_id}' if cnvd_id else ''


@bp.route('/summary', methods=['GET'])
@require_auth
def cve_summary():
    from src.models.database import query

    days = _parse_days()
    now, threshold, started = _window_start(days)

    totals = query(_window_cte() + """
        SELECT COUNT(*) AS links,
               COUNT(DISTINCT snapshot_id) AS snapshots,
               COUNT(DISTINCT cve_id) AS distinct_cves,
               COUNT(DISTINCT CASE WHEN cvss_score IS NOT NULL THEN cve_id END) AS scored_cves,
               COUNT(DISTINCT CASE WHEN cnvd_id != '' THEN cve_id END) AS cnvd_cves
        FROM cve_window
    """, (threshold,))[0]

    severity_rows = query(_window_cte() + """
        SELECT severity, COUNT(DISTINCT cve_id) AS n
        FROM cve_window
        GROUP BY severity
    """, (threshold,))
    by_severity = {sev: 0 for sev in SEVERITIES}
    for row in severity_rows:
        by_severity[row['severity'] if row['severity'] in by_severity else 'unknown'] = row['n']

    by_device = query(_window_cte() + """
        SELECT product_name,
               COUNT(DISTINCT cve_id) AS cves,
               COUNT(*) AS links,
               SUM(CASE WHEN pkg_kind = 'sys' THEN 1 ELSE 0 END) AS sys_links,
               SUM(CASE WHEN pkg_kind = 'rule' THEN 1 ELSE 0 END) AS rule_links,
               MAX(released_at) AS latest_at
        FROM cve_window
        GROUP BY product_name
        ORDER BY cves DESC, links DESC, product_name
        LIMIT 20
    """, (threshold,))

    by_package_type = query(_window_cte() + """
        SELECT package_type, pkg_kind,
               COUNT(DISTINCT cve_id) AS cves,
               COUNT(*) AS links
        FROM cve_window
        GROUP BY package_type, pkg_kind
        ORDER BY links DESC, package_type
        LIMIT 30
    """, (threshold,))

    top_rows = query(_window_cte() + f"""
        SELECT cve_id,
               MAX(cvss_score) AS cvss_score,
               MAX(cvss_version) AS cvss_version,
               MAX(description_en) AS description_en,
               MAX(name_zh) AS name_zh,
               MAX(description_zh) AS description_zh,
               MAX(cnvd_id) AS cnvd_id,
               severity,
               COUNT(DISTINCT product_name) AS device_count,
               COUNT(*) AS links,
               MAX(released_at) AS latest_at
        FROM cve_window
        GROUP BY cve_id
        ORDER BY {_severity_rank_sql()} DESC,
                 (MAX(cvss_score) IS NULL),
                 MAX(cvss_score) DESC,
                 latest_at DESC,
                 cve_id
        LIMIT 10
    """, (threshold,))

    top_cves = []
    if top_rows:
        placeholders = ','.join(['?'] * len(top_rows))
        detail_rows = query(_window_cte() + f"""
            SELECT DISTINCT cve_id, product_name, pkg_kind
            FROM cve_window
            WHERE cve_id IN ({placeholders})
            ORDER BY cve_id, product_name
        """, (threshold, *[row['cve_id'] for row in top_rows]))
        devices = {}
        pkg_kinds = {}
        for row in detail_rows:
            devices.setdefault(row['cve_id'], []).append(row['product_name'])
            kinds = pkg_kinds.setdefault(row['cve_id'], [])
            if row['pkg_kind'] not in kinds:
                kinds.append(row['pkg_kind'])
        for row in top_rows:
            top_cves.append({
                'cve_id': row['cve_id'],
                'cvss_score': row['cvss_score'],
                'cvss_version': row['cvss_version'] or '',
                'description': row['description_en'] or '',
                'name_zh': row['name_zh'] or '',
                'description_zh': row['description_zh'] or '',
                'cnvd_id': row['cnvd_id'] or '',
                'cnvd_url': _cnvd_url(row['cnvd_id'] or ''),
                'severity': row['severity'],
                'device_count': row['device_count'],
                'devices': devices.get(row['cve_id'], []),
                'pkg_kinds': pkg_kinds.get(row['cve_id'], []),
                'links': row['links'],
                'latest_at': row['latest_at'],
            })

    trend_rows = query(_window_cte() + """
        SELECT substr(released_at, 1, 10) AS day,
               severity,
               COUNT(DISTINCT cve_id) AS n
        FROM cve_window
        GROUP BY day, severity
    """, (threshold,))
    trend_map = {(row['day'], row['severity']): row['n'] for row in trend_rows}
    trend = []
    day = started.date()
    end_day = now.date()
    while day <= end_day:
        key = day.isoformat()
        point = {'date': key}
        for sev in SEVERITIES:
            point[sev] = trend_map.get((key, sev), 0)
        trend.append(point)
        day += timedelta(days=1)

    distinct_cves = totals['distinct_cves'] or 0
    scored_cves = totals['scored_cves'] or 0
    return jsonify({'code': 0, 'data': {
        'generated_at': utc_iso(now),
        'window_days': days,
        'totals': {
            'links': totals['links'] or 0,
            'snapshots': totals['snapshots'] or 0,
            'distinct_cves': distinct_cves,
            'scored_cves': scored_cves,
            'unscored_cves': max(0, distinct_cves - scored_cves),
            'cnvd_cves': totals['cnvd_cves'] or 0,
        },
        'by_severity': by_severity,
        'by_device': [dict(row) for row in by_device],
        'by_package_type': [dict(row) for row in by_package_type],
        'top_cves': top_cves,
        'trend': trend,
    }})


@bp.route('/list', methods=['GET'])
@require_auth
def cve_list():
    from src.models.database import query

    days = _parse_days()
    _, threshold, _ = _window_start(days)

    severity = (request.args.get('severity') or '').strip().lower()
    if severity and severity not in SEVERITIES:
        return jsonify({'code': 1, 'message': 'severity 取值不合法'}), 400
    product = (request.args.get('product') or '').strip()[:120]
    package_type = (request.args.get('package_type') or '').strip()[:120]
    keyword = (request.args.get('q') or '').strip()[:64]

    page = request.args.get('page', 1, type=int) or 1
    page = max(1, page)
    limit = request.args.get('limit', 50, type=int) or 50
    limit = max(1, min(MAX_LIMIT, limit))

    where = []
    params = [threshold]
    if severity:
        where.append('severity = ?')
        params.append(severity)
    if product:
        where.append('product_name = ?')
        params.append(product)
    if package_type:
        where.append('package_type = ?')
        params.append(package_type)
    if keyword:
        like = f'%{_escape_like(keyword)}%'
        where.append(
            "(cve_id LIKE ? ESCAPE '\\' OR description_en LIKE ? ESCAPE '\\' "
            "OR name_zh LIKE ? ESCAPE '\\' OR description_zh LIKE ? ESCAPE '\\' "
            "OR cnvd_id LIKE ? ESCAPE '\\' "
            "OR file_name LIKE ? ESCAPE '\\' OR package_version LIKE ? ESCAPE '\\')"
        )
        params.extend([like, like, like, like, like, like, like])
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    total = query(_window_cte() + f"SELECT COUNT(*) AS n FROM cve_window {where_sql}", tuple(params))[0]['n']

    rows = query(_window_cte() + f"""
        SELECT snapshot_id, cve_id, evidence, confidence,
               product_name, package_type, pkg_kind,
               version_branch, file_name, package_version, source_url,
               released_at, severity, cvss_score, cvss_version,
               description_en AS description, name_zh, description_zh,
               cnvd_id, meta_status
        FROM cve_window
        {where_sql}
        ORDER BY {_severity_rank_sql()} DESC,
                 (cvss_score IS NULL),
                 cvss_score DESC,
                 released_at DESC,
                 cve_id,
                 snapshot_id
        LIMIT ? OFFSET ?
    """, tuple(params) + (limit, (page - 1) * limit))

    items = []
    for row in rows:
        item = dict(row)
        item['cnvd_url'] = _cnvd_url(item.get('cnvd_id') or '')
        items.append(item)

    return jsonify({'code': 0, 'data': {
        'items': items,
        'total': total,
        'page': page,
        'limit': limit,
        'days': days,
    }})
