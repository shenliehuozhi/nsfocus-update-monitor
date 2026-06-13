# 模块四：订阅规则 (Subscriptions)

## 功能说明

订阅规则定义了什么情况下向哪些客户推送升级通知。一条规则描述了"哪些产品的哪些升级包"应该通知"哪个客户/哪些渠道"。

## 数据模型

### subscription_rules 表

```sql
CREATE TABLE subscription_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,                    -- 规则名称
    enabled INTEGER DEFAULT 1,              -- 1=启用, 0=停用
    filter_conditions TEXT DEFAULT '{}',   -- JSON，过滤条件
    delay_days INTEGER DEFAULT 0,         -- 延迟推送天数
    delay_strategy TEXT DEFAULT 'reset',   -- reset/append/window
    min_interval_hours INTEGER DEFAULT 0,  -- 最小推送间隔（小时）
    digest_mode TEXT DEFAULT '',           -- ''/weekly/monthly/quarterly
    digest_last_sent TEXT DEFAULT '',
    digest_config TEXT DEFAULT '{}',
    quiet_start TEXT DEFAULT '',           -- 静默开始时间（如 "22:00"）
    quiet_end TEXT DEFAULT '',             -- 静默结束时间（如 "08:00"）
    notify_rollback INTEGER DEFAULT 1,     -- 是否通知撤回
    customer_id INTEGER REFERENCES customers(id),  -- 关联客户
    valid_until TEXT DEFAULT '',           -- 规则有效期截止
    customer_emails TEXT DEFAULT '',       -- 额外邮件通知地址
    attachment_max_mb INTEGER DEFAULT 0,  -- 附件大小限制（MB），0=不附件
    window_config TEXT DEFAULT '{}',       -- 窗口配置 JSON
    created_at TEXT DEFAULT (datetime('now'))
)
```

### rule_channels 表

订阅规则与渠道的绑定关系：

```sql
CREATE TABLE rule_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL REFERENCES subscription_rules(id) ON DELETE CASCADE,
    channel_id INTEGER REFERENCES channels(id),
    customer_id INTEGER REFERENCES customers(id)
)
```

## filter_conditions 过滤条件（JSON）

```json
{
  "product_names": ["WEB应用防护系统(WAF)", "网络入侵防护系统(IPS)"],
  "version_branches": ["V6.0.9", "V7.0.0"],
  "package_types": ["rule", "sys"],
  "urgency": ["critical", "high"]
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| product_names | string[] | 匹配的产品名称列表 |
| version_branches | string[] | 匹配的版本分支 |
| package_types | string[] | 匹配的包类型 |
| urgency | string[] | 紧急程度（critical/high/normal） |

## 延迟推送策略

| 策略 | 说明 |
|------|------|
| reset | 新包到达后重置计时器（默认） |
| append | 追加，新包不重置计时器 |
| window | 在配置的时间窗口内才推送 |

## 摘要模式（digest_mode）

| 模式 | 说明 |
|------|------|
| '' | 不使用摘要模式，每次立即推送 |
| weekly | 每周汇总推送一次（周一） |
| monthly | 每月汇总推送一次（每月1日） |
| quarterly | 每季度汇总推送一次 |

摘要模式下，升级包不立即推送，而是进入 `digest_queue`，等待周期到期时一起发送。

## 推送流程

```
采集发现新升级包(snapshot)
        ↓
匹配所有启用的订阅规则
        ↓
对每条规则检查：
  ├── 过滤条件是否匹配？
  ├── 是否在静默时间内？
  ├── 是否在有效期内？
  ├── delay_days 是否到期？
  ├── min_interval 是否满足？
  └── 是否需要摘要模式？
        ↓（全部通过）
发送到对应渠道
        ↓
记录 delivery_log（sent/failed）
```

## API 设计

### GET /api/subscriptions

列出所有订阅规则（带客户名和渠道名）。

**响应**：
```json
{
  "code": 0,
  "data": [
    {
      "id": 1,
      "name": "客户A-WAF规则包订阅",
      "enabled": 1,
      "filter_conditions": {
        "product_names": ["WEB应用防护系统(WAF)"],
        "package_types": ["rule"]
      },
      "delay_days": 0,
      "delay_strategy": "reset",
      "customer_id": 1,
      "customer_name": "客户A",
      "channels": [1, 2],
      "channels_detail": [
        {"id": 1, "name": "企业微信-客户A", "type": "wecom"},
        {"id": 2, "name": "邮件-客户A", "type": "email"}
      ]
    }
  ]
}
```

### POST /api/subscriptions

创建订阅规则。

### PUT /api/subscriptions/:id

更新订阅规则（部分字段）。

### DELETE /api/subscriptions/:id

删除订阅规则（同时删除 rule_channels/delivery_log/delayed_queue/digest_queue）。

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/models/subscription.py` | 订阅规则数据访问层 |
| `src/models/channel.py` | 渠道管理 |
| `src/models/customer.py` | 客户管理 |
| `src/core/notifier.py` | 通知发送 |
