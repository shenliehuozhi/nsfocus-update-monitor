#!/usr/bin/env python3
"""Migrate snapshots table to URL-based identity (drop chain dimension).

Background
----------
Prior to 2026-07-16, snapshots.path_id was computed as
MD5(source_url + JSON(chain))[:12]. Whenever the collector evolved
(chain text grew new ser_c_b_tit block names, whitespace shifted, etc.)
all snapshots for the same physical file would be re-keyed, producing
hundreds of spurious NEW/WITHDRAWN notifications on the next collect.

This migration moves snapshots to a stable identity keyed only on the
physical URL of the final page:

    snapshots.path_id   := MD5(source_url)[:12]
    snapshots UNIQUE    := (source_id, source_url, path_id, file_name, md5_hash)
                          partial index WHERE status='active'

Multiple chains within the same source that share one URL now collapse to
one active snapshot row. Different sources remain separate because
source_id is the product/business ownership boundary.

Behavior
--------
For active rows sharing (source_id, source_url, file_name, md5_hash): keep
the one with the most recent last_seen_at and delete the other physical
duplicate rows. Before deletion, references in delivery_log, delayed_queue,
and digest_queue are repointed to the retained row. This resolves the legacy
6-18 multiple-chain duplicates without leaving artificial audit history.

For non-active rows (superseded/withdrawn) sharing the same key: leave
as-is. The new partial unique index (WHERE status='active') permits
audit-history rows to coexist with a current active row at the same
identity — exactly what happens when a vendor removes a file
(withdrawn) and later re-releases it (new active row).

Recompute path_id for every active row using the new MD5(source_url)
algorithm.

Drop the old UNIQUE index, recreate as a partial unique index on
active rows only.

Idempotent: re-running is a no-op once the marker is set.

Usage
-----
Dry-run (default): print planned changes, do not write.
Apply:            --apply

The script also auto-runs on service startup if the
snapshots_migration_v3 marker row in system_settings is absent (see
src/app.py migration check).
"""

import argparse
import hashlib
import os
import sys

# Add project root so we can import src.models.database
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.models.database import get_db


MIGRATION_KEY = 'snapshots_migration_v3'


def md5_url(source_url: str) -> str:
    """Mirror of _compute_path_id in src/core/scheduler.py."""
    if not source_url:
        return ''
    return hashlib.md5(source_url.encode()).hexdigest()[:12]


def already_migrated(db) -> bool:
    """Return True if this migration has already run on this DB."""
    row = db.execute(
        "SELECT value FROM system_settings WHERE key=?", (MIGRATION_KEY,)
    ).fetchone()
    return bool(row and row[0] == '1')


def plan(db):
    """Return a dict describing planned changes.

    Three kinds of duplicates are resolved:
    1. Same key + same status='active' (multiple chains writing the same
       file under the new URL-based identity) → keep the one with the most
       recent last_seen_at, delete the other physical duplicates.
    2. Same key + mixed statuses (historical: file was once withdrawn then
       re-released, or legacy 6-18 path_id collisions) → keep the most
       recent 'active' row, others stay in their existing status (they
       are valid history once a partial unique index allows it).
    3. Same key + all non-active (pure history) → leave alone, partial
       unique index will allow it.
    """
    # Active rows with non-empty source_url, grouped by
    # (source_id, url, file, md5). source_id is the product boundary.
    rows = db.execute("""
        SELECT id, source_id, source_url, file_name, md5_hash,
               path_id, last_seen_at, status
        FROM snapshots
        WHERE source_url != ''
        ORDER BY source_id, source_url, file_name, md5_hash,
                 last_seen_at DESC, id DESC
    """).fetchall()

    # Group by identity. Within each group, keep the first row (most recent
    # last_seen_at, tie-break by highest id). All other ACTIVE rows in the
    # same group are deleted. Non-active rows are valid history and remain.
    seen_keys = {}  # (source_id, url, file, md5) -> keep_id
    delete_rows = []  # (duplicate_id, keep_id)
    for r in rows:
        key = (r[1], r[2], r[3], r[4])
        if r[7] != 'active':
            continue
        if key not in seen_keys:
            seen_keys[key] = r[0]
        else:
            delete_rows.append((r[0], seen_keys[key]))

    # Path_id updates: every retained active row needs URL-based path_id.
    duplicate_ids = {duplicate_id for duplicate_id, _ in delete_rows}
    pathid_updates = []
    for r in rows:
        if r[7] != 'active' or r[0] in duplicate_ids:
            continue
        new_pid = md5_url(r[2])
        if r[5] != new_pid:
            pathid_updates.append((r[0], new_pid))

    # Check current UNIQUE index shape
    idx = db.execute("""
        SELECT sql FROM sqlite_master
        WHERE type='index' AND name='idx_snapshots_unique'
    """).fetchone()
    current_sql = idx[0] if idx else ''
    normalized_sql = ' '.join((current_sql or '').lower().split())
    expected_columns = '(source_id, source_url, path_id, file_name, md5_hash)'
    needs_index_rebuild = (
        expected_columns not in normalized_sql
        or "where status='active'" not in normalized_sql.replace(' = ', '=')
    )

    return {
        'delete_rows': delete_rows,
        'pathid_updates': pathid_updates,
        'current_unique_sql': current_sql,
        'needs_index_rebuild': needs_index_rebuild,
        'active_total': sum(1 for r in rows if r[7] == 'active'),
        'active_unique': len(seen_keys),
    }


