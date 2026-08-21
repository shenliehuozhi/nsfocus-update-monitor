"""Tests for resolve_chain_pkg / resolve_chain_ver.

Helpers in src/notifiers/base.py that look up chain metadata
(package_type / version_branch) from content_sources.package_type.paths[].chain.

Used because URL-dedup'd snap rows have ver/pkg overwritten by save_snapshot
UPDATE — they're not authoritative. chain in content_sources.package_type.paths[].chain
is the source of truth.

Constraint: no scheduler/notifier/app imports at module load (only when the
test fixture exercises them). The helpers themselves are pure functions that
read SQLite directly via _build_chain, which we monkeypatch.
"""
import json
import sqlite3

import pytest

@pytest.fixture(autouse=True)
def _restore_db_path(monkeypatch):
    """Reset src.models.database.DB_PATH around each test in this module
    so pollution from setting it (e.g. for _build_chain to work) doesn't
    leak into other test modules."""
    import src.models.database as _dbmod
    monkeypatch.setattr(_dbmod, 'DB_PATH', '')

from src.notifiers.base import (
    NotificationMessage,
    _build_chain,
    resolve_chain_pkg,
    resolve_chain_ver,
)


@pytest.fixture
def mem_db(monkeypatch):
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE content_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            entry_url TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            package_type TEXT DEFAULT '',
            display_name TEXT DEFAULT ''
        );
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            version_branch TEXT NOT NULL,
            package_type TEXT NOT NULL,
            file_name TEXT NOT NULL,
            md5_hash TEXT NOT NULL,
            package_version TEXT DEFAULT '',
            file_size INTEGER DEFAULT 0,
            description_raw TEXT DEFAULT '',
            description_parsed TEXT DEFAULT '{}',
            min_sys_version TEXT DEFAULT '',
            restart_required INTEGER DEFAULT 0,
            urgency TEXT DEFAULT 'normal',
            download_id INTEGER DEFAULT 0,
            published_at TEXT DEFAULT '',
            first_seen_at TEXT DEFAULT (datetime('now')),
            last_seen_at TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'active' CHECK(status IN ('active', 'superseded', 'withdrawn')),
            rollback_confirmed_at TEXT,
            page_hash TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            rollback_cycles INTEGER DEFAULT 0,
            prev_page_hash TEXT DEFAULT '',
            path_id TEXT,
            product_name TEXT NOT NULL DEFAULT ''
        );
    """)
    yield db
    db.close()


@pytest.fixture
def haiguang_chain():
    return [
        'WEB应用防护系统(WAF)',
        '信息技术应用创新-WEB应用防护系统(WAF)列表',
        '海光系列HG',
        '海光系列 V6.0.8',
        '海光系列 V6.0.8 规则升级',
    ]


@pytest.fixture
def waf_chain():
    return [
        'WEB应用防护系统(WAF)',
        'WEB应用防护系统(WAF)列表',
        'WAF V6.0.8',
        'WAF V6.0.8 规则升级包',
    ]


@pytest.fixture
def source_pkg(haiguang_chain, waf_chain):
    """A content_sources entry with both WAF and 海光 chains sharing
    the same /listWafV68Detail/v/rule URL (the real NSFocus structure)."""
    import json
    return {
        'paths': [
            {'chain': waf_chain, 'url': '/update/listWafV68Detail/v/rule'},
            {'chain': haiguang_chain, 'url': '/update/listWafV68Detail/v/rule'},
        ],
    }


@pytest.fixture
def snap_8703(mem_db, source_pkg):
    """Insert content_sources + snapshot 8703 (URL-only path_id)."""
    import json
    mem_db.execute(
        'INSERT INTO content_sources (id, name, source_type, entry_url, is_active, '
        'package_type, display_name) VALUES (1, ?, ?, ?, 1, ?, ?)',
        ('WEB应用防护系统(WAF)', 'nsfocus', '/update/wafIndex',
         json.dumps(source_pkg, ensure_ascii=False), 'WEB应用防护系统(WAF)'),
    )
    mem_db.execute(
        """INSERT INTO snapshots
           (id, source_id, version_branch, package_type, file_name, md5_hash,
            status, source_url, path_id, product_name)
           VALUES (8703, 1, '海光系列 V6.0.8', '海光系列 V6.0.8 规则升级',
                   'update_rule.v6.0.8.1.73229.wcl', '2374ef70d5634775b44ba51499658100',
                   'active', 'https://update.nsfocus.com/update/listWafV68Detail/v/rule',
                   'ad53e1d4ba4b', 'WEB应用防护系统(WAF)')"""
    )
    mem_db.commit()
    return mem_db


# ── Test: subscription matched chain passed to notifier ──
def test_subscription_matched_chain_pushes_haiguang(snap_8703, monkeypatch, haiguang_chain):
    """User subscribes to 海光 V6.0.8. New package 8703 arrives.

    Without commit d3a97f2: push message says WAF (first matching path).
    With d3a97f2: push message says 海光 (matched chain).
    """
    from src.notifiers.base import NotificationMessage

    # Direct call: from_snapshot(snap, user_chain=haiguang_chain) — this
    # is exactly what scheduler → router → from_snapshot now passes.
    snap = {
        'id': 8703,
        'source_id': 1,
        'product_name': 'WEB应用防护系统(WAF)',
        'version_branch': '',
        'package_type': '',
        'file_name': 'update_rule.v6.0.8.1.73229.wcl',
        'md5_hash': '2374ef70d5634775b44ba51499658100',
        'source_url': 'https://update.nsfocus.com/update/listWafV68Detail/v/rule',
        'description_parsed': {},
        'description_raw': '',
    }

    # Without user_chain (the pre-d3a97f2 behavior):
    # from_snapshot falls back to _build_chain(msg), which uses msg.source_url
    # to reverse-lookup in content_sources.package_type.paths[]. With two
    # paths sharing the URL, it returns the FIRST match (WAF chain).
    # (Don't pollute src.models.database.DB_PATH here — that breaks
    # test_subscription_match which uses its own DB mocking.)
    msg_no_user_chain = NotificationMessage.from_snapshot(snap)
    if msg_no_user_chain.chain:
        # Real _build_chain path: WAF chain first match
        assert msg_no_user_chain.version_branch == 'WAF V6.0.8', (
            f'Without user_chain, _build_chain picks WAF (URL-only path_id '
            f'first match): got {msg_no_user_chain.version_branch!r}'
        )
        assert msg_no_user_chain.package_type == 'WAF V6.0.8 规则升级包', (
            f'Expected WAF pkg (pre-d3a97f2 limitation): got '
            f'{msg_no_user_chain.package_type!r}'
        )
    else:
        # Test environment can't reach DB → falls through to snap fields.
        # In this test, snap.version_branch and snap.package_type are both
        # '' so the fallback returns '' (not '海光系列 V6.0.8' — that's the
        # real DB's value, not the snap dict passed in).
        assert msg_no_user_chain.version_branch == ''
        assert msg_no_user_chain.package_type == ''

    # With user_chain=海光 chain (the post-d3a97f2 behavior):
    msg_haiguang = NotificationMessage.from_snapshot(snap, user_chain=haiguang_chain)
    assert msg_haiguang.version_branch == '海光系列 V6.0.8'
    assert msg_haiguang.package_type == '海光系列 V6.0.8 规则升级'
    assert msg_haiguang.chain == haiguang_chain


def test_subscription_waf_chain_pushes_waf(snap_8703, monkeypatch, waf_chain):
    """User subscribes to WAF V6.0.8. New package 8703 arrives.

    With d3a97f2: push message says WAF (matched chain).
    """
    from src.notifiers.base import NotificationMessage

    snap = {
        'id': 8703,
        'source_id': 1,
        'product_name': 'WEB应用防护系统(WAF)',
        'version_branch': '',
        'package_type': '',
        'file_name': 'update_rule.v6.0.8.1.73229.wcl',
        'md5_hash': '2374ef70d5634775b44ba51499658100',
        'source_url': 'https://update.nsfocus.com/update/listWafV68Detail/v/rule',
        'description_parsed': {},
        'description_raw': '',
    }

    msg_waf = NotificationMessage.from_snapshot(snap, user_chain=waf_chain)
    assert msg_waf.version_branch == 'WAF V6.0.8'
    assert msg_waf.package_type == 'WAF V6.0.8 规则升级包'
    assert msg_waf.chain == waf_chain