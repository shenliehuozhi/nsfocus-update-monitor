"""Bounded synthetic SQLite contention benchmark; never opens an existing database."""

import argparse
import gc
import json
import logging
import math
import os
from pathlib import Path
import platform
import sqlite3
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def percentiles(values):
    values = sorted(values)
    return {f'p{p}': round(values[math.ceil(len(values) * p / 100) - 1], 3)
            if values else None for p in (50, 95, 99)}


def legacy_execute(db, sql, params, busy_ms, samples):
    started = time.monotonic()
    lock_wait = sqlite_time = 0.0
    retries = 0
    outcome = 'error'
    try:
        for attempt in range(30):
            try:
                queued = time.monotonic()
                with db._write_lock:
                    lock_wait += time.monotonic() - queued
                    conn = sqlite3.connect(db.DB_PATH, timeout=60, isolation_level=None)
                    conn.execute(f'PRAGMA busy_timeout={busy_ms}')
                    conn.row_factory = sqlite3.Row
                    sql_start = time.monotonic()
                    try:
                        cursor = conn.execute(sql, params)
                    finally:
                        sqlite_time += time.monotonic() - sql_start
                    outcome = 'ok'
                    return cursor.lastrowid
            except sqlite3.OperationalError as exc:
                if attempt < 29 and 'database is locked' in str(exc):
                    retries += 1
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
    finally:
        samples.append({'lock_wait_ms': lock_wait * 1000,
                        'sqlite_ms': sqlite_time * 1000,
                        'total_ms': (time.monotonic() - started) * 1000,
                        'retries': retries, 'outcome': outcome})


def seed(path, mode, rows):
    with closing(sqlite3.connect(path)) as conn:
        actual = conn.execute(f'PRAGMA journal_mode={mode}').fetchone()[0]
        conn.executescript('''
            CREATE TABLE snapshots (id INTEGER PRIMARY KEY, product_name TEXT,
                                    published_at TEXT, payload TEXT);
            CREATE TABLE delivery_log (id INTEGER PRIMARY KEY, snapshot_id INTEGER,
                                       status TEXT, created_at TEXT);
            CREATE INDEX delivery_time ON delivery_log(created_at);
            CREATE TABLE delayed_queue (id INTEGER PRIMARY KEY, status TEXT, attempts INTEGER);
        ''')
        conn.executemany('INSERT INTO snapshots VALUES (?, ?, ?, ?)',
                         [(i, f'product-{i % 12}', '2026-11-12 00:00:00', 'x' * 256)
                          for i in range(1, rows + 1)])
        conn.executemany('INSERT INTO delivery_log VALUES (?, ?, ?, ?)',
                         [(i, i, 'sent', '2026-11-12 00:00:00') for i in range(1, rows + 1)])
        conn.execute("INSERT INTO delayed_queue VALUES (1, 'pending', 0)")
        conn.commit()
        synchronous = conn.execute('PRAGMA synchronous').fetchone()[0]
    return actual, synchronous


