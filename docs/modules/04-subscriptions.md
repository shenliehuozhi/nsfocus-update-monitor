# 模块四：订阅规则 (Subscriptions)

## 功能说明

订阅规则定义了"什么升级包应该推送给谁"。每条规则描述了过滤条件（产品/版本/包类型/紧急度）、推送策略（延迟/静默/摘要）以及目标渠道。

---

## 规则匹配流程（采集完成后）

```
采集完成 → 发现新包
        ↓
scheduler 遍历所有 enabled=1 的规则
        ↓
get_new_for_subscription(rule, new_items)
        ↓
  ├── 校验 valid_until（过期跳过）
  ├── 空 conditions → 匹配全部
  ├── chains 匹配 → 按链路径精确匹配
  └── 旧结构匹配 → products/versions/package_types/urgency/keywords
        ↓
匹配成功 → route_notifications(snapshot_id, rule_id)
        ↓
  ├── rollback 通知 → 立即发送
  ├── digest_mode → 加入 digest_queue
  ├── 静默时间 → 加入 delayed_queue
  ├── min_interval 未到 → 加入 delayed_queue
  ├── 延迟策略（delay_days > 0）→ 加入 delayed_queue
  ├── 窗口策略 → 检查是否在窗口内
  └── 无策略 → 立即发送 _send_immediate()
        ↓
每个渠道发送通知 → 记录 delivery_log
```

---

## 过滤条件（filter_conditions）

### 新结构：chains（链路径匹配）

```json
{
  "chains": [
    {
      "chain": ["WEB应用防护系统(WAF)", "V6.0.9", "规则升级包"],
      "match": "leaf"
    },
    {
      "chain": ["网络入侵防护系统(IPS)"],
      "match": "subtree"
    }
  ]
}
```

| match 模式 | 含义 |
|-----------|------|
| `leaf` | snap_chain 必须与 chain 完全相等（精确到具体包类型） |
| `subtree` | snap_chain 以 chain 为前缀（订阅该节点下所有包） |

**subtree 示例**：`["IPS"]` 的 subtree 匹配 `IPS/V5.6.8/规则升级包`、`IPS/V5.6.8/系统升级包` 等所有 IPS 下的包。

chain 的反查通过 `scheduler._get_chain(source_id, source_url)` 实现，从 `content_sources.package_type_discovered.paths` 中查找。

### 旧结构（向后兼容）

```json
{
  "products": ["WEB应用防护系统(WAF)", "网络入侵防护系统(IPS)"],
  "versions": ["V6.0.9"],
  "package_types": ["rule", "nti"],
  "urgency": ["critical", "high"],
  "keywords": ["高危", "漏洞"]
}
```

| 字段 | 匹配方式 |
|------|---------|
| products | 快照 product_name 在列表中 |
| versions | 快照 version_branch 在列表中 |
| package_types | 快照 package_type 在列表中（支持逗号分隔） |
| urgency | 快照 urgency 在列表中（critical/high/normal） |
| keywords | description_raw 中包含任意关键词 |

---

## 推送策略

### 延迟策略（delay_days + delay_strategy）

| 策略 | 行为 |
|------|------|
| `reset`（默认） | 新包到达后重置计时器（默认） |
| `append` | 追加，新包不重置计时器 |
| `window` | 在配置的时间窗口内才推送 |

**delay_days**：延迟天数。delay_days=0 表示立即推送。

**reset 示例**：设置延迟3天 + reset策略 → 如果第2天又来了新包，计时器重置为从第2天起算。

### 静默时间（quiet_start / quiet_end）

在指定时间段内不推送，自动延后到静默结束后：

```python
# 判断逻辑（支持跨越午夜）
def is_quiet_time(rule):
    if quiet_start <= quiet_end:
        return quiet_start <= now <= quiet_end
    else:  # 跨越午夜：22:00 - 08:00
        return now >= quiet_start or now <= quiet_end
```

### 窗口推送（window_config）

仅在工作时间段推送：

```json
{
  "days": [1, 2, 3, 4, 5],    // 周一到周五（0=周一）
  "start": "09:00",
  "end": "18:00"
}
```

### 最小推送间隔（min_interval_hours）

防止同一产品频繁推送。检查 delivery_log 中最近 N 小时内是否有成功推送：

```python
# 示例：min_interval_hours=24
# 24小时内对同一 product_name 有过成功推送 → 跳过本次
```

### 摘要模式（digest_mode）

不立即推送，而是积累到周期结束时统一发送：

| 模式 | 周期 | 发送时机 |
|------|------|---------|
| `weekly` | 周 | 每周一 |
| `monthly` | 月 | 每月1日 |
| `quarterly` | 季度 | 每季度首月1日 |

