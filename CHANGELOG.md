# CHANGELOG — 绿盟监控项目

每次重要改动完成后追加记录，格式如下：

```markdown
## YYYY-MM-DD — 改动标题

**根因**：问题产生的直接原因

**修复内容**：

| 文件 | 改动 |
|---|---|
| `src/...` | 具体改动说明 |

**逻辑说明**：（可选，复杂改动才写）
- 关键逻辑点1
- 关键逻辑点2

**后续**：未来可改进方向或关联事项
```

---

## 2026-05-24 — 系统事件通知发送失败修复 + 调度器全局开关

**根因**：`get_notify_channel()` 直接查 DB 返回原始记录，`config` 字段是加密字符串而非 dict。传给 `WecomNotifier.send()` 时 `config.get('webhook_url', '')` 返回空字符串，通知静默失败（无错误日志）。

**修复内容**：

| 文件 | 改动 |
|---|---|
| `src/models/event_log.py` | `get_notify_channel()` 改用 `channel.get_by_id()` 查询，拿到解密后的 dict |

**逻辑说明**：
- 订阅规则推送走 `router._send_immediate()` → `get_by_id()` → 解密 config → 正常
- 系统事件通知走 `event_handler.emit_*()` → `get_notify_channel()` → 直接查 DB → 原始加密串 → 失败
- 修复：统一走 `get_by_id()` 解密渠道配置

**调测**：企业微信 channel_id=4，`get_notify_channel()` 返回 `config={'webhook_url': '...'}` ✓；手动 `emit_collection_summary()` 测试通知发送成功，event_log id=5 写入 ✓；修复采集完成通知重发问题：新增 5 分钟防重逻辑，避免重复推送 ✓

---

## 2026-05-24 — 调度器全局开关 + 订阅规则删除根因修复

**背景**：调试时每次重启进程，`start_scheduler()` 强制清除 `collection_running` 导致采集立即重新进行，DB 被 Flask 持 WAL 锁无法连接诊断。

**新增 — 调度器全局开关**：

| 文件 | 改动 |
|---|---|
| `src/core/scheduler.py` | `start_scheduler()` 启动时检查 `scheduler_enabled` 设置，为 `'0'` 时创建空 scheduler 实例（无 job）；`get_status()` 返回 `enabled` 字段 |
| `src/web/templates/index.html` | 系统设置 → 采集设置区加 toggle 开关，默认开启；`saveSettings()` 保存 `scheduler_enabled` |

**根因 — 订阅规则删除 500**：`delivery_log` 表有 `rule_id INTEGER REFERENCES subscription_rules(id)` FK 约束，`delete_rule()` 遗漏清理 37 条关联记录，导致 FK 约束失败。

| 文件 | 改动 |
|---|---|
| `src/models/subscription.py` | `delete_rule()` 增加 `DELETE FROM delivery_log WHERE rule_id = ?` |

**用途**：维护调试期间关闭采集开关 → DB 锁释放 → 可正常连接诊断。诊断完毕后重新开启。

---

## 2026-05-24 — 采集完成通知（无新包时也发送）

**根因**：`_collect_quick` 的 dedup safety net 将所有 items 过滤掉时，`all_items` 为空 → scheduler 走 early return → `emit_collection_summary` 永不调用。导致无新包时完全没有结束信号。

**修复内容**：

| 文件 | 改动 |
|---|---|
| `src/core/scheduler.py` | early return 前调用 `emit_collection_summary`，确保每次采集都发通知；`except` 块同样处理 |

## 2026-05-24 — 订阅规则删除诊断埋点

**背景**：valid_until 应由用户在订阅规则页面独立设置，不应隐式关联客户维保期。简化设计：客户管理删除维保字段，订阅规则基本信息展示客户邮箱 + 独立有效期至。

**订阅规则改动**：

| 文件 | 改动 |
|---|---|
| `src/web/templates/index.html` | 基本信息区：移除旧维保提示，添加客户邮箱只读展示（📧 客户邮箱）、有效期至移入基本信息（placeholder="不限制"）；推送配置区：删除原 valid_until 行；`onCustChange()` 改为展示客户邮箱而非同步维保日期 |

**客户管理改动**：

| 文件 | 改动 |
|---|---|
| `src/web/templates/index.html` | 列表删除"维保"列（colspan 7→5）；弹窗表单删除维保开始/截止行；`openCModal()` 删除 ws/we 参数；`saveC()` 删除 warranty 字段 |
| `src/models/customer.py` | `create()` 删除 warranty_start/warranty_end 字段引用 |

**语义**：
- `valid_until` 为空 = 不限制
- `valid_until` 有值 = 到期后规则不触发推送
- 客户邮箱只读展示，不参与任何逻辑

