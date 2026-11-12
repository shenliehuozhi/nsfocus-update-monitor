"""站点探测历史：写入、时间窗过滤、降采样与过期清理。"""
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    import src.models.database as db_mod

    monkeypatch.setattr(db_mod, 'DB_PATH', str(tmp_path / 'site.db'))
    if hasattr(db_mod._local, 'conn'):
        monkeypatch.setattr(db_mod._local, 'conn', None)
    db_mod.get_db()
    from src.models import init_all_tables
    init_all_tables()
    return db_mod


def _iso(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat(timespec='seconds')


def _record(status='up', latency_ms=100, http_status=200, **delta):
    from src.models.site_probe import record
    record({
        'status': status,
        'latency_ms': latency_ms,
        'http_status': http_status,
        'checked_at': _iso(**delta),
        'error': '' if status == 'up' else 'HTTP 503',
    })


def test_history_summarizes_up_and_down_samples(db):
    from src.models.site_probe import history

    _record('down', 500, 503, hours=3)
    _record('up', 200, hours=2)
    _record('up', 100, hours=1)

    points, summary = history(24)
    assert [p['s'] for p in points] == ['down', 'up', 'up']
    assert points[0]['ms'] is None
    assert summary == {
        'samples': 3,
        'failures': 1,
        'avg_ms': 150,
        'max_ms': 200,
    }


def test_history_follows_window(db):
    from src.models.site_probe import history

    _record('up', 100, hours=1)
    _record('up', 900, days=10)

    _, day = history(24)
    assert day['samples'] == 1
    assert day['max_ms'] == 100

    _, month = history(720)
    assert month['samples'] == 2
    assert month['max_ms'] == 900


def test_history_downsamples_to_max_points(db):
    from src.models.site_probe import history, record

    base = datetime.now(timezone.utc) - timedelta(minutes=59)
    for i in range(20):
        record({
            'status': 'up',
            'latency_ms': 100 + i * 10,
            'http_status': 200,
            'checked_at': (base + timedelta(minutes=i * 3)).isoformat(timespec='seconds'),
            'error': '',
        })
    _record('down', 500, 503, minutes=1)

    points, summary = history(1, max_points=5)
    assert summary['samples'] == 21
    assert summary['failures'] == 1
    assert 1 <= len(points) <= 5
    assert any(p['s'] == 'down' and p['ms'] is None for p in points)
    assert all(p['ms'] is None for p in points if p['s'] == 'down')


def test_record_prunes_rows_older_than_retention(db):
    from src.models.database import execute, query

    old = _iso(days=40)
    execute(
        "INSERT INTO site_probe_history (checked_at, status, latency_ms) VALUES (?, 'up', 50)",
        (old,),
    )
    _record('up', 100, hours=1)

    rows = query("SELECT checked_at FROM site_probe_history")
    assert len(rows) == 1
    assert rows[0]['checked_at'] != old
