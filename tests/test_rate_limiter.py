"""Tests for src.core.rate_limiter (push rate limit + ban).

Strategy: monkeypatch the global DB_PATH in src.models.database to an in-memory
SQLite, create the push_rate_limits table via the module's create_tables(),
exercise check/record/clear_ban/get_all_bans. Original DB_PATH is restored in
fixture teardown so the real database is NEVER touched.
"""
import pytest
import sqlite3
from datetime import datetime, timedelta

import src.models.database as db_mod
import src.core.rate_limiter as rl


@pytest.fixture
def in_memory_db(monkeypatch, tmp_path):
    """Point the global DB at a private file, create schema, return path."""
    db_file = tmp_path / 'test_rate_limit.db'
    monkeypatch.setattr(db_mod, 'DB_PATH', str(db_file))
    # Also clear any thread-local cached connection so get_db() reopens
    if hasattr(db_mod._local, 'conn'):
        monkeypatch.setattr(db_mod._local, 'conn', None)
    # Create schema directly (avoids importing module that may touch other tables)
    con = sqlite3.connect(str(db_file))
    con.executescript(rl.SCHEMA)
    con.commit()
    con.close()
    return db_file


# ============================================================================
# check() — gating logic
# ============================================================================

def test_check_no_record_allows(in_memory_db):
    """No prior record → always allowed."""
    allowed, msg, retry = rl.check('user@example.com')
    assert allowed is True
    assert msg == ''
    assert retry == 0


def test_check_within_window_under_limit_allows(in_memory_db):
    """4 pushes in same window → still allowed (limit = 5)."""
    for i in range(4):
        rl.record('user@example.com')
    allowed, msg, retry = rl.check('user@example.com')
    assert allowed is True
    assert retry == 0


def test_check_exceeds_limit_bans(in_memory_db):
    """5 pushes + 6th check → ban, retry_after ≈ 600s."""
    for i in range(5):
        rl.record('user@example.com')
    allowed, msg, retry = rl.check('user@example.com')
    assert allowed is False
    assert '推送频率超限' in msg
    assert retry == rl.BAN_SECONDS


def test_check_ban_active_returns_remaining_seconds(in_memory_db):
    """While banned, check returns remaining time in seconds (not 600)."""
    for i in range(5):
        rl.record('user@example.com')
    # First check triggers the ban
    rl.check('user@example.com')
    # Second check while still banned → returns remaining (not full BAN_SECONDS)
    allowed, msg, retry = rl.check('user@example.com')
    assert allowed is False
    assert retry < rl.BAN_SECONDS
    assert retry > 0
    # Message format includes "X分Y秒 后重试"
    assert '分' in msg and '秒' in msg


def test_check_different_keys_independent(in_memory_db):
    """Banning key A doesn't affect key B."""
    for i in range(5):
        rl.record('a@x.com')
    rl.check('a@x.com')  # ban a
    # b is untouched
    allowed, msg, retry = rl.check('b@x.com')
    assert allowed is True
    assert retry == 0


# ============================================================================
# record() — increment logic
# ============================================================================

def test_record_first_time_inserts(in_memory_db):
    """First record for a key creates a row with count=1."""
    rl.record('new@x.com')
    rows = db_mod.query("SELECT * FROM push_rate_limits WHERE key = ?", ('new@x.com',))
    assert len(rows) == 1
    assert rows[0]['count'] == 1
    assert rows[0]['window_start'] is not None
    assert rows[0]['banned_until'] is None


def test_record_within_window_increments(in_memory_db):
    """Subsequent records in same window bump count."""
    rl.record('u@x.com')
    rl.record('u@x.com')
    rl.record('u@x.com')
    rows = db_mod.query("SELECT count FROM push_rate_limits WHERE key = ?", ('u@x.com',))
    assert rows[0]['count'] == 3


def test_record_resets_window_after_expiry(in_memory_db):
    """After window expires, record resets count to 1 (and clears ban)."""
    rl.record('u@x.com')
    rl.record('u@x.com')
    # Manually backdate window_start to simulate expiry
    past = (datetime.utcnow() - timedelta(seconds=rl.WINDOW_SECONDS + 10)).isoformat()
    db_mod.execute(
        "UPDATE push_rate_limits SET window_start = ? WHERE key = ?",
        (past, 'u@x.com'),
    )
    rl.record('u@x.com')
    rows = db_mod.query("SELECT count, window_start FROM push_rate_limits WHERE key = ?", ('u@x.com',))
    assert rows[0]['count'] == 1
    # window_start should be ~now, not the past value
    assert rows[0]['window_start'] > past


def test_record_clears_stale_ban_on_window_reset(in_memory_db):
    """If a stale ban exists but window has expired, record clears banned_until."""
    # Set up: count=0 with old window + banned_until in the past
    past_window = (datetime.utcnow() - timedelta(seconds=rl.WINDOW_SECONDS + 10)).isoformat()
    past_ban = (datetime.utcnow() - timedelta(seconds=10)).isoformat()
    db_mod.execute(
        "INSERT INTO push_rate_limits (key, count, window_start, banned_until) "
        "VALUES (?, ?, ?, ?)",
        ('u@x.com', 0, past_window, past_ban),
    )
    rl.record('u@x.com')
    rows = db_mod.query(
        "SELECT banned_until FROM push_rate_limits WHERE key = ?",
        ('u@x.com',),
    )
    # Should be cleared
    assert rows[0]['banned_until'] is None


