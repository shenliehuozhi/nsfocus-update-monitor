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

## 快速开始

```bash
cd /root/nsfocus-monitor
pip install -r requirements.txt
python src/app.py
# 访问 http://119.23.152.22:8800
```

## 文档

- [需求说明](docs/REQUIREMENTS.md)
- [架构设计](docs/ARCHITECTURE.md)
- [数据模型](docs/DATA_MODEL.md)
- [API 设计](docs/API.md)
- [部署运维](docs/DEPLOYMENT.md)