---

## 2026-05-23 — 即时模式 delay 字段单位改为天(d)

**背景**：即时推送模式延迟字段原单位为小时(h)，业务上用于"等几天确认无负面反馈再推"，改为天(d)更直观。

**改动**：

| 文件 | 改动 |
|---|---|
| `src/web/templates/index.html` | label `延迟(h)` → `延迟(d)`；tooltip 更新为"观察 N 天"；`saveRule` 字段名 `delay_hours` → `delay_days`；编辑分支兼容旧 `delay_hours` 字段；即时模式移除窗口策略相关校验和收集逻辑 |
| `src/detector/change.py` | `compute_push_time(delay_hours)` → `compute_push_time(delay_days)`，`timedelta(hours=)` → `timedelta(days=)` |
| `src/notifiers/router.py` | `delay_hours` → `delay_days`；`has_window` 从检查 `days` 存在改为检查 `start && end`（仅汇总模式）；日志改为 `{delay_days}d` |
| `src/models/subscription.py` | schema `delay_hours` → `delay_days`；`create_rule` 字段同步；迁移添加 `delay_days` 列，从 `delay_hours / 24` 迁移旧数据 |

**DB 迁移**：`ALTER TABLE subscription_rules ADD COLUMN delay_days`，旧数据按 `delay_hours / 24` 换算（小时转天，向下取整）。

**语义**：
- 即时模式：仅 `delay_days`，无窗口策略
- 汇总模式：有窗口策略（时间段），无 delay

---

## 2026-05-23 — P1-1 精简订阅规则推送配置：移除死代码字段

**背景**：即时推送模式（delay=0）下，策略（reset/append）和最小间隔两个字段为死代码；汇总模式下最小间隔从未生效。窗口策略独立为 checkbox，与推送模式解耦。

**UI 变更**：
- 移除策略 select（`rst`）和最小间隔输入（`rin`）
- 延迟(h) 独占一行
- 窗口策略改为独立 checkbox「⏰ 启用推送时间窗口」，勾选后展开配置
- 重置/编辑分支：窗口配置恢复逻辑从 `delay_strategy='window'` 改为 `window_config` 存在性判断

**后端变更**：
- `router.py`：`has_window` 判断从 `delay_strategy=='window'` 改为 `window_config` 存在性 + `delay_strategy=='window'` 兜底（兼容旧数据）
- `change.py`：`compute_next_window_push_time` 新增旧数据兼容；`is_window_time` 对 `days=[]`（汇总模式）退化为仅时间检查，`compute_next_window_push_time` 对 `days=[]` 找今天/明天窗口开启时刻

**字段清理**：`delay_strategy`（reset/append/window）和 `min_interval_hours` 不再通过 UI 配置，但 DB 列保留（向后兼容旧数据）。

**窗口策略行为**：
- 即时模式：无窗口策略（已移除）
- 汇总模式：只限制时间段（周几由汇总周期决定，如每周日），`days=[]` 后端解释为仅时间限制

**delay 语义**：即时模式下 delay 独立计时，新包不取消旧计时器，每个包各自等待 delay 后发送。

---

## 2026-05-23 — P1-3 valid_until 过期规则不触发推送

- `get_new_for_subscription` 入口处加 `valid_until` 时间校验
- 有值且已过期：返回空列表，跳过匹配
- 有值但格式错误 / 空值：视为不限制
- 调测：过期规则不匹配任何新包

---

## 2026-05-23 — P1-2 包撤回时取消delay/digest队列

**三处防线**：

|| 位置 | 逻辑 |
|---|---|---|
| `snapshot.py::mark_rollback_pending` | 快照进入 rollback_pending 时，立即取消该 snapshot 在 delayed_queue 和 digest_queue 中的所有待处理条目 |
| `router.py::process_delayed_queue` | 重放时二次检查 snapshot 状态，非 active 则跳过并 mark_pushed |
| `router.py::process_digests` | 发送前逐个检查 snapshot_status，非 active 则 cancel_digest_item 并从列表移除 |

**schema 变更**：`digest_queue.status` CHECK 新增 `'cancelled'`（原有 `'pending'|'sent'`）。

**涉及文件**：`src/models/snapshot.py`、`src/models/subscription.py`（+cancel_digest_for_snapshot/cancel_digest_item）、`src/notifiers/router.py`。

---

## 2026-05-21 — 订阅规则支持链路径匹配（精确到产品→子分类→版本→包类型）

**背景**：原 `filter_conditions` 为扁平三字段（products/versions/package_types），三个维度相互独立，无法精确表达层次结构。

**落地实现**（commit a0d781e / 7f4dfda / b1463e1）：

