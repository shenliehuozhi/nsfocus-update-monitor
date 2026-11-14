"""Isolated connection lifetime, contention budget and diagnostics regressions."""

from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import threading
import time

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    from src.models import database
    monkeypatch.setattr(database, 'DB_PATH', str(tmp_path / 'isolated.db'))
    monkeypatch.setattr(database, '_local', threading.local())
    monkeypatch.setattr(database, '_write_lock', threading.RLock())
    monkeypatch.setattr(database, '_write_samples', deque(maxlen=1024))
    monkeypatch.setattr(database, '_write_totals', Counter())
    monkeypatch.setattr(database, '_WRITE_LOCK_TIMEOUT', 0.1)
    with closing(sqlite3.connect(database.DB_PATH)) as conn:
        conn.execute('CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT UNIQUE)')
    yield database
    database.close_db()


@contextmanager
def hold_python_lock(db):
    ready, release = threading.Event(), threading.Event()

    def holder():
        with db._write_lock:
            ready.set()
            release.wait(3)

    thread = threading.Thread(target=holder)
    thread.start()
    assert ready.wait(2)
    try:
        yield release
    finally:
        release.set()
        thread.join(3)
        assert not thread.is_alive()


def test_fresh_write_connections_always_close(db, monkeypatch):
    original = sqlite3.connect
    connections = []

    class TrackedConnection(sqlite3.Connection):
        closed = False

        def close(self):
            self.closed = True
            return super().close()

    def connect(*args, **kwargs):
        conn = original(*args, factory=TrackedConnection, **kwargs)
        connections.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, 'connect', connect)
    assert db.execute('INSERT INTO items(value) VALUES (?)', ('a',)) == 1
    with pytest.raises(sqlite3.IntegrityError):
        db.execute('INSERT INTO items(value) VALUES (?)', ('a',))
    with pytest.raises(sqlite3.OperationalError):
        db.execute('INSERT INTO missing VALUES (1)')
    assert len(connections) == 3
    assert all(conn.closed for conn in connections)
    assert db.write_stats()['errors'] == 2
    assert db.write_stats()['timeouts'] == 0


def test_connections_are_thread_owned_and_teardown_is_idempotent(db):
    main = db.get_db()
    barrier = threading.Barrier(3)

    def worker():
        conn = db.get_db()
        assert conn is db.get_db()
        barrier.wait(2)
        with pytest.raises(sqlite3.ProgrammingError):
            main.execute('SELECT 1')
        db.close_db()
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute('SELECT 1')
        return conn

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        barrier.wait(2)
        a, b = [f.result(3) for f in futures]
    assert a is not b and a is not main and b is not main
    db.close_db()
    db.close_db()
    assert db.get_db() is not main


@pytest.mark.parametrize('mode', ['DELETE', 'WAL'])
def test_existing_journal_mode_is_preserved(db, mode):
    with closing(sqlite3.connect(db.DB_PATH)) as conn:
        assert conn.execute(f'PRAGMA journal_mode={mode}').fetchone()[0] == mode.lower()
    db.execute('INSERT INTO items(value) VALUES (?)', ('a',))
    assert db.query('PRAGMA journal_mode')[0]['journal_mode'] == mode.lower()
    db.close_db()
    assert db.query('PRAGMA journal_mode')[0]['journal_mode'] == mode.lower()


def test_python_lock_wait_is_bounded_and_reported(db):
    with hold_python_lock(db):
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match='local write queue'):
            db.execute("INSERT INTO items(value) VALUES ('a')")
        assert 0.07 <= time.monotonic() - started < 0.8
    stats = db.write_stats()
    assert stats['python_lock_timeouts'] == stats['timeouts'] == 1
    assert stats['sqlite_busy_errors'] == 0
    assert stats['lock_wait_ms']['p50'] >= 70
    assert stats['sqlite_ms']['p50'] == 0
    assert stats['timeout_rate'] == 1
    assert db.query('SELECT * FROM items') == []
    db.execute("INSERT INTO items(value) VALUES ('after')")


def test_external_write_lock_fails_once_then_recovers(db):
    with closing(sqlite3.connect(db.DB_PATH, isolation_level=None)) as external:
        external.execute('BEGIN IMMEDIATE')
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            db.execute("INSERT INTO items(value) VALUES ('a')")
        assert 0.07 <= time.monotonic() - started < 0.8
        external.execute('ROLLBACK')
    stats = db.write_stats()
    assert stats['operations'] == stats['timeouts'] == stats['sqlite_busy_errors'] == 1
    assert stats['sqlite_ms']['p50'] >= 70
    db.execute("INSERT INTO items(value) VALUES ('a')")
    assert len(db.query('SELECT * FROM items')) == 1


