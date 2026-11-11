"""Tests for scripts/rotate_encryption_key.py and explicit-key crypto helpers."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from src.core.crypto import decrypt_with_key, encrypt_with_key

OLD_KEY = 'a' * 64
NEW_KEY = 'b' * 64


def _load_script():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'rotate_encryption_key.py'
    spec = importlib.util.spec_from_file_location('rotate_encryption_key', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / 'nsfocus_monitor.db'
    conn = sqlite3.connect(str(path))
    conn.execute('CREATE TABLE channels (id INTEGER PRIMARY KEY, config TEXT NOT NULL)')
    conn.execute('CREATE TABLE user_sessions (id INTEGER PRIMARY KEY, cookie_value TEXT NOT NULL)')
    conn.execute('INSERT INTO channels (id, config) VALUES (1, ?)',
                 (encrypt_with_key('{"smtp_password": "pw"}', OLD_KEY),))
    conn.execute('INSERT INTO user_sessions (id, cookie_value) VALUES (1, ?)',
                 (encrypt_with_key('PHPSESSID=abc', OLD_KEY),))
    conn.commit()
    conn.close()
    return path


def _values(path):
    conn = sqlite3.connect(str(path))
    try:
        return dict(conn.execute('SELECT id, config FROM channels').fetchall()), \
               dict(conn.execute('SELECT id, cookie_value FROM user_sessions').fetchall())
    finally:
        conn.close()


def test_crypto_round_trip_with_explicit_keys():
    token = encrypt_with_key('secret', OLD_KEY)
    assert decrypt_with_key(token, OLD_KEY) == 'secret'
    with pytest.raises(Exception):
        decrypt_with_key(token, NEW_KEY)
    with pytest.raises(ValueError):
        encrypt_with_key('x', 'short')


def test_preview_counts_without_writing(db):
    mod = _load_script()
    before = _values(db)
    summary = mod.rotate(db, OLD_KEY, NEW_KEY, apply=False)
    assert summary == {'rotated': 2, 'already_new': 0, 'unreadable': [],
                       'dry_run': True, 'backup': None}
    assert _values(db) == before
    assert not list(db.parent.glob('*.bak.*_pre_rotate_enc'))


def test_apply_rotates_and_creates_backup(db):
    mod = _load_script()
    summary = mod.rotate(db, OLD_KEY, NEW_KEY, apply=True)
    assert summary['rotated'] == 2
    assert summary['unreadable'] == []
    assert Path(summary['backup']).is_file()

    channels, sessions = _values(db)
    assert decrypt_with_key(channels[1], NEW_KEY) == '{"smtp_password": "pw"}'
    assert decrypt_with_key(sessions[1], NEW_KEY) == 'PHPSESSID=abc'
    with pytest.raises(Exception):
        decrypt_with_key(channels[1], OLD_KEY)


def test_second_run_is_idempotent(db):
    mod = _load_script()
    mod.rotate(db, OLD_KEY, NEW_KEY, apply=True)
    summary = mod.rotate(db, OLD_KEY, NEW_KEY, apply=True)
    assert summary['rotated'] == 0
    assert summary['already_new'] == 2


def test_unreadable_row_left_untouched(db):
    mod = _load_script()
    conn = sqlite3.connect(str(db))
    conn.execute('INSERT INTO channels (id, config) VALUES (2, ?)', ('not-base64-ciphertext',))
    conn.commit()
    conn.close()

    summary = mod.rotate(db, OLD_KEY, NEW_KEY, apply=True)
    assert summary['rotated'] == 2
    assert summary['unreadable'] == ['channels.config id=2']

    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute('SELECT config FROM channels WHERE id=2').fetchone()[0] == 'not-base64-ciphertext'
    finally:
        conn.close()


def test_resolve_keys_requires_new_key(monkeypatch):
    mod = _load_script()
    monkeypatch.delenv('MONITOR_SECRET_KEY_NEW', raising=False)
    with pytest.raises(SystemExit):
        mod.resolve_keys()

    monkeypatch.setenv('MONITOR_SECRET_KEY_NEW', OLD_KEY)
    with pytest.raises(SystemExit):
        mod.resolve_keys()

    monkeypatch.setenv('MONITOR_SECRET_KEY_NEW', NEW_KEY)
    monkeypatch.delenv('MONITOR_SECRET_KEY_OLD', raising=False)
    monkeypatch.delenv('MONITOR_SECRET_KEY', raising=False)
    assert mod.resolve_keys() == (OLD_KEY, NEW_KEY)
