# API 设计

## 约定

- 基础路径: `http://<HOST>:9999/api`
- 认证: 浏览器使用 HttpOnly Cookie（登录后由服务端下发 `nsm_session`）；脚本/API 客户端可继续使用 `Authorization: Bearer <JWT>`。Cookie 鉴权的 `POST/PUT/PATCH/DELETE` 必须回传 `X-CSRF-Token`（值取自可读 Cookie `nsm_csrf`）
- Content-Type: `application/json`
- 响应格式: `{"code": 0, "data": {...}}` 或 `{"code": 40001, "message": "..."}`

## 错误码

| code | 说明 |
|------|------|
| 0 | 成功 |
| 40001 | 参数错误 |
| 40100 | 未登录 |
| 40101 | Token 过期 |
| 40300 | 无权限 / CSRF 校验失败 |
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
// RESPONSE (Set-Cookie: nsm_session=<HttpOnly JWT>; nsm_csrf=<可读>; SameSite=Lax)
{"code": 0, "data": {"user": {"id": 1, "username": "admin", "is_admin": true}}}
```
凭证只通过 Cookie 下发，响应体不含 token。脚本客户端可用 `curl -c cookies.txt -b cookies.txt` 维持会话；
`Authorization: Bearer <JWT>` 仍然接受（旧会话/外部集成），且不需要 CSRF 头。

### POST /api/auth/logout
```json
// RESPONSE
{"code": 0, "message": "已退出登录"}
```
清除 `nsm_session` / `nsm_csrf`；重复调用无副作用。

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
    {"id": 1, "name": "客户A", "company": "xx公司", "owned_products": [
      {"product": "WEB应用防护系统(WAF)", "version": "V6.0.9"},
      {"product": "网络入侵防护系统(IPS)", "version": "5.6.11"}
    ]}
  ]
}
```
> 旧格式 `["WAF:V6.0.9"]` / `["WAF"]` 仍可写入，读取时会规整为 `{"product","version"}` 对象。

### GET /api/customers/:id/service-report
客户月度服务报告：当月推送明细。`month=YYYY-MM` 可选，默认当前月；月份边界按 UTC+8 计算（库里时间是 UTC）。返回 `summary`（推送总数 / 成功 / 失败 / 成功率）与 `deliveries`（最多 100 条，`truncated` 标记是否截断）。`month` 格式非法返回 `400`，客户不存在返回 `404`。

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

### POST /api/history/queue/:queue_id/retry
把终止的延迟队列项重新入队（事件中心 `delivery_error` 事件下的「重新入队」按钮调用）。

- 只接受 `cancelled_reason` 为 `retry_exhausted*` / `no_active_channel` 的行；
- 重置 `attempts=0`、`next_attempt_at=''`、`push_after=当前本地时间`，由 5 分钟队列任务重新投递；
- 已送达渠道仍由 `delivery_log` 去重，不会重复发送。

```json
// 200
{"code": 0, "message": "已重新入队，稍后自动重试", "data": {"id": 123}}
// 404（不存在 / 已成功 / 业务性取消）
{"code": 40400, "message": "队列项不存在或不可重试"}
```

---

## 8. 仪表盘（已下线）