**digest 流程**：
```
新包到达（is_rollback=False）
        ↓
digest_mode='weekly'
        ↓
enqueue_digest(rule_id, snapshot_id, period_key='2026-W24')
        ↓
周期到期（每周一）→ process_digests()
        ↓
get_digest_snapshots(rule_id, period_key) 读取所有 pending 项
        ↓
过滤已撤回的快照（status != 'active'）
        ↓
检查 is_window_time
        ↓
生成汇总消息（分组列出所有包）
        ↓
_send_digest_split() 发送（长消息自动分片）
        ↓
mark_digest_sent() 标记为已发送
```

---

## 立即发送流程（_send_immediate）

```
_send_immediate(snap, rule, is_rollback=False)
        ↓
NotificationMessage.from_snapshot(snap)
        ↓
get_rule_channels(rule_id) → 获取所有绑定渠道
        ↓
对每个渠道：
  ├── 去重检查：delivery_log 中已有 sent 记录 → 跳过
  ├── 查找对应 NOTIFIER（wecom/dingtalk/feishu/email/apprise）
  ├── 注入配置（channel config + rule 级覆盖）
  ├── 速率限制（IM 渠道间隔 3 秒）
  ├── notifier.send(message, config)
  └── log_delivery() 记录结果
```

### 去重机制

```sql
SELECT id FROM delivery_log
WHERE snapshot_id = ? AND channel_id = ? AND delivery_status = 'sent'
LIMIT 1
```

同一个快照对同一个渠道只发送一次。失败记录不阻塞重发。

### 速率限制

IM 渠道（企业微信/钉钉/飞书）发送间隔至少 3 秒（`MONITOR_RATE_LIMIT_SEC` 环境变量）。

### 长消息分片（摘要模式）

飞书/企业微信对单条消息有字节限制（通常 4000 字节），`_split_text()` 按行拆分：

```python
def _split_text(text, max_bytes=3800):
    # 按行组合，确保不超出 max_bytes
    # 单行超出限制 → 按字节截断
```

分片格式：`({i+1}/{total})\n{内容}`

---

## NotificationMessage 结构

```python
@dataclass
class NotificationMessage:
    product_name: str          # "WEB应用防护系统(WAF)"
    version_branch: str        # "V6.0.9"
    package_type: str          # "rule"
    file_name: str            # "WAF_V6.0.9_规则升级包.tar.gz"
    package_version: str      # "2025061201"
    urgency: str              # "critical" / "high" / "normal"
    published_at: str         # "2026-06-12T09:05:51"
    description: str          # 原始描述文本
    added: list              # 新增规则数
    modified: list           # 修改规则数
    deleted: list            # 删除规则数
    min_sys_version: str     # 最低系统版本
    restart_required: bool   # 是否需要重启
    is_rollback: bool       # 是否为撤回通知
    download_url: str        # 下载链接
    source_url: str          # 详情页链接
    md5_hash: str           # MD5
    file_size: int           # 文件大小（字节）
```

---

## 撤回通知

当快照状态变为 `rollback` 时：

1. `route_notifications(snapshot_id, rule_id, is_rollback=True)` 被调用
2. 直接跳过去重/延迟/digest 等检查
3. 通过 `_send_immediate(snap, rule, is_rollback=True)` 立即发送
4. 消息中包含 `is_rollback=True`，通知器生成特殊撤回消息

---

## 延迟队列处理（process_delayed_queue）

Scheduler 每轮采集结束后调用：

```python
process_delayed_queue()
        ↓
get_due_items() → SELECT * FROM delayed_queue
                  WHERE status='pending' AND push_after <= now
        ↓
对每个到期项：
  ├── 快照状态检查（status != 'active' → 跳过并标记 pushed）
  ├── 规则存在性检查
  ├── 静默时间检查（is_quiet_time → 跳过）
  ├── 窗口时间检查（is_window_time == False → 跳过）
  └── _send_immediate() → 发送后 mark_pushed()
```

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/detector/change.py` | 规则匹配（`get_new_for_subscription`）、策略判断 |
| `src/notifiers/router.py` | 推送路由（`route_notifications`、`_send_immediate`） |
| `src/notifiers/base.py` | 通知消息格式定义 |
| `src/notifiers/wecom.py` | 企业微信发送 |
| `src/notifiers/dingtalk.py` | 钉钉发送 |
| `src/notifiers/feishu.py` | 飞书发送 |
| `src/notifiers/email.py` | 邮件发送 |
| `src/models/subscription.py` | delayed_queue / digest_queue / delivery_log |
