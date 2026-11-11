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
    delay_days INTEGER DEFAULT 0,
    delay_strategy TEXT DEFAULT 'reset' CHECK(delay_strategy IN ('reset', 'append', 'window')),
    min_interval_hours INTEGER DEFAULT 0,
    digest_mode TEXT DEFAULT '' CHECK(digest_mode IN ('', 'weekly', 'monthly', 'quarterly')),
    digest_last_sent TEXT DEFAULT '',
    digest_config TEXT DEFAULT '{}',
    quiet_start TEXT DEFAULT '',
    quiet_end TEXT DEFAULT '',
    notify_rollback INTEGER DEFAULT 1,
    customer_id INTEGER REFERENCES customers(id),
    valid_until TEXT DEFAULT '',
    customer_emails TEXT DEFAULT '',
    attachment_max_mb INTEGER DEFAULT 0,
    window_config TEXT DEFAULT '{}',
    template TEXT DEFAULT 'full',
    created_at TEXT DEFAULT (datetime('now'))
)
"""

# Schema-sync source-of-truth for subscription_rules.
# Keep this in sync with INSERT/UPDATE field usage in create_rule / update_rule
# below. The startup sync (sync_table_columns in create_tables) will ALTER
# existing DBs to add any columns listed here that are missing.
# Format: (column_name, sqlite_type, default_sql). default_sql empty → no DEFAULT.
EXPECTED_SUBSCRIPTION_RULES_COLUMNS = [
    ('user_id', 'INTEGER', "0"),
    ('name', 'TEXT', "''"),
    ('enabled', 'INTEGER', '1'),
    ('filter_conditions', 'TEXT', "'{}'"),
    ('delay_days', 'INTEGER', '0'),
    ('delay_strategy', 'TEXT', "'reset'"),
    ('min_interval_hours', 'INTEGER', '0'),
    ('digest_mode', 'TEXT', "''"),
    ('digest_config', 'TEXT', "'{}'"),
    ('customer_id', 'INTEGER', '0'),
    ('valid_until', 'TEXT', "''"),
    ('quiet_start', 'TEXT', "''"),
    ('quiet_end', 'TEXT', "''"),
    ('notify_rollback', 'INTEGER', '1'),
    ('customer_emails', 'TEXT', "''"),
    ('window_config', 'TEXT', "'{}'"),
    ('template', 'TEXT', "'full'"),
    # digest_last_sent — never INSERTed but UPDATEd via kwargs in
    # mark_digest_sent() et al. Sync keeps old DBs from breaking writes.
    ('digest_last_sent', 'TEXT', "''"),
    # attachment_max_mb deliberately omitted — deprecated; readers use customers.attachment_max_mb.
    # id / created_at — never altered (auto-increment / row-creation default).
]


def create_rule(user_id: int, **kwargs) -> int:
    from src.models.database import execute
    if 'filter_conditions' in kwargs and not isinstance(kwargs['filter_conditions'], str):
        kwargs['filter_conditions'] = json.dumps(kwargs['filter_conditions'], ensure_ascii=False)
    if 'digest_config' in kwargs and not isinstance(kwargs['digest_config'], str):
        kwargs['digest_config'] = json.dumps(kwargs['digest_config'], ensure_ascii=False)
    if 'window_config' in kwargs and not isinstance(kwargs['window_config'], str):
        kwargs['window_config'] = json.dumps(kwargs['window_config'], ensure_ascii=False)
    if 'channel_templates' in kwargs:
        # 临时兼容代码:之前实验新增的字段名,2026-07-10 §32.4 终版改回 template 字段。
        # 收到老 wire-format 时把内容搬进 template 列(JSON 字典)。后续版本可移除。
        kwargs['template'] = json.dumps(_sanitize_channel_templates(kwargs['channel_templates']), ensure_ascii=False)
        del kwargs['channel_templates']
    elif 'template' in kwargs and isinstance(kwargs['template'], dict):
        # 字典形式(JSON 形态),验证后 dump
        kwargs['template'] = json.dumps(_sanitize_channel_templates(kwargs['template']), ensure_ascii=False)
    elif 'template' in kwargs:
        # 标量形式(老数据 / 兼容路径):full / strip / brief / feishu_full
        from src.notifiers.base import TEMPLATE_NAMES
        if kwargs['template'] not in TEMPLATE_NAMES:
            kwargs['template'] = 'full'
    kwargs['user_id'] = user_id
    fields = ['user_id', 'name', 'enabled', 'filter_conditions', 'delay_days',
              'delay_strategy', 'min_interval_hours', 'digest_mode', 'digest_config',
              'customer_id', 'valid_until', 'quiet_start', 'quiet_end', 'notify_rollback',
              'customer_emails', 'window_config', 'template']
    # NOTE: subscription_rules.attachment_max_mb column is deprecated.
    # notifiers/router.py). The column is retained in SCHEMA_SUBSCRIPTION for
    # backward compatibility with existing rows but create_rule() no longer
    # writes to it — new code must read customers.attachment_max_mb instead.
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
    if 'window_config' in kwargs and not isinstance(kwargs['window_config'], str):
        kwargs['window_config'] = json.dumps(kwargs['window_config'], ensure_ascii=False)
    if 'channel_templates' in kwargs:
        # 临时兼容代码:把老 wire-format 内容搬进 template 列(JSON 字典)。后续版本可移除。
        kwargs['template'] = json.dumps(_sanitize_channel_templates(kwargs['channel_templates']), ensure_ascii=False)
        del kwargs['channel_templates']
    elif 'template' in kwargs and isinstance(kwargs['template'], dict):
        kwargs['template'] = json.dumps(_sanitize_channel_templates(kwargs['template']), ensure_ascii=False)
    sets = ', '.join(f'{k} = ?' for k in kwargs)
    execute(f"UPDATE subscription_rules SET {sets} WHERE id = ?", tuple(kwargs.values()) + (rule_id,))


def get_rule(rule_id: int) -> dict | None:
    from src.models.database import query
    # 2026-07-22: LEFT JOIN customers 把 cu.name as customer_name 加进 dict
    # 让所有用 rule.get('customer_name') 的地方(推送汇总 / 推送通知 / frontend)都自动拿到客户名
    # 之前是 SELECT *,没有 customer_name,显示"未指定客户"bug
    rows = query(
        """SELECT r.*, cu.name as customer_name, cu.email as customer_email
           FROM subscription_rules r
           LEFT JOIN customers cu ON r.customer_id = cu.id
           WHERE r.id = ?""",
        (rule_id,))
    return _parse_rule(rows[0]) if rows else None


def get_enabled_rules() -> list:
    from src.models.database import query
    # 2026-07-22: 同样 LEFT JOIN,enabled 列表也带 customer_name
    rows = query(
        """SELECT r.*, cu.name as customer_name, cu.email as customer_email
           FROM subscription_rules r
           LEFT JOIN customers cu ON r.customer_id = cu.id
           WHERE r.enabled = 1""")
    return [_parse_rule(r) for r in rows]


def list_rules(user_id: int = None) -> list:
    from src.models.database import query
    from src.models.channel import _decrypt_config
    if user_id:
        rows = query("SELECT * FROM subscription_rules WHERE user_id = ? ORDER BY name", (user_id,))
    else:
        rows = query("SELECT * FROM subscription_rules ORDER BY name")
    rules = [_parse_rule(r) for r in rows]
    for rule in rules:
        ch_rows = query("SELECT * FROM rule_channels WHERE rule_id = ?", (rule['id'],))
        # 2026-07-11: 返回完整 rule_channels 行 (per-channel 字段),前端编辑规则时回填
        rule['channels'] = [
            {
                'channel_id': r['channel_id'],
                'customer_id': r['customer_id'],
                'customer_emails': r.get('customer_emails') or '',
                'attachment_max_mb': r.get('attachment_max_mb') or 0,
                'template': r.get('template') or '',
                'extras': r.get('extras') or '{}',
            }
            for r in ch_rows if r.get('channel_id')
        ]
        # 2026-07-14: 通知对象按渠道分组,每 binding 一组,前端表格逐行渲染
        # 收件人 fallback 链(每个 binding 独立算):
        #   rule_channels.customer_emails → customers.email → channels.config.to_list(email)/渠道名(机器人)
        # 跟 src/notifiers/router.py 里的发送端 fallback 对称,避免列表显示和实际发送对不上
        TYPE_LABEL = {'email': '邮件', 'wecom': '企微', 'dingtalk': '钉钉', 'feishu': '飞书'}
        channel_ids = [r['channel_id'] for r in ch_rows if r.get('channel_id')]
        # 一次性查所有渠道 + 所有客户,N+1 改 2 次查询
        channels_by_id = {}
        if channel_ids:
            placeholders = ','.join('?' * len(channel_ids))
            for c in query(f"SELECT * FROM channels WHERE id IN ({placeholders})", tuple(channel_ids)):
                channels_by_id[c['id']] = _decrypt_config(c)
        cust_ids = {r.get('customer_id') for r in ch_rows if r.get('customer_id')}
        customers_by_id = {}
        if cust_ids:
            placeholders = ','.join('?' * len(cust_ids))
            for cu in query(f"SELECT id, name, email FROM customers WHERE id IN ({placeholders})", tuple(cust_ids)):
                customers_by_id[cu['id']] = cu
        groups = []
        for r in ch_rows:
            cid = r.get('channel_id')
            ch = channels_by_id.get(cid) if cid else None
            ch_type = (ch or {}).get('type', '')
            type_label = TYPE_LABEL.get(ch_type, ch_type)
            ch_name = (ch or {}).get('name', '')
            cust_id = r.get('customer_id')
            cust = customers_by_id.get(cust_id) if cust_id else None
            # 收件人 fallback:per-channel customer_emails > customers.email > 渠道 to_list(邮件) / 渠道名(机器人)
            recipients = []
            rc_emails = (r.get('customer_emails') or '').strip()
            if rc_emails:
                recipients = [e.strip() for e in rc_emails.split(',') if e.strip()]
            elif cust and (cust.get('email') or '').strip():
                recipients = [(cust['email'] or '').strip()]
            elif ch_type == 'email' and ch:
                to_list = (ch.get('config') or {}).get('to_list') or []
                recipients = [e for e in to_list if e]
            elif ch:
                # 机器人:收件人=渠道名(对接群里就是这个机器人)
                recipients = [ch_name]
            attach_mb = int(r.get('attachment_max_mb') or 0)
            groups.append({
                'channel_id': cid,
                'channel_type': ch_type,
                'channel_name': ch_name,
                'type_label': type_label,
                'customer_id': cust_id,
                'customer_name': (cust or {}).get('name', '') if cust else '',
                'recipients': recipients,
                'attachment_max_mb': attach_mb,
            })
        rule['notification_groups'] = groups
        # 保留旧字段兼容(只读路径/history 等可能引用),合并为扁平邮箱列表,无 binding 时给空数组
        flat = []
        for g in groups:
            for e in g['recipients']:
                if e and e not in flat:
                    flat.append(e)
        rule['notification_targets'] = flat
    return rules


def delete_rule(rule_id: int) -> None:
    from src.models.database import execute
    execute("DELETE FROM rule_channels WHERE rule_id = ?", (rule_id,))
    execute("DELETE FROM digest_queue WHERE rule_id = ?", (rule_id,))
    execute("DELETE FROM delayed_queue WHERE rule_id = ?", (rule_id,))
    execute("DELETE FROM delivery_log WHERE rule_id = ?", (rule_id,))  # delivery_log has FK to subscription_rules
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
    if row.get('window_config'):
        try:
            row['window_config'] = json.loads(row['window_config'])
        except (json.JSONDecodeError, TypeError):
            row['window_config'] = {}
    else:
        row['window_config'] = {}
    # template: 字典形式(JSON 形态)时 = {"<channel_id>": "<template_name>", ...}
    # 标量形式(老数据 / 兼容路径):full / strip / brief / feishu_full
    # 老规则 / 新规则 template 为空时,返回 {} (notifier router 按渠道类型 fallback 到默认模板)
    if row.get('template'):
        try:
            parsed = json.loads(row['template'])
            if isinstance(parsed, dict):
                row['template'] = parsed
            elif isinstance(parsed, str):
                # 标量(老数据):保持原值,router 会识别
                row['template'] = parsed
            else:
                row['template'] = {}
        except (json.JSONDecodeError, TypeError):
            # 不是 JSON 也不是合法模板名 → 当作空
            row['template'] = {}
    else:
        row['template'] = {}
    return row


def _sanitize_channel_templates(raw) -> dict:
    """校验 channel_templates 字典的 key/value 合法性。

    key 必须是 int (channel_id) 或可转 int 的字符串;value 必须是
    TEMPLATE_NAMES 白名单内名称。不合法项一律丢弃,确保 DB 写入安全。
    """
    from src.notifiers.base import TEMPLATE_NAMES
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        try:
            cid = int(k)
        except (TypeError, ValueError):
            continue
        if not isinstance(v, str) or v not in TEMPLATE_NAMES:
            continue
        out[cid] = v
    return out


# ── Rule-Channel-Customer Bindings ──────────────────────────

SCHEMA_RULE_CHANNELS = """
CREATE TABLE IF NOT EXISTS rule_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL REFERENCES subscription_rules(id) ON DELETE CASCADE,
    channel_id INTEGER REFERENCES channels(id),
    customer_id INTEGER REFERENCES customers(id)
