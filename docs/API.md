# API 设计

## 约定

- 基础路径: `http://119.23.152.22:8800/api`
- 认证: `Authorization: Bearer <JWT>`
- Content-Type: `application/json`
- 响应格式: `{"code": 0, "data": {...}}` 或 `{"code": 40001, "message": "..."}`

## 错误码

| code | 说明 |
|------|------|
| 0 | 成功 |
| 40001 | 参数错误 |
| 40100 | 未登录 |
| 40101 | Token 过期 |
| 40300 | 无权限 |
| 40400 | 资源不存在 |
| 50000 | 服务器错误 |

---

## 1. 认证

### POST /api/auth/register
```json
// REQUEST
{"username": "admin", "password": "xxx"}
// RESPONSE
{"code": 0, "data": {"id": 1, "username": "admin"}}
```

### POST /api/auth/login
```json
// REQUEST
{"username": "admin", "password": "xxx"}
// RESPONSE
{"code": 0, "data": {"token": "eyJ...", "user": {"id": 1, "username": "admin"}}}
```

### POST /api/auth/refresh
```json
// REQUEST - Header: Authorization: Bearer <old_token>
// RESPONSE
{"code": 0, "data": {"token": "eyJ..."}}
```

---

## 2. Session 管理

### GET /api/sessions
返回当前用户的所有 Session 及全局 Session 池状态。

```json
{
  "code": 0,
  "data": {
    "my_sessions": [
      {"id": 1, "status": "active", "last_valid": "2026-05-10T10:00:00", "expires_at": "2026-05-11T10:00:00"}
    ],
    "pool_status": {
      "total": 2,
      "active": 2,
      "expired": 0,
      "primary": "user_a_session"
    }
  }
}
```

### POST /api/sessions
```json
// REQUEST
{"cookie_value": "jvrprvielrfbmgrla348mim23b"}
// RESPONSE
{"code": 0, "data": {"id": 1, "status": "validating", "message": "正在验证..."}}
```

### DELETE /api/sessions/:id

### POST /api/sessions/:id/validate
手动触发 Session 验证。

---

## 3. 客户管理

### GET /api/customers
```json
{
  "code": 0,
  "data": [
    {"id": 1, "name": "客户A", "company": "xx公司", "owned_products": ["WAF:V6.0.9", "IPS:5.6.11"]}
  ]
}
```

### POST /api/customers
### PUT /api/customers/:id
### DELETE /api/customers/:id
删除前检查是否被订阅规则引用，有引用返回 `409`：
```json
{"code": 40900, "message": "该客户被以下订阅规则引用，请先取消引用再删除：「规则A」"}
```

```json
// REQUEST
{
  "name": "客户A",
  "company": "xx科技有限公司",
  "contact": "张三",
  "email": "zhangsan@example.com",
  "phone": "13800138000",
  "owned_products": ["WAF:V6.0.9", "WAF:V6.0.8", "IPS:5.6.11"],
  "notes": "合同到期 2026-12-31"
}
```

---

## 4. 内容源

### GET /api/sources
```json
{
  "code": 0,
  "data": [
    {"id": 1, "name": "WAF", "source_type": "nsfocus", "is_active": true,
     "health_status": "ok", "last_collected_at": "2026-05-10T09:00:00",
     "snapshot_count": 45}
  ]
}
```

### POST /api/sources/:id/preview
手动触发一次抓取预览，返回本次采集到的数据（不写入数据库）。

```json
{
  "code": 0,
  "data": {
    "collected_at": "2026-05-10T18:00:00",
    "duration_ms": 8200,
    "items": [
      {
        "product_name": "WAF",
        "version_branch": "V6.0.9",
        "package_type": "rule",
        "file_name": "update_rule.V6.0R09F00.29622898.wcl",
        "package_version": "V6.0R09F00.29622898",
        "md5_hash": "7138a6f8c4c4347811b51dbd739bfcbe",
        "file_size": 2456625,
        "download_id": 187442,
        "urgency": "normal",
        "is_new": true
      }
    ],
    "stats": {"total": 3, "new": 1, "existing": 2, "rollback": 0}
  }
}
```

### POST /api/sources/collect
手动触发全量采集（写入数据库，正常走检测和通知流程）。

### GET /api/sources/:id/versions
获取该产品的版本列表（实时从绿盟站点拉取）。

```json
{"code": 0, "data": ["V6.0.9", "V6.0.8", "V6.0.7", "..."]}
```

### GET /api/sources/:id/package-types?version=V6.0.9
获取指定版本的包类型列表。

```json
{"code": 0, "data": ["系统升级包", "规则升级包", "威胁情报升级包"]}
```

---

## 5. 订阅规则

### GET /api/subscriptions
### POST /api/subscriptions
### PUT /api/subscriptions/:id
### DELETE /api/subscriptions/:id

```json
// REQUEST
{
  "name": "WAF关键规则通知",
  "enabled": true,
  "filter_conditions": {
    "source_types": ["nsfocus"],
    "products": ["WAF"],
    "versions": ["V6.0.9", "V6.0.8"],
    "package_types": ["rule"],
    "keywords": [],
    "urgency": ["high", "critical"]
  },
  "delay_hours": 72,
  "delay_strategy": "window",
  "min_interval_hours": 168,
  "quiet_start": "22:00",
  "quiet_end": "08:00",
  "channels": [1, 2],
  "customers": [1]
}
```

