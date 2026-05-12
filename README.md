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
| `MONITOR_SECRET_KEY` | (随机) | AES 加密密钥 |
| `MONITOR_RATE_LIMIT_SEC` | 3 | IM 渠道冷却间隔 |
| `MONITOR_ATTACHMENT_MAX_SIZE` | 10485760 | 邮件附件上限(字节) |

## 版本历史

| Tag | 说明 |
|-----|------|
| v1.3 | 撤回检测全模式 + 推送三维度 + 维护模式 + 规则回退开关 + 安全脱敏 + 时区修复 |
| v1.2 | Quick采集模式 + 邮箱通知 + 注册屏蔽 + UTC时区 |
| v1.1 | 邮箱通知 + checkbox多选 + 订阅规则 |
| v1.0 | 基线：6产品采集 + 4渠道通知 + Web仪表盘 |
