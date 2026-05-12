"""Subscription rule model + rule-channel-customer binding + delivery log + delayed queue."""

import json

# ── Subscription Rule ───────────────────────────────────────

SCHEMA_SUBSCRIPTION = """
CREATE TABLE IF NOT EXISTS subscription_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    filter_conditions TEXT DEFAULT '{}',
    delay_hours INTEGER DEFAULT 0,
    delay_strategy TEXT DEFAULT 'reset' CHECK(delay_strategy IN ('reset', 'append', 'window')),
    min_interval_hours INTEGER DEFAULT 0,
    digest_mode TEXT DEFAULT '' CHECK(digest_mode IN ('', 'weekly', 'monthly', 'quarterly')),
    digest_last_sent TEXT DEFAULT '',
    digest_config TEXT DEFAULT '{}',
    quiet_start TEXT DEFAULT '',
    quiet_end TEXT DEFAULT '',
    notify_rollback INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
)
"""


def create_rule(user_id: int, **kwargs) -> int:
    from src.models.database import execute
    if 'filter_conditions' in kwargs and not isinstance(kwargs['filter_conditions'], str):
        kwargs['filter_conditions'] = json.dumps(kwargs['filter_conditions'], ensure_ascii=False)
    if 'digest_config' in kwargs and not isinstance(kwargs['digest_config'], str):
        kwargs['digest_config'] = json.dumps(kwargs['digest_config'], ensure_ascii=False)
    kwargs['user_id'] = user_id
    fields = ['user_id', 'name', 'enabled', 'filter_conditions', 'delay_hours',
              'delay_strategy', 'min_interval_hours', 'digest_mode', 'digest_config',
              'customer_id', 'valid_until', 'quiet_start', 'quiet_end', 'notify_rollback']
    values = [kwargs.get(f, '') for f in fields]
    values[2] = int(values[2]) if values[2] != '' else 1
    values[4] = values[4] or 0
    values[6] = values[6] or 0
    # Default delay_strategy
    if not values[5] or values[5] not in ('reset', 'append', 'window'):
        values[5] = 'reset'
    return execute(
        f"INSERT INTO subscription_rules ({','.join(fields)}) VALUES ({','.join(['?']*len(fields))})",
        tuple(values)
    )


def update_rule(rule_id: int, **kwargs) -> None:
    from src.models.database import execute
    if 'filter_conditions' in kwargs and not isinstance(kwargs['filter_conditions'], str):
        kwargs['filter_conditions'] = json.dumps(kwargs['filter_conditions'], ensure_ascii=False)
    if 'digest_config' in kwargs and not isinstance(kwargs['digest_config'], str):
        kwargs['digest_config'] = json.dumps(kwargs['digest_config'], ensure_ascii=False)
    sets = ', '.join(f'{k} = ?' for k in kwargs)
    execute(f"UPDATE subscription_rules SET {sets} WHERE id = ?", tuple(kwargs.values()) + (rule_id,))


def get_rule(rule_id: int) -> dict | None:
    from src.models.database import query
    rows = query("SELECT * FROM subscription_rules WHERE id = ?", (rule_id,))
    return _parse_rule(rows[0]) if rows else None


def get_enabled_rules() -> list:
    from src.models.database import query
    rows = query("SELECT * FROM subscription_rules WHERE enabled = 1")
    return [_parse_rule(r) for r in rows]


def list_rules(user_id: int = None) -> list:
    from src.models.database import query
    if user_id:
        rows = query("SELECT * FROM subscription_rules WHERE user_id = ? ORDER BY name", (user_id,))
    else:
        rows = query("SELECT * FROM subscription_rules ORDER BY name")
    return [_parse_rule(r) for r in rows]


def delete_rule(rule_id: int) -> None:
    from src.models.database import execute
    execute("DELETE FROM rule_channels WHERE rule_id = ?", (rule_id,))
    execute("DELETE FROM subscription_rules WHERE id = ?", (rule_id,))


def _parse_rule(row: dict) -> dict:
    if row.get('filter_conditions'):
        try:
            row['filter_conditions'] = json.loads(row['filter_conditions'])
        except (json.JSONDecodeError, TypeError):
            row['filter_conditions'] = {}
    if row.get('digest_config'):
        try:
            row['digest_config'] = json.loads(row['digest_config'])
        except (json.JSONDecodeError, TypeError):
            row['digest_config'] = {}
    else:
        row['digest_config'] = {}
    return row


# ── Rule-Channel-Customer Bindings ──────────────────────────