def run_case(db, root, variant, mode, scenario, args, repeat):
    path = root / f'{repeat}-{variant}-{mode}-{scenario}.db'
    assert not path.exists()
    actual, synchronous = seed(path, mode, args.rows)
    db.close_db()
    db.DB_PATH = str(path)
    db._WRITE_LOCK_TIMEOUT = args.busy_ms / 1000
    db._write_samples.clear()
    db._write_totals.clear()
    legacy_samples, durations, reader_ms, holder_ms = [], [], [], []
    errors = []
    ready = threading.Event()
    barrier = threading.Barrier(5) if scenario == 'mixed' else None

    def write(worker, count):
        if barrier:
            barrier.wait(timeout=5)
        for index in range(count):
            t0 = time.monotonic()
            sql, params = ("INSERT INTO delivery_log(snapshot_id, status, created_at) "
                           "VALUES (?, 'sent', '2026-11-12 01:00:00')", (index + 1,))
            if index % 3 == 1:
                sql, params = 'UPDATE delayed_queue SET attempts=attempts+1 WHERE id=?', (1,)
            elif index % 3 == 2:
                sql, params = 'UPDATE snapshots SET published_at=? WHERE id=?', ('2026-11-12 01:00:00', index + 1)
            try:
                if variant == 'legacy':
                    legacy_execute(db, sql, params, args.busy_ms, legacy_samples)
                else:
                    db.execute(sql, params)
                durations.append((time.monotonic() - t0) * 1000)
            except sqlite3.OperationalError:
                errors.append('write_busy')
        db.close_db()

    def reader():
        with closing(sqlite3.connect(path, timeout=args.busy_ms / 1000,
                                     isolation_level=None)) as conn:
            barrier.wait(timeout=5)
            for _ in range(args.operations):
                t0 = time.monotonic()
                try:
                    conn.execute('''SELECT s.product_name, COUNT(*) FROM delivery_log d
                                    JOIN snapshots s ON s.id=d.snapshot_id
                                    GROUP BY s.product_name''').fetchall()
                    reader_ms.append((time.monotonic() - t0) * 1000)
                except sqlite3.OperationalError:
                    errors.append('read_busy')
                time.sleep(0.002)

    def holder():
        with closing(sqlite3.connect(path, isolation_level=None)) as conn:
            t0 = time.monotonic()
            if scenario == 'long_reader':
                conn.execute('BEGIN').close()
                conn.execute('SELECT COUNT(*) FROM snapshots').fetchall()
            else:
                conn.execute('BEGIN IMMEDIATE').close()
            ready.set()
            time.sleep(args.hold_ms / 1000)
            conn.execute('ROLLBACK').close()
            holder_ms.append((time.monotonic() - t0) * 1000)

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=5) as pool:
        if scenario == 'mixed':
            jobs = [pool.submit(write, i, args.operations) for i in range(3)]
            jobs.extend(pool.submit(reader) for _ in range(2))
        else:
            held = pool.submit(holder)
            if not ready.wait(5):
                held.result()
                raise RuntimeError('holder did not acquire lock')
            jobs = [pool.submit(write, 0, 1), held]
        for job in jobs:
            job.result(timeout=30)
    elapsed = time.monotonic() - start
    stats = db.write_stats()
    if variant == 'legacy':
        stats = {name: percentiles([s[name] for s in legacy_samples])
                 for name in ('lock_wait_ms', 'sqlite_ms', 'total_ms')}
        stats['retries'] = sum(s['retries'] for s in legacy_samples)
    gc.collect()
    with closing(sqlite3.connect(path)) as conn:
        integrity = conn.execute('PRAGMA quick_check').fetchone()[0]
        final_mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
    sizes = {suffix or 'db': Path(str(path) + suffix).stat().st_size
             if Path(str(path) + suffix).exists() else 0 for suffix in ('', '-wal', '-shm')}
    return {'variant': variant, 'mode': actual, 'final_mode': final_mode,
            'scenario': scenario, 'repeat': repeat, 'synchronous': synchronous,
            'elapsed_s': round(elapsed, 3), 'successful_writes': len(durations),
            'write_errors': errors.count('write_busy'), 'read_errors': errors.count('read_busy'),
            'write_error_rate': errors.count('write_busy') / (len(durations) + errors.count('write_busy')),
            'writes_per_second': round(len(durations) / elapsed, 2),
            'successful_write_ms': percentiles(durations), 'read_ms': percentiles(reader_ms),
            'held_transaction_ms': percentiles(holder_ms), 'write_stats': stats,
            'file_bytes': sizes, 'integrity': integrity}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operations', type=int, default=40, choices=range(1, 101))
    parser.add_argument('--rows', type=int, default=2000, choices=range(100, 5001))
    parser.add_argument('--busy-ms', type=int, default=100, choices=range(50, 10001))
    parser.add_argument('--hold-ms', type=int, default=250, choices=range(1, 1001))
    parser.add_argument('--repeats', type=int, default=3, choices=range(1, 6))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='nsm-sqlite-bench-') as temp:
        root = Path(temp)
        os.environ['MONITOR_DATA_DIR'] = str(root / 'data')
        os.environ['MONITOR_LOG_DIR'] = str(root / 'logs')
        from src.models import database as db
        db.logger.setLevel(logging.ERROR)
        results = [run_case(db, root, variant, mode, scenario, args, repeat)
                   for repeat in range(1, args.repeats + 1)
                   for variant, mode in (('legacy', 'DELETE'), ('fixed', 'DELETE'), ('fixed', 'WAL'))
                   for scenario in ('mixed', 'long_reader', 'external_writer')]
        db.close_db()
        for handler in logging.getLogger('monitor').handlers[:]:
            handler.close()
            logging.getLogger('monitor').removeHandler(handler)
        print(json.dumps({'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
                          'platform': platform.platform(), 'parameters': vars(args),
                          'notes': ['Synthetic data only; not a production workload replay.',
                                    'Legacy execute policy reproduced with timing added and configurable busy_timeout.',
                                    'sqlite_ms includes SQLite wait, execution and autocommit; not pure lock time.',
                                    'held_transaction_ms measures controlled holder transactions, not production transactions.',
                                    'Both modes keep synchronous=FULL; no durability downgrade.'],
                          'results': results}, indent=2))


if __name__ == '__main__':
    main()
