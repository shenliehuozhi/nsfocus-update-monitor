"""Models package — schema initialization."""

from src.models.database import get_db, transaction


def init_all_tables():
    """Create all tables if they don't exist."""
    from src.models.database import get_db
    db = get_db()

    from src.models import user
    user.create_tables(db)

    from src.models import customer
    customer.create_tables(db)

    from src.models import user_session
    user_session.create_tables(db)

    from src.models import channel
    channel.create_tables(db)

    from src.models import snapshot
    snapshot.create_tables(db)

    from src.models import subscription
    subscription.create_tables(db)

    from src.models import audit
    audit.create_tables(db)

    from src.models import event_log
    event_log.create_tables(db)

    # system_settings: key-value config store (used by app.py, scheduler.py, router.py, etc.)
    db.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    from src.core.rate_limiter import create_tables as create_rate_limit_table
    create_rate_limit_table(db)

    from src.core.email_rate_limiter import create_tables as create_email_rate_table
    # Migration: drop old single-key schema and recreate with composite PK
    db.execute("DROP TABLE IF EXISTS email_rate_counters")
    create_email_rate_table(db)

    # Initialize schema version
    rows = db.execute("SELECT MAX(version) as v FROM schema_version").fetchall()
    current = rows[0]['v'] if rows and rows[0]['v'] else 0
    if current == 0:
        db.execute("INSERT INTO schema_version (version, description) VALUES (1, 'Initial schema')")
        db.commit()

    # Initialize nsfocus product data from bundled JSON (first deployment convenience)
    _init_content_sources(db)


def _init_content_sources(db):
    """Bootstrap nsfocus content sources from bundled initial_sources.json.

    Only inserts sources that don't already exist in the DB.
    Discovered paths are bundled so first deployment shows full product tree
    without needing manual "discover" step.
    """
    import json, os

    seed_path = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'initial_sources.json')
    if not os.path.exists(seed_path):
        return

    with open(seed_path, encoding='utf-8') as f:
        sources = json.load(f)

    existing = set()
    for row in db.execute("SELECT name FROM content_sources WHERE source_type=?", ('nsfocus',)):
        existing.add(row['name'])

    from src.models.snapshot import upsert_source
    for src in sources:
        if src['name'] in existing:
            continue
        pt = src.get('package_type') or {}
        pkg_json = json.dumps(pt) if pt.get('paths') else None
        upsert_source(
            name=src['name'],
            source_type='nsfocus',
            entry_url=src.get('entry_url', ''),
            strategy=src.get('strategy', 'standard'),
            category=src.get('category', 'security'),
            display_name=src.get('display_name', src['name']),
            is_active=src.get('is_active', 1),
            is_manual=src.get('is_manual', 0),
            package_type=pkg_json,
        )
