# API 设计

## 约定

- 基础路径: `http://<HOST>:9999/api`
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

Session 按 `purpose`（用途）和 `collect_mode`（采集模式）分类：

| purpose | collect_mode | 说明 |
|---------|-------------|------|
| discover | (空) | 自动发现，被污染无所谓 |
| collect | standard | 标品采集（`/update/downloads/id/N`） |
| collect | vm | 虚拟化采集（`/update/downloadsVm/id/N`） |

### GET /api/sessions
返回当前用户的所有 Session 及全局 Session 池状态。

```json
{
  "code": 0,
  "data": {
    "my_sessions": [
      {
        "id": 1,
        "status": "active",
        "purpose": "collect",
        "collect_mode": "standard",
        "last_valid": "2026-05-10T10:00:00",
        "expires_at": "2026-05-11T10:00:00",
        "last_heartbeat": {"status": "ok", "latency_ms": 312, "checked_at": "2026-05-10T09:58:00"}
      },
      {
        "id": 2,
        "status": "active",
        "purpose": "discover",
        "collect_mode": null,
        "last_valid": "2026-05-10T09:00:00",
        "expires_at": "2026-05-11T09:00:00",
        "last_heartbeat": {"status": "ok", "latency_ms": 287, "checked_at": "2026-05-10T09:55:00"}
      }
    ],
    "pool_status": {
      "total": 2,
      "active": 2,
      "expired": 0,
      "collect_standard": 1,
      "collect_vm": 0,
      "discover": 1
    }
  }
}
```

### POST /api/sessions
```json
// REQUEST（采集 session，默认）
{"cookie_value": "jvrprvielrfbmgrla348mim23b"}

// REQUEST（指定用途）
{"cookie_value": "jvrprvielrfbmgrla348mim23b", "purpose": "discover", "collect_mode": "standard"}

// RESPONSE
{"code": 0, "data": {"id": 1, "status": "validating", "message": "正在验证..."}}
```

### PATCH /api/sessions/:id
更新 Session 的 purpose / collect_mode：

```json
// REQUEST
{"purpose": "collect", "collect_mode": "vm"}

// RESPONSE
{"code": 0, "data": {"id": 1, "purpose": "collect", "collect_mode": "vm"}}
```

### DELETE /api/sessions/:id
删除指定 Session：

```
DELETE /api/sessions/:id

响应: {"code": 0, "message": "已删除"}
```

### POST /api/sessions/:id/validate
手动触发 Session 验证。

### GET /api/sessions/:id/heartbeats
查询心跳历史（最近 30 条）：

```json
{
  "code": 0,
  "data": {
    "items": [
      {"status": "ok", "latency_ms": 312, "checked_at": "2026-05-10T09:58:00"},
      {"status": "ok", "latency_ms": 298, "checked_at": "2026-05-10T09:28:00"}
    ]
  }
}
```

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

---

## 3.5 产品管理

### GET /api/system/products
返回所有产品（含内置产品和手动注册产品）：

```json
{
  "code": 0,
  "data": {
    "products": [
      {
        "id": 1,
        "name": "WAF",
        "source_type": "nsfocus",
        "entry_url": "/update/wafIndex",
        "strategy": "standard",
        "is_active": true,
        "health_status": "ok",
        "last_collected_at": "2026-05-10T09:00:00",
        "package_type": "[{\"chain\":[\"更新\"],\"types\":[\"sys\",\"rule\"]}]",
        "created_at": "2026-05-01T00:00:00"
      }
    ],
    "total": 1,
    "builtin_products": ["WAF"]
  }
}
```

> 注意：`package_type` 字段是 JSON 字符串（数组），存储完整导航链和类型信息，由发现流程写入。

### POST /api/system/products
手动注册产品：

```
POST /api/system/products
Body: {"name": "WAF", "entry_url": "/update/wafIndex", "strategy": "standard"}

响应: {"code": 0, "message": "产品「WAF」已添加", "data": {"id": 7}}
```

### PATCH /api/system/products/:id
更新产品（开关、策略、名称）：

```
PATCH /api/system/products/:id
Body: {"is_active": true, "strategy": "recursive"}

响应: {"code": 0, "data": {"id": 1, "is_active": true, "strategy": "recursive"}}
```

### DELETE /api/system/products/:id
删除产品（仅当无快照时允许）：

```
成功: {"code": 0, "data": {"deleted": 1}}
失败: {"code": 409, "message": "该产品尚有 45 条快照记录，请先清理后再删除"}
```