原仪表盘页面与 `GET /api/dashboard` 已移除，页面内容并入「仪表盘」页（`data-page="observability"`）；对应数据改由 `GET /api/metrics` 的 `today` / `customers` / `sessions` / `process` 字段提供，见 [13. 运行指标](#13-运行指标)。

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

可用事件类型：`push_summary`、`digest_summary`、`collection_summary`、`cleanup_summary`、`log_error`、`session_error`、`session_expired`、`channel_error`、`delivery_error`、`login_bruteforce`。

### GET /api/system/events
查询最近事件记录。可选参数：`severity`（严重度）、`event_type`（事件类型）、`since_hours`（时间窗，默认 24，`0` = 全部时间）、`unacked=1`（只看未读且未解决）、`state`（`open` / `acked` / `resolved`）、`limit`（默认 50，上限 500）、`offset`（分页）。返回项额外包含 `acked_at`、`resolved_at`、`resolved_by` 与派生状态 `state`。

### POST /api/system/events/&lt;id&gt;/ack
标记单个事件已读（`acked_at`），不改变解决状态。

### POST /api/system/events/&lt;id&gt;/resolve
标记单个事件已解决（`resolved_at` + `resolved_by`），同时补写 `acked_at`；已解决事件不重复处理。

### POST /api/system/events/ack-all
批量标记已读。Body：`{"severity": "ERROR"|null, "since_hours": 24}`；只更新当前时间窗与严重度范围内 `acked_at IS NULL AND resolved_at IS NULL` 的行。

### POST /api/system/events/resolve-all
批量标记已解决。Body：`{"severity": "ERROR"|null, "event_type": "site_unavailable"|null, "since_hours": 24}`；只更新范围内 `resolved_at IS NULL` 的行，返回实际解决条数。

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

---

## 13. 运行指标

### GET /api/metrics

机器可读的运行指标，用于排障、看板或 Prometheus 抓取。

| 参数 | 说明 |
|------|------|
| `hours` | 统计窗口小时数，默认 24，范围 1–720；非法值回退 24；投递、产品更新与客户推送聚合同样使用该窗口 |
| `format` | `prometheus` 时返回 `text/plain` 指标，否则返回 JSON |

鉴权：`Authorization: Bearer <JWT>`；配置 `MONITOR_METRICS_TOKEN` 后也可用 `X-Metrics-Token: <token>` 或 `Authorization: Bearer <token>`（Prometheus 的 `authorization.credentials_file` 原生发送后者；JWT 24h 过期不适合长期抓取）。

```json
{
  "code": 0,
  "data": {
    "generated_at": "2026-11-11T01:17:25+08:00",
    "window_hours": 24,
    "delivery": {
      "total": 575, "sent": 575, "failed": 0, "pending": 0,
      "success_rate": 1.0, "undated_failed": 0,
      "by_channel": [
        {"channel_type": "wecom", "total": 570, "sent": 570, "failed": 0,
         "pending": 0, "success_rate": 1.0}
      ]
    },
    "today": {
      "date": "2026-11-11",
      "delivery": {"total": 12, "sent": 11, "failed": 1, "success_rate": 0.9167},
      "products": [{"name": "WAF", "count": 2, "last_published_at": "2026-11-10 16:30:00"}],
      "product_count": 1,
      "package_count": 2
    },
    "updates": {
      "window_hours": 24,
      "total": 2,
      "items": [
        {"name": "WAF", "count": 2, "last_published_at": "2026-11-11T03:30:00"}
      ]
    },
    "customers": {
      "window_hours": 24,
      "items": [
        {"id": 1, "name": "客户A", "total": 10, "success": 9, "failed": 1,
         "last_sent_at": "2026-11-11 09:31:00", "last_product": "WAF"}
      ]
    },
    "sessions": {
      "total": 5, "active": 3, "expired": 1, "unknown": 1,
      "active_but_expired": 0, "collect_total": 4, "collect_active": 2,
      "collect_polluted": 0, "discover_active": 1
    },
    "process": {"started_at": "2026-11-10T23:00:00+00:00", "uptime_seconds": 90000},
    "queues": {
      "delayed": {"pending": 0, "overdue": 0, "retrying": 0, "oldest_age_minutes": null},
      "digest": {"pending": 0, "retrying": 0, "by_rule": []}
    },
    "collection": {
      "enabled": true, "is_running": false, "last_run": null,
      "last_full_run": null, "last_quick_run": null, "next_run": null,
      "interval_hours": 4, "current": null,
      "last_duration_s": 1012.0, "avg_duration_s": 565.4,
      "sources_total": 80, "sources_stale": 0, "stale_after_hours": 24
    },
    "rules": {"total": 8, "enabled": 5, "digest_enabled": 0},
    "events": {
      "window_total": 4,
      "by_severity": {"INFO": 3, "WARNING": 1},
      "unacked": {"WARNING": 11}
    },
    "storage": {
      "db_size_bytes": 22532096, "snapshots_total": 3571,
      "snapshots_active": 725, "delivery_log_rows": 7596,
      "digest_queue_rows": 0, "system_event_log_rows": 412
    },
    "database": {
      "operations": 1284, "errors": 0, "timeouts": 0,
      "python_lock_timeouts": 0, "sqlite_busy_errors": 0,
      "sample_count": 1024, "sample_limit": 1024,
      "wait_budget_seconds": 10.0, "timeout_rate": 0.0,
      "lock_wait_ms": {"p50": 0.1, "p95": 12.4, "p99": 30.2},
      "sqlite_ms": {"p50": 3.8, "p95": 6.1, "p99": 9.4},
      "total_ms": {"p50": 4.0, "p95": 18.3, "p99": 39.7}
    },
    "maintenance": {"enabled": false},
    "site": {
      "url": "https://update.nsfocus.com/", "status": "up",
      "http_status": 200, "latency_ms": 276,
      "checked_at": "2026-11-11T09:40:22+00:00",
      "error": "", "consecutive_failures": 0,
      "last_ok_at": "2026-11-11T09:40:22+00:00", "last_fail_at": null
    },
    "scheduler_jobs": [
      {"id": "nsfocus_collect", "next_run": "2026-11-11T05:00:00Z",
       "seconds_until": 3600, "status": "running"},
      {"id": "nsfocus_site_check", "next_run": "2026-11-11T05:05:00Z",
       "seconds_until": 3900, "status": "running"},
      {"id": "nsfocus_channel_health", "next_run": null,
       "seconds_until": null, "status": "disabled"}
    ]
  }
}
```

口径说明：

- `delivery` 按 `COALESCE(created_at, sent_at)` 落在窗口内统计；`undated_failed` 是 `created_at` 上线前写入、无法定位时间的失败行，仅做总量参考。
- `success_rate = sent / (sent + failed)`，窗口内没有已完成交付时为 `null`。
- `queues.delayed.overdue` 用本地时间比较 `push_after`（`compute_push_time` 存的本地时间），不要与 UTC 的 `datetime('now')` 混比。
- `queues.*.retrying` 统计 `attempts > 0` 的 pending 行（发送失败、等待退避重试）；重试上限由 `MONITOR_DELIVERY_MAX_ATTEMPTS` / `MONITOR_DIGEST_MAX_ATTEMPTS` 控制（默认 6），耗尽后行状态转为 `cancelled`，原因见 `cancelled_reason`。
- `collection.last_duration_s` / `avg_duration_s` 取最近 20 条 `collection_summary` 系统事件，不受 `hours` 限制。
- `collection.sources_stale` 统计 24 小时内未成功采集的启用源。
- `today` 固定统计 CST 当天 00:00 至当前时刻，不随 `hours` 变化，保留给 Prometheus 今日指标使用，网页不再展示独立「今日概览」。`date` 为 CST 日期，`delivery` 包含推送/成功/失败/成功率，按 `COALESCE(NULLIF(created_at, ''), sent_at)` 归属日期；`products` 按 `published_at` 统计当天发布的产品大类与包数，`product_count` / `package_count` 分别为产品数和包数，不是当天入库量。未来时间、空时间与不可解析时间不计入。
- `updates` 按 `snapshots.product_name`（产品大类）聚合窗口内更新包数，跟随 `hours`；`published_at` 入库时已转为 UTC ISO，窗口按 UTC 比较，页面展示时再转 CST。
- `customers` 与 `delivery` 共用 `hours` 窗口聚合 `delivery_log`，`total` / `success` / `failed` 为窗口内计数，`last_product` 取最近一次推送的产品名；窗口内无推送的客户仍返回且计数为 0。
- `sessions.total` 包含污染等所有状态；`collect_active` / `discover_active` 按 `purpose` + `status='active'` 统计，与池选择规则一致，不保证远端即时有效。`collect_polluted` 包含状态或心跳标记污染的采集 Session；`active_but_expired` 表示心跳为过期/污染/错误但仍标记 active，页面显示为「心跳异常但仍标记活跃」，不代表按 `expires_at` 判定。
- `process.uptime_seconds` 由进程启动时间派生，无法解析时为 `null`（Prometheus 输出为 `NaN`）。
- `database` 是进程内 SQLite 写路径统计，不随 `hours` 变化：`operations` / `errors` / `timeouts` 自本次进程启动累计，重启清零；`timeouts` = `python_lock_timeouts`（进程内写队列等待超预算）+ `sqlite_busy_errors`（SQLite 报 locked/busy），`timeout_rate` = `timeouts / operations`，无写操作时为 `0.0`。`lock_wait_ms` / `sqlite_ms` / `total_ms` 分别是排队等待、SQL 执行、两者合计的毫秒分位数（最近 `sample_limit`=1024 次写的最近秩 p50/p95/p99），无样本时为 `null`（Prometheus 输出 `NaN`）。写队列与 SQLite `busy_timeout` 共用 `wait_budget_seconds`（默认 10 秒）预算，超时即失败返回，不再无限重试。Prometheus 对应 `nsfocus_db_write_operations`、`nsfocus_db_write_errors`、`nsfocus_db_write_timeouts{reason="python_lock"|"sqlite_busy"}`、`nsfocus_db_write_timeout_rate`、`nsfocus_db_write_wait_budget_seconds`、`nsfocus_db_write_samples{scope="count"|"limit"}`、`nsfocus_db_write_{lock_wait,sqlite,total}_ms{quantile="p50"|"p95"|"p99"}`。
- `maintenance.enabled` 读取 `system_settings.maintenance_mode`；开启后所有订阅推送被抑制，采集照常。
- `site` 为 `nsfocus_site_check` 每 5 分钟对 `https://update.nsfocus.com/` 的探测结果：HTTP `< 500` 视为 `up`，5xx/超时/连接错误为 `down`，从未探测为 `unknown`；`consecutive_failures` 连续失败次数，状态切换时写 `site_unavailable` / `site_recovered` 系统事件。`site.history` 为时间升序采样点 `{t, ms, s}`，`site.history_summary` 为 `{samples, failures, avg_ms, max_ms}`，`site.history_hours` 为窗口小时数；超过 180 个采样按时间桶平均，失败桶保留 `s=down` / `ms=null`。`format=prometheus` 时不返回历史，避免抓取响应体膨胀。
- `scheduler_jobs` 始终返回已知任务（采集、心跳、汇总、投递队列、清理、渠道体检、站点可用性），`status` 为 `running` / `stopped` / `disabled`；任务未注册时 `next_run` / `seconds_until` 为 `null`。站点探测独立于采集调度器，采集关闭时仍可能为 `running`。
- 网页端「仪表盘」页面消费同一接口，提供窗口切换、30 秒自动刷新和 Prometheus 抓取地址；页面不读取或展示 `MONITOR_METRICS_TOKEN`。
- 「重启服务」调用 `POST /api/system/restart`（登录鉴权，Cookie 请求需 CSRF）。固定检查 `nsfocus-monitor.service` 的 MainPID 是否等于当前进程，非受管实例返回 HTTP 409 / code 409；systemd 查询或线程安排失败返回 HTTP 503 / code 503；成功返回 code 0，仅表示已安排延迟重启请求。使用 systemd restart 而非 SIGTERM，后者在 `Restart=on-failure` 下不会自动重启。
