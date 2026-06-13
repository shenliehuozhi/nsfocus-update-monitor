# 绿盟升级监控系统 — 模块文档索引

本文档描述系统各功能模块的设计细节、API 规范和数据模型。

---

## 模块列表

| # | 模块 | 文件 | 说明 |
|---|------|------|------|
| 01 | 仪表盘 | [01-仪表盘.md](./01-仪表盘.md) | 系统运行状态总览 |
| 02 | Session | [02-Session管理.md](./02-Session管理.md) | 绿盟登录凭证（PHPSESSID）管理 |
| 03 | 通知渠道 | [03-通知渠道.md](./03-通知渠道.md) | 推送通道配置（企业微信/钉钉/飞书/邮件） |
| 04 | 订阅规则 | [04-订阅规则.md](./04-订阅规则.md) | 升级通知推送规则定义 |
| 05 | 客户管理 | [05-客户管理.md](./05-客户管理.md) | 客户信息管理 |
| 06 | 采集数据 | [06-采集数据.md](./06-采集数据.md) | 升级包快照存储与展示 |
| 07 | 推送历史 | [07-推送历史.md](./07-推送历史.md) | 推送记录查询/重发/清空 |
| 08 | 系统配置 | [08-系统配置.md](./08-系统配置.md) | 调度器参数/事件通知配置 |
| 09 | 产品管理 | [09-产品管理.md](./09-产品管理.md) | 产品启用/禁用（默认启用5个） |
| 10 | 系统日志 | [10-系统日志.md](./10-系统日志.md) | app.log 实时查看与审计日志 |

---

## 数据模型关系图

```
users
  ├── user_sessions (PHPSESSID + heartbeat)
  ├── channels (通知渠道)
  │     └── rule_channels (规则-渠道绑定)
  └── subscription_rules (订阅规则)
        ├── rule_channels (规则-渠道/客户绑定)
        ├── snapshots ←── content_sources (采集源/产品)
        └── delivery_log (推送记录)
              └── customers (客户)

content_sources ──is_active──► 调度器采集控制
system_settings ────────────► 调度器参数/事件配置
system_event_config ─────────► 系统事件通知
```

---

## 核心数据表

| 表名 | 模块 | 说明 |
|------|------|------|
| `users` | 认证 | 用户账户 |
| `user_sessions` | Session | 加密的 PHPSESSID + 心跳状态 |
| `heartbeat_log` | Session | 心跳历史（已废弃，改用文件） |
| `channels` | 渠道 | 通知渠道配置（加密） |
| `customers` | 客户 | 客户基本信息 |
| `subscription_rules` | 订阅规则 | 推送规则定义 |
| `rule_channels` | 订阅规则 | 规则-渠道-客户绑定 |
| `content_sources` | 产品管理 | 产品定义 + 采集开关 |
| `snapshots` | 采集数据 | 升级包快照 |
| `delivery_log` | 推送历史 | 推送结果记录 |
| `delayed_queue` | 订阅规则 | 延迟推送队列 |
| `digest_queue` | 订阅规则 | 摘要推送队列 |
| `system_settings` | 系统配置 | 键值对配置 |
| `system_event_config` | 系统配置 | 事件通知配置 |

---

## 统一响应格式

所有 API 响应均为：

```json
{
  "code": 0,           // 0=成功，非0=失败
  "data": { ... },     // 成功时返回的数据
  "message": "..."      // 失败时的错误信息
}
```

时间格式：所有时间均为 **UTC** ISO 8601 字符串（前端通过 `fmtTZ()` 转换本地时间）。
