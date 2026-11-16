"""CVE 提取与扫描：正则、去重、置信度、增量扫描与重扫。"""

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    import src.models.database as db_mod

    try:
        db_mod.close_db()
    except Exception:
        pass
    monkeypatch.setattr(db_mod, 'DB_PATH', str(tmp_path / 'nsfocus_monitor.db'))
    if hasattr(db_mod._local, 'conn'):
        monkeypatch.setattr(db_mod._local, 'conn', None)
    db_mod.get_db()

    from src.models import init_all_tables
    init_all_tables()

    from src.models.database import execute
    execute("DELETE FROM content_sources")
    execute(
        "INSERT INTO content_sources (id, name, source_type, is_active) "
        "VALUES (1, 'WAF', 'nsfocus', 1)"
    )
    return db_mod


def _insert_snapshot(sid, desc, status='active'):
    from src.models.database import execute
    execute(
        "INSERT INTO snapshots (id, source_id, product_name, version_branch, package_type, "
        "file_name, md5_hash, description_raw, status, source_url, path_id) "
        "VALUES (?, 1, 'WAF', 'V6.0', 'sys', ?, 'md5', ?, ?, 'u', 'p')",
        (sid, f'f{sid}.bin', desc, status),
    )


def test_extract_dedupes_and_normalizes_case():
    from src.core.cve_extract import extract_cves

    items = extract_cves('修复 CVE-2026-1234 漏洞；另见 cve-2026-1234。')
    assert len(items) == 1
    assert items[0]['cve_id'] == 'CVE-2026-1234'
    assert 0.6 <= items[0]['confidence'] <= 0.95
    assert 'CVE-2026-1234' in items[0]['evidence']


def test_extract_multiple_and_list_bonus():
    from src.core.cve_extract import extract_cves

    items = extract_cves('关联 CVE-2026-1111、CVE-2026-2222')
    assert [it['cve_id'] for it in items] == ['CVE-2026-1111', 'CVE-2026-2222']
    assert all(it['confidence'] == 0.7 for it in items)


def test_extract_negative_context_lowers_confidence():
    from src.core.cve_extract import LOW_CONFIDENCE, extract_cves

    items = extract_cves('本次更新不受影响 CVE-2026-1111，无需升级。')
    assert len(items) == 1
    assert items[0]['confidence'] < LOW_CONFIDENCE


def test_extract_ignores_malformed_ids():
    from src.core.cve_extract import extract_cves

    assert extract_cves('CVE-26-1234 CVE-2026-123 XCVE-2026-5678 CVE-2026-12345678') == []


def test_extract_evidence_is_bounded():
    from src.core.cve_extract import MAX_EVIDENCE, extract_cves

    text = 'x' * 1500 + ' 修复 CVE-2026-3333 漏洞 ' + 'y' * 1500
    items = extract_cves(text)
    assert len(items) == 1
    assert len(items[0]['evidence']) <= MAX_EVIDENCE


def test_scan_inserts_links_and_state(db):
    from src.core.cve_extract import PARSER_VERSION, scan_pending
    from src.models.database import query

    _insert_snapshot(1, '修复 CVE-2026-1111 远程执行漏洞')
    stats = scan_pending()
    assert stats == {'candidates': 1, 'scanned': 1, 'cves': 1, 'errors': 0}

    links = query('SELECT * FROM snapshot_cve WHERE snapshot_id = 1')
    assert [row['cve_id'] for row in links] == ['CVE-2026-1111']
    state = query('SELECT * FROM cve_scan_state WHERE snapshot_id = 1')[0]
    assert state['cve_count'] == 1
    assert state['parser_version'] == PARSER_VERSION
    assert state['desc_hash']


def test_scan_skips_unchanged_snapshot(db):
    from src.core.cve_extract import scan_pending

    _insert_snapshot(1, '修复 CVE-2026-1111 漏洞')
    assert scan_pending()['scanned'] == 1

    stats = scan_pending()
    assert stats == {'candidates': 0, 'scanned': 0, 'cves': 0, 'errors': 0}


def test_scan_replaces_links_when_description_changes(db):
    from src.core.cve_extract import scan_pending
    from src.models.database import execute, query

    _insert_snapshot(1, '修复 CVE-2026-1111 漏洞')
    scan_pending()

    execute(
        "UPDATE snapshots SET description_raw = '修复 CVE-2026-2222 漏洞，替代旧版本' WHERE id = 1"
    )
    stats = scan_pending()
    assert stats['scanned'] == 1
    rows = query('SELECT cve_id FROM snapshot_cve WHERE snapshot_id = 1')
    assert [r['cve_id'] for r in rows] == ['CVE-2026-2222']
    assert query('SELECT cve_count FROM cve_scan_state WHERE snapshot_id = 1')[0]['cve_count'] == 1


def test_force_rescans_same_length_change(db):
    from src.core.cve_extract import scan_pending
    from src.models.database import execute, query

    _insert_snapshot(1, '修复 CVE-2026-1111 漏洞')
    scan_pending()

    execute("UPDATE snapshots SET description_raw = '修复 CVE-2026-2222 漏洞' WHERE id = 1")
    assert scan_pending()['scanned'] == 0

    stats = scan_pending(force=True)
    assert stats['scanned'] == 1
    rows = query('SELECT cve_id FROM snapshot_cve WHERE snapshot_id = 1')
    assert [r['cve_id'] for r in rows] == ['CVE-2026-2222']


def test_scan_skips_withdrawn_and_empty_descriptions(db):
    from src.core.cve_extract import scan_pending
    from src.models.database import query

    _insert_snapshot(1, '修复 CVE-2026-1111 漏洞', status='withdrawn')
    _insert_snapshot(2, '')

    stats = scan_pending()
    assert stats['candidates'] == 0
    assert query('SELECT * FROM snapshot_cve') == []
