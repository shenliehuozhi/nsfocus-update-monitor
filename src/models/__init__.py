"""Models package — schema initialization."""

from src.models.database import get_db, transaction


def init_all_tables():
    """Create all tables if they don't exist."""
    from src.models.database import get_db
    db = get_db()

    from src.models import user
    user.create_tables(db)
    user.create_login_ban_table(db)

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