SCHEMA_RULE_CHANNELS = """
CREATE TABLE IF NOT EXISTS rule_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL REFERENCES subscription_rules(id) ON DELETE CASCADE,
    channel_id INTEGER REFERENCES channels(id),
    customer_id INTEGER REFERENCES customers(id)
)
"""


def bind_channel(rule_id: int, channel_id: int = None, customer_id: int = None) -> int:
    from src.models.database import execute
    return execute(
        "INSERT INTO rule_channels (rule_id, channel_id, customer_id) VALUES (?, ?, ?)",
        (rule_id, channel_id, customer_id)
    )


def get_rule_channels(rule_id: int) -> list:
    from src.models.database import query
    return query(
        """SELECT rc.*, c.type as channel_type, c.name as channel_name,
                  cu.name as customer_name, cu.email as customer_email
           FROM rule_channels rc
           LEFT JOIN channels c ON rc.channel_id = c.id
           LEFT JOIN customers cu ON rc.customer_id = cu.id
           WHERE rc.rule_id = ?""",
        (rule_id,)
    )


def unbind_channel(rule_id: int):
    from src.models.database import execute
    execute("DELETE FROM rule_channels WHERE rule_id = ?", (rule_id,))


# ── Delivery Log ─────────────────────────────────────────────

SCHEMA_DELIVERY_LOG = """
CREATE TABLE IF NOT EXISTS delivery_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    channel_id INTEGER REFERENCES channels(id),
    channel_type TEXT NOT NULL,
    channel_name TEXT DEFAULT '',
    customer_id INTEGER REFERENCES customers(id),
    delivery_status TEXT DEFAULT 'pending' CHECK(delivery_status IN ('pending', 'sent', 'failed')),
    error_message TEXT DEFAULT '',
    sent_at TEXT,
    retry_count INTEGER DEFAULT 0
)
"""


