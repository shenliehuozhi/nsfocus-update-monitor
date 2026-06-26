# 绿盟升级监控平台 (NSFOCUS Update Monitor)

监控绿盟科技升级站点 (update.nsfocus.com) 的软件版本发布，发现新升级包后通过多渠道通知客户。

## 一键部署

### 方式 A: Docker 部署 (推荐,跨平台一致)

镜像: `ghcr.io/shenliehuozhi/nsfocus-update-monitor` (已修复 MONITOR_HOST 默认值,HEALTHCHECK 正常工作)

```bash
# 1. 拉镜像 (latest 跟踪最新,或固定 sha-XXX 长期可重现部署)
sudo docker pull ghcr.io/shenliehuozhi/nsfocus-update-monitor:latest

# 2. 准备持久化目录
mkdir -p ~/nsfocus-monitor/{data,logs}

# 3. 启动 (一行搞定)
sudo docker run -d \
  --name nsupdate-monitor \
  --restart unless-stopped \
  -p 9999:9999 \
  -v ~/nsfocus-monitor/data:/app/data \
  -v ~/nsfocus-monitor/logs:/app/logs \
  ghcr.io/shenliehuozhi/nsfocus-update-monitor:latest

# 4. 等 5 秒看初始密码
sleep 5 && cat ~/nsfocus-monitor/data/initial_password.txt
```

**生产推荐用 docker-compose** (更安全,密钥不会进 `docker inspect`):
```bash
mkdir -p ~/nsfocus-monitor && cd ~/nsfocus-monitor
curl -O https://raw.githubusercontent.com/shenliehuozhi/nsfocus-update-monitor/master/docker-compose.yml
curl -O https://raw.githubusercontent.com/shenliehuozhi/nsfocus-update-monitor/master/.env.example
cp .env.example .env && chmod 600 .env
# 编辑 .env 填 MONITOR_SECRET_KEY / MONITOR_JWT_SECRET (openssl rand -hex 32)
docker compose up -d
```

> **完整文档**: [INSTALL.md](INSTALL.md) (含升级流程、PHPSESSID 配置、内网离线部署、HTTPS 反代、GHCR 配额、4 个实战踩坑总结)

### 方式 B: systemd 一键安装 (传统 Linux)

```bash
git clone https://github.com/shenliehuozhi/nsfocus-update-monitor.git
cd nsfocus-monitor
bash start.sh
```

首次部署自动完成:环境检测、依赖安装、随机密钥生成、79 个产品导入、管理员账户创建。访问 `http://IP:9999` 即可。

### 方式 C: Windows 绿色版

见 [Releases](https://github.com/shenliehuozhi/nsfocus-update-monitor/releases) 下载 zip 解压即用。

## 首次登入必做

```bash
# 1. 拿到初始密码 (Docker 部署)
cat ~/nsfocus-monitor/data/initial_password.txt

# 2. 浏览器打开 http://IP:9999,用户名 admin,密码上面那行
# 3. 进入系统后第一件事:右上角「改密」改默认密码
# 4. 左侧「会话管理」→ 新增监控会话 → 粘贴绿盟 PHPSESSID cookie (F12 抓)
#    详细步骤见 INSTALL.md §10
# 5. 配告警渠道:系统设置页 → 通知渠道(企业微信/钉钉/飞书/邮件)
# 6. 建订阅规则:订阅规则 → 新建规则 → 选产品/版本/渠道/收件人
```

## 版本更新

### Docker 部署
```bash
# 拉新镜像 + 重启
sudo docker pull ghcr.io/shenliehuozhi/nsfocus-update-monitor:latest
sudo docker stop nsupdate-monitor && sudo docker rm nsupdate-monitor
# 然后重新跑上面的 docker run 命令 (用新镜像)
# 或 docker compose 部署:cd ~/nsfocus-monitor && docker compose pull && docker compose up -d
```

数据文件在 `~/nsfocus-monitor/data/` 目录,不随镜像更新,所有历史数据(产品配置 / Session / 推送记录)不受影响。

### systemd 部署
```bash
cd nsfocus-monitor
git pull
systemctl restart nsfocus-monitor
```

### Windows 绿色版
> ⚠️ Windows 版数据存储在独立目录(`%LOCALAPPDATA%\nsfocus-monitor-data\`),替换 exe 不会丢失数据。

1. 关掉运行中的 `nsfocus-monitor.exe`
2. 删除旧版 exe
3. 从 [Releases](https://github.com/shenliehuozhi/nsfocus-update-monitor/releases) 下载最新版覆盖
4. 重新运行

详细操作见 [用户手册](docs/用户手册.md)。

## 核心能力

- **79 产品监控**: 覆盖 WAF / IPS / IDS / RSAS / UTS 等 79 个绿盟产品线（默认全开,可在 UI 关停个别）
- **4 渠道通知**: 企业微信 / 钉钉 / 飞书 / 邮件（支持附件）
- **双模采集**: Quick 扫描（每小时~30s）+ Full 扫描（每24h~25min）
- **撤回检测**: 全模式支持，最少2次确认，间隔24h
- **灵活推送**: 即时/延迟/汇总/维度选择（规则/渠道/客户）
- **客户管理**: 客户档案、持有产品/版本、邮箱覆盖
- **安全脱敏**: 渠道密钥编辑时掩码显示

## 技术栈

- 后端: Python 3 + Flask + APScheduler
- 数据库: SQLite (WAL 模式)
- 前端: 原生 HTML/CSS/JS（零依赖）
- 部署: systemd + 单机运行
- 端口: 9999

## 文档

| 文档 | 说明 |
|------|------|
| [INSTALL.md](INSTALL.md) | **Docker / docker-compose 部署**(推荐) + 4 个实战踩坑 + 升级流程 + PHPSESSID 配置 + 内网离线 + HTTPS 反代 |
| [用户手册](docs/用户手册.md) | 功能说明 + 参数配置 + FAQ |
| [部署运维](docs/部署运维.md) | systemd 一键安装 + 故障处理 |
| [需求说明](docs/需求说明.md) | 业务需求 |
| [架构设计](docs/系统架构.md) | 系统架构 |
| [数据模型](docs/数据模型.md) | 数据库表结构 |
| [API 设计](docs/API接口文档.md) | REST API 接口 |
| [迭代优化记录](docs/迭代优化记录.md) | 版本演进与优化设计 |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MONITOR_PORT` | 9999 | 服务端口 |
| `MONITOR_JWT_SECRET` | ⚠️ 硬编码默认值 | JWT 签名密钥，**生产必须修改** |
| `MONITOR_SECRET_KEY` | ⚠️ 硬编码默认值 | AES 加密密钥，**生产必须修改** |
| `MONITOR_RATE_LIMIT_SEC` | 3 | IM 渠道冷却间隔 |
| `MONITOR_ATTACHMENT_MAX_SIZE` | 10485760 | 邮件附件上限(字节) |

## 安全加固建议

部署完成后，只允许可信 IP 访问 Web 端口。以下任选一种：

**方案 A — iptables（推荐，零依赖）**
```bash
iptables -A INPUT -p tcp --dport 9999 -s 你的办公IP -j ACCEPT
iptables -A INPUT -p tcp --dport 9999 -s 127.0.0.1 -j ACCEPT
iptables -A INPUT -p tcp --dport 9999 -j DROP
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
