# 模块三：通知渠道 (Channels)

## 功能说明

通知渠道配置推送通道，负责将升级包信息实际发送给客户。支持多种渠道类型：企业微信、钉钉、飞书、邮件、Apprise。

## 数据模型

### channels 表

```sql
CREATE TABLE channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,                    -- 渠道名称，如"企业微信-客户A"
    type TEXT NOT NULL CHECK(type IN ('wecom','dingtalk','feishu','email')),
    config TEXT NOT NULL,                  -- 加密存储的渠道配置（JSON）
    is_active INTEGER DEFAULT 1,           -- 1=启用, 0=停用
    email_hourly_limit INTEGER DEFAULT 0,  -- 邮件每小时限流（0=不限）
    email_daily_limit INTEGER DEFAULT 0,   -- 邮件每天限流（0=不限）
    created_at TEXT DEFAULT (datetime('now'))
)
```

### rule_channels 表（订阅规则绑定）

```sql
CREATE TABLE rule_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL REFERENCES subscription_rules(id) ON DELETE CASCADE,
    channel_id INTEGER REFERENCES channels(id),
    customer_id INTEGER REFERENCES customers(id)
)
```

## 渠道类型及配置

### 企业微信（wecom）

```json
{
  "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx"
}
```

### 钉钉（dingtalk）

```json
{
  "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=xxxx"
}
```

### 飞书（feishu）

```json
{
  "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxx"
}
```

### 邮件（email）

```json
{
  "smtp_host": "smtp.qq.com",
  "smtp_port": 465,
  "smtp_user": "notice@example.com",
  "smtp_password": "xxxx",       -- SMTP 密码或授权码
  "from_name": "绿盟升级通知",
  "recipients": ["a@客户.com", "b@客户.com"]
}
```

## API 设计

### GET /api/channels

列出当前用户所有渠道。

**响应**：
```json
{
  "code": 0,
  "data": [
    {
      "id": 1,
      "name": "企业微信-客户A",
      "type": "wecom",
      "is_active": 1,
      "config": { "webhook_url": "https://..." },
      "email_hourly_limit": 0,
      "email_daily_limit": 0
    }
  ]
}
```

### POST /api/channels

创建渠道。

**请求体**：
```json
{
  "name": "钉钉-客户B",
  "type": "dingtalk",
  "config": { "webhook_url": "https://..." },
  "is_active": 1
}
```

### PUT /api/channels/:id

更新渠道（部分字段）。

### DELETE /api/channels/:id

删除渠道。

## 加密存储

渠道配置（包含 webhook URL、SMTP 密码等敏感信息）使用 Fernet 对称加密后存储：

```python
# 写入时
config_encrypted = encrypt(json.dumps(config))
db.execute("UPDATE channels SET config=? WHERE id=?", (config_encrypted, id))

# 读取时
row = db.query("SELECT config FROM channels WHERE id=?", (id,))
config = json.loads(decrypt(row['config']))
```

## 通知发送

发送逻辑在 `src/core/notifier.py` 中，根据 `channel.type` 分发到不同的 sender：

| type | sender |
|------|--------|
| wecom | `notify_wecom()` |
| dingtalk | `notify_dingtalk()` |
| feishu | `notify_feishu()` |
| email | `notify_email()` |

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/web/routes/api_routes.py` | 渠道 CRUD API |
| `src/models/channel.py` | 渠道数据访问层 |
| `src/core/notifier.py` | 通知发送实现 |
| `src/core/crypto.py` | 配置加密/解密 |
