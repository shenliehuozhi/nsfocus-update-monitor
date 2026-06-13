# 模块八：系统配置 (Settings)

## 功能说明

系统配置模块管理调度器参数、事件通知、系统分类等全局设置。

## 数据模型

### system_settings 表

```sql
CREATE TABLE system_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
)
```

### system_event_config 表

```sql
CREATE TABLE system_event_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT UNIQUE NOT NULL,  -- heartbeat_expired/heartbeat_error/collect_done/network_error/log_error/rollback_detected
    enabled INTEGER DEFAULT 1,        -- 1=启用, 0=停用
    channel_id INTEGER REFERENCES channels(id),
    created_at TEXT
)
```

## 配置项（system_settings）

| key | 类型 | 默认值 | 说明 |
|-----|------|--------|------|
| scheduler_enabled | string | "1" | 调度器总开关：1=启用, 0=停用 |
| collect_interval | string | "240" | 采集间隔（分钟） |
| heartbeat_enabled | string | "1" | 心跳检测开关 |
| heartbeat_interval | string | "30" | 心跳检测间隔（分钟） |
| collection_running | string | JSON | 当前采集运行状态 |
| package_classification | string | JSON | 包分类配置 |
| collect_timeout | string | "5" | 采集单次请求超时（秒） |
| notify_rollback_default | string | "1" | 默认通知撤回行为 |

## system_event_config 事件类型

| event_type | 说明 | 触发时机 |
|-----------|------|---------|
| heartbeat_expired | Session 过期 | 预检心跳收到 302 跳转 |
| heartbeat_error | Session 错误 | 预检心跳遇到网络错误 |
| collect_done | 采集完成 | 采集任务完成（无论成功失败） |
| log_error | 日志错误 | scheduler 扫描 app.log 发现网络错误模式 |
| rollback_detected | 撤回检测 | 检测到升级包被撤回 |
| session_poll_abnormal | Session 池异常 | 活跃 session 数为 0 或 active_but_expired > 0 |

## API 设计

### GET /api/settings/scheduler

获取调度器状态。

**响应**：
```json
{
  "code": 0,
  "data": {
    "running": false,
    "last_run": "2026-06-12T10:00:00",
    "next_run": "2026-06-12T14:00:00",
    "collect_interval": 240,
    "heartbeat_interval": 30,
    "scheduler_enabled": true
  }
}
```

### POST /api/settings/scheduler/trigger

手动触发一次采集（后台异步执行）。

**响应**：`{"code": 0, "message": "采集任务已触发"}`

### GET /api/settings/config

获取所有系统配置项。

**响应**：
```json
{
  "code": 0,
  "data": {
    "scheduler_enabled": "1",
    "collect_interval": "240",
    "heartbeat_enabled": "1",
    "heartbeat_interval": "30",
    "collect_timeout": "5"
  }
}
```

### PUT /api/settings/config

更新系统配置（批量）。

### GET /api/settings/classification

获取包分类配置。

### PUT /api/settings/classification

更新包分类配置。

### GET /api/settings/event-config

获取系统事件通知配置。

**响应**：
```json
{
  "code": 0,
  "data": {
    "enabled": true,
    "channel_id": 1,
    "event_types": ["heartbeat_expired", "heartbeat_error", "log_error"]
  }
}
```

### PUT /api/settings/event-config

更新系统事件通知配置。

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/web/routes/api_routes.py` | Settings Blueprint (`bp_settings`) |
| `src/core/scheduler.py` | 调度器核心逻辑 |
| `src/models/event_log.py` | 事件通知配置 |
| `src/models/database.py` | system_settings 读写 |