def test_queue_and_sqlite_share_one_budget(db, monkeypatch):
    monkeypatch.setattr(db, '_WRITE_LOCK_TIMEOUT', 0.3)
    with closing(sqlite3.connect(db.DB_PATH, isolation_level=None)) as external:
        external.execute('BEGIN IMMEDIATE')
        with hold_python_lock(db) as release:
            timer = threading.Timer(0.18, release.set)
            timer.start()
            try:
                started = time.monotonic()
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    db.execute("INSERT INTO items(value) VALUES ('a')")
                elapsed = time.monotonic() - started
            finally:
                timer.join()
        external.execute('ROLLBACK')
    stats = db.write_stats()
    assert 0.25 <= elapsed < 0.6
    assert stats['lock_wait_ms']['p50'] >= 140
    assert stats['sqlite_ms']['p50'] < 230
    assert stats['total_ms']['p50'] >= 250


@pytest.mark.parametrize('mode,blocked', [('DELETE', True), ('WAL', False)])
def test_long_reader_blocks_delete_but_not_wal(db, mode, blocked):
    with closing(sqlite3.connect(db.DB_PATH, isolation_level=None)) as reader:
        reader.execute(f'PRAGMA journal_mode={mode}')
        reader.execute('BEGIN')
        reader.execute('SELECT * FROM items').fetchall()
        if blocked:
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                db.execute("INSERT INTO items(value) VALUES ('a')")
        else:
            db.execute("INSERT INTO items(value) VALUES ('a')")
        reader.execute('ROLLBACK')
    assert len(db.query('SELECT * FROM items')) == (0 if blocked else 1)


def test_batch_failure_does_not_replay_committed_rows(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.executemany('INSERT INTO items(value) VALUES (?)', [('a',), ('a',), ('b',)])
    assert db.query('SELECT value FROM items') == [{'value': 'a'}]
    db.executemany('INSERT INTO items(value) VALUES (?)', [])
    assert db.execute_rowcount("UPDATE items SET value='b'") == 1
    assert db.execute_rowcount("DELETE FROM items WHERE value='missing'") == 0


def test_cleanup_keeps_foreign_key_checks(db):
    with closing(sqlite3.connect(db.DB_PATH)) as conn:
        conn.execute('CREATE TABLE child (parent_id INTEGER REFERENCES items(id))')
        conn.execute("INSERT INTO items VALUES (1, 'a')")
        conn.execute('INSERT INTO child VALUES (1)')
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute_rowcount('DELETE FROM items WHERE id=1')
    assert db.query('SELECT COUNT(*) AS n FROM items')[0]['n'] == 1


def test_diagnostics_are_bounded_and_do_not_log_sql_or_values(db, caplog):
    stats = db.write_stats()
    assert stats['sample_count'] == 0
    assert stats['total_ms']['p99'] is None
    for _ in range(1100):
        db._record_write(0.001, 0.002, 0.003, 'ok')
    stats = db.write_stats()
    assert stats['sample_count'] == 1024
    assert stats['operations'] == 1100
    assert stats['lock_wait_ms'] == {'p50': 1, 'p95': 1, 'p99': 1}
    with pytest.raises(sqlite3.OperationalError):
        db.execute('INSERT INTO no_such_table VALUES (?)', ('private-fixture-value',))
    assert 'db_write outcome=error' in caplog.text
    assert 'private-fixture-value' not in caplog.text
    assert 'no_such_table' not in caplog.text


def test_concurrent_writes_keep_all_rows(db, monkeypatch):
    monkeypatch.setattr(db, '_WRITE_LOCK_TIMEOUT', 2)
    barrier = threading.Barrier(4)

    def write(worker):
        barrier.wait(3)
        for i in range(10):
            db.execute('INSERT INTO items(value) VALUES (?)', (f'{worker}-{i}',))
        db.close_db()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(4)))
    assert db.query('SELECT COUNT(*) AS n FROM items')[0]['n'] == 40
    assert db.write_stats()['timeouts'] == 0


def test_flask_teardown_closes_connection(db):
    from flask import Flask
    app = Flask(__name__)
    app.teardown_appcontext(db.close_db)
    with app.app_context():
        conn = db.get_db()
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute('SELECT 1')
    source = (Path(__file__).resolve().parents[1] / 'src/app.py').read_text(encoding='utf-8')
    assert 'app.teardown_appcontext(close_db)' in source


def test_history_cleanup_returns_only_current_delete_count(db):
    from src.models.subscription import clear_history
    db.execute('CREATE TABLE delivery_log (id INTEGER PRIMARY KEY, sent_at TEXT)')
    db.execute("INSERT INTO delivery_log VALUES (1, '2000-01-01')")
    db.execute("INSERT INTO delivery_log VALUES (2, '2999-01-01')")
    db.get_db().execute("INSERT INTO items(value) VALUES ('unrelated')").close()
    assert clear_history(90) == 1
    assert clear_history(90) == 0
    assert clear_history() == 1


def test_cleanup_paths_use_serialized_writer():
    source = (Path(__file__).resolve().parents[1] / 'src/core/scheduler.py').read_text(encoding='utf-8')
    assert 'deleted += execute_rowcount(' in source
    assert 'deleted += cur.rowcount' not in source