, customer_emails TEXT DEFAULT '', attachment_max_mb INTEGER DEFAULT 0, template TEXT DEFAULT '', extras TEXT DEFAULT '{}');
"""


# 2026-07-11: rule_channels 加 4 个 per-channel 字段
#   customer_emails: per-channel 收件人 (替代全局 subscription_rules.customer_emails)
#   attachment_max_mb: per-channel 附件大小限制 (替代 customers.attachment_max_mb)
#   template: per-channel 模板 (替代全局 subscription_rules.template JSON dict)
#   extras: JSON 兜底字段,以后新加 per-channel 属性用它,不用再 ALTER
# 老数据兼容: 新列默认 '', 0, '', '{}',发送时 4 级 fallback 自动兼容
def _migrate_rule_channels_per_channel(db):
    cols = [r[1] for r in db.execute("PRAGMA table_info(rule_channels)")]
    if 'customer_emails' not in cols:
        db.execute("ALTER TABLE rule_channels ADD COLUMN customer_emails TEXT DEFAULT ''")
    if 'attachment_max_mb' not in cols:
        db.execute("ALTER TABLE rule_channels ADD COLUMN attachment_max_mb INTEGER DEFAULT 0")
    if 'template' not in cols:
        db.execute("ALTER TABLE rule_channels ADD COLUMN template TEXT DEFAULT ''")
    if 'extras' not in cols:
        db.execute("ALTER TABLE rule_channels ADD COLUMN extras TEXT DEFAULT '{}'")


def bind_channel(rule_id: int, channel_id: int = None, customer_id: int = None,
                 customer_emails: str = '', attachment_max_mb: int = 0,
                 template: str = '', extras: str = '{}') -> int:
    from src.models.database import execute
    return execute(
        """INSERT INTO rule_channels (rule_id, channel_id, customer_id,
           customer_emails, attachment_max_mb, template, extras)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (rule_id, channel_id, customer_id, customer_emails,
         attachment_max_mb, template, extras)
    )