---

## 6. 通知渠道

### GET /api/channels
```json
{
  "code": 0,
  "data": [
    {"id": 1, "name": "企微-售后群", "type": "wecom", "is_active": true},
    {"id": 2, "name": "邮件-客户A", "type": "email", "is_active": true}
  ]
}
```

### POST /api/channels
```json
// REQUEST (企业微信)
{
  "name": "企微-售后群",
  "type": "wecom",
  "config": {"webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"}
}

// REQUEST (邮件)
{
  "name": "邮件-客户A",
  "type": "email",
  "config": {
    "smtp_host": "smtp.exmail.qq.com",
    "smtp_port": 465,
    "smtp_user": "notify@example.com",
    "smtp_password": "xxx",
    "from_name": "绿盟升级通知",
    "to_list": ["clientA@example.com", "clientB@example.com"]
  },
  "email_hourly_limit": 50,   // 可选，0=不限制
  "email_daily_limit": 200    // 可选，0=不限制
}
```

### PUT /api/channels/:id
同 POST，支持更新 `email_hourly_limit` 和 `email_daily_limit`。

### DELETE /api/channels/:id
删除前检查是否被订阅规则引用，有引用返回 `409`：
```json
{"code": 40900, "message": "该渠道被以下订阅规则引用，请先取消引用再删除：「规则A」「规则B」"}
```
### POST /api/channels/:id/test
发送测试消息到该渠道。

---

## 7. 推送历史

### GET /api/history
```json
// QUERY: ?page=1&limit=20&product=WAF&customer_id=1&from=2026-05-01&to=2026-05-10
{
  "code": 0,
  "data": {
    "items": [
      {
        "id": 1,
        "snapshot": {"product_name": "WAF", "version_branch": "V6.0.9", "package_type": "rule",
                     "package_version": "V6.0R09F00.29622898", "urgency": "normal"},
        "deliveries": [
          {"channel_name": "企微-售后群", "status": "sent", "sent_at": "2026-05-10T09:05:00"},
          {"channel_name": "邮件-客户A", "status": "sent", "sent_at": "2026-05-10T09:05:00"}
        ],
        "pushed_at": "2026-05-10T09:05:00"
      }
    ],
    "total": 42,
    "page": 1
  }
}
```

### POST /api/history/:id/resend
重新推送指定历史记录。

---

## 8. 仪表盘

### GET /api/dashboard
```json
{
  "code": 0,
  "data": {
    "session_status": {"pool_active": 2, "pool_total": 3},
    "last_collection": {"time": "2026-05-10T09:00:00", "duration_ms": 8200, "products_ok": 6, "products_fail": 0},
    "stats_today": {"new_packages": 1, "rollbacks": 0, "notifications_sent": 2},
    "stats_this_week": {"new_packages": 3, "rollbacks": 1, "notifications_sent": 6},
    "products_summary": [
      {"name": "WAF", "total_packages": 45, "latest_release": "2026-05-07T13:59:41"},
      {"name": "IPS", "total_packages": 23, "latest_release": "2026-04-20T10:00:00"}
    ]
  }
}
```

---

## 9. 手动推送

### POST /api/history/{snapshot_id}/push

对指定快照执行手动推送。推送前检查频率限制（同key 1分钟≤5次，超限10分钟禁用）。

**模式**：

| mode | body 参数 | 说明 |
|------|----------|------|
| `customer` | `target_id` (客户ID) | 推送至客户的邮箱（通过邮件渠道中继），客户须有邮箱 |
| `channel` | `target_id` (渠道ID) | 推送到指定渠道（webhook/email等） |
| `manual_email` | `email` (字符串) | 推送到手动填写的邮箱，多个逗号分隔 |

**REQUEST (customer 模式)**:
```json
{"mode": "customer", "target_id": 3}
```

**REQUEST (manual_email 模式)**:
```json
{"mode": "manual_email", "email": "a@163.com,b@qq.com"}
```

**RESPONSE (成功)**:
```json
{
  "code": 0,
  "data": {
    "results": [{"channel": "QQ邮箱 → t2", "success": true, "error": ""}],
    "total": 1,
    "success": 1
  },
  "message": "已推送到 1/1 个目标"
}
```

**RESPONSE (频率超限)**:
```json
{
  "code": 42900,
  "message": "推送频率超限（1分钟内超过5次），功能已禁用10分钟",
  "data": {"retry_after": 600}
}
```

### 频率限制管理

**GET /api/system/rate-limits** — 查看所有封禁中的 key

**POST /api/system/rate-limits/reset** — 重置封禁
```json
// 重置单个
{"key": "user@example.com"}
// 重置全部
{}
```

---

## 10. 定时任务控制

### GET /api/scheduler
```json
{"code": 0, "data": {"next_run": "2026-05-10T13:00:00", "interval": "4h", "jobs": [...]}}
```

### POST /api/scheduler/trigger
手动触发定时采集任务（同 `/api/sources/collect`）。

### PUT /api/scheduler
```json
{"interval": "6h"}
```
