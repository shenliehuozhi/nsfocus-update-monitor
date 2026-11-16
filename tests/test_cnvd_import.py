"""CNVD/CNNVD 本地导出导入：中文名称、CNVD 编号、增量与去重。"""

import gzip

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
    return db_mod


@pytest.fixture
def cnvd_dir(tmp_path):
    path = tmp_path / 'cnvd'
    path.mkdir()
    return path


def _write(directory, name, text):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def _meta(cve_id):
    from src.models.database import query
    rows = query("SELECT * FROM cve_meta WHERE cve_id = ?", (cve_id,))
    return rows[0] if rows else None


def test_import_xml_fills_chinese_name_and_keeps_nvd_fields(db, cnvd_dir):
    from src.models.database import execute
    from src.core.cnvd_import import import_cnvd_files

    execute(
        "INSERT INTO cve_meta (cve_id, cvss_score, severity, status, description_en) "
        "VALUES ('CVE-2026-0001', 9.8, 'critical', 'ok', 'English description.')"
    )
    _write(cnvd_dir, 'week.xml', """
<cnvdList>
  <vulnerability>
    <CNVD编号>CNVD-2026-10001</CNVD编号>
    <相关编号><CVE>CVE-2026-0001</CVE></相关编号>
    <漏洞名称>示例产品远程代码执行漏洞</漏洞名称>
    <漏洞描述>攻击者可构造请求执行任意命令。</漏洞描述>
  </vulnerability>
</cnvdList>
""")
    result = import_cnvd_files(str(cnvd_dir))

    assert result['imported_files'] == 1
    assert result['cves'] == 1
    meta = _meta('CVE-2026-0001')
    assert meta['name_zh'] == '示例产品远程代码执行漏洞'
    assert meta['description_zh'] == '攻击者可构造请求执行任意命令。'
    assert meta['cnvd_id'] == 'CNVD-2026-10001'
    assert meta['cnvd_url'] == 'https://www.cnvd.org.cn/flaw/show/CNVD-2026-10001'
    assert meta['cvss_score'] == 9.8
    assert meta['severity'] == 'critical'
    assert meta['description_en'] == 'English description.'
    assert meta['status'] == 'ok'


def test_import_json_csv_and_gzip_with_multiple_cves(db, cnvd_dir):
    from src.core.cnvd_import import import_cnvd_files

    _write(cnvd_dir, 'api.json', """
{"items": [
  {"cnvd_id": "CNVD-2025-2",
   "references": [{"id": "CVE-2025-1000"}, {"id": "CVE-2025-1001"}],
   "name": "JSON 中文漏洞", "description": "JSON 描述"}
]}
""")
    _write(cnvd_dir, 'export.csv',
           'CNVD编号,引用编号,漏洞名称,漏洞描述\n'
           'CNVD-2025-3,CVE-2025-2000,CSV 中文漏洞,CSV 描述\n')
    xml = """
<vulList><item>
  <cnvd>CNVD-2025-4</cnvd>
  <refs><ref>CVE-2025-3000</ref></refs>
  <name>GZIP 中文漏洞</name>
  <description>GZIP 描述</description>
</item></vulList>
"""
    with gzip.open(cnvd_dir / 'week.xml.gz', 'wt', encoding='utf-8') as fh:
        fh.write(xml)

    result = import_cnvd_files(str(cnvd_dir))
    assert result['imported_files'] == 3
    assert result['cves'] == 4
    assert _meta('CVE-2025-1000')['name_zh'] == 'JSON 中文漏洞'
    assert _meta('CVE-2025-1001')['cnvd_id'] == 'CNVD-2025-2'
    assert _meta('CVE-2025-2000')['name_zh'] == 'CSV 中文漏洞'
    assert _meta('CVE-2025-3000')['cnvd_id'] == 'CNVD-2025-4'


