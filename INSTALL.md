# NSFocus 升级监控平台 部署文档

> 镜像：`ghcr.io/shenliehuozhi/nsfocus-update-monitor`
> 部署目标：沙箱机器（Ubuntu 26.04, systemd, 普通用户 `hermes`）
> 部署时间：2026-06-25

---

## 1. 部署环境

| 项目 | 值 |
|---|---|
| 主机 | Linux 7.0.0-14-generic x86_64, 8 核, 7GB RAM, 82GB 磁盘 |
| OS | Ubuntu 26.04 LTS (Resolute Raccoon) |
| 用户 | `hermes` (uid 1000, 在 `sudo` 组) |
| 防火墙 | ufw inactive, iptables INPUT policy ACCEPT |
| 网络 | 内网 `10.10.10.0/24`, 主机 IP `10.10.10.118` |
| 代理 | SOCKS5 `10.10.10.107:10810` (Auth), 沙箱出公网用, 不影响 docker |
| docker socket | `/var/run/docker.sock` (root:docker, chmod 660) |

**关键约束**:
- `sudo` 需要交互式密码（不能用 stdin 传）
- 普通用户不在 docker 组，跑 `docker` 命令需 `sudo` 前缀
- 后台/PTY 模式 sudo 不会弹出密码输入框 → 所有需要密码的 `sudo` 命令必须前台运行或人手动执行

---

## 2. 安装步骤

### 2.1 安装 Docker (root 操作)

**执行者**: 人（手动 `sudo apt install`）

```bash
sudo apt update && sudo apt install -y docker.io
```

包版本：`29.1.3-0ubuntu4.1`，装好后自动 `systemctl start docker` + `systemctl enable docker`。

**验证**:
```bash
docker --version        # 期望: Docker version 29.1.3
systemctl is-active docker  # 期望: active
ls -la /var/run/docker.sock # 期望: srw-rw---- root docker
```

### 2.2 把当前用户加入 docker 组（可选，省 sudo）

**执行者**: 人（手动 sudo）

```bash
sudo usermod -aG docker $USER
newgrp docker   # 重新加载组, 或重新登录
```

> 新加组对新会话生效。当前半会话用 `sudo docker ...` 也能跑。

### 2.3 拉取镜像

**执行者**: 人手动跑（避免 sudo 后台 PTY 密码问题）

```bash
sudo docker pull ghcr.io/shenliehuozhi/nsfocus-update-monitor:sha-d238b8830578219c3f9db72ff1dca57730594a32
```

**预计时长**: 5-10 分钟（镜像 249MB，沙箱出公网慢但稳定）。

**验证**:
```bash
sudo docker images | grep nsfocus
# 期望: ghcr.io/.../nsfocus-update-monitor ...  3bc7b73b5392  249MB
```

### 2.4 准备持久化目录

**执行者**: hermes（不需要 root）

```bash
mkdir -p /home/hermes/nsfocus-monitor/data
mkdir -p /home/hermes/nsfocus-monitor/logs
```

### 2.5 启动容器

**执行者**: hermes + sudo

**生成密钥** (32 字节 = 64 hex 字符):

```bash
openssl rand -hex 32  # MONITOR_SECRET_KEY
openssl rand -hex 32  # MONITOR_JWT_SECRET
```

**启动命令**:

```bash
sudo docker run -d \
  --name nsupdate-monitor \
  --restart unless-stopped \
  -p 9999:9999 \
  -v /home/hermes/nsfocus-monitor/data:/app/data \
  -v /home/hermes/nsfocus-monitor/logs:/app/logs \
  -e MONITOR_HOST=0.0.0.0 \
  -e MONITOR_SECRET_KEY=<上面生成的密钥1> \
  -e MONITOR_JWT_SECRET=<上面生成的密钥2> \
  ghcr.io/shenliehuozhi/nsfocus-update-monitor:sha-d238b8830578219c3f9db72ff1dca57730594a32
```