### POST /api/system/products/discover
触发自动发现（后台执行，POST 立即返回，GET status 轮询进度）：

```
POST /api/system/products/discover
Authorization: Bearer <token>

响应: {"code": 0, "message": "自动发现已启动，请轮询状态"}
```

### GET /api/system/products/discover/status
轮询发现进度：

```
GET /api/system/products/discover/status
Authorization: Bearer <token>

响应:
{
  "code": 0,
  "data": {
    "active": true,
    "phase": "discovering_pkg_types",
    "progress": 5,
    "total": 78,
    "current": "网络入侵防护系统(IPS)",
    "log_lines": ["开始扫描产品列表...", "产品扫描完成: 新增=3 移除=0 未变=75"]
  }
}

# 发现完成时 phase=done，result 在 data.result 中:
{
  "code": 0,
  "data": {
    "active": false,
    "phase": "done",
    "result": {
      "products": {
        "added": [...],
        "removed": [...],
        "unchanged": [...]
      },
      "pkg_changes": [...]
    }
  }
}
```

### POST /api/system/products/discover/confirm
确认并应用发现结果（新增产品入库、包类型变更检测）：

```
POST /api/system/products/discover/confirm
Authorization: Bearer <token>
Content-Type: application/json

# body 可选，省略则使用发现时返回的完整结果
{
  "products": {"added": [...], "removed": [...], "unchanged": [...]},
  "pkg_changes": [...]
}

响应: {"code": 0, "message": "确认已启动"}
```

### GET /api/system/products/discover/confirm/status
SSE 流式返回确认应用进度：

```
GET /api/system/products/discover/confirm/status
Authorization: Bearer <token>

响应 (text/event-stream):
data: {"active": true, "phase": "applying", "log_lines": []}

data: {"active": true, "phase": "updating_pkg (1/3)", "log_lines": ["[confirm] 新增产品: WAF (id=12)"]}

data: {"active": true, "phase": "updating_pkg (2/3)", "log_lines": ["[confirm] [1/3] 更新包类型: IPS"]}

data: {"active": false, "phase": "done", "result": {"saved": [...], "deleted": [...], "pkg_updated": [...]}}

event: close
data: {}
```

### POST /api/system/products/discover-pkg-types
批量探测产品包类型（后台执行）：

```json
// REQUEST
{"source_ids": [1, 2, 3]}

// RESPONSE（text/event-stream）
event: progress
data: {"source_id": 1, "name": "WAF", "phase": "fetching_ver (1/3)", "current": 1, "total": 3}

event: result
data: {"source_id": 1, "name": "WAF", "package_types": ["sys", "rule", "nti"], "paths": [...]}

event: done
data: {"processed": 3, "errors": 0}
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
// REQUEST（v2.0 — 即时模式）
{
  "name": "WAF关键规则通知",
  "enabled": true,
  "filter_conditions": {
    "chains": [
      { "chain": ["WAF", "标准正式版", "V6.0.8", "规则"], "match": "leaf" },
      { "chain": ["IPS", "V6.0.9"], "match": "subtree" }
    ],
    "keywords": ["漏洞"],
    "urgency": ["high", "critical"]
  },
  "delay_days": 3,
  "push_mode": "instant",
  "quiet_start": "22:00",
  "quiet_end": "08:00",
  "valid_until": "2026-12-31T00:00:00",
  "channels": [1, 2],
  "customers": [1]
}

// REQUEST（v2.0 — 汇总模式）
{
  "name": "WAF周汇总",
  "enabled": true,
  "filter_conditions": {
    "chains": [
      { "chain": ["WAF", "V6.0.9"], "match": "subtree" }
    ],
    "keywords": [],
    "urgency": []
  },
  "push_mode": "digest",
  "window_config": {
    "days": [1, 2, 3, 4, 5],
    "start": "09:00",
    "end": "18:00"
  },
  "quiet_start": "22:00",
  "quiet_end": "08:00",
  "channels": [1],
  "customers": [1, 2]
}
```

> 注意：v2.0 已移除 `delay_strategy`（reset/append/window）、`min_interval_hours`，改用 `push_mode`（instant/digest）和 `window_config`。
> `delay_days` 仅在即时模式生效；汇总模式忽略 delay 字段。
> `filter_conditions` 为空 `{}` 表示匹配全部（向后兼容旧数据）。

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

