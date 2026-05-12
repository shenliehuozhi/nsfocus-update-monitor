# 部署运维

## 环境要求

- OS: CentOS 7+ / Ubuntu 18+ (当前服务器: CentOS 7)
- Python: 3.8+
- 内存: 服务器可用 ≥ 400MB (总物理内存 1.6GB)
- 磁盘: ≥ 500MB 可用空间

## 安装

```bash
cd /root/nsfocus-monitor

# 虚拟环境
python3 -m venv venv
source venv/bin/activate

# 依赖
pip install -r requirements.txt

# 生成密钥
python3 -c "import secrets; print(secrets.token_hex(32))"
# 将输出写入环境变量
```

## 环境变量

```bash
# /root/nsfocus-monitor/.env
MONITOR_SECRET_KEY=    # AES 加密密钥 (64 hex chars)
MONITOR_JWT_SECRET=    # JWT 签名密钥
MONITOR_PORT=8800
MONITOR_DATA_DIR=/root/nsfocus-monitor/data
MONITOR_LOG_DIR=/root/nsfocus-monitor/logs
MONITOR_COLLECT_INTERVAL=4  # 采集间隔(小时)
MONITOR_ROLLBACK_CONFIRM=2   # 回退确认次数
MONITOR_ATTACHMENT_MAX_SIZE=10485760  # 邮件附件上限(10MB)
```

## 初始化

```bash
# 创建数据目录
mkdir -p /root/nsfocus-monitor/{data,logs}

# 初始化数据库
python scripts/init_db.py

# 创建管理员用户
python scripts/create_admin.py --username admin --password <your_password>
```

## systemd 服务

```ini
# /etc/systemd/system/nsfocus-monitor.service
[Unit]
Description=NSFOCUS Update Monitor
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/nsfocus-monitor
EnvironmentFile=/root/nsfocus-monitor/.env
ExecStart=/root/nsfocus-monitor/venv/bin/python /root/nsfocus-monitor/run.py
Restart=on-failure
RestartSec=10
MemoryMax=400M

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable nsfocus-monitor
systemctl start nsfocus-monitor
systemctl status nsfocus-monitor
```

## 管理命令

```bash
# 查看日志
journalctl -u nsfocus-monitor -f

# 应用日志
tail -f /root/nsfocus-monitor/logs/app.log

# 手动触发采集
curl -X POST http://localhost:8800/api/scheduler/trigger \
  -H "Authorization: Bearer <token>"

# 查看数据库
sqlite3 /root/nsfocus-monitor/data/nsfocus_monitor.db

# 重启服务
systemctl restart nsfocus-monitor
```

## 备份

```bash
# 数据库备份
cp /root/nsfocus-monitor/data/nsfocus_monitor.db \
   /root/nsfocus-monitor/data/backups/nsfocus_monitor_$(date +%Y%m%d).db

# crontab 每天凌晨 3 点备份
0 3 * * * cp /root/nsfocus-monitor/data/nsfocus_monitor.db /root/backups/nsfocus_monitor_$(date +\%Y\%m\%d).db
```

## 监控指标

### 服务健康检查

```bash
# HTTP 200 = 正常
curl -s -o /dev/null -w "%{http_code}" http://localhost:8800/api/health
```

### 关键日志关键词

| 关键词 | 含义 | 处理 |
|--------|------|------|
| `WARNING - Session xxx expired` | Session 过期 | 提醒用户更新 |
| `ERROR - Collection failed` | 采集失败 | 检查绿盟站点可访问性 |
| `WARNING - Health check degraded` | 解析器异常 | 检查页面是否改版 |
| `ERROR - All sessions exhausted` | 无可用 Session | 紧急：所有用户更新 Session |
| `INFO - New package detected` | 正常：发现新包 | 无需处理 |

## 故障处理

| 问题 | 排查步骤 |
|------|----------|
| 无法启动 | `journalctl -u nsfocus-monitor -n 50` 查看错误 |
| 内存超限 | 检查 `MemoryMax` 是否生效，调整附件下载策略 |
| 采集失败 | `curl -b "PHPSESSID=xxx" https://update.nsfocus.com/update/wafIndex` 手动测试 |
| 通知不发 | 检查订阅规则是否启用、延迟队列状态、渠道配置 |
| 推送被频率限制 | 查看 `GET /api/system/rate-limits`，用 `POST /api/system/rate-limits/reset` 重置 |
| 数据库锁定 | SQLite 单写，确保没有多个进程同时写入 |

### 频率限制管理

手动推送内置防滥用机制：同一邮箱/渠道 1 分钟内最多 5 次，超限封禁 10 分钟。

```bash
# 查看当前封禁状态
curl -u admin:<your_password> http://127.0.0.1:9999/api/system/rate-limits

# 重置特定 key
curl -u admin:<your_password> -X POST http://127.0.0.1:9999/api/system/rate-limits/reset \
  -H 'Content-Type: application/json' -d '{"key":"user@example.com"}'

# 重置全部（不传 key）
curl -u admin:<your_password> -X POST http://127.0.0.1:9999/api/system/rate-limits/reset \
  -H 'Content-Type: application/json' -d '{}'
```

## 升级

```bash
cd /root/nsfocus-monitor
git pull  # 或手动更新文件
source venv/bin/activate
python scripts/migrate.py  # 执行数据库迁移
systemctl restart nsfocus-monitor
```