**环境变量说明** (来自镜像 inspect):

| 变量 | 必需? | 默认值 | 说明 |
|---|---|---|---|
| `MONITOR_HOST` | **必需** | `127.0.0.1` | Flask 绑定地址, **必须**设 `0.0.0.0` 才能从外部访问 |
| `MONITOR_PORT` | 否 | `9999` | Flask 监听端口 |
| `MONITOR_SECRET_KEY` | 强烈建议 | 镜像内置占位 | 加密/签名密钥 |
| `MONITOR_JWT_SECRET` | 强烈建议 | 镜像内置占位 | JWT 签名密钥 |
| `MONITOR_DATA_DIR` | 否 | `/app/data` | SQLite 数据库 + 初始密码文件目录 |
| `MONITOR_LOG_DIR` | 否 | `/app/logs` | 应用日志目录 |
| `MONITOR_DEBUG` | 否 | `false` | Flask debug 模式 |

### 2.6 验证

**等 3-5 秒启动**:

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
# 期望: nsupdate-monitor  Up X minutes  0.0.0.0:9999->9999/tcp

sudo docker logs nsupdate-monitor | tail -20
# 期望最后出现: 绿盟升级监控 — 初始化完成
#              用户名: admin
#              初始密码: <随机12字符>

curl --noproxy '*' http://localhost:9999/api/health
# 期望: {"code":0,"data":{"status":"ok"}}
```

**获取初始密码**:

```bash
cat /home/hermes/nsfocus-monitor/data/initial_password.txt
```

---

## 3. 访问

- **Web UI**: http://localhost:9999/ (本机) / http://10.10.10.118:9999/ (同内网)
- **默认账号**: `admin`
- **初始密码**: 上面命令输出, 或 `initial_password.txt`
- **首次登入必做**: 改默认密码 → 加监控会话 → 配告警渠道

---

## 4. 运维命令速查

```bash
# 状态
sudo docker ps | grep nsupdate-monitor
sudo docker logs --tail 50 nsupdate-monitor
sudo docker logs -f nsupdate-monitor  # 实时跟踪

# 启停
sudo docker stop nsupdate-monitor    # 停止 (容器进程退出, 数据保留)
sudo docker start nsupdate-monitor   # 启动 (用之前启动时的 env)
sudo docker restart nsupdate-monitor # 重启

# 改配置 / 轮换密钥: 需 stop+rm+重新 run
sudo docker stop nsupdate-monitor
sudo docker rm nsupdate-monitor
# 然后重新跑 §2.5 的 docker run 命令

# 数据备份
tar czf nsupdate-backup-$(date +%F).tar.gz \
  /home/hermes/nsfocus-monitor/data/nsfocus_monitor.db \
  /home/hermes/nsfocus-monitor/data/initial_password.txt

# 数据恢复 (新机器部署后)
# 1. 启动容器一次生成 schema
# 2. sudo docker stop nsupdate-monitor
# 3. sudo docker rm nsupdate-monitor
# 4. 覆盖: cp backup-nsfocus_monitor.db /home/hermes/nsfocus-monitor/data/
# 5. 重新启动容器