**参数**：`range` = `7` | `30` | `90`（默认30）

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
    ],
    "product_stats": [
      {"name": "WAF", "count": 12},
      {"name": "IPS", "count": 8}
    ],
    "timeline_stats": [
      {"date": "2026-05-01", "counts": {"WAF": 2, "IPS": 1}},
      {"date": "2026-05-02", "counts": {"WAF": 1, "IPS": 0}}
    ]
  }
}
```

**字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_status.pool_active` | int | 当前活跃的 Cookie Session 数量 |
| `session_status.pool_total` | int | Session 池总容量 |
| `last_collection.time` | ISO8601 | 上次采集完成时间 |
| `last_collection.duration_ms` | int | 上次采集耗时（毫秒） |
| `last_collection.products_ok` | int | 上次采集成功的产品数 |
| `last_collection.products_fail` | int | 上次采集失败的产品数 |
| `stats_today` | object | 今日统计 |
| `stats_this_week` | object | 本周统计 |
| `products_summary` | array | 各产品包统计概览 |
| `product_stats` | array | 近N天（range参数）各产品发布包数量分布，按数量降序 |
| `timeline_stats` | array | 近N天每日各产品发布包数量趋势，按日期升序 |

> **时区说明**：所有时间字段均为 UTC，前端通过 `fmtTZ()` 转换显示为本地时间（CST）。

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

### GET /api/settings/scheduler
```json
{"code": 0, "data": {"next_run": "2026-05-10T13:00:00", "interval": "4h", "jobs": [...]}}
```

### POST /api/settings/scheduler/trigger
手动触发一次采集任务（Quick 扫描）。返回 409 表示采集进行中。

### PUT /api/settings/scheduler
```json
{"interval": "6h", "enabled": "1"}
```

---

## 11. 系统事件通知

### GET /api/system/events/config
获取系统事件通知配置：

```json
{
  "code": 0,
  "data": {
    "enabled": true,
    "channel_id": 4,
    "event_types": []       // 空=全部启用
  }
}
```

### PUT /api/system/events/config
更新配置：

```json
// 开启全部事件，推送到 channel_id=4
{"enabled": true, "channel_id": 4, "event_types": []}

// 仅开启采集汇总和 Session 异常
{"enabled": true, "channel_id": 4, "event_types": ["collection_summary", "session_error"]}
```

### GET /api/system/events
查询最近事件记录：

```json
{
  "code": 0,
  "data": [
    {
      "id": 1,
      "event_type": "collection_summary",
      "severity": "INFO",
      "product_name": "WAF",
      "message": {"status": "success", "new_items": 1, "duration_ms": 8200},
      "created_at": "2026-05-25T09:00:00"
    },
    {
      "id": 2,
      "event_type": "session_error",
      "severity": "CRITICAL",
      "product_name": "调度器健康检查",
      "message": {"username": "scheduler", "reason": "心跳任务已有 45 分钟未执行"},
      "created_at": "2026-05-25T09:44:34"
    }
  ]
}
```

**事件类型**：

| event_type | 说明 | 严重级别 |
|-----------|------|---------|
| `collection_summary` | 采集完成汇总 | INFO/WARNING |
| `session_error` | Session 异常或健康检查失败 | CRITICAL/WARNING |
| `log_error` | 日志扫描异常 | CRITICAL |

**通知内容示例**：

```
【Session 异常】                       ← source='session'
用户名：admin
异常原因：Session 污染（上下文被 upLic/Vm 格式污染）
检测时间：2026-05-25 09:44:34
建议：请更新该用户的 Session
```

```
【系统健康检查告警】                   ← source='health_check'
用户名：scheduler
异常原因：心跳任务已有 45 分钟未执行（配置间隔 15 分钟）；心跳健康检查已有 2.3 小时未成功
检测时间：2026-05-25 09:44:34
建议：请检查调度器是否正常运行
```

---

## 12. 调度器状态

### GET /api/settings/scheduler
获取调度器运行状态：

```json
{
  "code": 0,
  "data": {
    "enabled": true,
    "is_running": false,
    "last_run": "2026-05-25T01:00:00",
    "next_run": "2026-05-25T07:00:00",
    "interval_h": 6
  }
}
```

### POST /api/settings/scheduler/enable
开启调度器（scheduler_enabled=1）。

### POST /api/settings/scheduler/disable
关闭调度器（scheduler_enabled=0），当前采集任务继续完成但不排下次。
