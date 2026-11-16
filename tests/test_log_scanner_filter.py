"""日志扫描忽略 NVD 外部抖动，保留真正的应用错误。"""

from src.core.log_scanner import _is_ignored_line


def test_ignores_expected_nvd_fetch_failures():
    assert _is_ignored_line(
        "2026-11-15 08:10:42 [WARNING] monitor.cve_meta: [CVE-META] "
        "CVE-2023-0334 fetch failed: HTTPSConnectionPool("
        "host='services.nvd.nist.gov'): Read timed out. (read timeout=15)"
    )
    assert _is_ignored_line(
        "2026-11-15 08:05:27 [WARNING] monitor.cve_meta: [CVE-META] "
        "CVE-2021-42911 fetch failed: HTTP 503 (retry #5 at 2026-11-16T00:05:27)"
    )
    assert _is_ignored_line(
        "2026-11-15 08:15:00 [WARNING] monitor.cve_meta: [CVE-META] "
        "NVD rate limited (HTTP 429), stop batch; retry after 2026-11-15T08:30:00"
    )


def test_keeps_unexpected_and_non_nvd_errors():
    assert not _is_ignored_line(
        "2026-11-15 08:15:00 [ERROR] monitor.scheduler: "
        "[CVE-META] sync failed: database is locked"
    )
    assert not _is_ignored_line(
        "2026-11-15 08:15:00 [ERROR] monitor.database: database is locked"
    )
    assert not _is_ignored_line(
        "2026-11-15 04:52:16 [WARNING] monitor.nsfocus: Quick IDS: "
        "RemoteDisconnected('Remote end closed connection without response')"
    )
