# 模块二：Session（绿盟登录凭证管理）

## 功能说明

Session 模块用于管理绿盟升级站点（update.nsfocus.com）的登录凭证（PHPSESSID cookie）。由于绿盟升级包的详情页面需要登录后才能访问，系统通过持有一个或多个有效的 PHPSESSID 来完成采集。

## 数据模型

### user_sessions 表

```sql
CREATE TABLE user_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),   -- 所属用户
    cookie_value TEXT NOT NULL,                     -- 加密后的 PHPSESSID
    last_valid TEXT,                               -- 最近一次验证成功的时间
    expires_at TEXT,                               -- 预计过期时间（暂无实现）
    status TEXT DEFAULT 'unknown',                  -- active/expired/unknown
    last_heartbeat_at TEXT,                        -- 最近心跳时间
    heartbeat_status TEXT DEFAULT '',               -- 正常/过期/污染/错误
    heartbeat_count INTEGER DEFAULT 0,              -- 累计心跳次数
    purpose TEXT DEFAULT 'collect',                 -- discover=发现 / collect=采集
    collect_mode TEXT DEFAULT 'standard',           -- standard / vm（虚拟机模式）
    created_at TEXT DEFAULT (datetime('now'))
)
```

### heartbeat_log 表（已废弃，改为文件日志）

原心跳日志写入数据库（`heartbeat_log` 表），现已改为写入文件 `~/.local/share/nsfocus-monitor-data/logs/heartbeat.log`，避免数据库频繁写入。

## Session 状态机

```
unknown ──创建时───────────→ unknown
  │                            │
  │   ┌───验证成功──────────→ active
  │   │                          │
  │   │                     ┌────┴────┐
  │   │                302跳转    网络错误/污染
  │   │               (到登录页)        │
  │   │                     ↓            ↓
  │   └──expired ◀──── expired      error
  │                                         │
  └───────────────────再验证成功──────────┘
                   （如果仍200 OK）
```

## 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| cookie_value | TEXT | 加密存储的 PHPSESSID（Fernet 对称加密） |
| status | TEXT | `active`=有效 / `expired`=已过期 / `unknown`=未知 |
| purpose | TEXT | `discover`=用于产品发现 / `collect`=用于采集 |
| collect_mode | TEXT | `standard`=标准模式 / `vm`=虚拟机模式 |
| heartbeat_status | TEXT | `正常`=探测成功 / `过期`=302跳转 / `污染`=内容异常 / `错误`=网络错误 |
| heartbeat_count | INT | 累计成功心跳次数 |

## API 设计

### GET /api/sessions

获取当前用户的 session 列表及池状态。

**响应**：
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
        "last_valid": "2026-06-12T10:00:00",
        "expires_at": "",
        "created_at": "2026-06-01T08:00:00",
        "last_heartbeat_at": "2026-06-12T14:00:00",
        "heartbeat_status": "正常",
        "heartbeat_count": 42
      }
    ],
    "pool_status": {
      "total": 3,
      "active": 2,
      "expired": 1,
      "active_but_expired": 0
    }
  }
}
```

### POST /api/sessions

创建新 session（创建时立即验证 cookie 有效性）。

**请求体**：
```json
{
  "cookie_value": "abc123...",
  "purpose": "collect",
  "collect_mode": "standard"
}
```

**验证流程**：
1. 用该 cookie 请求 `https://update.nsfocus.com/update/listBvsV6/v/bvssys`
2. **302 跳转** → session 已过期（跳转到 `/portal/index`）
3. **200 OK** → session 有效，记录首次心跳

**响应**：
```json
{
  "code": 0,
  "data": {
    "id": 5,
    "status": "active",
    "purpose": "collect",
    "collect_mode": "standard",
    "latency_ms": 312,
    "message": "Session 验证成功 (312ms)"
  }
}
```

### PATCH /api/sessions/:id

修改 session 的 `purpose` 和/或 `collect_mode`。

**请求体**：
```json
{
  "purpose": "discover",
  "collect_mode": "vm"
}
```

### DELETE /api/sessions/:id

删除 session（同时删除心跳日志）。

### GET /api/sessions/:id/heartbeat

获取指定 session 的心跳历史（从 heartbeat.log 文件读取）。

### POST /api/sessions/:id/validate

重新验证 session 有效性（手动触发心跳）。

- 如果验证通过：恢复为 `active` 状态
- 如果验证失败：更新为 `expired`/`error`，不改变状态

## 采集池机制

系统维护一个 Session 池，供采集和发现共同使用：

### 采集池（collect）

```
get_active_collect_sessions()
  → 返回 { 'standard': session_row, 'vm': session_row }
  → 每个 mode 只返回第一个 cookie（优先用最近验证过的）
```

### 发现池（discover）

```
get_active_sessions_by_purpose('discover')
  → 返回所有 purpose=discover 的 active session
```

## 心跳机制

心跳用于检测 session 是否仍然有效，是采集的前置检查：

1. **预检心跳**（scheduler 触发采集前）：
   - 用 session 访问健康检查 URL
   - 302 → 标记为 expired，不参与本次采集
   - 200 OK → 继续参与采集

2. **主动探测**（POST /sessions/:id/validate）：
   - 用户手动触发，立即验证
   - expired 可在验证通过后恢复为 active

3. **连续过期检测**：
   - `active_but_expired` = status=active 但 heartbeat_status ∈ {过期, 污染, 错误}
   - 说明 session 状态标为 active 但实际已失效（未及时更新）

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/web/routes/session_routes.py` | Session API 路由 |
| `src/models/user_session.py` | Session 数据访问层 |
| `src/core/crypto.py` | Fernet 加密/解密 cookie |

## 加密说明

cookie 值使用 Fernet 对称加密存储在数据库中：
```python
# 加密
encrypted = encrypt(cookie_value)  # 存储到 DB

# 解密
cookie_value = decrypt(row['cookie_value'])  # 使用前解密
```

加密密钥通过环境变量 `ENCRYPTION_KEY` 配置。
