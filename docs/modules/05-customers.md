# 模块五：客户管理 (Customers)

## 功能说明

客户是推送通知的最终接收方。一个客户可以关联多个通知渠道，同时客户也是订阅规则的指向对象。

## 数据模型

### customers 表

```sql
CREATE TABLE customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,              -- 客户简称（如"绿盟科技"）
    company TEXT DEFAULT '',         -- 公司全称
    contact TEXT DEFAULT '',        -- 联系人
    email TEXT DEFAULT '',          -- 通知邮箱
    phone TEXT DEFAULT '',          -- 联系电话
    owned_products TEXT DEFAULT '[]', -- JSON，拥有的产品列表
    notes TEXT DEFAULT '',          -- 备注
    created_by INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT (datetime('now'))
)
```

## 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| name | TEXT | 客户名称（唯一标识，用于推送历史显示） |
| company | TEXT | 公司全称 |
| contact | TEXT | 联系人姓名 |
| email | TEXT | 默认通知邮箱 |
| phone | TEXT | 联系电话 |
| owned_products | TEXT(JSON) | 客户购买/拥有的产品列表（仅供参考，不影响推送） |
| notes | TEXT | 备注信息 |

## 关联关系

客户与其他模块的关联：

```
customers
    │
    ├── subscription_rules.customer_id（一个客户可对应多条订阅规则）
    │
    ├── rule_channels.customer_id（订阅规则与渠道的绑定可关联客户）
    │
    └── delivery_log.customer_id（推送历史记录客户信息）
```

## API 设计

### GET /api/customers

列出所有客户。

**响应**：
```json
{
  "code": 0,
  "data": [
    {
      "id": 1,
      "name": "绿盟科技",
      "company": "绿盟科技股份有限公司",
      "contact": "张三",
      "email": "zhangsan@nsfocus.com",
      "phone": "13800138000",
      "owned_products": ["WEB应用防护系统(WAF)", "网络入侵防护系统(IPS)"],
      "notes": "VIP客户",
      "created_at": "2026-01-15T10:00:00"
    }
  ]
}
```

### POST /api/customers

创建客户。

**请求体**：
```json
{
  "name": "客户A",
  "company": "客户A有限公司",
  "contact": "李四",
  "email": "li@example.com",
  "phone": "13900001111",
  "owned_products": ["WEB应用防护系统(WAF)"],
  "notes": ""
}
```

### PUT /api/customers/:id

更新客户信息。

### DELETE /api/customers/:id

删除客户。

**级联操作**：
1. 删除 `rule_channels` 中关联此客户的记录
2. 将 `subscription_rules.customer_id` 设为 NULL
3. 将 `delivery_log.customer_id` 设为 NULL（保留推送历史）
4. 删除 `customers` 记录

## 典型使用场景

### 场景：客户订阅特定产品的规则

1. 在「客户」中添加客户 A
2. 在「通知渠道」中添加企业微信渠道（绑定到客户 A）
3. 在「订阅规则」中创建规则：客户 A + WAF + 规则包
4. 当 WAF 有新规则包时，自动推送到客户 A 的企业微信

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/web/routes/api_routes.py` | 客户 CRUD API |
| `src/models/customer.py` | 客户数据访问层 |
| `src/models/subscription.py` | 订阅规则（含 customer_id 关联） |
