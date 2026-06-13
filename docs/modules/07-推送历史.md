# 模块七：推送历史 (History)

## 功能说明

推送历史记录每一次升级包通知的发送结果。用户可以查看发送记录、重发推送、或清空历史。

## 数据模型

### delivery_log 表

```sql
CREATE TABLE delivery_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),  -- 关联快照
    rule_id INTEGER REFERENCES subscription_rules(id),      -- 关联订阅规则
    channel_id INTEGER REFERENCES channels(id),             -- 关联渠道
    channel_type TEXT NOT NULL,                              -- wecom/dingtalk/feishu/email
    channel_name TEXT DEFAULT '',                          -- 渠道名称（冗余存储）
    customer_id INTEGER REFERENCES customers(id),           -- 关联客户
    delivery_status TEXT DEFAULT 'pending',                  -- pending/sent/failed
    error_message TEXT DEFAULT '',                           -- 失败时的错误信息
    sent_at TEXT,                                           -- 实际发送时间
    retry_count INTEGER DEFAULT 0                           -- 重试次数
)
```

## 状态机

```
pending（待发送）
        │
   ┌────┴────┐
   ↓         ↓
 sent     failed
（成功）  （失败，可重发）
```

## API 设计

### GET /api/history

获取推送历史记录（分页）。

**查询参数**：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| page | int | 1 | 页码 |
| limit | int | 20 | 每页条数 |
| days | int | - | 只看最近N天，不传则不限 |
| product | string | - | 按产品名过滤 |
| customer_id | int | - | 按客户过滤 |

**响应**：
```json
{
  "code": 0,
  "data": {
    "items": [
      {
        "id": 100,
        "product_name": "WEB应用防护系统(WAF)",
        "version_branch": "V6.0.9",
        "package_type": "rule",
        "file_name": "WAF_V6.0.9_规则升级包.tar.gz",
        "package_version": "2025061201",
        "urgency": "high",
        "pushed_at": "2026-06-12T14:49:20",
        "deliveries": [
          {
            "channel_name": "企业微信-客户A",
            "channel_type": "wecom",
            "delivery_status": "sent",
            "error_message": "",
            "sent_at": "2026-06-12T14:49:20",
            "customer_name": "客户A"
          },
          {
            "channel_name": "钉钉-客户B",
            "channel_type": "dingtalk",
            "delivery_status": "failed",
            "error_message": "webhook timeout",
            "sent_at": null,
            "customer_name": "客户B"
          }
        ]
      }
    ],
    "total": 87,
    "page": 1
  }
}
```

### DELETE /api/history

清空推送历史。

**查询参数**：

| 参数 | 说明 |
|------|------|
| days | 保留最近N天，删除此天数之前的记录 |

### POST /api/history/:snapshot_id/resend

重新推送指定快照（触发所有匹配该快照的订阅规则）。

### POST /api/history/:snapshot_id/resend-targeted

定向重发——推送到指定客户+渠道。

**请求体**：
```json
{
  "channel_id": 1,
  "customer_id": 3
}
```

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/models/subscription.py` | `get_history()` / `clear_history()` / `log_delivery()` |
| `src/web/routes/api_routes.py` | History Blueprint |
| `src/notifiers/router.py` | `route_notifications()` 路由推送 |