# 实时看应用日志
tail -f /home/hermes/nsfocus-monitor/logs/app.log
```

---

## 5. 部署时遇到的 4 个坑 + 解决

### 坑 1: `sudo -S` 通过 stdin 传密码被 security guard 拦

```
ERROR: User denied this command... sudo password guessing via stdin
```

**原因**: security guard 不允许 agent 通过 stdin 给 sudo 喂密码（防暴力破解）

**解决**: 用户手动在终端跑 `sudo apt install ...`, 输密码。Agent 不接触密码。

### 坑 2: ghcr.io 仓库 401 Unauthorized

```
docker pull: failed to fetch anonymous token: 401 Unauthorized
```

**原因**: 仓库原本是 private。`shenliehuozhi` 这个 GitHub 账号的所有包默认 private, 改设置后才能匿名拉。

**解决**:
1. 进 https://github.com/shenliehuozhi/nsfocus-update-monitor/settings
2. 拉到 "Danger Zone" → "Change repository visibility" → 改 Public
3. 重新 docker pull 即可

**验证方法**:
```bash
curl -sS "https://ghcr.io/v2/shenliehuozhi/nsfocus-update-monitor/manifests/sha-d238b8830578219c3f9db72ff1dca57730594a32"
# public: 返回 manifest JSON
# private: {"errors":[{"code":"UNAUTHORIZED"}]}
```

### 坑 3: Docker 容器绑 127.0.0.1, 外部访问 RST

```
curl localhost:9999/api/health
# (56) Recv failure: Connection reset by peer
```

**原因**: 镜像里 `run.py:95` 默认 `MONITOR_HOST=127.0.0.1`, Flask 只接受 loopback 连接。Docker NAT 把 host:9999 转给容器时, 容器进程还没监听外部接口。

**解决**: 启动时加 `-e MONITOR_HOST=0.0.0.0`, Flask 监听所有接口。

**怎么发现的**: 进容器 `cat /proc/net/tcp` 看 `0.0.0.0:9999` 还是 `127.0.0.1:9999`。

### 坑 4: Docker healthcheck 永远 unhealthy

```
docker ps: nsupdate-monitor  Up X minutes (unhealthy)
```

**原因**: 镜像 Dockerfile 写了 `HEALTHCHECK CMD-SHELL curl -f http://localhost:9999/api/health`, 但 `python:3.10-slim` 镜像**不装 curl**。healthcheck 命令每 30 秒跑一次都 exit 127, 连续失败标 unhealthy。

**影响**: 仅影响 docker 自己的健康显示, **不影响应用**。`curl localhost:9999/api/health` 实际返回 200。

**解决** (3 个选 1):
- 接受 unhealthy, 靠应用层 health 端点监控
- 重建镜像, healthcheck 改成 `python3 -c "import urllib.request; urllib.request.urlopen(...)"`  
- 启动时加 `--no-healthcheck` 禁用 healthcheck

**当前选择**: 接受 unhealthy, 因为 stop+rm 重建会断服务, 收益小。

---

## 6. 已知遗留问题

| 问题 | 严重度 | 解决 |
|---|---|---|
| 容器 docker 状态 unhealthy | 低 | 应用层 OK, 见坑 4 |
| `hermes` 不在 docker 组 (本会话) | 低 | `newgrp docker` 或重新登录解决 |
| 启动 `restart.sh` 里有变量未定义风险 | 中 | 用前先 `bash -n` 校验 |
| `initial_password.txt` 权限 `644 root:root` | 低 | 启动后及时改默认密码即可 |
| 启动容器时 `-e` 传密钥, 密钥会进 `docker inspect` 输出 | 中 | 长期: 改用 `--env-file` 引用 `chmod 600` 的密钥文件 |

---

## 7. 完整启动命令 (复制粘贴用)

```bash
# 1. 拉镜像
sudo docker pull ghcr.io/shenliehuozhi/nsfocus-update-monitor:sha-d238b8830578219c3f9db72ff1dca57730594a32

# 2. 准备目录
mkdir -p /home/hermes/nsfocus-monitor/{data,logs}

# 3. 生成密钥
SECRET_KEY=$(openssl rand -hex 32)
JWT_SECRET=$(openssl rand -hex 32)

# 4. 启动
sudo docker run -d \
  --name nsupdate-monitor \
  --restart unless-stopped \
  -p 9999:9999 \
  -v /home/hermes/nsfocus-monitor/data:/app/data \
  -v /home/hermes/nsfocus-monitor/logs:/app/logs \
  -e MONITOR_HOST=0.0.0.0 \
  -e "MONITOR_SECRET_KEY=$SECRET_KEY" \
  -e "MONITOR_JWT_SECRET=$JWT_SECRET" \
  ghcr.io/shenliehuozhi/nsfocus-update-monitor:sha-d238b8830578219c3f9db72ff1dca57730594a32

# 5. 等 5 秒, 拿初始密码
sleep 5
cat /home/hermes/nsfocus-monitor/data/initial_password.txt

# 6. 访问 http://localhost:9999/, 用户名 admin, 密码上面那行
```

