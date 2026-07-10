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
    """删除客户。检查订阅规则和规则-渠道绑定是否引用,引用则拒绝 ValueError。

    delivery_log 是只读审计流水,不被检查/修改 — 历史就是历史,删客户不该破坏审计证据。
    前端展示靠 channel_name 拼接客户名 (见 nsfocus-monitor-channel-name-pattern skill)。
    """
    from src.models.database import query, execute
    # 检查 subscription_rules 引用
    ref_rules = query("SELECT id, name FROM subscription_rules WHERE customer_id = ?", (customer_id,))
    if ref_rules:
        names = '、'.join(f'「{r["name"]}」(id={r["id"]})' for r in ref_rules)
        raise ValueError(f'客户(ID={customer_id})被 {len(ref_rules)} 条订阅规则引用: {names}。请先删除或解绑这些规则')
    # 检查 rule_channels 引用 (渠道绑定里的客户)
    ref_rc = query(
        "SELECT sr.id, sr.name FROM subscription_rules sr "
        "INNER JOIN rule_channels rc ON sr.id = rc.rule_id "
        "WHERE rc.customer_id = ?", (customer_id,))
    if ref_rc:
        names = '、'.join(f'「{r["name"]}」(id={r["id"]})' for r in ref_rc)
        raise ValueError(f'客户(ID={customer_id})被 {len(ref_rc)} 条渠道绑定引用: {names}。请先清理')
    # 通过检查 → 删除 (delivery_log 不动)
    execute("DELETE FROM customers WHERE id = ?", (customer_id,))


def _parse_row(row: dict) -> dict:
    import json
    if row.get('owned_products'):
        try:
            row['owned_products'] = json.loads(row['owned_products'])
        except (json.JSONDecodeError, TypeError):
            row['owned_products'] = []
    return row
