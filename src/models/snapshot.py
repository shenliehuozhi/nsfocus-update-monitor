"""ContentSource model + Snapshot model."""

# ── ContentSource ────────────────────────────────────────────

SCHEMA_CONTENT_SOURCE = """
CREATE TABLE IF NOT EXISTS content_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('nsfocus', 'rss', 'wechat_mp')),
    category TEXT DEFAULT '',
    config TEXT DEFAULT '{}',
    is_active INTEGER DEFAULT 1,
    created_by INTEGER REFERENCES users(id),
    last_collected_at TEXT,
    health_status TEXT DEFAULT 'unknown',
    created_at TEXT DEFAULT (datetime('now'))
)
"""


def create_source(name: str, source_type: str, created_by: int = None, category: str = '', config: dict = None) -> int:
    from src.models.database import execute
    import json
    return execute(
        "INSERT INTO content_sources (name, source_type, category, config, created_by) VALUES (?, ?, ?, ?, ?)",
        (name, source_type, category, json.dumps(config or {}, ensure_ascii=False), created_by)
    )


def get_source(source_id: int) -> dict | None:
    from src.models.database import query
    rows = query("SELECT * FROM content_sources WHERE id = ?", (source_id,))
    return rows[0] if rows else None


def get_source_by_name(name: str) -> dict | None:
    from src.models.database import query
    rows = query("SELECT * FROM content_sources WHERE name = ?", (name,))
    return rows[0] if rows else None


def list_sources(source_type: str = None) -> list:
    from src.models.database import query
    if source_type:
        return query("SELECT * FROM content_sources WHERE source_type = ? ORDER BY name", (source_type,))
    return query("SELECT * FROM content_sources ORDER BY name")


def update_source_health(source_id: int, status: str, last_collected_at: str = None):
    from src.models.database import execute
    execute(
        "UPDATE content_sources SET health_status = ?, last_collected_at = COALESCE(?, last_collected_at) WHERE id = ?",
        (status, last_collected_at, source_id)
    )


def set_source_active(source_id: int, active: bool):
    from src.models.database import execute
    execute("UPDATE content_sources SET is_active = ? WHERE id = ?", (int(active), source_id))


# ── Snapshot ─────────────────────────────────────────────────

SCHEMA_SNAPSHOT = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES content_sources(id),
    product_name TEXT NOT NULL,
    version_branch TEXT NOT NULL,
    package_type TEXT NOT NULL,
    file_name TEXT NOT NULL,
    package_version TEXT DEFAULT '',
    md5_hash TEXT NOT NULL,
    file_size INTEGER DEFAULT 0,
    description_raw TEXT DEFAULT '',
    description_parsed TEXT DEFAULT '{}',
    min_sys_version TEXT DEFAULT '',
    restart_required INTEGER DEFAULT 0,
    urgency TEXT DEFAULT 'normal' CHECK(urgency IN ('normal', 'high', 'critical')),
    download_id INTEGER DEFAULT 0,
    published_at TEXT DEFAULT '',
    first_seen_at TEXT DEFAULT (datetime('now')),
    last_seen_at TEXT DEFAULT (datetime('now')),
    status TEXT DEFAULT 'active' CHECK(status IN ('active', 'rollback_pending', 'rollback')),
    rollback_confirmed_at TEXT,
    page_hash TEXT DEFAULT ''
)
"""

# Indexes
SNAPSHOT_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_snapshots_source ON snapshots(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_product ON snapshots(product_name, version_branch)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_status ON snapshots(status)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_md5 ON snapshots(md5_hash)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_unique ON snapshots(source_id, product_name, version_branch, package_type, md5_hash)",
]


def create_tables(db):
    db.execute(SCHEMA_CONTENT_SOURCE)
    db.execute(SCHEMA_SNAPSHOT)
    for idx in SNAPSHOT_INDEXES:
        db.execute(idx)


def save_snapshot(snap: dict) -> int:
    """Insert or update a snapshot. Match on unique key. Returns snapshot id."""
    import json
    from src.models.database import execute, query

    desc_parsed = snap.get('description_parsed', {})
    if not isinstance(desc_parsed, str):
        desc_parsed = json.dumps(desc_parsed, ensure_ascii=False)

    existing = query(
        """SELECT id FROM snapshots 
           WHERE source_id = ? AND product_name = ? AND version_branch = ? 
           AND package_type = ? AND md5_hash = ?""",
        (snap['source_id'], snap['product_name'], snap['version_branch'],
         snap['package_type'], snap['md5_hash'])
    )

    if existing:
        sid = existing[0]['id']
        execute("""
            UPDATE snapshots SET 
                file_name = ?, package_version = ?, file_size = ?,
                description_raw = ?, description_parsed = ?,
                min_sys_version = ?, restart_required = ?, urgency = ?,
                download_id = ?, published_at = ?, last_seen_at = datetime('now'),
                status = 'active', page_hash = ?
            WHERE id = ?
        """, (
            snap.get('file_name', ''), snap.get('package_version', ''),
            snap.get('file_size', 0), snap.get('description_raw', ''),
            desc_parsed, snap.get('min_sys_version', ''),
            int(snap.get('restart_required', False)),
            snap.get('urgency', 'normal'), snap.get('download_id', 0),
            snap.get('published_at', ''), snap.get('page_hash', ''),
            sid
        ))
        return sid
    else:
        return execute("""
            INSERT INTO snapshots 
            (source_id, product_name, version_branch, package_type,
             file_name, package_version, md5_hash, file_size,
             description_raw, description_parsed, min_sys_version,
             restart_required, urgency, download_id, published_at, page_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snap['source_id'], snap['product_name'], snap['version_branch'],
            snap['package_type'], snap.get('file_name', ''),
            snap.get('package_version', ''), snap['md5_hash'],
            snap.get('file_size', 0), snap.get('description_raw', ''),
            desc_parsed, snap.get('min_sys_version', ''),
            int(snap.get('restart_required', False)),
            snap.get('urgency', 'normal'), snap.get('download_id', 0),
            snap.get('published_at', ''), snap.get('page_hash', '')
        ))


