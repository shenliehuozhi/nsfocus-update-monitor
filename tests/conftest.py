"""Shared fixtures for nsfocus-monitor unit tests.

Design constraint: tests must NEVER trigger real notifications. So we avoid
importing src.core.scheduler (which starts the APScheduler daemon) and
src.notifiers.* at the top level. All scheduler._get_chain lookups are
stubbed via monkeypatch.
"""
import pytest


@pytest.fixture
def stub_chain_lookup():
    """Build a fake scheduler._get_chain replacement backed by a dict.

    Mapping key: (source_id, path_id) or (source_id, source_url) for fallback.
    """
    def _make(mapping: dict):
        def _lookup(source_id, source_url, path_id=None):
            if path_id and (source_id, path_id) in mapping:
                return mapping[(source_id, path_id)]
            return mapping.get((source_id, source_url), [])
        return _lookup
    return _make


@pytest.fixture
def base_snap():
    """Build a minimal (snapshot_id, snap_dict) tuple for subscription matching.

    Only fields consumed by get_new_for_subscription are populated by default;
    tests can override via kwargs.
    """
    def _make(**overrides):
        snap = {
            'source_id': 1,
            'source_url': '/update/xxx',
            'path_id': 'abc123',
            'file_name': 'test.zip',
            'urgency': 'high',
            'description_raw': 'some CVE description',
        }
        snap.update(overrides)
        return (100, snap)
    return _make