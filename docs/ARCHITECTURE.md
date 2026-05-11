# 架构设计

## 1. 系统架构

```
┌─────────────────────────────────────────────────────┐
│                  Web 管理面板 (Flask)                  │
│  port: 9999                                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │ 仪表盘   │ │ 内容源   │ │ 订阅规则  │ │ 客户    │ │
│  │          │ │ 管理     │ │ 配置     │ │ 管理    │ │
│  └──────────┘ └──────────┘ └──────────┘ └─────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐ │
│  │ Session  │ │ 渠道     │ │ 推送历史  │ │ 设置    │ │
│  │ 管理     │ │ 管理     │ │          │ │         │ │
│  └──────────┘ └──────────┘ └──────────┘ └─────────┘ │
└──────────────────────┬──────────────────────────────┘
                       │
          ┌────────────┼────────────────┐
          ▼            ▼                ▼
   ┌────────────┐ ┌──────────┐  ┌────────────┐
   │ Scheduler  │ │ Manual   │  │ Session    │
   │ (cron/     │ │ Trigger  │  │ Health     │
   │  systemd)  │ │ (Web UI) │  │ Checker    │
   └─────┬──────┘ └────┬─────┘  └─────┬──────┘
         │             │              │
         └──────┬──────┘              │
                ▼                     ▼
   ┌──────────────────────┐  ┌─────────────────┐
   │   CollectorRouter    │  │  AlertService   │
   │   ┌──────────────┐   │  │  (独立告警通道)  │
   │   │NsfocusCollector│  │  (独立告警通道)  │
   │   │(6 products)   │   │  └─────────────────┘
   │   │├ quick(HEAD)  │   │
   │   │└ full(recurse)│  │
   │   └──────┬───────┘   │
   │          ▼           │
   │   UnifiedContentItem │
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │   ChangeDetector     │
   │   ├─ New Detection   │
   │   ├─ Rollback Detect │
   │   └─ Dependency Parse│
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │   SubscriptionEngine │
   │   ├─ Rule Matching   │
   │   ├─ Delay Queue     │
   │   └─ Dedup           │
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │   NotificationRouter │
   │   ├─ WecomNotifier   │
   │   ├─ DingtalkNotifier│
   │   ├─ FeishuNotifier  │
   │   └─ EmailNotifier   │
   └──────────────────────┘
```

## 2. 目录结构

```
nsfocus-monitor/
├── README.md
├── requirements.txt
├── run.py                    # 应用入口
├── config.py                 # 全局配置
├── docs/
│   ├── REQUIREMENTS.md
│   ├── ARCHITECTURE.md
│   ├── DATA_MODEL.md
│   ├── API.md
│   └── DEPLOYMENT.md
├── src/
│   ├── __init__.py
│   ├── app.py                # Flask 应用工厂
│   ├── config.py             # 配置管理
│   ├── core/
│   │   ├── __init__.py
│   │   ├── scheduler.py      # 定时任务调度
│   │   ├── crypto.py         # AES 加解密
│   │   ├── logger.py         # 日志配置
│   │   └── exceptions.py     # 自定义异常
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── base.py           # BaseCollector 抽象类
│   │   ├── nsfocus.py        # NsfocusCollector
│   │   ├── rss.py            # RssCollector (Phase 2)
│   │   └── wechat_mp.py      # WechatMpCollector (Phase 2)
│   ├── models/
│   │   ├── __init__.py
│   │   ├── database.py       # SQLite 连接管理 + 迁移
│   │   ├── user.py           # User 模型
│   │   ├── customer.py       # Customer 模型
│   │   ├── content_source.py # ContentSource 模型
│   │   ├── snapshot.py       # Snapshot 模型
│   │   ├── subscription.py   # SubscriptionRule 模型
│   │   ├── channel.py        # Channel 模型
│   │   └── audit.py          # AuditLog 模型
│   ├── notifiers/
│   │   ├── __init__.py
│   │   ├── base.py           # BaseNotifier 抽象类
│   │   ├── wecom.py          # 企业微信
│   │   ├── dingtalk.py       # 钉钉
│   │   ├── feishu.py         # 飞书
│   │   └── email.py          # 邮件
│   ├── web/
│   │   ├── __init__.py
│   │   ├── auth.py           # JWT 认证
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── dashboard.py
│   │   │   ├── session.py    # Session 管理 API
│   │   │   ├── sources.py    # 内容源 API
│   │   │   ├── subscriptions.py
│   │   │   ├── channels.py
│   │   │   ├── customers.py
│   │   │   ├── history.py
│   │   │   └── settings.py
│   │   ├── templates/
│   │   └── static/
│   └── detector/
│       ├── __init__.py
│       ├── change.py         # 变化检测引擎
│       └── parser.py         # 描述解析器
├── tests/
│   ├── __init__.py
│   ├── test_collector.py
│   ├── test_detector.py
│   └── test_notifier.py
├── snapshots/                 # 测试用 HTML fixture
├── scripts/
│   ├── init_db.py            # 数据库初始化
│   └── migrate.py            # 迁移脚本
├── data/
│   └── nsfocus_monitor.db    # SQLite 数据库
└── logs/
    └── app.log
```

