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

    # Initialize schema version
    rows = db.execute("SELECT MAX(version) as v FROM schema_version").fetchall()
    current = rows[0]['v'] if rows and rows[0]['v'] else 0
    if current == 0:
        db.execute("INSERT INTO schema_version (version, description) VALUES (1, 'Initial schema')")
        db.commit()
