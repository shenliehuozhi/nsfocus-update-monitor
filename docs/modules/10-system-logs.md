# 模块十：系统日志 (System Logs)

## 功能说明

系统日志模块提供应用程序运行日志的实时查看能力，包括日志级别控制和日志文件列表。

## 日志文件

| 文件 | 说明 |
|------|------|
| `logs/app.log` | 主应用日志（INFO/WARNING/ERROR/DEBUG） |
| `logs/access.log` | Web 访问日志 |
| `logs/audit.log` | 操作审计日志（用户操作记录） |
| `logs/discover.log` | 产品发现日志 |
| `logs/heartbeat.log` | Session 心跳探测日志 |

## 日志格式

app.log 示例：
```
2026-06-12 10:00:00 [INFO] [scheduler] 采集任务开始
2026-06-12 10:00:05 [WARNING] [collector] 连接超时: update.nsfocus.com
2026-06-12 10:00:10 [ERROR] [collector] HTTPSConnectionPool: connection timeout
```

audit.log 示例：
```
2026-06-12T10:00:00 - [settings_update] user_id=1 ip=127.0.0.1 details={'keys': ['collect_interval']}
2026-06-12T10:05:00 - [subscription_create] user_id=1 ip=127.0.0.1 details={'id': 5, 'name': '客户A-WAF'}
```

## API 设计

### GET /api/system/log-files

列出日志文件（含大小、修改时间）。

**响应**：
```json
{
  "code": 0,
  "data": {
    "files": [
      {
        "name": "app.log",
        "size": 1048576,
        "size_human": "1.0MB",
        "modified": "2026-06-12T14:00:00"
      },
      {
        "name": "app.log.1",
        "size": 5242880,
        "size_human": "5.0MB",
        "modified": "2026-06-11T00:00:00"
      }
    ],
    "log_dir": "/root/nsfocus-monitor/logs"
  }
}
```

### GET /api/system/logs

查看日志内容（倒序分页）。

**查询参数**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| file | "app.log" | 日志文件名 |
| lines | 200 | 行数（最大1000） |
| level | - | 过滤级别：DEBUG/INFO/WARNING/ERROR |

**响应**：
```json
{
  "code": 0,
  "data": {
    "file": "app.log",
    "lines": [
      "2026-06-12 14:00:00 [ERROR] [collector] ConnectTimeoutError: ...",
      "2026-06-12 13:55:00 [INFO] [scheduler] 采集完成，发现 3 个新包"
    ],
    "total": 200,
    "requested": 200,
    "level": "ERROR",
    "current_log_level": "INFO"
  }
}
```

## 审计日志（audit.log）

记录用户的关键操作：

| 操作 | 说明 |
|------|------|
| settings_update | 修改系统配置 |
| event_config_update | 修改事件通知配置 |
| subscription_create | 创建订阅规则 |
| subscription_delete | 删除订阅规则 |
| customer_delete | 删除客户（含保护逻辑） |
| history_clear | 清空推送历史 |
| force_stop_settings | 强制停止采集并保存配置 |

格式：`[操作] user_id=N ip=地址 details={详细信息}`

## 日志存储路径

通过环境变量配置：
- `MONITOR_LOG_DIR`：日志根目录（默认 `~/.local/share/nsfocus-monitor-data/logs`）
- Linux 默认：`/root/.local/share/nsfocus-monitor-data/logs/`
- Windows 默认：`%LOCALAPPDATA%\nsfocus-monitor-data\logs\`

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/web/routes/system_routes.py` | 日志 API（`bp` Blueprint） |
| `src/core/logger.py` | 日志模块（`get_log_dir()` / `get_current_level()`） |
| `src/core/scheduler.py` | 调度器日志输出 |
| `src/collectors/nsfocus.py` | 采集器日志输出 |