## 3. 核心设计决策

### 3.1 采集统一抽象

```
BaseCollector
  ├── collect()          → List[UnifiedContentItem]
  ├── check_health()     → CollectorHealth
  └── source_type        → str

NsfocusCollector(BaseCollector)
  ├── 6 产品 URL 配置
  ├── 三级页面遍历
  ├── HTML 表格解析
  └── 描述结构化提取

RssCollector(BaseCollector)       # Phase 2
WechatMpCollector(BaseCollector)  # Phase 2
```

### 3.2 通知统一格式

```python
@dataclass
class NotificationMessage:
    title: str           # "WAF V6.0.9 规则包更新"
    summary: str         # 单行摘要
    fields: dict         # 结构化字段（详情）
    urgency: str         # normal | high | critical
    source_url: str      # 绿盟源页面链接
    download_url: str    # 下载直链
    attachment: Optional[bytes]  # 附件内容（≤10MB）
    attachment_name: str
```

每个 Notifier 实现 `send(message) → DeliveryResult`，返回送达状态。

### 3.3 推送确认链路

```
NotificationRouter.send()
  → for each channel in matched_channels:
      result = channel.notifier.send(message)
      # 记录发送结果
      save_delivery_log(message, channel, result)
  
  → # 对 IM 渠道发确认消息
  → for im_channel in im_channels_used:
      im_channel.notifier.send_confirmation(message, results)
```

### 3.4 Session 冗余策略

```
SessionPool:
  ├── 收集所有用户的活跃 PHPSESSID
  ├── 按优先级排序（最近验证有效 > 从未验证 > 最近失败）
  ├── 采集时取第一个有效 Session
  └── 采集失败 → 取下一个 Session 重试
```

### 3.5 延迟推送状态机

```
                ┌──────────┐
  新包检测 ───▶│ PENDING  │
                └────┬─────┘
                     │
          ┌──────────┼──────────┐
          ▼          ▼          ▼
     计时到期    新包出现     包被撤销
          │      (reset)        │
          ▼          ▼          ▼
     ┌────────┐ ┌────────┐ ┌──────────┐
     │  PUSH  │ │重置计时 │ │ CANCEL   │
     └────────┘ └────────┘ │+回退通知  │
                           └──────────┘
```

### 3.6 NsfocusCollector URL 映射

```python
NSFOCUS_PRODUCTS = {
    "WAF": {
        "base_url": "/update/wafIndex",
        "version_pattern": r"/update/wafV\d+Index",
        "detail_pattern": r"/update/listWafV\d+Detail/v/(sys|rule|nti)",
        "xinanchuang": ["/update/wafFTIndex", "/update/wafHGIndex/v/WAF-HG"],
        "special": ["/update/listWafSpecialIndex/v/special"],
    },
    "IPS": {
        "base_url": "/update/listIps",
        "version_pattern": r"/update/ipsIndex/v/[\d.]+",
        "special": ["/update/ipsInterfaceDetail/v/interfacev1.0",
                    "/update/listNewipsDetail/v/special"],
        "xinanchuang": [r"/update/ZGX\w*ipsIndex/v/.*"],
    },
    # ... IDS, RSAS, NF, UTS 类似
}
```

## 4. 技术选型理由

| 选择 | 理由 |
|------|------|
| SQLite | 单机部署，无需额外数据库进程，数据量小(<100MB) |
| Flask | 轻量，满足 Web 面板需求，无过度抽象 |
| Jinja2 | 服务端渲染，无需前端构建工具链 |
| requests | HTTP 客户端，支持 Session/Cookie 管理 |
| APScheduler | Python 定时任务，比 cron 更灵活 |
| cryptography | AES 加密，Python 官方推荐 |
| bcrypt | 密码哈希 |
| PyJWT | JWT 认证 |
