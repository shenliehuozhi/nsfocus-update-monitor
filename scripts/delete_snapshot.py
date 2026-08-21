#!/usr/bin/env python3
"""Generic snapshot deletion tool with chain-scoped awareness.

Replaces the original delete_snap_8863_preview.py (hardcoded to one id).
Now takes --snap-id (or --snap-id-file for batch) and supports three modes:

  preview         Default. Show what would be deleted. No writes.
  delete-snapshot Delete only the snapshots row(s). Leave delivery_log /
                  delayed_queue / digest_queue references intact (FK becomes
                  dangling; OK because production FK is OFF in some paths).
                  This matches the A-mode choice when cleaning a single
                  physical file before re-collecting to verify chain-scoped
                  push.
  delete-cascade  Also delete the FK-referencing rows in delivery_log /
                  delayed_queue / digest_queue. Use when you want a fully
                  clean slate.

Safety:
  - Default mode is preview; no DB writes.
  - --apply required to switch to a destructive mode.
  - --yes required to skip the interactive confirm.
  - A timestamped backup is created automatically before any destructive op.

Examples:
  python3 scripts/delete_snapshot.py --snap-id 8996
  python3 scripts/delete_snapshot.py --snap-id 8996 --apply --yes
  python3 scripts/delete_snapshot.py --snap-id 8996 --mode delete-cascade --apply --yes
  python3 scripts/delete_snapshot.py --snap-id-file /tmp/snap_ids.txt
"""

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path('/root/nsfocus-monitor/data/nsfocus_monitor.db')


def preview(conn, snap_id):
    """Show what deletion would affect."""
    print(f'=== Preview of DELETE snapshot_id={snap_id} from {DB_PATH} ===\n')
    row = conn.execute(
        'SELECT id, source_id, source_url, version_branch, package_type, '
        'file_name, status, first_seen_at, last_seen_at '
        'FROM snapshots WHERE id=?', (snap_id,)).fetchone()
    if not row:
        print(f'Snapshot id={snap_id} NOT FOUND in DB — nothing to do.')
        return False
    print('Snapshot row that would be deleted:')
    for k in row.keys():
        print(f'  {k}: {row[k]}')
    print()
    for table in ('delivery_log', 'delayed_queue', 'digest_queue'):
        try:
            refs = conn.execute(
                f'SELECT COUNT(*) FROM {table} WHERE snapshot_id=?', (snap_id,)
            ).fetchone()[0]
            chain_a = conn.execute(
                f'SELECT COUNT(*) FROM {table} WHERE snapshot_id=? '
                f'AND chain_json IS NOT NULL AND chain_json != "[]"', (snap_id,)
            ).fetchone()[0]
            print(f'  {table}: {refs} total ({chain_a} chain-scoped)')
        except Exception as e:
            print(f'  {table}: {e}')
    return True


def apply(conn, snap_id, mode):
    """Create backup, then DELETE per mode."""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = DB_PATH.with_suffix(
        f'.db.bak.{ts}_pre_delete_{snap_id}'
    )
    shutil.copy2(DB_PATH, backup)
    print(f'Backup created: {backup}')

    if mode == 'delete-cascade':
        for table in ('delivery_log', 'delayed_queue', 'digest_queue'):
            cur = conn.execute(
                f'SELECT COUNT(*) FROM {table} WHERE snapshot_id=?', (snap_id,)
            )
            n = cur.fetchone()[0]
            if n:
                cur = conn.execute(
                    f'DELETE FROM {table} WHERE snapshot_id=?', (snap_id,)
                )
                print(f'  deleted {n} {table} row(s)')

    cur = conn.execute('DELETE FROM snapshots WHERE id=?', (snap_id,))
    n = cur.rowcount
    print(f'  deleted {n} snapshots row(s)')
    conn.commit()
    print(f'\nDone. snapshot_id={snap_id} deleted ({mode}). Backup: {backup}')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--snap-id', type=int, help='Single snapshot id to operate on')
    g.add_argument('--snap-id-file', type=Path,
                   help='Path to file with one snapshot id per line')
    ap.add_argument('--mode', choices=['preview', 'delete-snapshot', 'delete-cascade'],
                    default='preview',
                    help='preview (default) shows impact without writing; '
                         'delete-snapshot removes the snapshots row only '
                         '(leave FK dangling); delete-cascade also removes '
                         'delivery_log/delayed_queue/digest_queue refs.')
    ap.add_argument('--apply', action='store_true',
                    help='Actually DELETE (requires backup).')
    ap.add_argument('--yes', action='store_true',
                    help='Skip interactive confirmation.')
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f'DB not found: {DB_PATH}')
        return 1

    # Resolve ids
    if args.snap_id:
        snap_ids = [args.snap_id]
    else:
        snap_ids = [int(line.strip()) for line in args.snap_id_file.read_text().splitlines()
                     if line.strip()]
    if not snap_ids:
        print('No snapshot ids provided.')
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # Preview every target first; abort if any not found
        any_missing = False
        for sid in snap_ids:
            exists = preview(conn, sid)
            if not exists:
                any_missing = True
        if any_missing:
            print('\nOne or more snapshots not found. Aborting.')
            return 2

        if args.mode == 'preview' or not args.apply:
            print('\nNo changes made (preview mode or --apply not passed).')
            return 0

        if not args.yes:
            print(f'\nAbout to {args.mode.upper()} {len(snap_ids)} snapshot(s) from the live DB.')
            print('Type "yes" to confirm:')
            ans = input().strip().lower()
            if ans != 'yes':
                print('Aborted.')
                return 1

        for sid in snap_ids:
            apply(conn, sid, args.mode)
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())