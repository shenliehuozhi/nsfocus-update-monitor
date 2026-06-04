# 白眼（Byakugan）升级监控平台 · Docker 部署

## 快速启动

```bash
# 1. 创建目录
mkdir -p /opt/byakugan/{data,logs}
cd /opt/byakugan

# 2. 下载 docker-compose.yml 和 .env.example

# 3. 配置环境变量（必须修改密钥）
cp .env.example .env
nano .env   # 修改 MONITOR_JWT_SECRET 和 MONITOR_SECRET_KEY

# 4. 启动
docker compose up -d

# 5. 验证
curl http://localhost:9999/api/health
```

默认账号：`admin` / `admin123`（首次登录后请修改）

---

## 数据持久化

| 宿主机目录 | 容器内路径 | 说明 |
|-----------|-----------|------|
| `./data` | `/app/data` | SQLite 数据库、快照、规则配置 |
| `./logs` | `/app/logs` | 应用日志 |

**重要**：删除容器不会丢失数据，重新启动后自动恢复。

---

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `MONITOR_JWT_SECRET` | 是 | - | JWT 会话密钥，**生产必须修改**，建议 32+ 字符随机字符串 |
| `MONITOR_SECRET_KEY` | 是 | - | Flask 密钥，**生产必须修改** |
| `MONITOR_PORT` | 否 | `9999` | 服务监听端口 |
| `MONITOR_LOG_LEVEL` | 否 | `INFO` | 日志级别：`DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `TZ` | 否 | `Asia/Shanghai` | 时区 |

---

## 镜像构建（本地）

```bash
# 构建本地镜像
docker build -t byakugan-monitor:local .

# 使用本地镜像（修改 image 为 byakugan-monitor:local）
docker compose -f docker-compose.local.yml up -d
```

---

## 更新升级

```bash
# 拉取新镜像
docker pull ghcr.io/shenliehuozhi/nsfocus-update-monitor:latest

# 重启（数据目录 ./data 会自动保留）
docker compose up -d
```

---

## 健康检查

```bash
# 检查容器状态
docker compose ps

# 查看应用日志
docker compose logs -f app

# 检查健康端点
curl http://localhost:9999/api/health
```

---

## 备份

```bash
# 备份数据
tar czvf byakugan-backup-$(date +%Y%m%d).tar.gz data/

# 恢复：解压到 /opt/byakugan/data
tar xzf byakugan-backup-20250601.tar.gz -C /opt/byakugan
```