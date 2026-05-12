# 绿盟升级监控平台 (NSFOCUS Update Monitor)

监控绿盟科技升级站点 (update.nsfocus.com) 的软件版本发布，发现新升级包后通过多渠道通知客户。

## 核心能力

- **6 产品监控**: WAF / IPS / IDS / RSAS / NF / UTS
- **4 渠道通知**: 企业微信 / 钉钉 / 飞书 / 邮件（支持附件）
- **双模采集**: Quick 扫描（每小时~30s）+ Full 扫描（每24h~25min）
- **撤回检测**: 全模式支持，最少2次确认，间隔24h
- **灵活推送**: 即时/延迟/汇总/维度选择（规则/渠道/客户）
- **客户管理**: 客户档案、持有产品/版本、邮箱覆盖
- **维保模式**: 一键静默所有推送，采集照常
- **安全脱敏**: 渠道密钥编辑时掩码显示

## 快速开始

```bash
cd /root/nsfocus-monitor
pip install -r requirements.txt
python run.py
# 访问 http://127.0.0.1:9999
```

详细操作见 [用户手册](docs/USER_MANUAL.md)。

## 技术栈

- 后端: Python 3 + Flask + APScheduler
- 数据库: SQLite (WAL 模式)
- 前端: 原生 HTML/CSS/JS (~950行，零依赖)
- 部署: systemd + 单机运行
- 端口: 9999

## 文档

| 文档 | 说明 |
|------|------|
| [用户手册](docs/USER_MANUAL.md) | 功能说明 + 参数配置 + FAQ |
| [需求说明](docs/REQUIREMENTS.md) | 业务需求 |
| [架构设计](docs/ARCHITECTURE.md) | 系统架构 |
| [数据模型](docs/DATA_MODEL.md) | 数据库表结构 |
| [API 设计](docs/API.md) | REST API 接口 |
| [详细设计](docs/DETAILED_DESIGN.md) | 函数级设计文档 |
| [部署运维](docs/DEPLOYMENT.md) | 部署指南 |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MONITOR_PORT` | 9999 | 服务端口 |
| `MONITOR_JWT_SECRET` | ⚠️ 硬编码默认值 | JWT 签名密钥，**生产必须修改** |
| `MONITOR_SECRET_KEY` | ⚠️ 硬编码默认值 | AES 加密密钥，**生产必须修改** |
| `MONITOR_RATE_LIMIT_SEC` | 3 | IM 渠道冷却间隔 |
| `MONITOR_ATTACHMENT_MAX_SIZE` | 10485760 | 邮件附件上限(字节) |

## 🔒 安全加固建议

本系统面向公网部署场景，以下建议按优先级排列，**配置完成后逐项落实**，最小化暴露面。

### 1. 网络层：限制源 IP（最优先）

部署完成后，只允许可信 IP 访问 Web 端口。以下任选一种：

**方案 A — iptables（推荐，零依赖）**
```bash
# 仅允许特定 IP 访问 9999 端口
iptables -A INPUT -p tcp --dport 9999 -s 你的办公IP -j ACCEPT
iptables -A INPUT -p tcp --dport 9999 -s 127.0.0.1 -j ACCEPT
iptables -A INPUT -p tcp --dport 9999 -j DROP

# 持久化 (Debian/Ubuntu)
apt install iptables-persistent && netfilter-persistent save
```

**方案 B — 应用层开关**
系统设置页 → 「🔒 API 仅限本机访问」勾选即可，所有 `/api/*` 仅接受 127.0.0.1。之后通过 SSH 隧道访问：
```bash
ssh -L 9999:127.0.0.1:9999 user@your-server
# 浏览器打开 http://127.0.0.1:9999
```

### 2. 认证层：改密码 + 换密钥

```bash
# ① 登录后立即修改默认密码（页面右上角「改密」）
# ② 生成强随机密钥
python3 -c "import secrets; print('JWT:', secrets.token_hex(32)); print('AES:', secrets.token_hex(32))"

# ③ 写入 .env
echo "MONITOR_JWT_SECRET=生成的JWT密钥" >> .env
echo "MONITOR_SECRET_KEY=生成的AES密钥" >> .env
systemctl restart nsfocus-monitor
```

> ⚠️ 修改 `MONITOR_SECRET_KEY` 会导致已存储的渠道配置（webhook URL/SMTP 密码）无法解密，需重新填写。

### 3. 传输层：HTTPS 反代

Flask 内置服务器不支持 TLS，通过 nginx 反代：

```nginx
server {
    listen 443 ssl;
    server_name monitor.your-domain.com;
    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    location / {
        proxy_pass http://127.0.0.1:9999;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

之后关闭 9999 端口对外暴露：
```bash
iptables -A INPUT -p tcp --dport 9999 -j DROP
```

### 4. 系统层：最小权限

```bash
# 以专用用户运行（不要用 root）
useradd -r -s /bin/false nsfocus
chown -R nsfocus:nsfocus /root/nsfocus-monitor

# systemd 服务加安全限制 (deploy/nsfocus-monitor.service)
[Service]
User=nsfocus
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/root/nsfocus-monitor/data /root/nsfocus-monitor/logs
```

### 5. 监控层：日志审计

```bash
# 定期检查异常登录
grep 'login_failed' /root/nsfocus-monitor/logs/app.log | tail -20

# 监控 API 访问来源
tail -f /root/nsfocus-monitor/logs/access.log | grep -v '127.0.0.1'
```

### 安全检查清单

| # | 检查项 | 命令/位置 |
|---|--------|----------|
| 1 | 默认密码已修改 | 页面右上角「改密」 |
| 2 | JWT 密钥已更换 | `grep JWT_SECRET .env` |
| 3 | 仅可信 IP 可访问 | `iptables -L -n \| grep 9999` |
| 4 | API 仅本机或已关闭对外 | 系统设置页 |
| 5 | 非 root 运行 | `ps aux \| grep run.py` |
| 6 | 维护模式已关闭（生产） | 系统设置页 |
| 7 | `.git` 目录不对外暴露 | nginx 配置 `location ~ /\.git { deny all; }` |

## 版本历史

| Tag | 说明 |
|-----|------|
| v1.3 | 撤回检测全模式 + 推送三维度 + 维护模式 + 规则回退开关 + 安全脱敏 + 时区修复 |
| v1.2 | Quick采集模式 + 邮箱通知 + 注册屏蔽 + UTC时区 |
| v1.1 | 邮箱通知 + checkbox多选 + 订阅规则 |
| v1.0 | 基线：6产品采集 + 4渠道通知 + Web仪表盘 |
