"""Quick 采集 URL 大量失败时进入采集汇总，避免不完整快照被当成成功。"""

import src.core.scheduler as sched


def _stats(**overrides):
    stats = {'fetches': 0, 'in_product_hits': 0, 'cross_sid_hits': 0,
             'url_failures': 0}
    stats.update(overrides)
    return stats


def test_all_urls_failed_is_reported():
    detail = sched._url_failure_detail(_stats(url_failures=3), (0, 0, 0, 0))
    assert detail == '3 个 URL 请求失败（本轮成功 0 个页面）'


def test_single_timeout_among_many_successes_not_reported():
    stats = _stats(fetches=21, url_failures=5)
    assert sched._url_failure_detail(stats, (0, 0, 0, 0)) == ''


def test_majority_failure_is_reported():
    stats = _stats(fetches=10, url_failures=17)
    detail = sched._url_failure_detail(stats, (0, 0, 0, 0))
    assert detail == '17 个 URL 请求失败（本轮成功 10 个页面）'


def test_cache_hits_count_as_successful_pages():
    stats = _stats(cross_sid_hits=20, url_failures=1)
    assert sched._url_failure_detail(stats, (0, 0, 0, 0)) == ''


def test_only_new_failures_since_snapshot_are_counted():
    stats = _stats(fetches=10, in_product_hits=2, cross_sid_hits=1,
                   url_failures=6)
    detail = sched._url_failure_detail(stats, (10, 2, 1, 4))
    assert detail == '2 个 URL 请求失败（本轮成功 0 个页面）'


def test_progress_done_counts_only_enabled_sources():
    """进度分母是已启用产品数，分子也不能把停用源算进去（否则 83/79 倒挂）。"""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / 'src/core/scheduler.py'
    text = src.read_text(encoding='utf-8')
    assert "_progress['products_done'] = len(existing_sources)" not in text
    assert 'enabled_names = set(_collector_products())' in text
    assert "sum(1 for n in existing_sources if n in enabled_names)" in text


def test_merge_progress_errors_dedupes(monkeypatch):
    monkeypatch.setattr(sched, '_progress', {'errors': ['A: x', 'B: y']})
    summary = {'errors': ['A: x']}
    sched._merge_progress_errors(summary)
    assert summary['errors'] == ['A: x', 'B: y']