# ============================================================================
# clear_ban() — ban removal
# ============================================================================

def test_clear_ban_specific_key(in_memory_db):
    """clear_ban(key) zeros that key's count and ban.

    Note: clear_ban() returns whatever execute() returns (lastrowid, which is
    None for UPDATE statements). The actual clearing is verifiable via check().
    """
    for i in range(5):
        rl.record('a@x.com')
    rl.check('a@x.com')  # ban
    rl.clear_ban('a@x.com')
    # After clearing, key should be allowed again (and counts reset)
    allowed, msg, retry = rl.check('a@x.com')
    assert allowed is True
    rows = db_mod.query("SELECT count, banned_until, window_start FROM push_rate_limits WHERE key = ?",
                        ('a@x.com',))
    assert rows[0]['count'] == 0
    assert rows[0]['banned_until'] is None
    assert rows[0]['window_start'] is None


def test_clear_ban_all_keys(in_memory_db):
    """clear_ban(None) clears all keys."""
    for i in range(5):
        rl.record('a@x.com')
    rl.record('b@x.com')
    rl.check('a@x.com')  # ban a
    rl.clear_ban(None)
    rows = db_mod.query("SELECT key, count, banned_until, window_start FROM push_rate_limits")
    by_key = {r['key']: r for r in rows}
    assert 'a@x.com' in by_key and 'b@x.com' in by_key
    for r in rows:
        assert r['count'] == 0
        assert r['banned_until'] is None
        assert r['window_start'] is None


def test_clear_ban_nonexistent_key_no_error(in_memory_db):
    """Clearing a non-existent key does not raise."""
    # Should not raise
    rl.clear_ban('nobody@x.com')
    # And the table remains empty
    rows = db_mod.query("SELECT * FROM push_rate_limits WHERE key = ?", ('nobody@x.com',))
    assert rows == []


# ============================================================================
# get_all_bans() — introspection
# ============================================================================

def test_get_all_bans_empty_when_no_bans(in_memory_db):
    """No banned keys → empty list."""
    rl.record('a@x.com')
    assert rl.get_all_bans() == []


def test_get_all_bans_returns_active_bans(in_memory_db):
    """Active ban shows up with key + remaining_seconds."""
    for i in range(5):
        rl.record('a@x.com')
    rl.check('a@x.com')  # trigger ban
    bans = rl.get_all_bans()
    assert len(bans) == 1
    assert bans[0]['key'] == 'a@x.com'
    assert 0 < bans[0]['remaining_seconds'] <= rl.BAN_SECONDS


def test_get_all_bans_ignores_expired(in_memory_db):
    """Ban whose banned_until is in the past → not in returned list."""
    past = (datetime.utcnow() - timedelta(seconds=10)).isoformat()
    db_mod.execute(
        "INSERT INTO push_rate_limits (key, count, window_start, banned_until) "
        "VALUES (?, 5, ?, ?)",
        ('old@x.com', past, past),
    )
    bans = rl.get_all_bans()
    assert bans == []


def test_get_all_bans_filters_zero_remaining(in_memory_db):
    """remaining_seconds <= 0 → filtered out (defensive against clock skew)."""
    now_iso = datetime.utcnow().isoformat()
    db_mod.execute(
        "INSERT INTO push_rate_limits (key, count, window_start, banned_until) "
        "VALUES (?, 5, ?, ?)",
        ('skew@x.com', now_iso, now_iso),
    )
    bans = rl.get_all_bans()
    assert bans == []


# ============================================================================
# Constants sanity
# ============================================================================

def test_constants_are_sane():
    """Sanity check on the limits — if anyone changes them, this fails."""
    assert rl.MAX_PER_WINDOW == 5
    assert rl.WINDOW_SECONDS == 60
    assert rl.BAN_SECONDS == 600


# ============================================================================
# Integration scenario — full lifecycle
# ============================================================================

def test_full_lifecycle_push_ban_recover(in_memory_db):
    """Realistic flow: 4 pushes → 5th → ban → clear → push again."""
    # 4 successful pushes
    for i in range(4):
        allowed, _, _ = rl.check('user@x.com')
        assert allowed is True
        rl.record('user@x.com')
    # 5th push: still allowed, records
    allowed, _, _ = rl.check('user@x.com')
    assert allowed is True
    rl.record('user@x.com')
    # 6th check → ban
    allowed, _, _ = rl.check('user@x.com')
    assert allowed is False
    # Confirm banned
    allowed, _, _ = rl.check('user@x.com')
    assert allowed is False
    # Clear ban
    rl.clear_ban('user@x.com')
    # Now allowed again
    allowed, msg, retry = rl.check('user@x.com')
    assert allowed is True
    assert msg == ''
    assert retry == 0