def apply(db, plan_data):
    """Apply the migration. Idempotent."""
    n_deleted = 0
    n_repointed = 0
    n_pathid = 0

    # 1. Repoint dependent rows, then physically delete duplicate snapshots.
    # Foreign keys use NO ACTION, so deleting first would fail (or orphan rows
    # when foreign_keys is disabled).
    reference_tables = ('delivery_log', 'delayed_queue', 'digest_queue')
    for duplicate_id, keep_id in plan_data['delete_rows']:
        for table in reference_tables:
            table_exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not table_exists:
                continue
            cur = db.execute(
                f"UPDATE {table} SET snapshot_id=? WHERE snapshot_id=?",
                (keep_id, duplicate_id),
            )
            n_repointed += cur.rowcount
        db.execute("DELETE FROM snapshots WHERE id=?", (duplicate_id,))
        n_deleted += 1

    # 2. Recompute path_id
    if plan_data['pathid_updates']:
        for sid, new_pid in plan_data['pathid_updates']:
            db.execute(
                "UPDATE snapshots SET path_id=? WHERE id=? AND status='active'",
                (new_pid, sid)
            )
            n_pathid += 1

    # 3. Rebuild UNIQUE index AFTER data is consistent (no duplicates, all
    # path_id consistent). The final shape includes source_id as the business
    # ownership boundary and is partial — only active rows must be unique.
    # Withdrawn/superseded rows can coexist with a later active row.
    index_rebuilt = False
    if plan_data['needs_index_rebuild']:
        db.execute("DROP INDEX IF EXISTS idx_snapshots_unique")
        db.execute("""
            CREATE UNIQUE INDEX idx_snapshots_unique
            ON snapshots(source_id, source_url, path_id, file_name, md5_hash)
            WHERE status = 'active'
        """)
        index_rebuilt = True

    # 4. Mark migration done
    db.execute("""
        INSERT OR REPLACE INTO system_settings (key, value, updated_at)
        VALUES (?, '1', datetime('now'))
    """, (MIGRATION_KEY,))

    db.commit()
    return {
        'deleted': n_deleted,
        'references_repointed': n_repointed,
        'pathid_updated': n_pathid,
        'unique_index_rebuilt': index_rebuilt,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--apply', action='store_true',
                    help='Apply changes (default is dry-run).')
    args = ap.parse_args()

    db = get_db()

    if already_migrated(db):
        print(f'✓ Already migrated (system_settings.{MIGRATION_KEY}=1). Nothing to do.')
        return 0

    plan_data = plan(db)
    print('=' * 60)
    print('Plan:')
    print(f'  active rows (with source_url): {plan_data["active_total"]}')
    print(f'  unique (source_id, source_url, file, md5): {plan_data["active_unique"]}')
    print(f'  to delete (duplicates):         {len(plan_data["delete_rows"])}')
    print(f'  path_id updates:                {len(plan_data["pathid_updates"])}')
    print(f'  unique index needs rebuild:     {plan_data["needs_index_rebuild"]}')
    print(f'  current unique SQL: {plan_data["current_unique_sql"][:80]}...' if plan_data['current_unique_sql'] else '')
    print('=' * 60)

    if not args.apply:
        print('Dry-run (use --apply to commit).')
        return 0

    result = apply(db, plan_data)
    print()
    print('✓ Applied:')
    print(f'  deleted duplicates: {result["deleted"]} rows')
    print(f'  references repointed: {result["references_repointed"]} rows')
    print(f'  path_id updated: {result["pathid_updated"]} rows')
    print(f'  unique index rebuilt: {result["unique_index_rebuilt"]}')
    print(f'  marker: system_settings.{MIGRATION_KEY}=1')


if __name__ == '__main__':
    sys.exit(main() or 0)