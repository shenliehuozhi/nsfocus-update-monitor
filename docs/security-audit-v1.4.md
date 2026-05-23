# 安全审计报告

> 版本：v1.0
> 日期：2026-05-24
> 审计范围：v1.4-system-events (b561479)

---

## 一、认证与会话

### 1.1 JWT 认证 ✅

| 项目 | 状态 | 说明 |
|------|------|------|
| 算法 | ✅ HS256 |业界标准 |
| 密钥 | ✅ 环境变量 | `MONITOR_JWT_SECRET`，dev fallback 明确标注 |
| 过期时间 | ✅ 24h | 合理 |
| 错误处理 | ✅ 无信息泄露 | ExpiredSignatureError/InvalidTokenError 均返回统一 40101 |
| 注册功能 | ✅ 已关闭 | `/api/auth/register` 返回 403 |

### 1.2 密码存储 ✅

- bcrypt 哈希（cost factor 默认）
- 修改密码需验证旧密码
- 密码长度校验（≥4位）

### 1.3 API 本地访问限制 ✅

- `api_localhost_only` 配置项
- 基于 `request.remote_addr`（TCP层，不可伪造）
- 白名单：`127.0.0.1`, `::1`, `localhost`

**建议**：生产环境开启 `api_localhost_only`。

---

## 二、注入防护

### 2.1 SQL 注入 ✅

所有数据库操作使用参数化查询：
```python
execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
query("SELECT * FROM users WHERE username = ?", (username,))
```

无字符串拼接 SQL，所有 `?` 占位符均传 tuple 参数。

### 2.2 路径遍历 ✅

日志文件读取有严格校验：
```python
if not filename.endswith('.log') or '..' in filename or '/' in filename:
    return {'code': 400, 'message': 'Invalid filename'}, 400
```

仅允许 `.log` 文件，跳过 `..` 和 `/`。

### 2.3 XSS ⚠️ 需确认

前端使用 `innerHTML` 插入动态内容，但需确认 `escHtml()` 是否在所有用户输入点被调用。建议审查 `index.html` 中所有动态插入点。

### 2.4 命令注入 ✅

未发现 `os.system`、`subprocess` 等直接系统调用。

---

## 三、敏感数据保护

### 3.1 密钥存储 ✅

| 字段 | 加密方式 |
|------|---------|
| PHPSESSID | AES-256-GCM |
| SMTP 密码 | AES-256-GCM |
| Webhook URL | AES-256-GCM |

- 密钥来源：`MONITOR_SECRET_KEY` 环境变量（64 hex = 32 bytes）
- Dev fallback 有明确警告注释

### 3.2 JWT Secret ✅

- 环境变量：`MONITOR_JWT_SECRET`
- Dev fallback 有明确标注

### 3.3 CORS ⚠️

`CORS(app)` 无限制配置，允许所有来源。
**风险**：如果前端和后端在不同域，生产环境应限制允许的源。

**建议**：
```python
CORS(app, origins=['https://your-frontend.com'])
```

---

## 四、速率限制

### 4.1 推送限流 ✅

三级限流机制：
1. 渠道级别（小时/日限制）
2. 目标级别（每个收件人限制）
3. 全局限流（黑名单 ban）

实现：`rate_limiter.py` + `email_rate_limiter.py`

### 4.2 登录限流 ⚠️ 未发现

未发现登录尝试次数限制。暴力破解风险存在。

**建议**：添加 `login_attempts` 表，连续失败 N 次后临时封禁 IP。

---

## 五、审计日志

### 5.1 操作审计 ✅

`audit` 表记录关键操作：
- 登录/登录失败
- 密码修改
- 关键配置变更
- 推送记录

### 5.2 访问日志 ✅

- 所有请求记录到 `access.log`
- 包含 IP/Method/Path/Status/Duration
- 日志轮转（10MB × 10）

---

## 六、网络安全

### 6.1 外发请求 ⚠️

- 绿盟 API：PHPSESSID Cookie 认证
- 通知渠道：webhook URL（可能含敏感 token）

**建议**：确保 webhook URL 使用 HTTPS，添加证书验证。

### 6.2 外部依赖 ⚠️

- `requests` 库未配置默认超时
- 部分调用有 timeout（10-30s），部分未明确

**建议**：统一配置 `requests` session 默认超时。

---

## 七、配置安全

### 7.1 默认密钥 ⚠️ 中风险

```
MONITOR_SECRET_KEY = 'dev-secret-change-me'  # dev fallback
MONITOR_JWT_SECRET = 'dev-jwt-secret-change-me'
```

生产环境未设置环境变量时会使用弱密钥。

### 7.2 数据库 ⚠️

- SQLite 无密码保护（文件系统级保护）
- WAL 模式（正常）
- busy_timeout=10s（防止锁）

### 7.3 Flask 配置 ⚠️

`threaded=False` 已设置（防止 SQLite 并发问题）。

---

## 八、总结

### 高优先级

| 问题 | 风险 | 建议 |
|------|------|------|
| CORS 无限制 | 中 | 生产环境配置允许的源 |
| 登录无次数限制 | 中 | 添加登录失败限流 |
| Dev 密钥 fallback | 中 | 确保生产环境设置环境变量 |

### 中优先级

| 问题 | 风险 | 建议 |
|------|------|------|
| XSS 防护边界 | 低-中 | 全面审查 `innerHTML` 调用点 |
| 外发请求无证书验证 | 低 | 添加 HTTPS 证书验证 |
| requests 默认超时 | 低 | 统一配置 session 超时 |

### 已做得好的

- ✅ 参数化 SQL 查询
- ✅ 路径遍历防护
- ✅ JWT 认证规范
- ✅ 敏感字段 AES-256-GCM 加密
- ✅ 操作审计日志
- ✅ 推送速率限制
- ✅ 注册功能关闭
- ✅ API 本地访问限制
- ✅ CORS 配置（需生产环境调整）

---

## 九、部署建议

1. **环境变量**（必须）：
   ```bash
   export MONITOR_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
   export MONITOR_JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
   ```

2. **CORS**（生产环境）：
   ```bash
   export CORS_ORIGINS=https://your-frontend.com
   ```

3. **API 限制**（可选）：
   ```bash
   # 在系统设置中开启"仅允许本地访问API"
   ```

4. **登录限流**：作为独立 Issue 跟进（Phase 2）