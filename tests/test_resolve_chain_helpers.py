"""Tests for resolve_chain_pkg / resolve_chain_ver.

Helpers in src/notifiers/base.py that look up chain metadata
(package_type / version_branch) from content_sources.package_type.paths[].chain.

Used because URL-dedup'd snap rows have ver/pkg overwritten by save_snapshot
UPDATE — they're not authoritative. chain in content_sources IS authoritative.

Constraint: no scheduler/notifier/app imports at module load (only when the
test fixture exercises them). The helpers themselves are pure functions that
read SQLite directly via _build_chain, which we monkeypatch.
"""
import json
import sqlite3

import pytest

from src.notifiers.base import (
    NotificationMessage,
    _build_chain,
    resolve_chain_pkg,
    resolve_chain_ver,
)


@pytest.fixture
def mem_db(monkeypatch):
    """In-memory SQLite with content_sources populated for the WAF V6.0.8
    / 海光系列 V6.0.8 multi-chain shared URL scenario."""
    import src.models.database as db_mod
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE content_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            entry_url TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            package_type TEXT DEFAULT ''
        );
    """)
    pt = {
        'types': ['WAF V6.0.8 规则升级包', '海光系列 V6.0.8 规则升级'],
        'paths': [
            {
                'chain': ['WEB应用防护系统(WAF)', 'WEB应用防护系统(WAF)列表',
                           'WAF V6.0.8', 'WAF V6.0.8 规则升级包'],
                'url': '/update/listWafV68Detail/v/rule',
            },
            {
                'chain': ['WEB应用防护系统(WAF)', '信息技术应用创新-WEB应用防护系统(WAF)列表',
                           '海光系列HG', '海光系列 V6.0.8', '海光系列 V6.0.8 规则升级'],
                'url': '/update/listWafV68Detail/v/rule',
            },
        ],
    }
    db.execute(
        "INSERT INTO content_sources (id, name, source_type, entry_url, is_active, package_type) "
        "VALUES (1, 'WEB应用防护系统(WAF)', 'nsfocus', '/update/wafIndex', 1, ?)",
        (json.dumps(pt, ensure_ascii=False),)
    )
    db.commit()

    # Monkeypatch DB_PATH so _build_chain uses our in-memory DB
    monkeypatch.setattr(db_mod, 'DB_PATH', ':memory:')

    # _build_chain uses sqlite3.connect directly, not the module helpers.
    # Monkeypatch sqlite3.connect to dispatch on db_path='memory:'
    import sqlite3 as _sqlite3
    real_connect = _sqlite3.connect

    def _connect(db_path, *args, **kwargs):
        if db_path == ':memory:':
            return db
        return real_connect(db_path, *args, **kwargs)

    monkeypatch.setattr(_sqlite3, 'connect', _connect)
    return db


def test_resolve_chain_pkg_returns_pkg_from_chain(mem_db):
    """Snap with source_id=1, source_url matching the WAF chain path
    returns the WAF chain's last element (pkg_type), even if snap's
    stale package_type says '海光系列 V6.0.8 规则升级' (Bug #1 historical)."""
    snap = {
        'source_id': 1,
        'source_url': 'https://update.nsfocus.com/update/listWafV68Detail/v/rule',
        # snap 行是历史脏数据,故意写错
        'version_branch': '海光系列 V6.0.8',
        'package_type': '海光系列 V6.0.8 规则升级',
    }
    # 函数会找 paths[0] (WAF chain),因为 URL 匹配,但两条 chain 的 URL 一样
    # 所以返回 paths[0] chain 的最后一个 = 'WAF V6.0.8 规则升级包'
    assert resolve_chain_pkg(snap) == 'WAF V6.0.8 规则升级包'


def test_resolve_chain_ver_returns_version_from_chain(mem_db):
    snap = {
        'source_id': 1,
        'source_url': 'https://update.nsfocus.com/update/listWafV68Detail/v/rule',
        'version_branch': '海光系列 V6.0.8',
        'package_type': '海光系列 V6.0.8 规则升级',
    }
    # paths[0] chain[-2] = 'WAF V6.0.8'
    assert resolve_chain_ver(snap) == 'WAF V6.0.8'


def test_fallback_to_snap_when_chain_not_found(mem_db):
    """No matching path in content_sources → fall back to snap.package_type."""
    snap = {
        'source_id': 1,
        'source_url': '/update/some-url-not-in-paths',
        'package_type': 'fallback pkg',
        'version_branch': 'fallback ver',
    }
    assert resolve_chain_pkg(snap) == 'fallback pkg'
    assert resolve_chain_ver(snap) == 'fallback ver'


def test_fallback_empty_when_no_chain_no_snap(mem_db):
    snap = {'source_id': 1, 'source_url': '/update/no-such-url'}
    assert resolve_chain_pkg(snap) == ''
    assert resolve_chain_ver(snap) == ''


def test_from_snapshot_uses_chain_metadata():
    """NotificationMessage.from_snapshot() should use chain-derived ver/pkg,
    NOT snap's stale ver/pkg (which may have been overwritten by URL dedup)."""
    # Build a NotificationMessage via from_snapshot without DB I/O
    # by passing minimal snap with source_id/source_url that resolve
    snap = {
        'source_id': 1,
        'source_url': '/update/listWafV68Detail/v/rule',
        'product_name': 'WAF',
        'file_name': 'test.wcl',
        'package_version': 'v1',
        # snap 行故意写脏
        'version_branch': 'WRONG-SNAP-VER',
        'package_type': 'WRONG-SNAP-PKG',
        'description_parsed': '{}',
    }
    # Need to monkeypatch DB connection for _build_chain lookup
    import sqlite3 as _sqlite3

    # Build a temporary in-memory DB with content_sources
    mem = _sqlite3.connect(':memory:')
    mem.row_factory = _sqlite3.Row
    mem.executescript("""
        CREATE TABLE content_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, source_type TEXT, entry_url TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1, package_type TEXT DEFAULT ''
        );
    """)
    pt = {'paths': [{
        'chain': ['WAF', 'WAF V6.0.8', 'WAF V6.0.8 规则升级包'],
        'url': '/update/listWafV68Detail/v/rule',
    }]}
    mem.execute(
        "INSERT INTO content_sources (id, name, source_type, package_type) "
        "VALUES (1, 'WAF', 'nsfocus', ?)",
        (json.dumps(pt),)
    )
    mem.commit()

    # Patch DB_PATH and sqlite3.connect globally
    import src.models.database as db_mod
    db_mod.DB_PATH = ':memory:'

    real_connect = _sqlite3.connect

    def _connect(db_path, *args, **kwargs):
        if db_path == ':memory:':
            return mem
        return real_connect(db_path, *args, **kwargs)

    _sqlite3.connect = _connect

    try:
        msg = NotificationMessage.from_snapshot(snap)
        # 必须用 chain 的值,不是 snap 行写错的
        assert msg.package_type == 'WAF V6.0.8 规则升级包'
        assert msg.version_branch == 'WAF V6.0.8'
        assert msg.title == 'WAF WAF V6.0.8 规则升级包'
        # chain 字段也已填
        assert msg.chain == ['WAF', 'WAF V6.0.8', 'WAF V6.0.8 规则升级包']
    finally:
        _sqlite3.connect = real_connect


def test_from_snapshot_falls_back_to_snap_on_chain_failure():
    """When chain lookup fails, from_snapshot uses snap fields (best-effort)."""
    snap = {
        'source_id': 999,  # non-existent source
        'source_url': '/update/no-such-url',
        'product_name': 'Test',
        'file_name': 'test.zip',
        'package_version': 'v1',
        'version_branch': 'fallback-ver',
        'package_type': 'fallback-pkg',
        'description_parsed': '{}',
    }
    msg = NotificationMessage.from_snapshot(snap)
    # Chain lookup failed → fall back to snap fields
    assert msg.version_branch == 'fallback-ver'
    assert msg.package_type == 'fallback-pkg'
    assert msg.title == 'Test fallback-pkg'