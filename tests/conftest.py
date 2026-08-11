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
    2026-08-08: 返回值改 list of list of str (list of chains),匹配新 _get_chain
    行为(URL 去重下多 chain 共享 path_id,_get_chain 返回所有 chain)。
    测试 fixture 仍可传单 chain list 模拟单 chain 场景。
    """
    def _make(mapping: dict):
        def _lookup(source_id, source_url, path_id=None):
            if path_id and (source_id, path_id) in mapping:
                val = mapping[(source_id, path_id)]
                # 自动 wrap: 单 chain 转 list of list(新 API 要求)
                if val and isinstance(val[0], str):
                    return [val]
                return val
            val = mapping.get((source_id, source_url), [])
            if val and isinstance(val[0], str):
                return [val]
            return val
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