def get_rule_channels(rule_id: int) -> list:
    from src.models.database import query
    return query(
        """SELECT rc.*, c.type as channel_type, c.name as channel_name,
                  cu.name as customer_name, cu.email as customer_email
           FROM rule_channels rc
           LEFT JOIN channels c ON rc.channel_id = c.id
           LEFT JOIN customers cu ON rc.customer_id = cu.id
           WHERE rc.rule_id = ?
           ORDER BY rc.id""",
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
    rule_id INTEGER REFERENCES subscription_rules(id),
    channel_id INTEGER REFERENCES channels(id),
    channel_type TEXT NOT NULL,
    channel_name TEXT DEFAULT '',
    customer_id INTEGER REFERENCES customers(id),
    delivery_status TEXT DEFAULT 'pending' CHECK(delivery_status IN ('pending', 'sent', 'failed')),
    error_message TEXT DEFAULT '',
    sent_at TEXT,
    retry_count INTEGER DEFAULT 0,
    recipient TEXT DEFAULT '',
    sender TEXT DEFAULT '',
    chain_json TEXT DEFAULT '[]',
    created_at TEXT DEFAULT ''
)
"""
# 2026-11-09 P0 性能: delivery_log 此前一个索引都没有,推送历史、客户推送分布、
# 仪表盘 recent_deliveries 全部退化成 SCAN dl(EXPLAIN QUERY PLAN 实测)。
# 行数已 7k+ 且 90 天就产生 5k+ 条,现在补上,IF NOT EXISTS 对老库幂等安全。
SCHEMA_DELIVERY_LOG_INDEXES = [
    # 时间范围过滤 + 倒序取最近 (get_history / dashboard recent / clear_history)
    "CREATE INDEX IF NOT EXISTS idx_delivery_sent ON delivery_log(sent_at)",
    # 按客户过滤/聚合 (get_history?customer_id= / customers/push-summary)
    "CREATE INDEX IF NOT EXISTS idx_delivery_customer ON delivery_log(customer_id, sent_at)",
    # 按订阅规则过滤 (get_history?rule_id=)
    "CREATE INDEX IF NOT EXISTS idx_delivery_rule ON delivery_log(rule_id, sent_at)",
    # snapshot → deliveries 关联 (get_history 批量取 / 重推前查重)
    "CREATE INDEX IF NOT EXISTS idx_delivery_snapshot ON delivery_log(snapshot_id)",
]

# Schema-sync for delivery_log. Keep in sync with INSERT/UPDATE used by
# log_delivery / update_delivery / get_history.
EXPECTED_DELIVERY_LOG_COLUMNS = [
    ('snapshot_id', 'INTEGER', '0'),
    ('rule_id', 'INTEGER', '0'),
    ('channel_id', 'INTEGER', '0'),
    ('channel_type', 'TEXT', "''"),
    ('channel_name', 'TEXT', "''"),
    ('customer_id', 'INTEGER', '0'),
    ('delivery_status', 'TEXT', "'pending'"),
    ('error_message', 'TEXT', "''"),
    ('recipient', 'TEXT', "''"),
    ('sender', 'TEXT', "''"),
    ('chain_json', 'TEXT', "'[]'"),
    # created_at — attempt timestamp (stamped on every insert, success or failure).
    # 失败行 sent_at 为 NULL，没有 created_at 就无法按时间窗统计失败率。
    ('created_at', 'TEXT', "''"),
    # sent_at — auto-stamped by app when status='sent', DEFAULT '' if not set
    ('sent_at', 'TEXT', "''"),
    # retry_count — DEFAULT 0, but only UPDATEd via update_delivery
    ('retry_count', 'INTEGER', '0'),
]
# ── Delivery Log ────────────────────────────────────────────

# 2026-07-11 channel_name 改造: 写入时拼上客户名
# 格式: "<原渠道名>|Nsf0cus|<客户名>" (3 段,Nsf0cus 当分隔符)
# 前端按此分隔符 split 取 [0] 是渠道名,[1] 是客户名
# 优点: 不加列 / 不迁移老数据 / 删除客户后历史仍能看到名字
_SEP = '|Nsf0cus|'


def _compose_channel_name(channel_name: str, customer_id) -> str:
    """log_delivery 内部用: 把客户名拼进 channel_name。

    - customer_id 缺失 (None / 0 / ''): 不拼,原值返回
    - 客户已被删 (CUSTOMER_GONE): 写 '[已删除]'
    - 正常: 拼成 '<channel_name>|Nsf0cus|<customer_name>'

    注意: 已经拼过的 (含 |Nsf0cus|) 不重复拼,避免嵌套。
    """
    if not customer_id:
        return channel_name
    if _SEP in (channel_name or ''):
        # 已拼过,跳过 (幂等保护)
        return channel_name
    from src.models.database import query as _q
    rows = _q("SELECT name FROM customers WHERE id = ?", (customer_id,))
    if rows:
        return f"{channel_name}{_SEP}{rows[0]['name']}"
    return f"{channel_name}{_SEP}[已删除]"


def log_delivery(snapshot_id: int, channel_id: int, channel_type: str,
                 channel_name: str = '', customer_id: int = None,
                 status: str = 'pending', error: str = '',
                 rule_id: int = None, recipient: str = '', sender: str = '',
                 chain_json: str = '[]') -> int:
    from src.models.database import execute
    # 2026-07-11: 写入时把客户名拼进 channel_name (详见 _compose_channel_name)
    channel_name = _compose_channel_name(channel_name, customer_id)
    from datetime import datetime
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    sent_at = now if status == 'sent' else None
    return execute(
        """INSERT INTO delivery_log
           (snapshot_id, rule_id, channel_id, channel_type, channel_name,
            customer_id, delivery_status, error_message, sent_at, recipient, sender, chain_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (snapshot_id, rule_id, channel_id, channel_type, channel_name, customer_id, status, error, sent_at, recipient, sender, chain_json or '[]', now)
    )


