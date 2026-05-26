"""ContentSource model + Snapshot model."""

# ── ContentSource ────────────────────────────────────────────

SCHEMA_CONTENT_SOURCE = """
CREATE TABLE IF NOT EXISTS content_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('nsfocus', 'rss', 'wechat_mp')),
    entry_url TEXT DEFAULT '',
    strategy TEXT DEFAULT 'auto',
    category TEXT DEFAULT '',
    config TEXT DEFAULT '{}',
    is_active INTEGER DEFAULT 1,
    is_manual INTEGER DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    display_name TEXT DEFAULT '',
    last_collected_at TEXT,
    health_status TEXT DEFAULT 'unknown',
    package_type TEXT DEFAULT '',
    package_type_discovered TEXT DEFAULT '',
    package_type_changed INTEGER DEFAULT 0,
    force_type TEXT DEFAULT '',
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


def get_source_by_url(entry_url: str) -> dict | None:
    from src.models.database import query
    rows = query("SELECT * FROM content_sources WHERE entry_url = ?", (entry_url,))
    return rows[0] if rows else None


def list_sources(source_type: str = None) -> list:
    from src.models.database import query
    if source_type:
        return query("SELECT * FROM content_sources WHERE source_type = ? ORDER BY id", (source_type,))
    return query("SELECT * FROM content_sources ORDER BY id")


def update_source_health(source_id: int, status: str, last_collected_at: str = None):
    from src.models.database import execute
    execute(
        "UPDATE content_sources SET health_status = ?, last_collected_at = COALESCE(?, last_collected_at) WHERE id = ?",
        (status, last_collected_at, source_id)
    )

def touch_active_snapshots(source_ids: list):
    """Update last_seen_at for all active snapshots of given sources (batch).

    Called after quick/full collection to reflect that collection ran,
    even when pages were unchanged. Uses a single SQL for all sources
    to minimize SQLite lock contention."""
    if not source_ids:
        return
    from src.models.database import execute
    placeholders = ','.join(['?'] * len(source_ids))
    execute(
        f"UPDATE snapshots SET last_seen_at = datetime('now') WHERE source_id IN ({placeholders}) AND status = 'active'",
        tuple(source_ids)
    )


def set_source_active(source_id: int, active: bool):
    from src.models.database import execute
    execute("UPDATE content_sources SET is_active = ? WHERE id = ?", (int(active), source_id))


def update_source(source_id: int, *, name: str = None, entry_url: str = None,
                  strategy: str = None, is_active: bool = None, category: str = None,
                  display_name: str = None, package_type: str = None,
                  force_type: str = None,
                  package_type_discovered: str = None, package_type_changed: int = None):
    """Update one or more fields of a content source. Only provided fields are updated."""
    from src.models.database import execute
    fields, vals = [], []
    if name is not None:
        fields.append("name = ?")
        vals.append(name)
    if entry_url is not None:
        fields.append("entry_url = ?")
        vals.append(entry_url)
    if strategy is not None:
        fields.append("strategy = ?")
        vals.append(strategy)
    if is_active is not None:
        fields.append("is_active = ?")
        vals.append(int(is_active))
    if category is not None:
        fields.append("category = ?")
        vals.append(category)
    if display_name is not None:
        fields.append("display_name = ?")
        vals.append(display_name)
    if package_type is not None:
        fields.append("package_type = ?")
        vals.append(package_type)
    if force_type is not None:
        fields.append("force_type = ?")
        vals.append(force_type)
    if package_type_discovered is not None:
        fields.append("package_type_discovered = ?")
        vals.append(package_type_discovered)
    if package_type_changed is not None:
        fields.append("package_type_changed = ?")
        vals.append(int(package_type_changed))
    if not fields:
        return
    vals.append(source_id)
    execute(f"UPDATE content_sources SET {', '.join(fields)} WHERE id = ?", tuple(vals))


def upsert_source(name: str, source_type: str, entry_url: str, strategy: str,
                  created_by: int = None, category: str = 'security',
                  display_name: str = None, is_active: bool = True,
                  is_manual: bool = False, package_type: str = None,
                  force_type: str = None) -> int:
    """Insert or update a content source by name. Returns source id."""
    from src.models.database import execute, query
    existing = query("SELECT id FROM content_sources WHERE name = ?", (name,))
    if existing:
        source_id = existing[0]['id']
        execute(
            "UPDATE content_sources SET entry_url=?, strategy=?, source_type=?, category=?, display_name=?, is_active=?, is_manual=?, package_type=?, force_type=? WHERE id=?",
            (entry_url, strategy, source_type, category, display_name, int(is_active), int(is_manual), package_type, force_type, source_id)
        )
        return source_id
    return execute(
        "INSERT INTO content_sources (name, source_type, entry_url, strategy, category, created_by, display_name, is_active, is_manual, package_type, force_type) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, source_type, entry_url, strategy, category, created_by, display_name, int(is_active), int(is_manual), package_type, force_type)
    )


def delete_source(source_id: int):
    """Delete a content source and all its snapshots."""
    from src.models.database import execute
    execute("DELETE FROM snapshots WHERE source_id = ?", (source_id,))
    execute("DELETE FROM content_sources WHERE id = ?", (source_id,))


def discover_products_from_index() -> list[dict]:
    """Fetch the NSFOCUS update index page and extract all product links.

    Returns list of dicts: [{'name': str, 'entry_url': str, 'display_name': str}, ...]
    The entry_url is the relative path (e.g. '/update/wafIndex').
    The display_name is the Chinese name extracted from the page text.
    """
    import requests, re
    from bs4 import BeautifulSoup

    resp = requests.get('https://update.nsfocus.com/', timeout=30,
                        headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'})
    resp.raise_for_status()
    # Always decode as UTF-8, replacing broken chars; GBK fallback no longer needed
    if resp.encoding != 'utf-8':
        resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'html.parser')

    products = []
    # The software upgrade section contains links in the format /update/[xxx]
    for a in soup.find_all('a', href=True):
        href = a['href']
        if not href.startswith('/update/'):
            continue
        # Skip non-product paths (these are navigation/selector pages, not products)
        path = href.split('?')[0].rstrip('/')
        if path in ('/update', '/update/'):
            continue
        # Skip selector/navigation paths that are not actual products
        seg = path.split('/')[-1].lower()
        if seg in ('prolist', 'selectpro') or seg.startswith('selectpro'):
            continue
        raw_text = a.get_text(strip=True)
        # Skip entries with empty text (these are UI fragments, not products)
        if not raw_text:
            continue
        display_name = raw_text  # Chinese name from page text
        if not display_name:
            # Fallback: derive name from path
            seg = path.split('/')[-1]
            for prefix in ('list', 'index', 'detail', 'waf', 'ips', 'ids', 'nf', 'uts', 'bsa'):
                if seg.lower().startswith(prefix):
                    seg = seg[len(prefix):]
            display_name = seg.strip('_/-').title()
        products.append({'name': display_name, 'entry_url': path, 'display_name': display_name})

    # Deduplicate by entry_url, keep first (longest text = most likely Chinese name)
    seen, result = set(), []
    for p in products:
        if p['entry_url'] not in seen:
            seen.add(p['entry_url'])
            result.append(p)
    return result


def detect_product_strategy(session_cookie: str, entry_url: str) -> str:
    """Detect whether a product uses 'standard' or 'recursive' strategy.

    Fetches the product's start page and checks if it has a 3-level
    structure (version → package type → table) or variable depth.
    Returns 'standard' or 'recursive'.
    """
    import requests, re
    from bs4 import BeautifulSoup

    base = 'https://update.nsfocus.com'
    full_url = base + entry_url

    s = requests.Session()
    s.cookies.set('PHPSESSID', session_cookie, domain='update.nsfocus.com')

    resp = s.get(full_url, timeout=30)
    html = resp.text

    # Recursive products (RSAS/NF) have links that don't follow strict version→pkg hierarchy
    # Standard products have distinct "package type" links (sys/rule/nti/...)
    pkg_type_indicators = {'sys', 'rule', 'nti', 'av', 'apprule', 'url', 'wcs', 'judge', 'geo'}
    link_texts = set()
    for a in BeautifulSoup(html, 'html.parser').find_all('a', href=True):
        link_texts.add(a.get_text(strip=True).lower())

    matched = link_texts & pkg_type_indicators
    if matched:
        return 'standard'
    return 'recursive'


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
    page_hash TEXT DEFAULT '',
    source_url TEXT DEFAULT ''
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
                status = 'active', rollback_cycles = 0, page_hash = ?, source_url = ?
            WHERE id = ?
        """, (
            snap.get('file_name', ''), snap.get('package_version', ''),
            snap.get('file_size', 0), snap.get('description_raw', ''),
            desc_parsed, snap.get('min_sys_version', ''),
            int(snap.get('restart_required', False)),
            snap.get('urgency', 'normal'), snap.get('download_id', 0),
            snap.get('published_at', ''), snap.get('page_hash', ''),
            snap.get('source_url', ''),
            sid
        ))
        return sid
    else:
        return execute("""
            INSERT INTO snapshots 
            (source_id, product_name, version_branch, package_type,
             file_name, package_version, md5_hash, file_size,
             description_raw, description_parsed, min_sys_version,
             restart_required, urgency, download_id, published_at, page_hash, source_url, path_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snap['source_id'], snap['product_name'], snap['version_branch'],
            snap['package_type'], snap.get('file_name', ''),
            snap.get('package_version', ''), snap['md5_hash'],
            snap.get('file_size', 0), snap.get('description_raw', ''),
            desc_parsed, snap.get('min_sys_version', ''),
            int(snap.get('restart_required', False)),
            snap.get('urgency', 'normal'), snap.get('download_id', 0),
            snap.get('published_at', ''), snap.get('page_hash', ''),
            snap.get('source_url', ''), snap.get('path_id', ''),
        ))


def mark_rollback_pending(seen_ids: set, source_id: int):
    """Mark snapshots not in seen_ids as rollback_pending.

    Uses rollback_cycles to track consecutive misses.
    First miss: status='rollback_pending', rollback_cycles=1
    Subsequent miss: rollback_cycles += 1
    Confirmation only when rollback_cycles >= ROLLBACK_CONFIRM.

    Also cancels any pending push/digest entries so rolled-back packages
    are never accidentally sent (P1-2 fix).
    """
    from src.models.database import execute, query
    from src.models.subscription import cancel_for_snapshot, cancel_digest_for_snapshot

    active = query(
        "SELECT id FROM snapshots WHERE source_id = ? AND status = 'active'",
        (source_id,)
    )
    for row in active:
        if row['id'] not in seen_ids:
            sid = row['id']
            # Cancel pending push entries (both queues) so withdrawn packages
            # are never accidentally pushed during the rollback window
            cancel_for_snapshot(sid, reason='rollback_pending')
            cancel_digest_for_snapshot(sid, reason='rollback_pending')
            execute(
                "UPDATE snapshots SET status = 'rollback_pending', rollback_cycles = 1 WHERE id = ?",
                (sid,)
            )

    # Increment rollback_cycles for already-pending snapshots (still missing)
    pending = query(
        "SELECT id FROM snapshots WHERE source_id = ? AND status = 'rollback_pending'",
        (source_id,)
    )
    for row in pending:
        if row['id'] not in seen_ids:
            execute(
                "UPDATE snapshots SET rollback_cycles = rollback_cycles + 1 WHERE id = ?",
                (row['id'],)
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
