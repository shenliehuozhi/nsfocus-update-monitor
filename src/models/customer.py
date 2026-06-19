"""Customer model — the end recipients of notifications."""

SCHEMA_CUSTOMER = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    company TEXT DEFAULT '',
    contact TEXT DEFAULT '',
    email TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    owned_products TEXT DEFAULT '[]',
    notes TEXT DEFAULT '',
    attachment_max_mb INTEGER DEFAULT 0,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT (datetime('now'))
)
"""

# Migration: add attachment_max_mb to existing customer rows
def _migrate_customer_attachment(db):
    cols = [r[1] for r in db.execute("PRAGMA table_info(customers)")]
    if 'attachment_max_mb' not in cols:
        db.execute("ALTER TABLE customers ADD COLUMN attachment_max_mb INTEGER DEFAULT 0")


def create_tables(db):
    db.execute(SCHEMA_CUSTOMER)
    _migrate_customer_attachment(db)


def create(created_by: int, **kwargs) -> int:
    from src.models.database import execute
    import json
    if 'owned_products' in kwargs and not isinstance(kwargs['owned_products'], str):
        kwargs['owned_products'] = json.dumps(kwargs['owned_products'], ensure_ascii=False)
    # Ensure created_by is in kwargs for the field lookup
    kwargs['created_by'] = created_by
    fields = ['name', 'company', 'contact', 'email', 'phone', 'owned_products', 'notes',
              'attachment_max_mb', 'created_by']
    values = [kwargs.get(f, '') for f in fields]
    placeholders = ','.join(['?'] * len(fields))
    sql = f"INSERT INTO customers ({','.join(fields)}) VALUES ({placeholders})"
    return execute(sql, tuple(values))


def update(customer_id: int, **kwargs) -> None:
    from src.models.database import execute
    import json
    # Whitelist: only allow actual columns to be updated
    _ALLOWED = {'name', 'company', 'contact', 'email', 'phone', 'owned_products', 'notes', 'attachment_max_mb'}
    kwargs = {k: v for k, v in kwargs.items() if k in _ALLOWED}
    if not kwargs:
        return
    if 'owned_products' in kwargs and not isinstance(kwargs['owned_products'], str):
        kwargs['owned_products'] = json.dumps(kwargs['owned_products'], ensure_ascii=False)
    sets = ', '.join(f'{k} = ?' for k in kwargs)
    execute(f"UPDATE customers SET {sets} WHERE id = ?", tuple(kwargs.values()) + (customer_id,))


def get_by_id(customer_id: int) -> dict | None:
    from src.models.database import query
    rows = query("SELECT * FROM customers WHERE id = ?", (customer_id,))
    return _parse_row(rows[0]) if rows else None


def list_all() -> list:
    from src.models.database import query
    return [_parse_row(r) for r in query("SELECT * FROM customers ORDER BY name")]


def delete(customer_id: int) -> None:
    from src.models.database import execute
    execute("DELETE FROM rule_channels WHERE customer_id = ?", (customer_id,))
    execute("UPDATE subscription_rules SET customer_id = NULL WHERE customer_id = ?", (customer_id,))
    execute("UPDATE delivery_log SET customer_id = NULL WHERE customer_id = ?", (customer_id,))
    execute("DELETE FROM customers WHERE id = ?", (customer_id,))


def _parse_row(row: dict) -> dict:
    import json
    if row.get('owned_products'):
        try:
            row['owned_products'] = json.loads(row['owned_products'])
        except (json.JSONDecodeError, TypeError):
            row['owned_products'] = []
    return row
