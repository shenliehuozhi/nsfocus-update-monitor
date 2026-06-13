# 模块八：系统配置 (Settings)

## 功能说明

系统配置管理调度器参数、事件通知配置、包分类规则。

---

## 调度器参数

### 采集模式

#### delta 模式（快速/默认）

定时调度使用此模式。调用 `_collect_quick()`：

```
_content_sources.is_active=1 的产品列表_
        ↓
读取 content_sources.package_type_discovered.paths（已知最终页面 URL）
        ↓
逐个 URL 发起 HTTP 请求（HEAD + GET）
        ↓
计算页面 MD5（page_hash）→ 与 snapshots.page_hash 比对
        ↓
变化 → _extract_table_items() 重新解析
未变 → 跳过（复用已有快照）
        ↓
耗时：约 20 秒（5 产品）
```

#### full 模式（深度/手动）

通过 `POST /api/settings/scheduler/trigger` 或 `mode=full` 触发。调用 `_collect_full()`：

```
_content_sources.is_active=1 的产品列表_
        ↓
对每个产品：
  ├── 入口 URL → 获取 HTML
  ├── 提取顶级链接（排除侧边栏）
  └── 递归抓取（depth ≤ 6）
        ↓
对每个最终页面：
  _extract_table_items() 解析包列表
  ↓
全量写入/更新 snapshots 表
        ↓
耗时：约 15-20 分钟
```

### 调度器工作流程

```
scheduler.run_now(mode)
        ↓
① 预检所有活跃 Session（verify_session）
        ↓
  ├── 302 跳转 /portal/index → expired（事件通知）
  └── 200 OK → active，继续
        ↓
② 获取 is_active=1 的产品列表
        ↓
③ 执行采集（delta=快速，full=深度）
        ↓
④ 对每个产品执行 run_detection
        ↓
  ├── 新包 → 插入 snapshots（触发订阅规则）
  └── 撤回包 → status=rollback（触发撤回通知）
        ↓
⑤ 扫描 app.log 网络错误
        ↓
⑥ 发送采集完成通知（emit_collection_summary）
        ↓
⑦ 处理 delayed_queue（延迟推送）
        ↓
⑧ 处理 digests（摘要推送）
        ↓
⑨ 发送推送汇总通知（emit_push_summary）
```

### 调度配置项

| key | 默认值 | 说明 |
|-----|--------|------|
| `scheduler_enabled` | "1" | 调度器总开关，0=暂停调度 |
| `collect_interval` | "240" | 采集间隔（分钟），默认 4 小时 |
| `heartbeat_enabled` | "1" | 心跳检测开关 |
| `heartbeat_interval` | "30" | 心跳检测间隔（分钟），默认 30 分钟 |
| `collect_timeout` | "5" | 单次 HTTP 请求超时（秒） |
| `skip_page_hash_check` | "0" | 1=跳过 page_hash 比对，强制重新解析所有页面 |
| `full_scan_interval_hours` | "168" | full scan 间隔（小时），默认 7 天 |

---

## Session 池机制

### 用途分类

| purpose | 说明 |
|---------|------|
| `discover` | 用于产品发现（递归抓取） |
| `collect` | 用于日常采集（快速检查） |

### collect_mode

| mode | 说明 |
|------|------|
| `standard` | 标准模式 |
| `vm` | 虚拟机模式（针对 /upLic 重定向的产品） |

### 心跳预检流程

Scheduler 每轮采集开始前，对**所有活跃 Session** 逐个执行预检：

```python
for sess in all_sessions:
    _collector._set_cookie(sess['cookie_value'])
    if _collector.verify_session(HEALTH_URL):
        update_status(sess['id'], 'active')
        update_heartbeat(sess['id'], '正常')
        log_heartbeat(sess['id'], '正常')
        if is_collect and valid_session is None:
            valid_session = sess  # 选用第一个有效的 collect session
    else:
        update_status(sess['id'], 'expired')
        update_heartbeat(sess['id'], '过期')
        emit_session_expired(...)  # 事件通知
```

**关键**：只有 `purpose=collect` 的 Session 才会被用于采集；`purpose=discover` 仅用于自动发现阶段。

### 采集池（collect）

