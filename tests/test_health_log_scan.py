"""仪表盘「异常 (2h内)」日志扫描回归。

历史问题: 正则要求 ISO 'T' 分隔 + 裸 ERROR, 而 app.log 实际是
`2026-11-10 16:54:24 [ERROR] monitor.detector: ...`, 生产日志里 24 条
WARNING/ERROR 一条都进不来。
"""
from datetime import datetime

from src.web.routes.system_routes import _scan_recent_log_alerts, _tail_lines


CUTOFF = datetime(2026, 11, 10, 12, 0, 0)


def _write(tmp_path, lines):
    p = tmp_path / 'app.log'
    p.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return str(p)


def test_scans_real_log_format_and_skips_old_or_info(tmp_path):
    log = _write(tmp_path, [
        '2026-11-10 13:00:00 [ERROR] monitor.detector: Failed to save snapshot: file=a.bin',
        '2026-11-10 13:01:00 [WARNING] monitor.scheduler: something odd',
        '2026-11-10 13:02:00 [INFO] monitor.scheduler: all good',
        '2026-11-10 09:00:00 [ERROR] monitor.detector: too old',
        'not a log line at all',
    ])
    alerts = _scan_recent_log_alerts(log, CUTOFF)
    assert [a['level'] for a in alerts] == ['ERROR', 'WARNING']
    assert 'Failed to save snapshot' in alerts[0]['msg']


def test_iso_timestamp_is_still_accepted(tmp_path):
    log = _write(tmp_path, [
        '2026-11-10T13:00:00 [ERROR] monitor.detector: iso style',
    ])
    assert len(_scan_recent_log_alerts(log, CUTOFF)) == 1


def test_kind_classification(tmp_path):
    log = _write(tmp_path, [
        '2026-11-10 13:00:00 [ERROR] monitor.x: 关键字段缺失',
        '2026-11-10 13:01:00 [ERROR] monitor.x: Collection failed: timeout',
        '2026-11-10 13:02:00 [WARNING] monitor.x: Session expired, please renew',
        '2026-11-10 13:03:00 [ERROR] monitor.x: 页面解析异常',
        '2026-11-10 13:04:00 [ERROR] monitor.x: sendmail returned 535',
    ])
    kinds = [a['kind'] for a in _scan_recent_log_alerts(log, CUTOFF)]
    assert kinds == ['field_missing', 'collection_failed', 'session_error', 'parse_error', 'other']


def test_limit_keeps_latest_alerts(tmp_path):
    lines = [f'2026-11-10 13:{i:02d}:00 [ERROR] monitor.x: err-{i}' for i in range(10)]
    log = _write(tmp_path, lines)
    alerts = _scan_recent_log_alerts(log, CUTOFF, limit=3)
    assert [a['msg'].rsplit('err-', 1)[-1] for a in alerts] == ['7', '8', '9']


def test_tail_lines_caps_file_scan(tmp_path):
    lines = [f'2026-11-10 13:00:0{i} [ERROR] monitor.x: line-{i}' for i in range(6)]
    log = _write(tmp_path, lines)
    assert _tail_lines(log, 2) == lines[-2:]
    alerts = _scan_recent_log_alerts(log, CUTOFF, tail_lines=2)
    assert [a['msg'].rsplit('line-', 1)[-1] for a in alerts] == ['4', '5']


def test_missing_file_returns_empty(tmp_path):
    assert _tail_lines(str(tmp_path / 'nope.log')) == []
    assert _scan_recent_log_alerts(str(tmp_path / 'nope.log'), CUTOFF) == []
