# 模块一：仪表盘 (Dashboard)

## 功能说明

仪表盘是系统首页，提供绿盟升级监控系统运行状态的全局总览。用户登录后默认进入此页面。

## 显示内容

| 指标 | 说明 |
|------|------|
| Session 状态 | 池中总数 / 活跃数 / 已过期数 / 活跃但实际已过期数 |
| 产品健康 | 各产品的采集状态（正常/异常/未知） |
| 今日推送 | 当天成功/失败/总数 |
| 本周推送 | 最近7天成功/失败/总数 |
| 快照总数 | 当前有效的升级包快照数量 |
| 最近推送 | 最近推送记录（按时间倒序，最新50条） |

## API 设计

### GET /api/dashboard

**认证**：需要登录（`@require_auth`）

**请求参数**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| range | string | "7" | 最近推送的时间范围，支持 1/7/30/365 |

**响应字段**：

```json
{
  "code": 0,
  "data": {
    "session_status": {
      "active": 2,          // 活跃 session 数
      "total": 3,           // 总 session 数
      "active_but_expired": 0 // 状态为 active 但实际已过期（污染/错误）
    },
    "session_detail": [      // 最多3条最近活跃 session
      {
        "id": 1,
        "status": "active",
        "last_heartbeat_at": "2026-06-12T10:30:00Z",
        "heartbeat_status": "正常",
        "heartbeat_count": 42
      }
    ],
    "sources": [             // 产品采集状态
      {
        "name": "WEB应用防护系统(WAF)",
        "health": "ok",      // ok=正常, error=异常, unknown=未知
        "last_collected": "2026-06-12T14:49:20"
      }
    ],
    "push_today": {
      "total": 12,
      "success": 11,
      "failed": 1
    },
    "push_week": {
      "total": 87,
      "success": 85,
      "failed": 2
    },
    "total_snapshots": 254,  // 所有 status='active' 的快照数
    "recent_deliveries": [    // 最近推送明细
      {
        "sent_at": "2026-06-12T14:49:20",
        "channel_name": "企业微信-客户A",
        "channel_type": "wecom",
        "delivery_status": "sent",
        "customer_name": "客户A",
        "product_name": "WEB应用防护系统(WAF)",
        "version_branch": "V6.0.9",
        "package_type": "rule",
        "file_name": "WAF_V6.0.9_规则升级包.tar.gz",
        "package_version": "2026061201",
        "urgency": "high"
      }
    ]
  }
}
```

## 核心 SQL 查询

### 今日推送统计
```sql
SELECT COUNT(*) as total,
       SUM(CASE WHEN delivery_status='sent' THEN 1 ELSE 0 END) as success,
       SUM(CASE WHEN delivery_status='failed' THEN 1 ELSE 0 END) as failed
FROM delivery_log
WHERE date(sent_at) = date('now')
```

### 本周推送统计
```sql
SELECT COUNT(*) as total,
       SUM(CASE WHEN delivery_status='sent' THEN 1 ELSE 0 END) as success,
       SUM(CASE WHEN delivery_status='failed' THEN 1 ELSE 0 END) as failed
FROM delivery_log
WHERE sent_at >= date('now', '-7 days')
```

### 最近推送（带关联）
```sql
SELECT dl.sent_at, dl.channel_name, dl.channel_type, dl.delivery_status,
       c.name as customer_name, s.product_name, s.version_branch,
       s.package_type, s.file_name, s.package_version, s.urgency
FROM delivery_log dl
JOIN snapshots s ON dl.snapshot_id = s.id
LEFT JOIN customers c ON dl.customer_id = c.id
WHERE dl.sent_at >= date('now', '-7 days')
ORDER BY dl.sent_at DESC
LIMIT 50
```

## 前端实现

**文件**：`src/web/routes/dashboard.py`

**轮询机制**：前端每 5 秒轮询一次 `/api/dashboard`，更新页面数据。

**数据缓存**：session_detail 缓存到 `window._lastSessionDetail`，减少重复渲染。

## 状态说明

### Session 状态（status 字段）
| 值 | 说明 |
|----|------|
| active | 活跃，cookie 验证通过 |
| expired | 已过期（302 跳转到登录页） |
| unknown | 未知（从未验证或状态不确定） |

### 心跳状态（heartbeat_status 字段）
| 值 | 说明 |
|----|------|
| 正常 | 最近一次心跳成功（200 OK） |
| 过期 | session 已失效（302 重定向到登录页） |
| 污染 | 页面内容异常（返回了登录页 HTML 但状态码是 200） |
| 错误 | 网络错误或绿盟站点异常 |

### 产品健康状态（health_status 字段）
| 值 | 说明 |
|----|------|
| ok | 上次采集成功 |
| error | 上次采集失败 |
| unknown | 从未采集过 |

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/web/routes/dashboard.py` | 仪表盘 API 路由 |
| `src/models/snapshot.py` | `list_sources()` 获取产品列表 |
| `src/models/user_session.py` | `count_by_status()` / `get_expired_active_count()` |
| `src/models/subscription.py` | `log_delivery()` 记录推送日志 |