```python
get_active_collect_sessions()
  → 返回 { 'standard': session_row, 'vm': session_row }
  → 每个 mode 只返回第一个（优先最近验证过的）
```

---

## 系统事件通知

### system_event_config 表

```sql
CREATE TABLE system_event_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT UNIQUE NOT NULL,
    enabled INTEGER DEFAULT 1,
    channel_id INTEGER REFERENCES channels(id),
    created_at TEXT
)
```

### 事件类型

| event_type | 说明 | 触发时机 |
|-----------|------|---------|
| `heartbeat_expired` | Session 过期 | 预检时收到 302 跳转到登录页 |
| `heartbeat_error` | Session 错误 | 预检时遇到网络错误 |
| `collect_done` | 采集完成 | 采集任务结束（无论成功失败） |
| `log_error` | 日志错误 | scheduler 扫描 app.log 发现网络错误关键词 |
| `rollback_detected` | 撤回检测 | 确认包被撤回（连续2次消失） |
| `session_poll_abnormal` | Session 池异常 | 活跃 session=0 或 active_but_expired>0 |
| `push_summary` | 推送汇总 | 每轮推送完成后的汇总报告 |

### 事件通知流程

```python
# event_handler.py
emit_session_expired(session_id, purpose, reason, source)
        ↓
is_event_enabled('heartbeat_expired') → False → 不通知
        ↓
channel = get_notify_channel()  # 查找 system_event_config 绑定的渠道
        ↓
format_message(event_type, details)  # 渲染消息格式
        ↓
notifier.send(message, channel_config)
```

### 日志错误扫描（log_error）

scheduler 每轮采集结束后扫描 app.log：

```python
_scan_network_errors_from_log(started_at, finished_at)
        ↓
读取 app.log 在时间窗口内的内容
        ↓
匹配正则模式：
  ConnectTimeoutError / ConnectError / ConnectionError
  NewConnectionError / Max retries exceeded
  Connection to / timed out / DNS / Network is unreachable
        ↓
如果匹配到 → emit_network_error(errors)
        ↓
通过 log_error 事件类型发送一条聚合通知
```

---

## 包分类配置

`package_classification` 系统配置项用于**按产品+类型分组显示**，不影响采集逻辑：

```json
{
  "WEB应用防护系统(WAF)": {
    "rule": { "label": "规则升级包", "icon": "🛡️", "color": "#1890ff" },
    "sys": { "label": "系统升级包", "icon": "⚙️", "color": "#52c41a" }
  }
}
```

前端据此渲染采集数据的产品树状结构。

---

## API 设计

### GET /api/settings/scheduler

获取调度器状态：

```json
{
  "code": 0,
  "data": {
    "running": false,
    "mode": "delta",
    "last_run": "2026-06-12T10:00:00",
    "next_run": "2026-06-12T14:00:00",
    "collect_interval": 240,
    "heartbeat_interval": 30,
    "scheduler_enabled": true,
    "last_full_run": "2026-06-05T10:00:00",
    "next_full_run": "2026-06-12T10:00:00"
  }
}
```

### POST /api/settings/scheduler/trigger

手动触发采集（后台异步）：

```json
{"mode": "delta"}   // 默认 delta
{"mode": "full"}   // 深度采集
```

### PUT /api/settings/config

批量更新配置：

```json
{
  "scheduler_enabled": "1",
  "collect_interval": "120",
  "heartbeat_enabled": "1",
  "heartbeat_interval": "30",
  "collect_timeout": "5"
}
```

配置更新后自动重调度：

- `collect_interval` → `reschedule_collect()`
- `heartbeat_enabled=1` + `heartbeat_interval` → `reschedule_heartbeat()`
- `scheduler_enabled` → `refresh_scheduler_jobs()`

### PUT /api/settings/event-config

```json
{
  "enabled": true,
  "channel_id": 1,
  "event_types": ["heartbeat_expired", "heartbeat_error", "log_error"]
}
```

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/core/scheduler.py` | 调度器核心（delta/full 模式实现） |
| `src/core/event_handler.py` | 事件通知（emit_* 系列函数） |
| `src/detector/change.py` | 变更检测 |
| `src/models/event_log.py` | 事件配置读写 |
| `src/models/subscription.py` | delayed_queue / digest_queue |
| `src/notifiers/router.py` | 推送路由 |