def update_delivery(delivery_id: int, status: str, error: str = ''):
    # 2026-09-02: 改用 Python datetime.utcnow() 而非 SQLite datetime('now')
    # SQLite 在 CST 服务器上 datetime('now') 会把 CST 减 8h 当 UTC,前端 +8h 后少 8h
    from datetime import datetime
    sent_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S') if status == 'sent' else None
    if sent_at:
        execute(
            "UPDATE delivery_log SET delivery_status = ?, error_message = ?, sent_at = ? WHERE id = ?",
            (status, error, sent_at, delivery_id)
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
                customer_id: int = None, days: int = None,
                rule_id: int = None) -> tuple:
    """获取推送历史。feat/customer-push-card 2026-09-02 修复 cust 过滤:
    必须按 dl.customer_id 过滤,不能 GROUP BY s.id 后丢失交付关联。
    rule_id 同理按 dl.rule_id 过滤(delivery_log.rule_id 记录命中的订阅规则)。
    """
    from src.models.database import query
    conditions = []
    params = []
    if product:
        conditions.append("s.product_name = ?")
        params.append(product)
    if customer_id:
        # 直接按 dl 过滤,确保只返回推给该客户的 snap,后续 deliveries 也只列该客户的
        conditions.append("dl.customer_id = ?")
        params.append(customer_id)
    if rule_id:
        conditions.append("dl.rule_id = ?")
        params.append(rule_id)
    if days:
        conditions.append("dl.sent_at >= datetime('now', ?)")
        params.append(f'-{days} days')
    where = " AND ".join(conditions) if conditions else "1=1"

    # 2026-11-09 P1: total 必须是"包数"(distinct 快照), 不是交付行数 —— 下面 rows
    # 按 GROUP BY s.id 每包只出一行。原来 COUNT(dl.*) 在"同包推 3 个客户"时把 total
    # 报成 3, 前端拿它算页数就会得到完全错误的页码 (7124 vs 实际 2464 包)。
    total = query(
        f"""SELECT COUNT(DISTINCT s.id) as cnt
            FROM delivery_log dl JOIN snapshots s ON dl.snapshot_id = s.id
            WHERE {where}""",
        tuple(params)
    )
    total = total[0]['cnt'] if total else 0

    # ⚠️ feat/customer-push-card 2026-09-02 修复:
    # 原来 GROUP BY s.id + MAX(sent_at) 会让 customer_id 过滤失效 (返回该 snap 的所有客户)
    # 改成直接 SELECT dl.*,取 delivery 行,distinct snapshot_id
    # 因同 snap 推给同客户会有多条 (email 重试/多渠道),用 GROUP BY s.id 取最新一条
    rows = query(
        f"""SELECT s.*, MAX(dl.sent_at) as pushed_at
            FROM delivery_log dl JOIN snapshots s ON dl.snapshot_id = s.id
            WHERE {where}
            GROUP BY s.id
            ORDER BY pushed_at DESC LIMIT ? OFFSET ?""",
        tuple(params) + (limit, (page - 1) * limit)
    )

    # For each snapshot, get the delivery details.
    # 2026-11-09 P0 性能: 原来每个快照行单查一次 deliveries —— limit=200 时实测
    # 202 条 SQL / ~750ms。改成一次 IN (...) 批量取再按 snapshot_id 分组。
    # feat/customer-push-card 2026-09-02 的语义保持不变: deliveries 仍按 customer_id /
    # rule_id 过滤,避免列表行混入其他客户或其他规则的交付记录。
    if rows:
        snap_ids = [r['id'] for r in rows]
        del_cond = "dl.snapshot_id IN (" + ','.join('?' * len(snap_ids)) + ")"
        del_params = list(snap_ids)
        if customer_id:
            del_cond += " AND dl.customer_id = ?"
            del_params.append(customer_id)
        if rule_id:
            del_cond += " AND dl.rule_id = ?"
            del_params.append(rule_id)
        deliveries = query(
            f"""SELECT dl.snapshot_id,
                      dl.channel_name, dl.channel_type, dl.delivery_status,
                      dl.error_message, dl.sent_at, dl.channel_id, dl.customer_id,
                      dl.recipient, dl.sender, dl.chain_json,
                      c.name as customer_name, c.company as customer_company,
                      CASE
                        WHEN dl.chain_json IS NOT NULL AND dl.chain_json != '[]'
                          THEN json_extract(dl.chain_json, '$[#-1]')
                        ELSE s.package_type
                      END AS derived_package_type
               FROM delivery_log dl
               JOIN snapshots s ON dl.snapshot_id = s.id
               LEFT JOIN customers c ON dl.customer_id = c.id
               WHERE {del_cond}
               ORDER BY dl.sent_at DESC""",
            tuple(del_params)
        )
        grouped = {}
        for d in deliveries:
            grouped.setdefault(d['snapshot_id'], []).append(dict(d))
        for row in rows:
            row['deliveries'] = grouped.get(row['id'], [])

    return rows, total