def test_import_is_incremental_and_prefers_latest_cnvd(db, cnvd_dir):
    from src.core.cnvd_import import import_cnvd_files

    path = _write(cnvd_dir, 'week.xml', """
<vulList><vul>
  <cnvd_id>CNVD-2024-1</cnvd_id>
  <refs><ref>CVE-2024-0001</ref></refs>
  <name>旧名称</name>
</vul></vulList>
""")
    assert import_cnvd_files(str(cnvd_dir))['cves'] == 1
    second = import_cnvd_files(str(cnvd_dir))
    assert second['imported_files'] == 0
    assert second['skipped_files'] == 1

    path.write_text("""
<vulList><vul>
  <cnvd_id>CNVD-2025-100</cnvd_id>
  <refs><ref>CVE-2024-0001</ref></refs>
  <name>新名称</name>
</vul></vulList>
""", encoding='utf-8')
    third = import_cnvd_files(str(cnvd_dir), force=True)
    assert third['cves'] == 1
    meta = _meta('CVE-2024-0001')
    assert meta['cnvd_id'] == 'CNVD-2025-100'
    assert meta['name_zh'] == '新名称'


def test_import_skips_records_without_cve_and_rejects_dtd(db, cnvd_dir):
    from src.core.cnvd_import import import_cnvd_files

    _write(cnvd_dir, 'a.xml', """
<vulList><vul>
  <cnvd_id>CNVD-2024-1</cnvd_id>
  <name>没有 CVE 的漏洞</name>
</vul></vulList>
""")
    _write(cnvd_dir, 'b.xml',
           '<!DOCTYPE x [<!ENTITY y "z">]><vulList><vul>'
           '<cnvd_id>CNVD-2024-2</cnvd_id><refs><ref>CVE-2024-0002</ref></refs>'
           '</vul></vulList>')

    result = import_cnvd_files(str(cnvd_dir))
    assert result['unmatched'] == 1
    assert result['errors'] == 1
    assert result['cves'] == 0
    assert _meta('CVE-2024-0001') is None


def test_import_reads_grouped_gbt_xml(db, cnvd_dir):
    from src.core.cnvd_import import import_cnvd_files

    _write(cnvd_dir, 'gbt.xml', """
<漏洞库>
  <漏洞>
    <标识项>
      <标识号>CNVD-2026-9</标识号>
      <相关编号><CNVD>CNVD-2026-9</CNVD><CVE>CVE-2026-9000</CVE></相关编号>
    </标识项>
    <描述项>
      <名称>国标分组漏洞</名称>
      <描述>国标分组描述。</描述>
    </描述项>
  </漏洞>
</漏洞库>
""")
    assert import_cnvd_files(str(cnvd_dir))['cves'] == 1
    meta = _meta('CVE-2026-9000')
    assert meta['cnvd_id'] == 'CNVD-2026-9'
    assert meta['name_zh'] == '国标分组漏洞'
    assert meta['description_zh'] == '国标分组描述。'


def test_import_missing_directory_is_noop(db, tmp_path):
    from src.core.cnvd_import import import_cnvd_files

    result = import_cnvd_files(str(tmp_path / 'missing'))
    assert result['reason'] == 'dir_missing'
    assert result['imported_files'] == 0


def test_startup_migration_adds_cnvd_columns(tmp_path, monkeypatch):
    import sqlite3

    import src.models.database as db_mod

    try:
        db_mod.close_db()
    except Exception:
        pass
    db_path = tmp_path / 'old.db'
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE cve_meta (
            cve_id TEXT PRIMARY KEY,
            cvss_version TEXT DEFAULT '',
            cvss_score REAL,
            severity TEXT DEFAULT 'unknown',
            vector TEXT DEFAULT '',
            description_en TEXT DEFAULT '',
            published_at TEXT,
            modified_at TEXT,
            source TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            fetched_at TEXT,
            next_retry_at TEXT,
            retry_count INTEGER DEFAULT 0
        )
    """)
    conn.execute(
        "INSERT INTO cve_meta (cve_id, cvss_score, status, description_en) "
        "VALUES ('CVE-2020-0001', 7.5, 'ok', 'old row')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db_mod, 'DB_PATH', str(db_path))
    if hasattr(db_mod._local, 'conn'):
        monkeypatch.setattr(db_mod._local, 'conn', None)

    from src.models import init_all_tables
    init_all_tables()

    from src.models.database import query
    row = query('SELECT * FROM cve_meta WHERE cve_id = ?', ('CVE-2020-0001',))[0]
    assert row['description_en'] == 'old row'
    assert row['cvss_score'] == 7.5
    assert row['name_zh'] == ''
    assert row['cnvd_id'] == ''