def log_delivery(snapshot_id: int, channel_id: int, channel_type: str,
                 channel_name: str = '', customer_id: int = None,
                 status: str = 'pending', error: str = '') -> int:
    from src.models.database import execute
    sent_at = None
    if status == 'sent':
        from datetime import datetime
        sent_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    return execute(
        """INSERT INTO delivery_log (snapshot_id, channel_id, channel_type, channel_name,
           customer_id, delivery_status, error_message, sent_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (snapshot_id, channel_id, channel_type, channel_name, customer_id, status, error, sent_at)
    )


def update_delivery(delivery_id: int, status: str, error: str = ''):
    from src.models.database import execute
    sent_at = "datetime('now')" if status == 'sent' else None
    if sent_at:
        execute(
            f"UPDATE delivery_log SET delivery_status = ?, error_message = ?, sent_at = {sent_at} WHERE id = ?",
            (status, error, delivery_id)
        )
    else:
        execute(
            "UPDATE delivery_log SET delivery_status = ?, error_message = ? WHERE id = ?",
            (status, error, delivery_id)
        )


def get_delivery_by_snapshot(snapshot_id: int) -> list:
    from src.models.database import query
    return query(
        "SELECT * FROM delivery_log WHERE snapshot_id = ? ORDER BY sent_at",
        (snapshot_id,)
    )


def get_history(page: int = 1, limit: int = 20, product: str = None,
                customer_id: int = None, days: int = None) -> tuple:
    from src.models.database import query
    conditions = []
    params = []
    if product:
        conditions.append("s.product_name = ?")
        params.append(product)
    if customer_id:
        conditions.append("dl.customer_id = ?")
        params.append(customer_id)
    if days:
        conditions.append("dl.sent_at >= datetime('now', ?)")
        params.append(f'-{days} days')
    where = " AND ".join(conditions) if conditions else "1=1"

    total = query(
        f"""SELECT COUNT(DISTINCT dl.snapshot_id) as cnt 
            FROM delivery_log dl JOIN snapshots s ON dl.snapshot_id = s.id 
            WHERE {where}""",
        tuple(params)
    )
    total = total[0]['cnt'] if total else 0

    # Get distinct snapshots with their pushed_at time
    rows = query(
        f"""SELECT DISTINCT s.*, dl.sent_at as pushed_at
            FROM delivery_log dl JOIN snapshots s ON dl.snapshot_id = s.id
            WHERE {where}
            ORDER BY dl.sent_at DESC LIMIT ? OFFSET ?""",
        tuple(params) + (limit, (page - 1) * limit)
    )

    # For each snapshot, get the delivery details
    for row in rows:
        deliveries = query(
            """SELECT dl.channel_name, dl.channel_type, dl.delivery_status,
                      dl.error_message, dl.sent_at, dl.channel_id, dl.customer_id,
                      c.name as customer_name, c.company as customer_company
               FROM delivery_log dl
               LEFT JOIN customers c ON dl.customer_id = c.id
               WHERE dl.snapshot_id = ?
               ORDER BY dl.sent_at""",
            (row['id'],)
        )
        row['deliveries'] = [dict(d) for d in deliveries]

    return rows, total


def clear_history(older_than_days: int = None) -> int:
    """Delete delivery_log entries. If older_than_days is set, only delete older entries."""
    from src.models.database import execute
    if older_than_days:
        return execute(
            "DELETE FROM delivery_log WHERE sent_at < datetime('now', ?) OR sent_at IS NULL",
            (f'-{older_than_days} days',)
        )
    else:
        return execute("DELETE FROM delivery_log")


# ── Delayed Queue ────────────────────────────────────────────

SCHEMA_DELAYED_QUEUE = """
CREATE TABLE IF NOT EXISTS delayed_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    rule_id INTEGER NOT NULL REFERENCES subscription_rules(id),
    push_after TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'cancelled', 'pushed')),
    cancelled_reason TEXT DEFAULT '',
    pushed_at TEXT
)
"""


def enqueue(snapshot_id: int, rule_id: int, push_after: str) -> int:
    from src.models.database import execute
    # Dedup: same snapshot + rule shouldn't be enqueued twice
    existing = execute.__self__ if hasattr(execute, '__self__') else None
    from src.models.database import query
    dup = query(
        "SELECT id FROM delayed_queue WHERE snapshot_id = ? AND rule_id = ? AND status = 'pending'",
        (snapshot_id, rule_id)
    )
    if dup:
        return dup[0]['id']
    return execute(
        "INSERT INTO delayed_queue (snapshot_id, rule_id, push_after) VALUES (?, ?, ?)",
        (snapshot_id, rule_id, push_after)
    )


def cancel_for_snapshot(snapshot_id: int, reason: str = ''):
    from src.models.database import execute
    execute(
        "UPDATE delayed_queue SET status = 'cancelled', cancelled_reason = ? WHERE snapshot_id = ? AND status = 'pending'",
        (reason, snapshot_id)
    )


def get_due_items() -> list:
    from src.models.database import query
    return query(
        "SELECT * FROM delayed_queue WHERE status = 'pending' AND push_after <= datetime('now')"
    )


def mark_pushed(queue_id: int):
    from src.models.database import execute
    execute(
        "UPDATE delayed_queue SET status = 'pushed', pushed_at = datetime('now') WHERE id = ?",
        (queue_id,)
    )


def reset_timer_for_rule(rule_id: int):
    """For 'reset' strategy: when a new package arrives, reset pending timers."""
    from src.models.database import execute
    execute(
        "UPDATE delayed_queue SET status = 'cancelled', cancelled_reason = 'reset_by_new_package' WHERE rule_id = ? AND status = 'pending'",
        (rule_id,)
    )


# ── Schema Version ───────────────────────────────────────────

SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT DEFAULT (datetime('now')),
    description TEXT
)
"""


def create_tables(db):
    db.execute(SCHEMA_SUBSCRIPTION)
    db.execute(SCHEMA_RULE_CHANNELS)
    db.execute(SCHEMA_DELIVERY_LOG)
    db.execute(SCHEMA_DELAYED_QUEUE)
    db.execute(SCHEMA_DIGEST_QUEUE)
    db.execute(SCHEMA_VERSION)
    # Migrations for existing DBs
    try:
        db.execute("ALTER TABLE subscription_rules ADD COLUMN digest_mode TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE subscription_rules ADD COLUMN digest_last_sent TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE subscription_rules ADD COLUMN digest_config TEXT DEFAULT '{}'")
    except Exception:
        pass


# ── Digest Queue ──────────────────────────────────────────────

SCHEMA_DIGEST_QUEUE = """
CREATE TABLE IF NOT EXISTS digest_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL REFERENCES subscription_rules(id),
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    period_key TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'sent')),
    created_at TEXT DEFAULT (datetime('now'))
)
"""


def enqueue_digest(rule_id: int, snapshot_id: int, period_key: str) -> int:
    """Add a snapshot to a rule's digest queue."""
    from src.models.database import execute, query
    # Dedup
    dup = query(
        "SELECT id FROM digest_queue WHERE rule_id=? AND snapshot_id=? AND status='pending'",
        (rule_id, snapshot_id))
    if dup:
        return dup[0]['id']
    return execute(
        "INSERT INTO digest_queue (rule_id, snapshot_id, period_key) VALUES (?, ?, ?)",
        (rule_id, snapshot_id, period_key))


