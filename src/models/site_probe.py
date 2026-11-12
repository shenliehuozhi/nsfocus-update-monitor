"""update.nsfocus.com 探测历史采样。

每轮探测写一行；观测页按时间窗读取并降采样成延时曲线点。
默认保留 30 天，写入时顺带清理过期行，避免表无限增长。
"""

from datetime import datetime, timedelta, timezone

RETENTION_DAYS = 30
DEFAULT_MAX_POINTS = 180

SCHEMA_SITE_PROBE_HISTORY = """
CREATE TABLE IF NOT EXISTS site_probe_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at TEXT NOT NULL,
    status TEXT NOT NULL,
    http_status INTEGER,
    latency_ms INTEGER,
    error TEXT DEFAULT ''
)
"""

SCHEMA_SITE_PROBE_HISTORY_INDEX = """
CREATE INDEX IF NOT EXISTS idx_site_probe_history_checked
    ON site_probe_history (checked_at)
"""


def create_tables(db):
    db.execute(SCHEMA_SITE_PROBE_HISTORY)
    db.execute(SCHEMA_SITE_PROBE_HISTORY_INDEX)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec='seconds')


def record(state: dict):
    """Append one probe sample and prune rows beyond the retention window."""
    from src.models.database import execute
    checked_at = state.get('checked_at') or _iso(datetime.now(timezone.utc))
    execute(
        "INSERT INTO site_probe_history "
        "(checked_at, status, http_status, latency_ms, error) VALUES (?, ?, ?, ?, ?)",
        (checked_at, state.get('status') or 'unknown', state.get('http_status'),
         state.get('latency_ms'), state.get('error') or ''),
    )
    cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS))
    execute("DELETE FROM site_probe_history WHERE datetime(checked_at) < datetime(?)", (cutoff,))


def _parse_ts(value):
    if not value:
        return None
    text = str(value)
    dt = None
    try:
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        try:
            dt = datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _summary(rows: list) -> dict:
    up = [r['latency_ms'] for r in rows
          if r['status'] == 'up' and r['latency_ms'] is not None]
    return {
        'samples': len(rows),
        'failures': sum(1 for r in rows if r['status'] == 'down'),
        'avg_ms': int(round(sum(up) / len(up))) if up else None,
        'max_ms': max(up) if up else None,
    }


def history(hours: int, max_points: int = DEFAULT_MAX_POINTS) -> tuple[list, dict]:
    """Return ``(points, summary)`` for the trailing window.

    ``points`` is ordered by time; when there are more rows than ``max_points``
    they are averaged into time buckets. Down buckets keep ``ms=None`` so the
    chart can draw a failure marker instead of a fake latency.
    """
    from src.models.database import query
    hours = max(1, int(hours))
    max_points = max(2, int(max_points))
    now = datetime.now(timezone.utc)
    rows = query(
        "SELECT checked_at, status, latency_ms FROM site_probe_history "
        "WHERE datetime(checked_at) >= datetime(?) "
        "ORDER BY datetime(checked_at), id",
        (_iso(now - timedelta(hours=hours)),),
    )
    summary = _summary(rows)
    if len(rows) <= max_points:
        return [{
            't': r['checked_at'],
            'ms': r['latency_ms'] if r['status'] == 'up' else None,
            's': 'up' if r['status'] == 'up' else 'down',
        } for r in rows], summary

    bucket_s = hours * 3600 / max_points
    start = now - timedelta(hours=hours)
    buckets: dict[int, list] = {}
    for row in rows:
        ts = _parse_ts(row['checked_at'])
        if ts is None:
            continue
        idx = int((ts - start).total_seconds() / bucket_s)
        idx = min(max(idx, 0), max_points - 1)
        buckets.setdefault(idx, []).append(row)

    points = []
    for idx in sorted(buckets):
        bucket = buckets[idx]
        up = [r['latency_ms'] for r in bucket
              if r['status'] == 'up' and r['latency_ms'] is not None]
        down = any(r['status'] == 'down' for r in bucket)
        points.append({
            't': bucket[-1]['checked_at'],
            'ms': None if down or not up else int(round(sum(up) / len(up))),
            's': 'down' if down else 'up',
        })
    return points, summary
