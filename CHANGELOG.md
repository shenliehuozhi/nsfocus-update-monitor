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

## 2026-05-27 — 订阅规则条件细化：支持链路径匹配（方案设计）

**根因**：现有 `filter_conditions` 为扁平三字段（products/versions/package_types），三个维度相互独立，无法精确表达"产品 → 子分类 → 版本 → 包类型"的层次结构，导致订阅粒度不可控。

**设计目标**：支持用户精确选择 `产品 > 子分类 > 版本 > 包类型` 的完整路径，或选中中间节点订阅其下全部。

**设计方案**：

| 组件 | 改动 |
|------|------|
| `src/core/scheduler.py` | 启动时构建 `_url_chain_cache: dict[source_id, dict norm_url → chain]`，运行时提供 `_get_chain(source_id, source_url)` 从缓存反查 chain |
| `src/detector/change.py` | 重写 `get_new_for_subscription`，用 `_get_chain()` 反查 chain 后按新结构匹配；新增 `_chain_matches(snap_chain, rule_chains)` |
| `src/web/templates/index.html` | 订阅条件 UI 重写为树形选择器（复用 `buildDataTreeHtml`），支持 leaf/subtree 两种匹配模式 |

**`filter_conditions` 新结构**：

```json
{
  "chains": [
    { "chain": ["WAF", "标准正式版", "V6.0.8", "规则"], "match": "leaf" },
    { "chain": ["IPS", "V6.0.9"], "match": "subtree" }
  ],
  "keywords": ["漏洞"],
  "urgency": ["high"]
}
```

**关键设计决策**：
- **不新增 chain 列**：snapshot 通过 `source_url` 在 `content_sources.package_type.paths[]` 中反查 chain，运行时解决，无需 schema 改动
- **内存缓存**：scheduler 启动时加载所有 source 的 package_type 到 `_url_chain_cache`，避免每次匹配都查 DB
- **复用产品管理树**：`buildDataTreeHtml(pt, snapshotsMap, MAP, verTypeMap, alias, sid)` 直接复用，节点点击改为 toggle 选中而非过滤右侧面板
- **不破坏现有数据**：旧规则（扁平结构）可继续使用，迁移策略待定

**逻辑说明**：
- `_chain_matches`：`leaf` 模式要求 snap_chain 与 rule_chain 完全相等；`subtree` 模式要求 snap_chain 以 rule_chain 为前缀
- 匹配优先级：chain 过滤 → keywords → urgency，三者同时满足才推送

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

