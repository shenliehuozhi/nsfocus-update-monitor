"""Tests for the DB-stamp response cache."""

import os

from src.web.response_cache import DBStampCache


def test_stamp_none_for_missing_file(tmp_path):
    assert DBStampCache.stamp(str(tmp_path / 'missing.db')) is None


def test_stamp_changes_after_write(tmp_path):
    db = tmp_path / 'db.sqlite'
    db.write_bytes(b'a')
    os.utime(db, ns=(1_000_000_000, 1_000_000_000))
    first = DBStampCache.stamp(str(db))
    db.write_bytes(b'ab')
    os.utime(db, ns=(2_000_000_000, 2_000_000_000))
    second = DBStampCache.stamp(str(db))
    assert first is not None and second is not None and first != second


def test_put_get_roundtrip():
    cache = DBStampCache()
    cache.put((1, 2, 3), 'body')
    assert cache.get((1, 2, 3)) == 'body'
    assert cache.get((9, 9, 9)) is None
    assert cache.get(None) is None


def test_none_stamp_is_never_cached():
    cache = DBStampCache()
    cache.put(None, 'body')
    assert cache.get(None) is None


def test_evicts_oldest_beyond_max():
    cache = DBStampCache(max_entries=2)
    cache.put((1, 0, 0), 'a')
    cache.put((2, 0, 0), 'b')
    cache.put((3, 0, 0), 'c')
    assert cache.get((1, 0, 0)) is None
    assert cache.get((2, 0, 0)) == 'b'
    assert cache.get((3, 0, 0)) == 'c'