def mark_rollback_pending(seen_ids: set, source_id: int):
    """Mark snapshots not in seen_ids as rollback_pending (single miss)."""
    from src.models.database import execute, query
    active = query(
        "SELECT id FROM snapshots WHERE source_id = ? AND status = 'active'",
        (source_id,)
    )
    for row in active:
        if row['id'] not in seen_ids:
            execute(
                "UPDATE snapshots SET status = 'rollback_pending' WHERE id = ?",
                (row['id'],)
            )


def confirm_rollbacks(source_id: int, confirm_count: int = 2):
    """Confirm rollback_pending → rollback after N consecutive misses."""
    from src.models.database import execute
    # We track consecutive misses externally; here we just flip pending that have
    # been pending for enough cycles (simplified: direct call when confirmed)
    execute(
        "UPDATE snapshots SET status = 'rollback', rollback_confirmed_at = datetime('now') "
        "WHERE source_id = ? AND status = 'rollback_pending'",
        (source_id,)
    )


def get_active_snapshots(source_id: int) -> list:
    from src.models.database import query
    return query(
        "SELECT * FROM snapshots WHERE source_id = ? AND status = 'active' ORDER BY product_name, version_branch",
        (source_id,)
    )


def get_snapshot(snapshot_id: int) -> dict | None:
    from src.models.database import query
    rows = query("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,))
    return rows[0] if rows else None


def get_rollback_snapshots() -> list:
    from src.models.database import query
    return query("SELECT * FROM snapshots WHERE status = 'rollback' AND rollback_confirmed_at > datetime('now', '-1 day')")


def get_new_since(since: str) -> list:
    from src.models.database import query
    return query("SELECT * FROM snapshots WHERE first_seen_at > ? AND status = 'active' ORDER BY first_seen_at DESC", (since,))


def get_latest_by_product(source_id: int) -> list:
    """Get the latest snapshot for each product/version/package_type combo."""
    from src.models.database import query
    return query("""
        SELECT s.* FROM snapshots s
        INNER JOIN (
            SELECT product_name, version_branch, package_type, MAX(last_seen_at) as max_ts
            FROM snapshots WHERE source_id = ? AND status = 'active'
            GROUP BY product_name, version_branch, package_type
        ) latest ON s.product_name = latest.product_name 
            AND s.version_branch = latest.version_branch 
            AND s.package_type = latest.package_type
            AND s.last_seen_at = latest.max_ts
        WHERE s.source_id = ? AND s.status = 'active'
    """, (source_id, source_id))