def get_digest_snapshots(rule_id: int = None, period_key: str = None) -> list:
    """Get pending digest snapshots, optionally filtered by rule/period."""
    from src.models.database import query
    conditions = ["dq.status = 'pending'"]
    params = []
    if rule_id:
        conditions.append("dq.rule_id = ?")
        params.append(rule_id)
    if period_key:
        conditions.append("dq.period_key = ?")
        params.append(period_key)
    where = " AND ".join(conditions)
    return query(
        f"""SELECT dq.*, s.product_name, s.version_branch, s.package_type,
                   s.file_name, s.package_version, s.md5_hash, s.file_size,
                   s.description_raw, s.urgency, s.download_id, s.published_at,
                   s.min_sys_version
            FROM digest_queue dq JOIN snapshots s ON dq.snapshot_id = s.id
            WHERE {where}
            ORDER BY s.product_name, s.published_at""",
        tuple(params))


def mark_digest_sent(rule_id: int, period_key: str):
    """Mark all pending digest items as sent for a rule+period."""
    from src.models.database import execute
    execute(
        "UPDATE digest_queue SET status = 'sent' WHERE rule_id = ? AND period_key = ? AND status = 'pending'",
        (rule_id, period_key))


def get_rules_due_for_digest() -> list:
    """Get rules with digest_mode that are due based on their schedule config."""
    from src.models.database import query
    from datetime import datetime

    now = datetime.now()
    today = now.date()

    # Compute current period keys for default schedules
    week_key = f"{now.year}-W{now.isocalendar()[1]:02d}"
    month_key = now.strftime('%Y-%m')
    quarter_key = f"{now.year}-Q{(now.month - 1) // 3 + 1}"

    rules = query(
        """SELECT * FROM subscription_rules 
           WHERE enabled = 1 AND digest_mode != ''""")

    due = []
    for r in rules:
        r = _parse_rule(r)
        config = r.get('digest_config', {}) or {}
        mode = r['digest_mode']
        due_flag = False
        period_key = ''

        if mode == 'weekly':
            # Default: Sunday (weekday 6). Config: {"weekday": 1} = Monday
            target_weekday = config.get('weekday', 6)  # 0=Mon ... 6=Sun
            period_key = week_key
            if today.weekday() == target_weekday:
                due_flag = True

        elif mode == 'monthly':
            # Default: last day of month. Config: {"month_day": 15} or {"month_day": "last"}
            target_day = config.get('month_day', 'last')
            period_key = month_key
            if target_day == 'last':
                # Check if today is last day of month
                import calendar
                last_day = calendar.monthrange(today.year, today.month)[1]
                if today.day == last_day:
                    due_flag = True
            elif isinstance(target_day, (int, float)):
                target_day = int(target_day)
                # If target day > days in this month, use last day
                import calendar
                last_day = calendar.monthrange(today.year, today.month)[1]
                effective = min(target_day, last_day)
                if today.day >= effective:
                    due_flag = True

        elif mode == 'quarterly':
            # Default: last day of quarter.
            # Config: {"quarter_day": "last"} | {"quarter_day": "first"} | {"quarter_month": 1, "quarter_day": 15}
            import calendar
            period_key = quarter_key
            q_start_month = ((today.month - 1) // 3) * 3 + 1
            q_end_month = q_start_month + 2

            position = config.get('quarter_day', 'last')
            if position == 'last':
                last_day = calendar.monthrange(today.year, q_end_month)[1]
                target = today.replace(month=q_end_month, day=last_day)
                if today >= target:
                    due_flag = True
            elif position == 'first':
                target = today.replace(month=q_start_month, day=1)
                if today >= target:
                    due_flag = True
            elif isinstance(position, dict):
                q_month = position.get('quarter_month', 1)  # 1, 2, or 3 within quarter
                q_day = position.get('quarter_day', 1)
                actual_month = q_start_month + q_month - 1
                last_day = calendar.monthrange(today.year, actual_month)[1]
                effective = min(q_day, last_day)
                target = today.replace(month=actual_month, day=effective)
                if today >= target:
                    due_flag = True

        if due_flag and r.get('digest_last_sent', '') != period_key:
            r['_period_key'] = period_key
            due.append(r)

    return due


def mark_digest_rule_sent(rule_id: int, period_key: str):
    """Update the rule's digest_last_sent field."""
    from src.models.database import execute
    execute(
        "UPDATE subscription_rules SET digest_last_sent = ? WHERE id = ?",
        (period_key, rule_id))
