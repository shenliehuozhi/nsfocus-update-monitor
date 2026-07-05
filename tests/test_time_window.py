"""Tests for is_window_time and is_quiet_time (pure functions, no DB).

Targets cross-midnight logic which has historically been a regression risk.
"""
from datetime import datetime
from unittest.mock import patch
from src.detector.change import is_window_time, is_quiet_time


def test_window_normal_range():
    """09:00-18:00 normal range, 12:00 should be inside."""
    rule = {'window_config': {'days': [], 'start': '09:00', 'end': '18:00'}}
    with patch('src.detector.change.datetime') as m:
        m.now.return_value = datetime(2026, 7, 5, 12, 0)
        assert is_window_time(rule) is True


def test_window_cross_midnight_inside():
    """22:00-06:00 crosses midnight; 02:00 should still be inside."""
    rule = {'window_config': {'days': [], 'start': '22:00', 'end': '06:00'}}
    with patch('src.detector.change.datetime') as m:
        m.now.return_value = datetime(2026, 7, 5, 2, 0)
        assert is_window_time(rule) is True


def test_quiet_cross_midnight():
    """22:00-08:00 quiet period; 23:00 should be inside (suppressed)."""
    rule = {'quiet_start': '22:00', 'quiet_end': '08:00'}
    with patch('src.detector.change.datetime') as m:
        m.now.return_value = datetime(2026, 7, 5, 23, 0)
        assert is_quiet_time(rule) is True