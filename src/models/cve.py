"""CVE 情报模型：快照关联 CVE、评分元数据、解析扫描状态。

语义：``snapshot_cve`` 只表示「该升级包描述中提及该 CVE」，规则包里的
CVE 可能只是防护覆盖，不等同于「本次修复」。所有时间窗口一律按快照的
``published_at``（缺失回退 ``first_seen_at``）计算，避免历史 CVE 被反复
当成新漏洞。
"""

SCHEMA_SNAPSHOT_CVE = """
CREATE TABLE IF NOT EXISTS snapshot_cve (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    cve_id TEXT NOT NULL,
    evidence TEXT DEFAULT '',
    confidence REAL DEFAULT 0,
    parser_version TEXT DEFAULT 'v1',
    extracted_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (snapshot_id, cve_id)
)
"""

SCHEMA_CVE_META = """
CREATE TABLE IF NOT EXISTS cve_meta (
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
    retry_count INTEGER DEFAULT 0,
    name_zh TEXT DEFAULT '',
    description_zh TEXT DEFAULT '',
    cnvd_id TEXT DEFAULT '',
    cnvd_url TEXT DEFAULT '',
    cnvd_updated_at TEXT DEFAULT ''
)
"""

# 老库升级时补齐中文名称 / CNVD 字段；NVD 字段保持原样
EXPECTED_CVE_META_COLUMNS = [
    ('name_zh', 'TEXT', "''"),
    ('description_zh', 'TEXT', "''"),
    ('cnvd_id', 'TEXT', "''"),
    ('cnvd_url', 'TEXT', "''"),
    ('cnvd_updated_at', 'TEXT', "''"),
]

SCHEMA_CNVD_IMPORT_STATE = """
CREATE TABLE IF NOT EXISTS cnvd_import_state (
    path TEXT PRIMARY KEY,
    size INTEGER DEFAULT 0,
    mtime REAL DEFAULT 0,
    records INTEGER DEFAULT 0,
    cves INTEGER DEFAULT 0,
    imported_at TEXT DEFAULT ''
)
"""

SCHEMA_CVE_SCAN_STATE = """
CREATE TABLE IF NOT EXISTS cve_scan_state (
    snapshot_id INTEGER PRIMARY KEY REFERENCES snapshots(id),
    desc_hash TEXT NOT NULL,
    desc_len INTEGER DEFAULT 0,
    cve_count INTEGER DEFAULT 0,
    parser_version TEXT DEFAULT 'v1',
    scanned_at TEXT DEFAULT (datetime('now'))
)
"""

CVE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_snapshot_cve_cve ON snapshot_cve(cve_id)",
    "CREATE INDEX IF NOT EXISTS idx_cve_meta_severity ON cve_meta(severity, cvss_score)",
    "CREATE INDEX IF NOT EXISTS idx_cve_meta_retry ON cve_meta(status, next_retry_at)",
]


def create_tables(db):
    from src.models.database import sync_table_columns

    db.execute(SCHEMA_SNAPSHOT_CVE)
    db.execute(SCHEMA_CVE_META)
    db.execute(SCHEMA_CVE_SCAN_STATE)
    db.execute(SCHEMA_CNVD_IMPORT_STATE)
    sync_table_columns(db, 'cve_meta', EXPECTED_CVE_META_COLUMNS)
    for idx in CVE_INDEXES:
        db.execute(idx)