---

## 8. 文件清单

```
/home/hermes/nsfocus-monitor/
├── INSTALL.md           # 本文档
├── .env.example         # 环境变量模板 (cp 到 .env 后填实际密钥)
├── data/                # 容器 /app/data 卷
│   ├── nsfocus_monitor.db         # SQLite 数据库
│   └── initial_password.txt       # admin 初始密码 (启动时生成)
├── logs/                # 容器 /app/logs 卷
│   ├── app.log                     # 主应用日志
│   └── log_scanner.log             # 日志扫描器日志
└── restart.sh           # 重建容器脚本 (密钥轮换/改配置用, bash -n 校验过)
```

---

## 9. 升级流程

**适用**: 升级到新版本镜像(我们发新 release / 修复了关键 bug)

```bash
# 1. 看当前跑的 tag
sudo docker ps --format '{{.Names}}\t{{.Image}}' | grep nsupdate-monitor

# 2. 拉新镜像 (我们 CI 推 :latest + :sha-<commit> 两个 tag)
#    长期使用建议固定 sha-XXX,这样可重现部署,不会因 latest 变更意外升级
sudo docker pull ghcr.io/shenliehuozhi/nsfocus-update-monitor:sha-<新commit>

# 3. stop + 删旧容器 (data/logs 在卷里,不会丢)
sudo docker stop nsupdate-monitor
sudo docker rm nsupdate-monitor

# 4. 用同样参数跑新镜像 (镜像 tag 改一下)
sudo docker run -d \
  --name nsupdate-monitor \
  --restart unless-stopped \
  -p 9999:9999 \
  -v /home/hermes/nsfocus-monitor/data:/app/data \
  -v /home/hermes/nsfocus-monitor/logs:/app/logs \
  --env-file /home/hermes/nsfocus-monitor/.env \
  ghcr.io/shenliehuozhi/nsfocus-update-monitor:sha-<新commit>

# 5. 验证
sleep 5
curl --noproxy '*' http://localhost:9999/api/health
sudo docker logs --tail 20 nsupdate-monitor
```

