#!/usr/bin/env python3
"""Rotate MONITOR_SECRET_KEY without losing encrypted data.

MONITOR_SECRET_KEY is not just Flask's SECRET_KEY — it is the AES-GCM key for
channels.config (SMTP/webhook credentials) and user_sessions.cookie_value
(NSFOCUS PHPSESSID). Replacing it in .env without re-encrypting those rows
makes them unreadable.

This tool decrypts every encrypted row with the current key and re-encrypts it
with the new key in one transaction, after taking a timestamped DB backup.

Usage:
  # preview (default): counts only, no writes, no backup
  MONITOR_SECRET_KEY_NEW=<64hex> python3 scripts/rotate_encryption_key.py

  # apply (requires --apply --yes), service must be stopped
  MONITOR_SECRET_KEY_NEW=<64hex> python3 scripts/rotate_encryption_key.py --apply --yes

Old key resolution:
  MONITOR_SECRET_KEY_OLD if set, else MONITOR_SECRET_KEY if valid, else the
  same 'a'*64 development fallback that src/core/crypto.py uses.

After a successful apply, put MONITOR_SECRET_KEY=<new> into .env (chmod 600)
and start the service. Rows that fail both keys are reported and left as-is —
they were already unreadable before the rotation.
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.crypto import decrypt_with_key, encrypt_with_key  # noqa: E402

BAK_KEEP = int(os.getenv('MONITOR_DB_BAK_KEEP', '3'))
DEV_FALLBACK_KEY = 'a' * 64
TARGETS = (
    ('channels', 'config'),
    ('user_sessions', 'cookie_value'),
)


def resolve_db_path() -> Path:
    data_dir = os.getenv('MONITOR_DATA_DIR')
    if not data_dir:
        data_dir = str(REPO_ROOT / 'data')
    return Path(data_dir) / 'nsfocus_monitor.db'


def _valid_key(key: str) -> bool:
    if not key or len(key) != 64:
        return False
    try:
        bytes.fromhex(key)
    except ValueError:
        return False
    return True


def resolve_keys() -> tuple[str, str]:
    old_key = os.getenv('MONITOR_SECRET_KEY_OLD', '').strip()
    if not old_key:
        configured = os.getenv('MONITOR_SECRET_KEY', '').strip()
        old_key = configured if _valid_key(configured) else DEV_FALLBACK_KEY
    new_key = os.getenv('MONITOR_SECRET_KEY_NEW', '').strip()
    if not _valid_key(new_key):
        raise SystemExit('MONITOR_SECRET_KEY_NEW must be set to 64 hex chars')
    if new_key == old_key:
        raise SystemExit('MONITOR_SECRET_KEY_NEW equals the current key; nothing to do')
    return old_key, new_key


def backup_db(db_path: Path) -> Path:
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = db_path.with_name(f'{db_path.name}.bak.{ts}_pre_rotate_enc')
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(backup))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    _rotate_backups(db_path)
    return backup


def _rotate_backups(db_path: Path) -> None:
    if BAK_KEEP <= 0:
        return
    baks = sorted(db_path.parent.glob(f'{db_path.name}.bak.*_pre_rotate_enc'),
                  key=lambda p: p.name, reverse=True)
    for p in baks[BAK_KEEP:]:
        if p.is_symlink() or not p.is_file():
            continue
        try:
            p.unlink()
            print(f'  [轮转] 删除旧备份 {p.name}')
        except OSError as e:
            print(f'  [轮转] 删除失败 {p.name}: {e}')


def rotate(db_path: Path, old_key: str, new_key: str, apply: bool = False) -> dict:
    """Re-encrypt target columns. Returns a summary dict (no plaintext)."""
    summary = {'rotated': 0, 'already_new': 0, 'unreadable': [], 'dry_run': not apply, 'backup': None}
    if apply:
        summary['backup'] = str(backup_db(db_path))
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        if apply:
            conn.execute('BEGIN IMMEDIATE')
        for table, column in TARGETS:
            try:
                rows = conn.execute(
                    f'SELECT id, {column} AS value FROM {table} WHERE {column} IS NOT NULL AND {column} != ""'
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            for row in rows:
                try:
                    plaintext = decrypt_with_key(row['value'], old_key)
                except Exception:
                    try:
                        decrypt_with_key(row['value'], new_key)
                        summary['already_new'] += 1
                        continue
                    except Exception:
                        summary['unreadable'].append(f'{table}.{column} id={row["id"]}')
                        continue
                if not apply:
                    summary['rotated'] += 1
                    continue
                ciphertext = encrypt_with_key(plaintext, new_key)
                if decrypt_with_key(ciphertext, new_key) != plaintext:
                    raise RuntimeError(f'round-trip check failed for {table} id={row["id"]}')
                conn.execute(f'UPDATE {table} SET {column}=? WHERE id=?', (ciphertext, row['id']))
                summary['rotated'] += 1
        if apply:
            conn.execute('COMMIT')
    except Exception:
        if apply:
            try:
                conn.execute('ROLLBACK')
            except sqlite3.Error:
                pass
        raise
    finally:
        conn.close()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description='Re-encrypt channels/user_sessions with a new MONITOR_SECRET_KEY')
    parser.add_argument('--apply', action='store_true', help='write changes (default is preview)')
    parser.add_argument('--yes', action='store_true', help='skip interactive confirmation')
    args = parser.parse_args()

    db_path = resolve_db_path()
    if not db_path.is_file():
        print(f'DB not found: {db_path}')
        return 1

    old_key, new_key = resolve_keys()
    if old_key == DEV_FALLBACK_KEY:
        print('WARN: old key is the development fallback (a*64); pass MONITOR_SECRET_KEY_OLD to override')

    if args.apply and not args.yes:
        print(f'About to re-encrypt {db_path} using 2 tables.')
        if input('Type "rotate" to continue: ').strip() != 'rotate':
            print('Aborted')
            return 1

    if args.apply:
        print(f'Re-encrypting {db_path} (backup + 2 tables).')

    summary = rotate(db_path, old_key, new_key, apply=args.apply)
    mode = 'APPLY' if args.apply else 'PREVIEW'
    if summary['backup']:
        print(f'Backup created: {summary["backup"]}')
    print(f'[{mode}] rotated={summary["rotated"]} already_new={summary["already_new"]} '
          f'unreadable={len(summary["unreadable"])}')
    for item in summary['unreadable']:
        print(f'  [unreadable] {item}')
    if not args.apply:
        print('No writes performed. Re-run with --apply --yes during a maintenance window.')
    elif summary['unreadable']:
        print('Some rows were already unreadable with both keys; they were left untouched.')
    else:
        print('Done. Set MONITOR_SECRET_KEY to the new key in .env (chmod 600), then start the service.')
    return 0 if not summary['unreadable'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
