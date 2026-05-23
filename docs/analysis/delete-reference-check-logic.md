# 订阅规则 / 通知渠道 / 客户 删除校验逻辑分析

## 一、涉及的表

| 表名 | 中文 |
|------|------|
| `subscription_rules` | 订阅规则表 |
| `rule_channels` | 规则-渠道关联表 |
| `channels` | 通知渠道表 |
| `customers` | 客户表 |

## 二、`rule_channels` 表结构

```sql
CREATE TABLE rule_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL REFERENCES subscription_rules(id) ON DELETE CASCADE,
    channel_id INTEGER REFERENCES channels(id),
    customer_id INTEGER REFERENCES customers(id)
)
```

设计意图：一个规则可绑定多个渠道、多客户，存储在 `rule_channels` 中，每条记录含 `rule_id` + `channel_id` 或 `rule_id` + `customer_id`。

**现状**：前端 UI 只写入 `subscription_rules.customer_id`（单选下拉框），不写入 `rule_channels.customer_id`，导致 `rule_channels.customer_id` 字段永远为 NULL，属于历史遗留未启用字段。

## 三、删除校验逻辑

### 3.1 渠道删除（`DELETE /api/channels/{id}`）

**校验 SQL：**
```sql
SELECT sr.id, sr.name
FROM subscription_rules sr
INNER JOIN rule_channels rc ON sr.id = rc.rule_id
WHERE rc.channel_id = ?
```

**逻辑**：检查渠道是否被任何订阅规则的 `rule_channels` 关联。有 → 返回 409，列出引用规则名；无 → 删除。

### 3.2 客户删除（`DELETE /api/customers/{id}`）

**校验 SQL：**
```sql
-- 校验1：subscription_rules 表直接引用
SELECT id, name FROM subscription_rules WHERE customer_id = ?

-- 校验2：通过 rule_channels 间接引用（死代码）
SELECT sr.id, sr.name FROM subscription_rules sr
INNER JOIN rule_channels rc ON sr.id = rc.rule_id
WHERE rc.customer_id = ?
```

**逻辑**：
- 校验1：订阅规则表的 `customer_id` 字段直接引用客户。**实际生效的校验**，客户关联规则时会被拦住。
- 校验2：`rule_channels.customer_id` 字段永远为 NULL，此查询永远无结果。**形同虚设**，属于设计遗留。

### 3.3 订阅规则删除（`DELETE /api/subscriptions/{id}`）

**校验 SQL：** 无

**逻辑**：直接删除，级联清理 `rule_channels`（ON DELETE CASCADE）、`digest_queue`、`delayed_queue`。订阅规则删除不影响其他实体，逻辑合理。

## 四、写入路径（供参考）

订阅规则保存时写入：
- `subscription_rules.customer_id`（单值，前端下拉框）
- `rule_channels`（多对多通道/客户关联，API 层面支持但前端未启用 `customers` 数组）

## 五、总结

| 操作 | 校验是否有效 | 备注 |
|------|------------|------|
| 渠道删除 | ✅ 有效 | 查 `rule_channels.channel_id` |
| 客户删除 | ⚠️ 部分有效 | 校验1有效，校验2（rule_channels.customer_id）永远为空 |
| 订阅规则删除 | ✅ 无需校验 | 级联删除，不影响其他实体 |

## 六、遗留问题

1. `rule_channels.customer_id` 字段从未被前端写入，删除客户时的第二项校验为死代码
2. API 层面支持 `customers` 数组绑定到 `rule_channels`（行 191-192），但前端 UI 从未发送此字段