def clear_history(older_than_days: int = None) -> int:
    """Delete delivery_log entries. If older_than_days is set, only delete older entries.

    失败行 sent_at 为 NULL，旧 SQL 用 `OR sent_at IS NULL` 把它们每次清理都删光，
    导致推送失败在历史里查不到。现在只按 sent_at 判断，失败行保留。
    """
    from src.models.database import get_db
    db = get_db()
    if older_than_days:
        db.execute(
            "DELETE FROM delivery_log WHERE sent_at IS NOT NULL "
            "AND sent_at < datetime('now', ?)",
            (f'-{older_than_days} days',)
        )
    else:
        db.execute("DELETE FROM delivery_log")
    return db.total_changes


def cleanup_failed_delivery_log(older_than_days: int = 180) -> int:
    """删除超过保留期的失败交付记录。

    clear_history 只删已发送行，失败行会永久累积。created_at 是后补的列，
    没有时间戳的历史失败行无法判断年龄，保持不动（宁可留旧数据也不误删）。
    """
    from src.models.database import get_db
    db = get_db()
    before = db.total_changes
    db.execute(
        "DELETE FROM delivery_log WHERE delivery_status = 'failed' "
        "AND created_at IS NOT NULL "
        "AND datetime(created_at) < datetime('now', ?)",
        (f'-{older_than_days} days',)
    )
    return db.total_changes - before


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
    pushed_at TEXT,
    chain_json TEXT DEFAULT '[]',
    attempts INTEGER DEFAULT 0,
    next_attempt_at TEXT DEFAULT '',
    last_attempt_at TEXT DEFAULT '',
    last_error TEXT DEFAULT ''
)
"""


def enqueue(snapshot_id: int, rule_id: int, push_after: str, chain_json: str = '[]') -> int:
    from src.models.database import execute, query
    # Dedup by snapshot + rule + chain. A shared physical snapshot can have
    # multiple chain events, so snapshot + rule is not sufficient.
    dup = query(
        """SELECT id FROM delayed_queue
           WHERE snapshot_id = ? AND rule_id = ? AND chain_json = ?
             AND status = 'pending'""",
        (snapshot_id, rule_id, chain_json or '[]')
    )
    if dup:
        return dup[0]['id']
    return execute(
        """INSERT INTO delayed_queue
           (snapshot_id, rule_id, push_after, chain_json) VALUES (?, ?, ?, ?)""",
        (snapshot_id, rule_id, push_after, chain_json or '[]')
    )


def cancel_digest_for_snapshot(snapshot_id: int, reason: str = ''):
    """Cancel all pending digest entries for a snapshot (package rolled back)."""
    from src.models.database import execute
    execute(
        "UPDATE digest_queue SET status = 'cancelled' WHERE snapshot_id = ? AND status = 'pending'",
        (snapshot_id,)
    )


def cancel_digest_item(dq_id: int):
    """Mark a single digest_queue entry as cancelled (package no longer active)."""
    from src.models.database import execute
    execute(
        "UPDATE digest_queue SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
        (dq_id,)
    )


def cancel_for_snapshot(snapshot_id: int, reason: str = ''):
    """Cancel all pending delayed-queue entries for a snapshot (package rolled back)."""
    from src.models.database import execute
    execute(
        "UPDATE delayed_queue SET status = 'cancelled', cancelled_reason = ? WHERE snapshot_id = ? AND status = 'pending'",
        (reason, snapshot_id)
    )


def get_due_items(limit: int = 200) -> list:
    """Return pending delayed items whose timer and retry backoff have elapsed.

    push_after / next_attempt_at 均为服务器本地时间（compute_push_time 用
    datetime.now()），历史上这里用 datetime('now')（UTC）比较，CST 服务器上
    会把所有延迟推送硬生生拖后 8 小时。
    """
    from src.models.database import query
    from datetime import datetime
    now_local = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return query(
        """SELECT * FROM delayed_queue
           WHERE status = 'pending' AND push_after <= ?
             AND (next_attempt_at IS NULL OR next_attempt_at = '' OR next_attempt_at <= ?)
           ORDER BY push_after, id
           LIMIT ?""",
        (now_local, now_local, limit)
    )


def mark_pushed(queue_id: int):
    from src.models.database import execute
    from datetime import datetime
    now_local = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute(
        "UPDATE delayed_queue SET status = 'pushed', pushed_at = ?, last_attempt_at = ? "
        "WHERE id = ?",
        (now_local, now_local, queue_id)
    )


def mark_delivery_retry(queue_id: int, attempts: int, next_attempt_at: str,
                        error: str = ''):
    """Keep the item pending and schedule the next attempt after a backoff."""
    from src.models.database import execute
    from datetime import datetime
    now_local = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute(
        """UPDATE delayed_queue
           SET attempts = ?, next_attempt_at = ?, last_attempt_at = ?, last_error = ?
           WHERE id = ? AND status = 'pending'""",
        (attempts, next_attempt_at, now_local, (error or '')[:1000], queue_id)
    )


def mark_delivery_terminal(queue_id: int, reason: str, error: str = ''):
    """Stop retrying an item that cannot be delivered (config error / exhausted)."""
    from src.models.database import execute
    from datetime import datetime
    now_local = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute(
        """UPDATE delayed_queue
           SET status = 'cancelled', cancelled_reason = ?, last_error = ?,
               last_attempt_at = ?
           WHERE id = ? AND status = 'pending'""",
        (reason[:200], (error or '')[:1000], now_local, queue_id)
    )


def reset_delivery_for_retry(queue_id: int) -> dict | None:
    """把终止的延迟队列项重新放回 pending（人工重试）。

    只接受 retry_exhausted / no_active_channel 两类终态：reset_by_new_package、
    快照撤回等取消是业务选择，不是投递故障，不应被一键复活。
    返回更新后的行；不存在或不可重试返回 None。
    """
    from src.models.database import execute, query
    from datetime import datetime

    rows = query("SELECT * FROM delayed_queue WHERE id = ?", (queue_id,))
    if not rows:
        return None
    row = rows[0]
    reason = row.get('cancelled_reason') or ''
    if row.get('status') != 'cancelled':
        return None
    if not (reason.startswith('retry_exhausted') or reason == 'no_active_channel'):
        return None

    now_local = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    execute(
        """UPDATE delayed_queue
           SET status = 'pending', attempts = 0, next_attempt_at = '',
               cancelled_reason = '', pushed_at = NULL, last_error = '',
               last_attempt_at = '', push_after = ?
           WHERE id = ? AND status = 'cancelled'""",
        (now_local, queue_id)
    )
    return query("SELECT * FROM delayed_queue WHERE id = ?", (queue_id,))[0]


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
    # 全新部署走 CREATE TABLE IF NOT EXISTS (带 DEFAULT),老 DB 不动 schema
    db.execute(SCHEMA_SUBSCRIPTION)
    db.execute(SCHEMA_RULE_CHANNELS)
    db.execute(SCHEMA_DELIVERY_LOG)
    db.execute(SCHEMA_DELAYED_QUEUE)
    db.execute(SCHEMA_DIGEST_QUEUE)
    db.execute(SCHEMA_VERSION)

    for idx in SCHEMA_DELIVERY_LOG_INDEXES:
        db.execute(idx)
    for idx in SCHEMA_DIGEST_QUEUE_INDEXES:
        db.execute(idx)

    # 老 DB 自动补列 — sync 取代之前一长串 try/except ALTER TABLE。
    # 这样以后再加列,只需要在 EXPECTED_* 列表里加一行,启动期自动 ALTER。
    from src.models.database import sync_table_columns
    sync_table_columns(db, 'subscription_rules', EXPECTED_SUBSCRIPTION_RULES_COLUMNS)
    sync_table_columns(db, 'delivery_log', EXPECTED_DELIVERY_LOG_COLUMNS)
    sync_table_columns(db, 'delayed_queue', [
        ('chain_json', 'TEXT', "'[]'"),
        ('attempts', 'INTEGER', '0'),
        ('next_attempt_at', 'TEXT', "''"),
        ('last_attempt_at', 'TEXT', "''"),
        ('last_error', 'TEXT', "''"),
    ])
    sync_table_columns(db, 'digest_queue', [
        ('chain_json', 'TEXT', "'[]'"),
        ('attempts', 'INTEGER', '0'),
        ('next_attempt_at', 'TEXT', "''"),
        ('last_attempt_at', 'TEXT', "''"),
        ('last_error', 'TEXT', "''"),
    ])

    # 历史数据迁移: delay_hours → delay_days (小时/24)
    # 仅对老 DB 里有 delay_hours 列的起作用;新 DB 没这列 → 异常被吞。
    # 一次性的:迁移完所有行的 delay_days 都不为 0 后,这段代码可删。
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(subscription_rules)").fetchall()}
        if 'delay_hours' in cols:
            db.execute(
                "UPDATE subscription_rules SET delay_days = CAST(delay_hours AS INTEGER) / 24 "
                "WHERE delay_days = 0 AND delay_hours > 0"
            )
    except Exception:
        pass

    # 2026-07-11: rule_channels per-channel 字段 (4 列)
    _migrate_rule_channels_per_channel(db)


# ── Digest Queue ──────────────────────────────────────────────

SCHEMA_DIGEST_QUEUE = """
CREATE TABLE IF NOT EXISTS digest_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL REFERENCES subscription_rules(id),
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    period_key TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'sent', 'cancelled')),
    created_at TEXT DEFAULT (datetime('now')),
    chain_json TEXT DEFAULT '[]',
    attempts INTEGER DEFAULT 0,
    next_attempt_at TEXT DEFAULT '',
    last_attempt_at TEXT DEFAULT '',
    last_error TEXT DEFAULT ''
)
"""

SCHEMA_DIGEST_QUEUE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_digest_rule_status ON digest_queue(rule_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_digest_period ON digest_queue(period_key)",
]


def cleanup_digest_queue(older_than_days: int = 90) -> int:
    """删除已发送/已取消的 digest_queue 行。

    pending 行永不删除 —— 它们是未投递包的 outbox,清理掉就等于静默丢包。
    """
    from src.models.database import get_db
    db = get_db()
    before = db.total_changes
    db.execute(
        "DELETE FROM digest_queue WHERE status IN ('sent', 'cancelled') "
        "AND created_at < datetime('now', ?)",
        (f'-{older_than_days} days',)
    )
    return db.total_changes - before


def enqueue_digest(
    rule_id: int,
    snapshot_id: int,
    period_key: str,
    chain_json: str = '[]',
) -> int:
    """Add a snapshot to a rule's digest queue."""
    from src.models.database import execute, query
    # Dedup
    dup = query(
        """SELECT id FROM digest_queue
           WHERE rule_id=? AND snapshot_id=? AND chain_json=?
             AND status='pending'""",
        (rule_id, snapshot_id, chain_json or '[]'))
    if dup:
        return dup[0]['id']
    return execute(
        """INSERT INTO digest_queue
           (rule_id, snapshot_id, period_key, chain_json) VALUES (?, ?, ?, ?)""",
        (rule_id, snapshot_id, period_key, chain_json or '[]'))