**自动升级** (可选) —— 用 [Watchtower](https://github.com/containrrr/watchtower):
```bash
sudo docker run -d \
  --name watchtower \
  -v /var/run/docker.sock:/var/run/docker.sock \
  containrrr/watchtower \
  --interval 86400 \
  --cleanup \
  nsupdate-monitor
```

> **注意**: Watchtower 默认会拉 `:latest` 升级。**生产建议用 `sha-XXX` 固定版本,关闭自动升级**。

**数据库 schema 升级**: 我们在 `migrations/` 目录按时间顺序放 schema 变更记录。**应用启动时自动跑 schema 升级**,不用手动执行 SQL。

---

## 10. 绿盟 PHPSESSID 配置

**为什么必需**: 应用要抓绿盟升级包页面,需要登录态的 `PHPSESSID` cookie。没配 = 采集 0 条 = 平台白搭。

**首次配置流程**:

1. 浏览器打开 https://update.nsfocus.com/,登录你的账号
2. F12 开发者工具 → Network 标签 → 勾选 Preserve log
3. 刷新任意页面 → 找到任意一个请求 → 看 Request Headers 里的 `Cookie:` 字段
4. 复制 `PHPSESSID=xxxxxx` 这一段(只要这一段,其他 cookie 不需要)
5. 进平台 UI → 左侧 **"会话管理"** → **"新增监控会话"** → 粘贴 PHPSESSID → 选产品分类 → 保存
6. 验证: 看 `logs/app.log` 里有没有 `[session] PHPSESSID=xxx... active for ...` + 之后 5 分钟内是否有采集日志

**过期处理**: 绿盟 PHPSESSID 默认 30 分钟过期。**我们 scheduler 里有 `heartbeat_enabled=1` 的心跳任务,默认每 30 分钟自动续期一次**。如果你的 PHPSESSID 提前过期:

- UI → 会话管理 → 找到那个会话 → 点 "测试" 按钮 → 看返回值
- 如果返回 401/过期 → 重新按上面步骤 1-4 拿新 PHPSESSID → 编辑会话 → 粘贴新值
- 如果频繁过期(< 1 小时) → 查 NAT/防火墙/代理,确认 PHPSESSID 同一 IP 段使用

**多账号**: 一个会话对应一个绿盟账号。多个账号可建多个会话(UI 里挨个加),但同一 source (如 WAF) **只用 1 个会话采集**(取优先级最高且 is_active=1 的那个)。

---

## 11. docker-compose 部署 (推荐生产用)

`docker-compose.yml` 模板,放在 `/home/hermes/nsfocus-monitor/docker-compose.yml`:

```yaml
version: '3.8'
services:
  nsfocus-monitor:
    image: ghcr.io/shenliehuozhi/nsfocus-update-monitor:sha-<commit>
    container_name: nsupdate-monitor
    restart: unless-stopped
    ports:
      - "9999:9999"
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    env_file:
      - .env
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9999/api/health',timeout=5).status==200 else 1)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
```

**配套 `.env` 文件** (`chmod 600 .env`):

```bash
MONITOR_HOST=0.0.0.0
MONITOR_PORT=9999
MONITOR_SECRET_KEY=<openssl rand -hex 32>
MONITOR_JWT_SECRET=<openssl rand -hex 32>
MONITOR_DATA_DIR=/app/data
MONITOR_LOG_DIR=/app/logs
MONITOR_DEBUG=false
```

**好处**:
- `--env-file` 比 `-e` 安全(密钥不会进 `docker inspect`)
- `chmod 600` 防同机其他用户读到密钥
- 重启/stop/start 不用每次输长串命令 → `docker compose restart`
- 升级 → 改 `image:` 一行 + `docker compose pull && docker compose up -d`

**启动命令**:
```bash
cd /home/hermes/nsfocus-monitor
docker compose up -d
docker compose logs -f | head -30
```

---

## 12. 内网 / 离线部署

**场景**: 沙箱出公网慢/封了 GHCR,或客户机器完全隔离外网。

**方法 A: docker save / load** (推荐,最简单):

```bash
# 在能访问 GHCR 的机器上导出
sudo docker save -o nsfocus-monitor.tar \
  ghcr.io/shenliehuozhi/nsfocus-update-monitor:sha-<commit>

# 把 .tar 复制到目标机器 (scp / U盘 / 内网文件服务)
scp nsfocus-monitor.tar hermes@10.10.10.118:~/

# 目标机器导入
sudo docker load -i ~/nsfocus-monitor.tar
sudo docker images | grep nsfocus  # 验证
```

**方法 B: 私有镜像仓库** (多个客户机器共享):

```bash
# 在内网部署一个 registry (一次性)
sudo docker run -d -p 5000:5000 --restart=always --name registry registry:2

# 推送
sudo docker tag ghcr.io/shenliehuozhi/nsfocus-update-monitor:sha-<commit> \
  10.10.10.X:5000/nsfocus-update-monitor:sha-<commit>
sudo docker push 10.10.10.X:5000/nsfocus-update-monitor:sha-<commit>

# 客户机器拉
sudo docker pull 10.10.10.X:5000/nsfocus-update-monitor:sha-<commit>
```

**方法 C: 完全离线** (无任何镜像仓库):

- 步骤同方法 A,但目标机器**不能 `docker pull`**,只能 `docker load`
- 升级时在能联网的机器重新 `docker save` + 传输

---

## 13. 安全 / 反向代理 (公网部署必须)

**当前默认只适合内网 / 沙箱**。暴露到公网前必做:

1. **HTTPS** —— 用 nginx / caddy / traefik 反向代理 9999,加 Let's Encrypt 证书
2. **改默认密码** —— 第一次登入立即改(已在 §3 提)
3. **MONITOR_SECRET_KEY + MONITOR_JWT_SECRET** —— 用 `openssl rand -hex 32` 生成,不要用镜像内置占位
4. **CORS** —— `CORS_ORIGINS=https://your-domain.com` 限制来源
5. **fail2ban** —— 登录失败 N 次封 IP

**最小 nginx 反代配置**:
```nginx
server {
    listen 443 ssl;
    server_name monitor.example.com;
    ssl_certificate /etc/letsencrypt/live/monitor.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/monitor.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:9999;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## 14. 日常维护清单

| 任务 | 频率 | 命令 |
|---|---|---|
| 看应用日志 | 每天 | `tail -f /home/hermes/nsfocus-monitor/logs/app.log` |
| 数据备份 | 每天 (cron) | `tar czf backup-$(date +%F).tar.gz /home/hermes/nsfocus-monitor/data` |
| 清理备份 (留 7 天) | 每天 (cron) | `find /home/hermes/nsfocus-monitor/backup-*.tar.gz -mtime +7 -delete` |
| 升级镜像 | 每次发版 | 见 §9 |
| 看磁盘占用 | 每周 | `du -sh /home/hermes/nsfocus-monitor/{data,logs}` |
| 轮换密钥 | 季度 / 安全事件 | 见 §11 (改 .env) |
| 检查 GHCR 配额 | 每月 | `gh api user/settings/billing/actions` |

**示例 backup cron** (`crontab -e`):
```cron
# 每天凌晨 3 点备份
0 3 * * * tar czf /home/hermes/nsfocus-monitor/backup-$(date +\%F).tar.gz /home/hermes/nsfocus-monitor/data
# 每天凌晨 4 点清 7 天前的备份
0 4 * * * find /home/hermes/nsfocus-monitor/backup-*.tar.gz -mtime +7 -delete
```

---

## 15. GHCR 配额提醒 (沙箱/私人仓库用)

- **Free for private repo**: 每月 artifact storage 100MB + cache 2GB
- **Free for public repo**: 每月 artifact storage 2GB + cache 2GB
- 单个 docker 镜像现在 ~250MB,**public 仓库可以存 ~8 个镜像 / 月**
- 超出配额后 `docker pull` 报 401 错(类似"quota hit"),但**不会自动回退到匿名**

**建议**:
- **生产固定 `:sha-<commit>` tag**,不用 `:latest`(latest 每次 push 覆盖老 image,占空间)
- 定期用 `gh api user/packages?package_type=container` 看 tag 列表,清不用的老 tag:
  ```bash
  gh api -X DELETE user/packages/container/nsfocus-update-monitor/versions/<version_id>
  ```

---

## 16. 已知坑 (本次部署遇到,见 §5 详述)

| 坑 | 状态 (新版本) |
|---|---|
| §5.1 sudo 密码 stdin 被 security guard 拦 | 跟代码无关,运维约束 |
| §5.2 GHCR 包默认 private | **新包默认仍可能 private**:GHCR 改 package 公共属性**只影响已存在 tag**;以后 push 的新 image 还要在 GHCR UI 再改一次(我们 docker.yml 没传 `visibility: public` 参数)。**根因是 GHCR visibility 跟 repo visibility 是分开的**,改 repo 改 package 都不会自动同步 |
| §5.3 Docker 容器绑 127.0.0.1 外部 RST | **✅ 已修**: 新版镜像 ENV MONITOR_HOST=0.0.0.0 默认值 |
| §5.4 HEALTHCHECK 用 curl 镜像没装 | **✅ 已修**: 新版镜像 HEALTHCHECK 改用 python stdlib urllib |
