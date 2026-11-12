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


def normalize_assets(raw) -> list:
    """把 owned_products 统一成 [{'product': str, 'version': str}, ...]。

    兼容历史数据:
      - JSON 字符串 / list
      - 旧格式字符串 "WEB应用防护系统(WAF)" 或 "WAF:V6.0.9"
      - 新格式 dict {'product': ..., 'version': ...}
    空产品名丢弃;同一产品重复出现时保留第一条非空版本。
    """
    import json
    if raw is None or raw == '':
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = [raw]
    if not isinstance(raw, list):
        return []
    out, seen = [], {}
    for item in raw:
        if isinstance(item, dict):
            product = str(item.get('product') or '').strip()
            version = str(item.get('version') or '').strip()
        else:
            text = str(item or '').strip()
            product, _, version = text.partition(':')
            product, version = product.strip(), version.strip()
        if not product:
            continue
        if product in seen:
            if not seen[product].get('version') and version:
                seen[product]['version'] = version
            continue
        entry = {'product': product, 'version': version}
        seen[product] = entry
        out.append(entry)
    return out


def _dump_assets(raw) -> str:
    import json
    return json.dumps(normalize_assets(raw), ensure_ascii=False)


def create(created_by: int, **kwargs) -> int:
    from src.models.database import execute
    if 'owned_products' in kwargs:
        kwargs['owned_products'] = _dump_assets(kwargs['owned_products'])
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
    # Whitelist: only allow actual columns to be updated
    _ALLOWED = {'name', 'company', 'contact', 'email', 'phone', 'owned_products', 'notes', 'attachment_max_mb'}
    kwargs = {k: v for k, v in kwargs.items() if k in _ALLOWED}
    if not kwargs:
        return
    if 'owned_products' in kwargs:
        kwargs['owned_products'] = _dump_assets(kwargs['owned_products'])
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
    row['owned_products'] = normalize_assets(row.get('owned_products'))
    return row
