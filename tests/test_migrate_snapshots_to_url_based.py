"""Tests for the URL-based snapshot migration.

These tests use an isolated in-memory SQLite database. They never import the
scheduler/notifier/app and cannot send real notifications.
"""

import hashlib
import importlib.util
import sqlite3
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_snapshots_to_url_based.py"
_SPEC = importlib.util.spec_from_file_location("migrate_snapshots_to_url_based", _SCRIPT)
_MIGRATION = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MIGRATION)


def _db():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(
        """
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL,
            source_url TEXT NOT NULL,
            file_name TEXT NOT NULL,
            md5_hash TEXT NOT NULL,
            path_id TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            status TEXT NOT NULL,
            rollback_confirmed_at TEXT
        );
        CREATE UNIQUE INDEX idx_snapshots_unique
            ON snapshots(source_id, path_id, file_name, md5_hash);
        CREATE TABLE system_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        );
        CREATE TABLE delivery_log (
            id INTEGER PRIMARY KEY,
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id)
        );
        CREATE TABLE delayed_queue (
            id INTEGER PRIMARY KEY,
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id)
        );
        CREATE TABLE digest_queue (
            id INTEGER PRIMARY KEY,
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id)
        );
        """
    )
    return db


def _insert(db, row):
    db.execute(
        """INSERT INTO snapshots
           (id, source_id, source_url, file_name, md5_hash, path_id,
            last_seen_at, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        row,
    )


def test_plan_deduplicates_within_source_id_but_not_across_sources():
    db = _db()
    url = "https://update.nsfocus.com/update/shared"
    # Same source: row 2 is newer and must win. Different source: row 3 is
    # independent business data and must survive even though URL/file/MD5 match.
    _insert(db, (1, 10, url, "pkg.zip", "abc", "old-a", "2026-07-01", "active"))
    _insert(db, (2, 10, url, "pkg.zip", "abc", "old-b", "2026-07-02", "active"))
    _insert(db, (3, 20, url, "pkg.zip", "abc", "old-c", "2026-07-03", "active"))

    planned = _MIGRATION.plan(db)

    assert planned["delete_rows"] == [(1, 2)]
    assert planned["row_total"] == 3
    assert planned["row_unique"] == 2
    assert {row_id for row_id, _ in planned["pathid_updates"]} == {2, 3}


def test_plan_deduplicates_all_statuses_by_new_path_id_file_md5_and_sid():
    db = _db()
    url = "https://update.nsfocus.com/update/history"
    # Different legacy chain-based path_ids and statuses collapse after path_id
    # is recalculated from URL. The newest row wins regardless of status.
    _insert(db, (1, 10, url, "pkg.zip", "abc", "legacy-a", "2026-07-01", "active"))
    _insert(db, (2, 10, url, "pkg.zip", "abc", "legacy-b", "2026-07-02", "superseded"))
    _insert(db, (3, 10, url, "pkg.zip", "abc", "legacy-c", "2026-07-03", "withdrawn"))

    planned = _MIGRATION.plan(db)

    assert planned["delete_rows"] == [(2, 3), (1, 3)]
    assert planned["row_total"] == 3
    assert planned["row_unique"] == 1
    assert planned["pathid_updates"] == [
        (3, hashlib.md5(url.encode()).hexdigest()[:12])
    ]


def test_apply_repoints_references_deletes_duplicate_and_builds_sid_index():
    db = _db()
    url = "https://update.nsfocus.com/update/shared"
    expected_pid = hashlib.md5(url.encode()).hexdigest()[:12]
    _insert(db, (1, 10, url, "pkg.zip", "abc", "old-a", "2026-07-01", "active"))
    _insert(db, (2, 10, url, "pkg.zip", "abc", "old-b", "2026-07-02", "active"))
    # A different MD5 is a different identity and remains, but its historical
    # path_id is still migrated to the URL-based value.
    _insert(db, (4, 10, url, "pkg.zip", "different", "historic", "2026-06-01", "superseded"))
    for table in ("delivery_log", "delayed_queue", "digest_queue"):
        db.execute(f"INSERT INTO {table} (id, snapshot_id) VALUES (1, 1)")

    result = _MIGRATION.apply(db, _MIGRATION.plan(db))

    assert result == {
        "deleted": 1,
        "references_repointed": 3,
        "pathid_updated": 2,
        "unique_index_rebuilt": True,
    }
    assert db.execute("SELECT COUNT(*) FROM snapshots WHERE id=1").fetchone()[0] == 0
    assert db.execute("SELECT path_id FROM snapshots WHERE id=2").fetchone()[0] == expected_pid
    assert db.execute("SELECT status, path_id FROM snapshots WHERE id=4").fetchone() == (
        "superseded",
        expected_pid,
    )
    for table in ("delivery_log", "delayed_queue", "digest_queue"):
        assert db.execute(f"SELECT snapshot_id FROM {table} WHERE id=1").fetchone()[0] == 2

    index_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_snapshots_unique'"
    ).fetchone()[0]
    assert "source_id, source_url, path_id, file_name, md5_hash" in index_sql
    assert "WHERE status = 'active'" in index_sql
    assert db.execute(
        "SELECT value FROM system_settings WHERE key='snapshots_migration_v3'"
    ).fetchone()[0] == "1"
