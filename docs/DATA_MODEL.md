# 数据模型

## ER 图

```
users                       customers
┌──────────────┐            ┌──────────────────────┐
│ id (PK)      │            │ id (PK)              │
│ username     │            │ name                 │
│ password_hash│            │ company              │
│ is_admin     │            │ contact              │
│ created_at   │            │ email                │
└──┬───────────┘            │ phone                │
   │                        │ owned_products (JSON)│  ["WAF:V6.0.9", "IPS:5.6.11"]
   │                        │ notes                │
   │                        │ created_by (FK→user) │
   │                        └──────────────────────┘
   │
user_sessions               content_sources
┌──────────────┐            ┌──────────────────────────┐
│ id (PK)      │            │ id (PK)                  │
│ user_id (FK) │            │ name                     │  "WAF", "IPS-RSS"
│ cookie_value │            │ source_type              │  nsfocus|rss|wechat_mp
│ (encrypted)  │            │ category                 │  产品分类
│ last_valid   │            │ config (JSON)            │  源特有配置
│ expires_at   │            │ is_active                │
│ created_at   │            │ created_by (FK→user)     │
│ status       │  active/   │ last_collected_at        │
│              │  expired   │ health_status            │
└──────────────┘            └──────────┬───────────────┘
                                       │
                     ┌─────────────────┘
                     │
                     ▼
              snapshots
              ┌──────────────────────────────┐
              │ id (PK)                      │
              │ source_id (FK→content_source)│
              │ product_name                 │  "WAF"
              │ version_branch               │  "V6.0.9"
              │ package_type                 │  "rule" / "sys" / "nti"
              │ file_name                    │
              │ package_version              │  "V6.0R09F00.29622898"
              │ md5_hash                     │
              │ file_size                    │  bytes (整数)
              │ description_raw              │  原始描述文本
              │ description_parsed (JSON)    │  {added:[], modified:[], deleted:[]}
              │ min_sys_version              │  "V6.0R09F00.29386582"
              │ restart_required             │  bool
              │ urgency                      │  normal|high|critical
              │ download_id                  │  187442
              │ published_at                 │
              │ first_seen_at                │
              │ last_seen_at                 │
              │ status                       │  active|rollback_pending|rollback
              │ rollback_confirmed_at        │
              │ page_hash                    │  页面内容哈希(检测改版)
              └──────────────────────────────┘

subscription_rules            channels
┌──────────────────────┐      ┌──────────────────────┐
│ id (PK)              │      │ id (PK)              │
│ user_id (FK)         │      │ user_id (FK)         │
│ name                 │      │ name                 │
│ enabled              │      │ type                 │ wecom|dingtalk|feishu|email
│ filter_conditions    │      │ config (JSON)        │
│  (JSON)              │      │   (encrypted)        │
│  {                   │      │ is_active            │
│    source_types: [], │      │ created_at           │
│    products: [],     │      └──────────┬───────────┘
│    versions: [],     │                 │
│    package_types: [],│      ┌──────────┴───────────┐
│    keywords: [],     │      │                      │
│    urgency: []       │      ▼                      ▼
│  }                   │  rule_channels          delivery_log
│ delay_hours          │  ┌──────────────┐      ┌──────────────────────┐
│ delay_strategy       │  │ id (PK)      │      │ id (PK)              │
│  reset|append|window │  │ rule_id (FK) │      │ snapshot_id (FK)     │
│ min_interval_hours   │  │ channel_id   │◄─────│ channel_id (FK)      │
│ quiet_start          │  │ customer_id  │      │ delivery_status      │
│ quiet_end            │  │  (nullable)  │      │  sent|failed|pending │
│ created_at           │  └──────────────┘      │ error_message        │
└──────────┬───────────┘                        │ sent_at              │
           │                                    │ retry_count          │
           │                                    └──────────────────────┘
delayed_queue
┌──────────────────────┐
│ id (PK)              │      audit_log
│ snapshot_id (FK)     │      ┌──────────────────────┐
│ rule_id (FK)         │      │ id (PK)              │
│ push_after           │      │ user_id (FK)         │
│ created_at           │      │ action               │
│ status               │      │  login|config_change |
│  pending|cancelled   │      │  manual_push|retry   │
│  |pushed             │      │ details (JSON)       │
│ cancelled_reason     │      │ ip_address           │
│ pushed_at            │      │ created_at           │
└──────────────────────┘      └──────────────────────┘
```

## 表设计要点

### 1. encryption

以下字段使用 AES-256-GCM 加密存储：
- `user_sessions.cookie_value`
- `channels.config`（包含 webhook_url / smtp_password）

加密密钥存储在环境变量 `MONITOR_SECRET_KEY` 中，不写入配置文件。

### 2. JSON 字段

SQLite 原生支持 JSON 函数（>=3.38），以下字段使用 TEXT 存储 JSON：
- `customers.owned_products`: `["WAF:V6.0.9", "WAF:V6.0.8", "IPS:5.6.11"]`
- `subscription_rules.filter_conditions`: 结构化过滤条件
- `snapshots.description_parsed`: 解析后的结构化描述
- `channels.config`: 渠道配置

### 3. 索引

```sql
CREATE INDEX idx_snapshots_source ON snapshots(source_id);
CREATE INDEX idx_snapshots_product ON snapshots(product_name, version_branch);
CREATE INDEX idx_snapshots_status ON snapshots(status);
CREATE INDEX idx_snapshots_md5 ON snapshots(md5_hash);
CREATE INDEX idx_delivery_snapshot ON delivery_log(snapshot_id);
CREATE INDEX idx_delayed_queue_status ON delayed_queue(status, push_after);
CREATE INDEX idx_audit_user ON audit_log(user_id, created_at);
```

### 4. 表关系约束

- `snapshots`: (source_id, product_name, version_branch, package_type, md5_hash) 组合唯一
- `delayed_queue`: snapshot_id + rule_id 唯一（同一个包对同一个规则只入队一次）
- `delivery_log`: snapshot_id + channel_id 唯一

## 迁移策略

```sql
-- schema_version 表
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT DEFAULT (datetime('now')),
    description TEXT
);

-- 初始版本
INSERT INTO schema_version VALUES (1, datetime('now'), 'Initial schema');
```

后续迁移通过 `scripts/migrate.py` 执行，每个版本一个 SQL 文件。

## 数据清理

- 快照数据保留 180 天：`DELETE FROM snapshots WHERE last_seen_at < datetime('now', '-180 days') AND status = 'rollback'`
- 推送日志保留 365 天
- 审计日志保留 365 天
- 清理任务作为定时任务每周执行
