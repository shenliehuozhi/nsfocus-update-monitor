#!/usr/bin/env python3
"""Preview-and-execute helper for deleting snapshot 8863.

SAFETY: by default --dry-run. The DB is NOT modified unless --apply is
explicitly passed AND --yes is passed. A backup is created automatically
before any --apply.

Usage:
  python3 scripts/delete_snap_8863_preview.py            # preview only
  python3 scripts/delete_snap_8863_preview.py --apply    # create backup + DELETE
  python3 scripts/delete_snap_8863_preview.py --apply --yes  # skip confirmation
"""
import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path('/root/nsfocus-monitor/data/nsfocus_monitor.db')
SNAP_ID = 8863


def preview(conn):
    """Show what deletion would affect."""
    print(f'=== Preview of DELETE snapshot_id={SNAP_ID} from {DB_PATH} ===\n')
    row = conn.execute(
        'SELECT id, source_id, source_url, version_branch, package_type, '
        'file_name, status, first_seen_at, last_seen_at '
        'FROM snapshots WHERE id=?', (SNAP_ID,)).fetchone()
    if not row:
        print(f'Snapshot id={SNAP_ID} NOT FOUND in DB — nothing to do.')
        return False
    print('Snapshot row that would be deleted:')
    for k in row.keys():
        print(f'  {k}: {row[k]}')
    print()
    # Foreign-key references
    for table in ('delivery_log', 'delayed_queue', 'digest_queue'):
        try:
            refs = conn.execute(
                f'SELECT COUNT(*) FROM {table} WHERE snapshot_id=?', (SNAP_ID,)
            ).fetchone()[0]
            print(f'  {table} references: {refs}')
        except Exception as e:
            print(f'  {table}: {e}')
    return True


def apply(conn):
    """Create backup, then DELETE."""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = DB_PATH.with_suffix(
        f'.db.bak.{ts}_pre_delete_8863'
    )
    shutil.copy2(DB_PATH, backup)
    print(f'Backup created: {backup}')

    # Repoint FK references
    # (delivery_log has FK, but FK is OFF in some paths —
    #  repoint to NULL via UPDATE; we delete any reference rows on
    #  snap_id that has no other candidate.)
    # Simpler: just delete ref rows first (they're logs).
    cur = conn.execute('SELECT COUNT(*) FROM delivery_log WHERE snapshot_id=?', (SNAP_ID,))
    n_log = cur.fetchone()[0]
    if n_log:
        cur = conn.execute('DELETE FROM delivery_log WHERE snapshot_id=?', (SNAP_ID,))
        print(f'  deleted {n_log} delivery_log rows')
    cur = conn.execute('SELECT COUNT(*) FROM delayed_queue WHERE snapshot_id=?', (SNAP_ID,))
    n_dq = cur.fetchone()[0]
    if n_dq:
        cur = conn.execute('DELETE FROM delayed_queue WHERE snapshot_id=?', (SNAP_ID,))
        print(f'  deleted {n_dq} delayed_queue rows')
    cur = conn.execute('SELECT COUNT(*) FROM digest_queue WHERE snapshot_id=?', (SNAP_ID,))
    n_dg = cur.fetchone()[0]
    if n_dg:
        cur = conn.execute('DELETE FROM digest_queue WHERE snapshot_id=?', (SNAP_ID,))
        print(f'  deleted {n_dg} digest_queue rows')

    cur = conn.execute('DELETE FROM snapshots WHERE id=?', (SNAP_ID,))
    n = cur.rowcount
    print(f'  deleted {n} snapshot row(s)')
    conn.commit()
    print(f'\nDone. Snapshot id={SNAP_ID} deleted. Backup: {backup}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true',
                    help='Actually DELETE (requires backup).')
    ap.add_argument('--yes', action='store_true',
                    help='Skip interactive confirmation.')
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f'DB not found: {DB_PATH}')
        return 1

    conn = sqlite3.connect(DB_PATH)
    try:
        exists = preview(conn)
        if not exists:
            return 0
        if not args.apply:
            print('\nNo changes made (--apply not passed).')
            return 0
        if not args.yes:
            print('\nAbout to DELETE snapshot 8863 from the live DB.')
            print('Type "yes" to confirm:')
            ans = input().strip().lower()
            if ans != 'yes':
                print('Aborted.')
                return 1
        apply(conn)
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())