|| 组件 | 改动 |
|---|---|---|
| `src/core/scheduler.py` | 启动时构建 `_url_chain_cache: dict[source_id, dict norm_url → chain]`，运行时 `_get_chain(source_id, source_url)` 从缓存反查 chain |
| `src/detector/change.py` | 新增 `_chain_matches(snap_chain, rule_chains)`；`get_new_for_subscription` 支持 chains 结构匹配；旧扁平结构维持向后兼容 |
| `src/web/templates/index.html` | 订阅条件 UI 重写为树形选择器（复用 `buildDataTreeHtml`），支持 leaf/subtree 两种匹配模式；`_condChains` 统一为 `window` 全局变量解决 scope 问题 |

**`filter_conditions` 新结构**：

```json
{
  "chains": [
    { "chain": ["WAF", "标准正式版", "V6.0.8", "规则"], "match": "leaf" },
    { "chain": ["IPS", "V6.0.9"], "match": "subtree" }
  ]
}
```

**匹配语义**：
- `leaf`：snap_chain 与 rule_chain 完全相等（订阅具体包类型）
- `subtree`：snap_chain 以 rule_chain 为前缀（订阅中间节点下全部）

**不新增 snapshot 列**：通过 `source_url` 在 `content_sources.package_type.paths[]` 中反查 chain，运行时解决，无需 schema 改动。

---

## 2026-05-21 — 修复重复推送问题

**根因**：rule 11（绿盟企业微信）和 rule 13（绿盟企业微信通知）同时绑定企业微信渠道，且 `filter_conditions={}` 均匹配全部产品，导致同一 snapshot 发往同一 channel 两次。

**修复内容**：

| 文件 | 改动 |
|---|---|
| `src/models/subscription.py` | Schema 加 `rule_id` 列；`log_delivery()` 加 `rule_id` 参数 |
| `src/notifiers/router.py` | `_send_immediate()` 加去重检查；两处 `log_delivery` 传入 `rule_id` |
| `data/nsfocus_monitor.db` | delivery_log 表加 `rule_id` 列 |

**去重逻辑**（`_send_immediate` 入口）：
- 已有 **sent** 记录 → 跳过（不重复推送）
- 只有 **failed** 记录 → 继续发送（rate limit 瞬时失败不阻塞重试）

**后续**：rule_id 可追溯推送来源规则

---

## 2026-05-21 — process_delayed_queue 健壮性改进；CHANGELOG 模板固化

**问题**：`process_delayed_queue` 中若 `get_snapshot(item['snapshot_id'])` 返回 `None`，会把 `None` 传给 `_send_immediate`，可能导致后续异常，且队列项不会被 mark_pushed，造成积压。

**修复内容**：

| 文件 | 改动 |
|---|---|
| `src/notifiers/router.py` | 取 snapshot 失败时 mark_pushed 并 continue，避免空快照进入发送流程 |

**逻辑**：
- snapshot 不存在 → mark_pushed 清理队列项，继续下一个
- snapshot 存在 → 正常走 `_send_immediate` 去重检查

## 2026-05-23 — 订阅规则参数冲突校验与窗口策略UI

**背景**：延迟/策略/最小间隔三参数存在语义歧义和组合冲突，用户未配置时间窗口时选了窗口策略会导致功能完全失效。

**改动**：

| 文件 | 改动 |
|---|---|
| `src/web/templates/index.html` | 新增时间窗口配置面板（周几+时间段）；新增 validateRuleConf() 冲突校验；新增 onStrategyChange() 控制窗口面板显隐；编辑/重置时恢复窗口配置；saveRule() 收集 window_config 写入请求体 |

**设计决策**：
- 策略=窗口 + 未配置周几或时间段 → **硬拦截**，toast 报错，禁止保存
- 延迟>0 + 汇总模式 → **警告提示**（confirm 对话框），用户确认后可继续
- 窗口配置数据结构：`{days: [1,2,3,4,5], start: "09:00", end: "18:00"}`（days 为 0-6 数字数组）

**校验逻辑**（validateRuleConf）：
```
if 策略==窗口 and (未选周几 or 未配时间段) → block
if 延迟>0 and 汇总模式 != '' → warn
else → valid
```

## 2026-05-23 — P0 推送逻辑严重问题修复

**根因**：路由逻辑存在 4 处设计缺陷，导致回滚通知不过滤订阅条件、窗口策略不生效、延迟队列渠道override失效、静默期两套实现冲突。

**改动**：