def digest_period_key(mode: str, when=None) -> str:
    """当前周期的 key。weekly 用 ISO 年,不是日历年。

    12/31 与次年第一个周日同属一个 ISO 周,但 datetime.year 会给出
    2026 / 2027 两个不同的 key(实测 2026-12-31 → 2026-W53、
    2027-01-03 → 2027-W53),入队和发送对不上,那一周的包永远 pending。
    """
    from datetime import datetime as _dt
    dt = when or _dt.now()
    if mode == 'weekly':
        iso = dt.isocalendar()
        return f'{iso[0]}-W{iso[1]:02d}'
    if mode == 'monthly':
        return dt.strftime('%Y-%m')
    return f'{dt.year}-Q{(dt.month - 1) // 3 + 1}'


def get_digest_snapshots(rule_id: int = None, period_key: str = None) -> list:
    """Get pending digest snapshots, optionally filtered by rule/period.

    process_digests 不传 period_key: 所有 pending 行都要投递,包括过期周期
    漏发的、以及上一周期汇总发出后才到货的。只取当前周期会让这些行永远
    pending(既没有即时推送,也不会再进任何一期汇总)。
    """
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
                   s.min_sys_version, s.source_url, s.status AS snapshot_status
            FROM digest_queue dq JOIN snapshots s ON dq.snapshot_id = s.id
            WHERE {where}
            ORDER BY s.source_url, s.published_at""",
        tuple(params))


def mark_digest_sent(rule_id: int, period_key: str = None, max_id: int = None):
    """Mark pending digest items as sent.

    max_id: 只标记 id <= max_id 的行。发送侧把所有 pending(含往期漏发/
    迟到)打进同一封汇总后按本次最大行号标记,发送期间新入队的包留到下一期。
    period_key: 兼容旧调用,只标记该周期。两者都不传则标记全部 pending。
    """
    from src.models.database import execute
    if max_id is not None:
        execute(
            "UPDATE digest_queue SET status = 'sent' "
            "WHERE rule_id = ? AND status = 'pending' AND id <= ?",
            (rule_id, max_id))
    elif period_key:
        execute(
            "UPDATE digest_queue SET status = 'sent' "
            "WHERE rule_id = ? AND period_key = ? AND status = 'pending'",
            (rule_id, period_key))
    else:
        execute(
            "UPDATE digest_queue SET status = 'sent' "
            "WHERE rule_id = ? AND status = 'pending'",
            (rule_id,))


def defer_digest_items(rule_id: int, max_id: int, attempts: int,
                       next_attempt_at: str, error: str = ''):
    """Keep pending digest rows and schedule a retry (UTC timestamps).

    max_id 限定本次已尝试的行，发送期间新入队的包不受影响。
    """
    from src.models.database import execute
    from datetime import datetime
    now_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    execute(
        """UPDATE digest_queue
           SET attempts = ?, next_attempt_at = ?, last_attempt_at = ?, last_error = ?
           WHERE rule_id = ? AND status = 'pending' AND id <= ?""",
        (attempts, next_attempt_at, now_utc, (error or '')[:1000], rule_id, max_id)
    )


def get_rules_due_for_digest(now=None) -> list:
    """Get rules with digest_mode that are due based on their schedule config.

    到期条件(满足其一):
      1. 今天进入该规则配置的推送日/时段;
      2. 队列里还有上个周期留下的 pending 行 —— 说明那一期因为进程没跑、
         维护模式、窗口太窄等原因漏发了,现在补发(否则这些包永远不投递)。

    幂等由 digest_last_sent 保证:同一 period_key 只发一次。
    """
    from src.models.database import query
    from datetime import datetime

    now = now or datetime.now()
    today = now.date()

    rules = query(
        """SELECT * FROM subscription_rules 
           WHERE enabled = 1 AND digest_mode != ''""")

    due = []
    for r in rules:
        r = _parse_rule(r)
        config = r.get('digest_config', {}) or {}
        mode = r['digest_mode']
        due_flag = False
        period_key = digest_period_key(mode, now)

        if mode == 'weekly':
            # Default: Sunday (weekday 6). Config: {"weekday": 1} = Monday
            target_weekday = config.get('weekday', 6)  # 0=Mon ... 6=Sun
            # >= 而不是 ==:目标日进程没跑时,同周后续的检查还能补发
            if today.weekday() >= target_weekday:
                due_flag = True

        elif mode == 'monthly':
            # Default: last day of month. Config: {"month_day": 15} or {"month_day": "last"}
            target_day = config.get('month_day', 'last')
            if target_day == 'last':
                import calendar
                last_day = calendar.monthrange(today.year, today.month)[1]
                # >=:与自定义日(如 15 号)语义一致,当月后续检查还能补
                if today.day >= last_day:
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

        if not due_flag:
            overdue = query(
                """SELECT 1 FROM digest_queue
                   WHERE rule_id = ? AND status = 'pending'
                     AND period_key != ? LIMIT 1""",
                (r['id'], period_key))
            if overdue:
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
