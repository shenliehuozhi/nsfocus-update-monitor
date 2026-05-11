# 绿盟升级监控平台 (NSFOCUS Update Monitor)

监控绿盟科技升级站点 (update.nsfocus.com) 的软件版本发布，发现新升级包后通过多渠道通知客户。

## 核心能力

- **6 产品监控**: WAF / IPS / IDS / RSAS / NF / UTS
- **4 渠道通知**: 企业微信 / 钉钉 / 飞书 / 邮件
- **智能检测**: 新增检测、回退检测、依赖提取、紧急度判断
- **灵活推送**: 延迟推送、窗口合并、最小间隔控制
- **客户管理**: 客户档案、持有产品/版本关联
- **多用户**: 独立 Session、独立订阅规则、Session 池冗余

## 技术栈

- 后端: Python 3 + Flask
- 数据库: SQLite
- 前端: Jinja2 模板 + 原生 HTML/CSS/JS (轻量)
- 部署: systemd + 单机运行
- 端口: 9999 (仅监听 127.0.0.1，安全)

## 快速开始

```bash
cd /root/nsfocus-monitor
pip install -r requirements.txt

# 直接启动
python run.py
# 访问 http://127.0.0.1:9999

# 或通过 systemd
sudo cp deploy/nsfocus-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nsfocus-monitor
```

## 配置方式

| 方式 | 说明 |
|------|------|
| Web UI | 浏览器访问 (仅 localhost) |
| CLI 工具 | `python cli.py customer list` 等 (TODO) |
| 配置文件 | `.env` 环境变量 |

关键环境变量见 `.env` 文件。

## 通知限流

IM 渠道（企微/钉钉/飞书）有 API 频率限制。系统在每个渠道发送间自动等待 `MONITOR_RATE_LIMIT_SEC` 秒（默认 3s），防止被限流。

## 文档

- [需求说明](docs/REQUIREMENTS.md)
- [架构设计](docs/ARCHITECTURE.md)
- [数据模型](docs/DATA_MODEL.md)
- [API 设计](docs/API.md)
- [详细设计](docs/DETAILED_DESIGN.md)
- [部署运维](docs/DEPLOYMENT.md)

## 版本历史

```bash
git log --oneline
# v1.0-base  基线版本
# +fix 企微限流
# +fix delta回退误报
```