| 文件 | 改动 |
|---|---|
| `src/core/scheduler.py` | 回滚通知增加 `get_new_for_subscription` 订阅条件过滤，与普通检测逻辑一致 |
| `src/notifiers/router.py` | `process_delayed_queue` 改用 `get_rule()` 查询真实 rule 对象，修复 customer_emails/attachment_max_mb override 失效；移除 `_is_quiet_hours()` 全局静默函数（与规则级 quiet_time 冲突），统一走规则级 `is_quiet_time(rule)` |
| `src/detector/change.py` | 新增 `is_window_time(rule)` 和 `compute_next_window_push_time(rule)`；`route_notifications` 对策略=window 在窗口外时 enqueue 到下一个窗口开启时刻 |
| `src/models/subscription.py` | `subscription_rules` 表增加 `window_config` TEXT 列；`create_rule` 支持 window_config 字段；`_parse_rule` 解析 window_config JSON |

**设计说明**：
- 静默期统一为**规则级别**（`quiet_start`/`quiet_end`），不再从 system_settings 全局读取。全局静默期概念移除。
- 窗口策略语义：当前在窗口内 → 立即发送；当前在窗口外 → 入 delayed_queue，等下一个窗口开启时刻触发。
- `process_delayed_queue` 重放时不再跳过多层检查（直接查真实 rule），保证渠道override/附件限制生效。

## 2026-05-23 — 展开收缩再次展开时API缓存导致字段空白

**根因**：detail DOM 收缩时被移除，但 `_snapDetailCache[snapId]` 未清除。再次展开时直接复用缓存 promise（列表数据，无 md5/file_size/page_hash 字段），且 promise resolved 后 DOM 已不存在无法写入。

**修复内容**：
- `index.html` 行 1225：collapse 时 `delete _snapDetailCache[snapId]`

**影响**：仅影响"展开→收缩→再次展开"场景，首次展开正常。

---

## 2026-05-23 — 汇总模式隐藏延迟字段

**背景**：延迟在即时推送模式下有意义（安全观察期），汇总模式下 delay 字段无语义（被后端完全忽略）。原实现隐藏了逻辑但给了误报警告，用户困惑。

**改动**：
- 延迟字段包 `delayWrap`，`onDigestModeChange()` 在汇总模式下 `classList.add('hidden')`
- `validateRuleConf()` 移除 delay+digest 警告（汇总模式已无法配置延迟，warn 永不触发）
- `saveRule()` 移除 warn confirm 逻辑
- tooltip 文案更新为："检测到新版本后，观察 N 小时确认无问题再推送（仅即时推送模式）"
- 重置/编辑分支末尾调用 `onDigestModeChange()` 确保初始化状态一致

## 2026-05-24 — 客户名称唯一性校验

**根因**：客户名称字段未校验重复，数据库虽有 UNIQUE 约束但 API 层无友好提示，重复插入时直接暴露 constraint violation。

**修复内容**：

| 文件 | 改动 |
|---|---|
| `src/web/routes/api_routes.py` | `create_customer()` 新增 name 查重，存在则返回 40001 "客户名称已存在"；`update_customer()` 同样校验（排除自身） |
| `data/nsfocus_monitor.db` | 清理测试产生的重复数据（id=12/13/15/16） |

**逻辑说明**：
- 数据库层面已有 NOT NULL + 隐式 UNIQUE 约束，修复的是 API 层友好提示
- 查询使用 `SELECT id FROM customers WHERE name = ?`，返回非空即重复

**后续**：无

## 2026-05-24 — DELETE /customers/<id> 尾部斜杠 404

**根因**：Flask strict_slashes 对参数化 DELETE 路由（`/api/customers/<int:cid>`）不自动重定向尾部斜杠，`curl -X DELETE /customers/11/` 触发 404。

**修复内容**：

| 文件 | 改动 |
|---|---|
| `src/web/routes/api_routes.py` | 新增 `@bp_customers.route('/<int:cid>/', methods=['DELETE'], strict_slashes=False)` 显式支持尾部斜杠 |

**逻辑说明**：
- 单独路由 `strict_slashes=False` 覆盖 Blueprint 层级的 strict_slashes=True
- 同时保留 `/<int:cid>` 路由（无尾部斜杠），两条路由指向同一函数

**后续**：无

## 2026-05-25 — 采集汇总通知优化

**根因**：通知内容未经过 `_format_markdown_body`，直接显示原始空字段行，且时间未做时区转换

**修复内容**：
- `_format_markdown_body`/`_format_markdown_bodies` 新增 `skip_empty_meta` 参数，为空时跳过元数据行
- 企业微信渠道对系统通知直接发送纯文本（`description_full`）
- session_error/log_error 通知时间改用 `_utc_to_cst_display()` 转 CST 时区
- 钉钉/飞书/apprise 等渠道同步受益于 `skip_empty_meta`

**后续**：无
