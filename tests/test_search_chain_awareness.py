"""Tests for chain-aware /api/data/search response.

The data page search returns snapshot rows. Same physical snapshot can map
to N chains (URL shared by chain-A WAF + chain-B 海光 etc). Frontend renders
the right table; to show one row per chain (not collapsed into 1 per physical
file), backend must attach a `chains` list per hit describing all chains
the snap belongs to.
"""

import json
import sqlite3

import pytest


@pytest.fixture
def seeded_db(monkeypatch):
    """Build in-memory DB with snapshots + content_sources + 1 shared chain URL."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER,
        product_name TEXT DEFAULT '',
        version_branch TEXT DEFAULT '',
        package_type TEXT DEFAULT '',
        package_version TEXT DEFAULT '',
        file_name TEXT DEFAULT '',
        source_url TEXT DEFAULT '',
        status TEXT DEFAULT 'active',
        description_raw TEXT DEFAULT '',
        md5_hash TEXT DEFAULT '',
        published_at TEXT DEFAULT '',
        urgency TEXT DEFAULT 'normal'
    );

    CREATE TABLE content_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT DEFAULT '',
        package_type TEXT DEFAULT '{}'
    );
    """)

    # Source 1 (UTS): URL shared by 9 chains (4 UTS + 3 UTS-NDR + 3 信创海光)
    paths = [
        # UTS 标准版本 (4 chains, same URL)
        {"url": "/update/listBsaUtsDetail/v/rule3.0.0", "chain": ["UTS", "UTS标准", "V2.0R01F00", "规则升级包"]},
        {"url": "/update/listBsaUtsDetail/v/rule3.0.0", "chain": ["UTS", "UTS标准", "V2.0R01F01", "规则升级包"]},
        {"url": "/update/listBsaUtsDetail/v/rule3.0.0", "chain": ["UTS", "UTS标准", "V2.0R01F02", "规则升级包"]},
        {"url": "/update/listBsaUtsDetail/v/rule3.0.0", "chain": ["UTS", "UTS标准", "V2.0R01F02_CW", "规则升级包"]},
        # UTS-NDR (3 chains, same URL)
        {"url": "/update/listBsaUtsDetail/v/rule3.0.0", "chain": ["UTS", "UTS-NDR", "V2.0R01F00", "规则升级包"]},
        {"url": "/update/listBsaUtsDetail/v/rule3.0.0", "chain": ["UTS", "UTS-NDR", "V2.0R01F01", "规则升级包"]},
        {"url": "/update/listBsaUtsDetail/v/rule3.0.0", "chain": ["UTS", "UTS-NDR", "V2.0R01F02", "规则升级包"]},
        # 信创海光 (3 chains, same URL)
        {"url": "/update/listBsaUtsDetail/v/rule3.0.0", "chain": ["UTS", "信创海光", "海光系列", "V2.0R01F00", "规则升级包"]},
        {"url": "/update/listBsaUtsDetail/v/rule3.0.0", "chain": ["UTS", "信创海光", "海光系列", "V2.0R01F01", "规则升级包"]},
        {"url": "/update/listBsaUtsDetail/v/rule3.0.0", "chain": ["UTS", "信创海光", "海光系列", "V2.0R01F02", "规则升级包"]},
        # Unrelated URL for sanity check
        {"url": "/update/waf/v/rule6.0.9", "chain": ["WAF", "WAF V6.0.9", "规则升级包"]},
    ]
    conn.execute(
        "INSERT INTO content_sources (id, name, package_type) VALUES (?, ?, ?)",
        (1, "UTS", json.dumps({"paths": paths}, ensure_ascii=False)),
    )

    # One physical snapshot for UTS URL
    conn.execute(
        """INSERT INTO snapshots (id, source_id, product_name, version_branch,
                                 package_type, package_version, file_name,
                                 source_url, status, description_raw, md5_hash,
                                 published_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (8405, 1, 'UTS', 'V2.0R01F01', '规则升级包', '3.0.0.45416',
         'eoi.unify.allrulepatch.uts.3.0.0.45416.rule',
         'https://update.nsfocus.com/update/listBsaUtsDetail/v/rule3.0.0',
         'active',
         '规则版本变为3.0.0.45416',
         'e9b4f7dd164f2dccc731738048ccaf7d',
         '2026-08-21 09:00:00'),
    )
    conn.commit()
    return conn


def test_search_returns_chains_per_hit(monkeypatch, seeded_db):
    """Backend should attach `chains` (list of list-of-str) per hit."""
    # Import inside the test to avoid global module load side-effects
    from src.models import database as db_mod

    # Override db_mod.query to point at our in-memory conn
    conn = seeded_db

    def fake_query(sql, params=()):
        return [dict(zip(r.keys(), r)) for r in conn.execute(sql, params).fetchall()]

    monkeypatch.setattr(db_mod, 'query', fake_query)

    # Now invoke the route via Flask test client.
    # Simpler: import and call the function directly.
    from src.web.routes.api_routes import search_snapshots
    from flask import Flask, g
    from src.web.auth import require_auth

    # Bypass auth — call the wrapped function directly
    unwrapped = search_snapshots.__wrapped__ if hasattr(search_snapshots, '__wrapped__') else search_snapshots
    app = Flask(__name__)
    with app.test_request_context('/api/data/search?q=3.0.0.45416&field=version_branch&limit=20',
                                  headers={'Authorization': 'Bearer test-token'}):
        try:
            resp, _status = unwrapped()
        except TypeError:
            resp, _status = unwrapped(), 200
    raw = resp.get_json() if hasattr(resp, 'get_json') else resp
    data = raw['data']

    snaps = data['snapshots']
    assert len(snaps) == 1
    hit = snaps[0]
    assert hit['id'] == 8405
    assert hit['package_version'] == '3.0.0.45416'

    # NEW: chains list attached, length = number of chains sharing this URL
    chains = hit.get('chains', [])
    assert len(chains) == 10, (
        f'expected 10 chains (4 UTS标准 + 3 UTS-NDR + 3 信创海光), got {len(chains)}: {chains}'
    )

    # Each chain is a list of strings
    assert all(isinstance(c, list) for c in chains)
    assert all(isinstance(seg, str) for c in chains for seg in c)

    # Sanity: chain-A, chain-B distinct — confirm chain ending 'V2.0R01F00'
    # appears in 3 different product paths (UTS标准, UTS-NDR, 信创海光)
    endings = [c[-2] if len(c) >= 2 else None for c in chains]
    assert endings.count('V2.0R01F00') == 3, (
        f'V2.0R01F00 should appear 3 times (across 3 product sub-paths), '
        f'got: {endings}'
    )


def test_search_includes_chains_field_in_response(monkeypatch, seeded_db):
    """Regression marker: ensure the new field is documented in the response."""
    from src.models import database as db_mod
    conn = seeded_db

    def fake_query(sql, params=()):
        return [dict(zip(r.keys(), r)) for r in conn.execute(sql, params).fetchall()]

    monkeypatch.setattr(db_mod, 'query', fake_query)

    from src.web.routes.api_routes import search_snapshots
    from flask import Flask
    from src.web.auth import require_auth

    unwrapped = search_snapshots.__wrapped__ if hasattr(search_snapshots, '__wrapped__') else search_snapshots
    app = Flask(__name__)
    with app.test_request_context('/api/data/search?q=45416&field=file_name&limit=20',
                                  headers={'Authorization': 'Bearer test-token'}):
        try:
            resp, _status = unwrapped()
        except TypeError:
            resp, _status = unwrapped(), 200
    raw = resp.get_json() if hasattr(resp, 'get_json') else resp
    data = raw['data']

    hit = data['snapshots'][0]
    assert 'chains' in hit
    assert isinstance(hit['chains'], list)
    assert len(hit['chains']) > 0


def test_search_chains_empty_when_no_matching_url(monkeypatch, seeded_db):
    """When search hits no rows (sanity), chains list is irrelevant — endpoint
    returns empty list, no error."""
    from src.models import database as db_mod
    conn = seeded_db

    def fake_query(sql, params=()):
        return [dict(zip(r.keys(), r)) for r in conn.execute(sql, params).fetchall()]

    monkeypatch.setattr(db_mod, 'query', fake_query)

    from src.web.routes.api_routes import search_snapshots
    from flask import Flask
    from src.web.auth import require_auth

    unwrapped = search_snapshots.__wrapped__ if hasattr(search_snapshots, '__wrapped__') else search_snapshots
    app = Flask(__name__)
    with app.test_request_context('/api/data/search?q=zzz_nonexistent_zzz&field=file_name',
                                  headers={'Authorization': 'Bearer test-token'}):
        try:
            resp, _status = unwrapped()
        except TypeError:
            resp, _status = unwrapped(), 200
    raw = resp.get_json() if hasattr(resp, 'get_json') else resp
    data = raw['data']
    assert data['snapshots'] == []
    assert data['count'] == 0