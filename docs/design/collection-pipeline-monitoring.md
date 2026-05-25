# 采集链路全链路可视化监控

**状态**: 待办
**创建**: 2026-05-25
**背景**: 2026-05-25 database is locked 排查过程中发现，采集链路各 phase 缺乏独立计时和写入统计，导致问题根因难以快速定位。

## 需求背景

采集任务经历 10 个 phase，部分 phase 频繁写库（尤其是 Phase 3 采集）。出现问题时，日志碎片化，无法快速判断：
- 耗时在哪个 phase
- 哪个 phase 的 DB 写入量异常
- 是否有多进程并发导致锁竞争

## 目标

1. **可视化**：前端实时展示各 phase 状态、耗时、进度
2. **可排查**：每个 phase 的 DB 写入计数、数据量、异常记录独立记录
3. **可回溯**：保留最近 N 次采集的 phase summary，支持历史对比

## Phase 梳理（完整链路时序）

| # | Phase 名称 | 说明 | 典型耗时 | DB 写操作 |
|---|-----------|------|---------|---------|
| 0 | `startup` | 防重入检查、内存锁、collection_running 初始化 | <100ms | `system_settings`（1次） |
| 1 | `session_check` | 获取 cookie、预校验 session 有效性 | <1s | `sessions`（过期时才写） |
| 2 | `source_bootstrap` | 新增产品数据源补录 | <1s | `content_sources`（偶尔） |
| 3 | `collection` | 遍历 78 个产品抓取页面（quick/full） | 数分钟~数十分钟 | 高频：健康度更新、批量 touch、逐包写入 snapshots |
| 4 | `detection` | per-product 变更检测（新增/回滚） | 秒级 | `snapshots` 新增/回滚写入 |
| 5 | `notification` | per 规则过滤 + 路由通知 | 秒级~分钟 | `delivery_records` |
| 6 | `delayed_queue` | 处理延迟推送队列 | 秒级 | 更新 `delivery_records` 状态 |
| 7 | `pkg_type_refresh` | 包类型刷新（仅 full 模式） | 分钟级 | 更新 `package_type_discovered` |
| 8 | `push_summary` | 推送结果汇总 | 秒级 | 少量统计写入 |
| 9 | `system_event` | 系统事件通知 | 秒级 | `system_events` |
| 10 | `finish` | 清理 collection_running、更新 last_run | <1s | `system_settings`（1次） |

## 实现方案（待细化）

### 数据结构

在 `system_settings` 或新建 `collection_runs` 表：

```sql
CREATE TABLE collection_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT NOT NULL,           -- 'quick' | 'delta' | 'full'
  started_at TEXT NOT NULL,     -- ISO UTC
  finished_at TEXT,            -- ISO UTC
  status TEXT NOT NULL,         -- 'running' | 'ok' | 'warning' | 'error'
  total_new INTEGER DEFAULT 0,
  total_rollback INTEGER DEFAULT 0,
  phases TEXT,                  -- JSON: [{name, status, started_at, finished_at, duration_ms, writes, error}, ...]
  product_timings TEXT,         -- JSON: [{name, duration_ms, new_count, error}, ...]
  errors TEXT,                  -- JSON: [{phase, msg}, ...]
  UNIQUE(mode, started_at)
);
```

### phases JSON 结构

```json
[
  {
    "name": "session_check",
    "status": "done",
    "started_at": "2026-05-25T12:34:16.000Z",
    "finished_at": "2026-05-25T12:34:16.120Z",
    "duration_ms": 120,
    "writes": { "tables_updated": 0, "rows_written": 0 },
    "error": null
  },
  {
    "name": "collection",
    "status": "done",
    "started_at": "2026-05-25T12:34:16.120Z",
    "finished_at": "2026-05-25T12:38:45.000Z",
    "duration_ms": 228880,
    "writes": { "tables_updated": 3, "rows_written": 156 },
    "error": null
  },
  {
    "name": "detection",
    "status": "running",
    "started_at": "2026-05-25T12:38:45.000Z",
    "finished_at": null,
    "duration_ms": 0,
    "writes": { "tables_updated": 0, "rows_written": 0 },
    "error": null
  }
]
```

### API 扩展

```
GET  /api/collect/progress       → 返回 phases + 当前 phase + product_timings（已有字段扩展）
GET  /api/collect/history       → 最近 N 次采集的 summary（分页）
GET  /api/collect/history/<id> → 某次采集的完整 phases + product_timings
```

### 前端展示（待确认）

**方案 A**：现有"查看"弹窗扩展
- 在 `pollProgress()` 弹窗中用时序图/甘特图展示各 phase
- 显示 duration + 写入量 + 错误

**方案 B**：独立监控面板（Dashboard 页面）
- 甘特图展示最近 5 次采集的 phase 耗时对比
- 每个 phase 独立柱状图

**确认项**：
- [ ] 在哪里展示（弹窗内 vs 独立面板）
- [ ] 历史记录保留条数（建议 20 条）
- [ ] 是否需要实时写入统计（每个 phase 结束后写入，非最终汇总）

## 四、分散式采集调度（均匀分散扫描）

### 4.1 问题

**当前模式**：定时任务触发 → 一次性遍历全部803 URL → 峰值~40 req/s → 网关/目标服务器压力大

**用户需求**：把803 URL的请求均匀分散到整个采集周期（4小时）内，避免瞬时峰值。

---

### 4.2 分析

#### 问题建模

```
采集周期 T = 4小时 = 14400秒
URL总量 N = 803
请求数/URL = 2（HEAD + GET）
理论均匀速率 = 803 × 2 / 14400 ≈ 0.11 req/s（约7秒1个请求）
```

#### 关键约束

| 约束 | 影响 |
|------|------|
| **推送实时性** | 用户期望有新包立即通知，不能因"均匀"而引入额外延迟 |
| **订阅漏检** | 每个URL必须在采集周期内至少被扫一次，否则该URL下的新包会漏检 |
| **状态一致性** | scan_round跨批次必须一致，否则URL状态（prob/skip）会乱 |
| **中断恢复** | 进程重启后必须能从上次中断位置继续，不能重复扫已扫URL |
| **full_scan** | 约30天一次的全量扫描必须确保所有URL都被扫到 |

#### 核心矛盾

```
理想均匀：每7秒1个请求 → 推送延迟最多7秒 ✓
但：4小时内必须扫完803 URL → 每个URL最少扫1次 ✓

→ 均匀分散完全可行，不牺牲任何实时性
```

---

### 4.3 设计方案

#### 核心思想

将**一次性批量扫描**改为**持续分片扫描**：把采集周期划分为多个时间片，每个时间片只处理一部分URL，下一个采集周期再处理下一批。

#### 数据结构

**新增表 1: `collection_urls`（URL级扫描状态）**

```sql
CREATE TABLE collection_urls (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,           -- FK → content_sources
    url TEXT NOT NULL,                    -- 完整URL
    url_hash TEXT NOT NULL,               -- md5(url) 索引键
    priority INTEGER DEFAULT 50,          -- 优先级 1-100（高优先级先扫）
    scan_bucket INTEGER DEFAULT 0,        -- 分桶号 0-59（每小时60桶，共4小时60桶）
    last_scan_at TEXT,                    -- 上次扫描时间（UTC）
    last_scan_round INTEGER DEFAULT 0,   -- 上次扫描的轮次编号
    scan_count INTEGER DEFAULT 0,         -- 累计扫描次数
    skip_count INTEGER DEFAULT 0,         -- 连续跳过次数
    created_at TEXT,
    FOREIGN KEY (source_id) REFERENCES content_sources(id),
    UNIQUE(url_hash)
);
```

**新增表 2: `collection_scan_rounds`（轮次状态）**

```sql
CREATE TABLE collection_scan_rounds (
    round_id INTEGER PRIMARY KEY,         -- 全局递增轮次ID
    mode TEXT NOT NULL,                   -- 'quick' | 'full'
    started_at TEXT NOT NULL,             -- 轮次开始时间（UTC）
    finished_at TEXT,                     -- 轮次结束时间（UTC，未完成则为NULL）
    total_urls INTEGER DEFAULT 0,         -- 该轮总分片数
    completed_urls INTEGER DEFAULT 0,     -- 已完成URL数
    status TEXT DEFAULT 'running',        -- 'running' | 'completed' | 'aborted'
    created_at TEXT DEFAULT (datetime('now'))
);
```

**新增表 3: `collection_scan_slices`（分片进度）**

```sql
CREATE TABLE collection_scan_slices (
    id INTEGER PRIMARY KEY,
    round_id INTEGER NOT NULL,            -- FK → collection_scan_rounds
    bucket INTEGER NOT NULL,              -- 时间桶号 0-59
    started_at TEXT,                      -- 分片开始时间
    finished_at TEXT,                      -- 分片结束时间
    url_count INTEGER DEFAULT 0,          -- 该桶内URL数
    completed INTEGER DEFAULT 0,         -- 已完成数
    status TEXT DEFAULT 'pending',        -- 'pending' | 'running' | 'completed' | 'skipped'
    UNIQUE(round_id, bucket)
);
```

**新增表 4: `collection_checkpoints`（中断恢复点）**

```sql
CREATE TABLE collection_checkpoints (
    round_id INTEGER PRIMARY KEY,
    last_bucket INTEGER DEFAULT -1,      -- 上次处理到哪个桶
    last_url_hash TEXT,                   -- 桶内处理到哪个URL
    bucket_progress REAL DEFAULT 0.0,    -- 桶内完成百分比
    updated_at TEXT
);
```

#### 分散策略设计

**方案 A: 桶轮转（Bucket Rotation）— 推荐**

```
1. 将803个URL按 (source_id, product_name) 分组，每组分配到60个桶
2. 每个桶 = 约13-14个URL（803/60 ≈ 13.4）
3. 每5分钟（300秒）触发一次调度，取一个桶的URL进行采集
4. 4小时周期 × 12次触发 = 60桶全部覆盖
5. 下一个4小时周期：桶内URL按scan_round轮转，确保每桶URL不重复
```

**核心逻辑**：

```python
# 调度器每5分钟触发一次
def schedule_tick():
    current_bucket = get_current_bucket()  # 基于时间计算
    urls = get_urls_in_bucket(current_bucket)
    for url in urls:
        scan_single_url(url)

def get_current_bucket() -> int:
    """每5分钟一个桶，0-59循环"""
    elapsed = (now_utc() - cycle_start).total_seconds()
    return int(elapsed // 300) % 60
```

**方案 B: 优先级队列（Priority Queue）**

```
1. 所有URL按 priority 排序
2. 每5分钟取最高优先级的N个URL扫描
3. 高优先级URL会更快被扫描，低优先级可以延迟
4. 动态调整：推送频率高的URL自动升高priority
```

**方案 C: 均匀哈希（Consistent Hashing）— 兼容存量数据**

```
1. 对803个URL做哈希，均匀分配到60个桶
2. 每个桶内URL固定（基于url_hash % 60）
3. 每5分钟扫一个桶，下个周期继续扫下一个桶
4. 优点：URL分布稳定，可以预计算
5. 缺点：桶内URL数不均衡（有的桶14个，有的12个）
```

**推荐方案 A（桶轮转）**：分组均匀，优先级可调，容错性好。

#### 关键设计细节

**URL优先级计算**：

```python
def calc_url_priority(url_record: dict) -> int:
    """
    优先级 1-100，数值越大越优先
    """
    # 近期有新包的URL优先级升高
    if url_record['change_recency'] < 7:
        return 100
    elif url_record['change_recency'] < 30:
        return 80
    elif url_record['change_recency'] < 90:
        return 60
    elif url_record['never_delivered']:
        return 50  # 未推送过但也不能降太低
    elif url_record['age_days'] > 365:
        return 20  # 1年以上旧URL最低优先级
    else:
        return 40
```

**桶分配算法**：

```python
def assign_buckets(urls: list, total_buckets=60):
    """
    按product分组，然后轮转分配到不同桶
    确保同一产品的URL分散到不同桶（避免单桶请求集中）
    """
    # 按product_name分组
    groups = {}
    for url in urls:
        product = url['product_name']
        if product not in groups:
            groups[product] = []
        groups[product].append(url)

    # 每个组内URL轮转分配到60桶
    buckets = [[] for _ in range(total_buckets)]
    bucket_idx = 0
    for product, group_urls in groups.items():
        for url in group_urls:
            buckets[bucket_idx % total_buckets].append(url)
            bucket_idx += 1

    return buckets
```

**中断恢复**：

```python
def resume_from_checkpoint(round_id: int):
    """中断后从checkpoint恢复"""
    ckpt = query("SELECT * FROM collection_checkpoints WHERE round_id=?", (round_id,))
    if not ckpt:
        return 0  # 无checkpoint，从桶0开始

    last_bucket = ckpt['last_bucket']
    last_url = ckpt['last_url_hash']

    # 从上次位置继续
    remaining = query("""
        SELECT * FROM collection_urls
        WHERE scan_bucket > ? OR (scan_bucket = ? AND url_hash > ?)
        ORDER BY scan_bucket, url_hash
    """, (last_bucket, last_bucket, last_url))

    return len(remaining)
```

**漏检防止机制**：

```python
def ensure_coverage(round_id: int):
    """
    确保所有URL在本轮都被扫到
    轮次结束时检查：如果某桶未完成，补扫
    """
    incomplete = query("""
        SELECT bucket FROM collection_scan_slices
        WHERE round_id=? AND status != 'completed'
    """, (round_id,))

    for bucket in incomplete:
        logger.warning(f'补扫 bucket {bucket}')
        urls = get_urls_in_bucket(bucket)
        scan_bucket_urls(urls)
```

---

### 4.4 优缺点对比

#### 方案 A（桶轮转）

| 维度 | 优点 | 缺点 |
|------|------|------|
| **QPS降低** | 803 URL / 14400s = 0.11 req/s（降低99.7%） | 网络延迟抖动可能导致某个桶超时 |
| **实时性** | 最多延迟7秒（下一桶触发间隔） | 全量推送场景下无影响 |
| **公平性** | 同组URL均匀分散到各桶 | 不同product的URL数不同，桶大小不均 |
| **容错性** | 单桶失败不影响其他桶 | 某桶失败需补扫逻辑 |
| **实现复杂度** | 中等：需改scheduler调度逻辑 | 需新增4张表，迁移现有URL数据 |
| **中断恢复** | checkpoint精确恢复 | 需维护checkpoint写入 |

#### 方案 B（优先级队列）

| 维度 | 优点 | 缺点 |
|------|------|------|
| **QPS降低** | 同上 | 同上 |
| **实时性** | 高优先级URL更快被扫 | 低优先级URL可能轮转周期长 |
| **公平性** | 按业务价值分配扫描资源 | 可能导致部分URL长期得不到扫描 |
| **复杂度** | 高：需维护优先级队列状态 | 队列状态跨进程需持久化 |

#### 方案 C（均匀哈希）

| 维度 | 优点 | 缺点 |
|------|------|
| **QPS降低** | 同上 | 同上 |
| **简单性** | 最简单：哈希固定，无状态 | URL分布固定，无法动态调整 |
| **均衡性** | 哈希本身均匀 | 桶大小略有差异（~1个URL） |
| **容错性** | 失败直接重扫该桶 | 无优先级概念 |

---

### 4.5 关键问题与解答

#### Q1: 分散后推送实时性是否受影响？

**不受影响**。新包发现后的推送逻辑不变：
- 采集过程中发现新包 → 立即写入DB → 触发检测 → 立即推送
- 分散只是拉长采集过程，不是延迟推送

推送触发点是"发现新包时"，不是"采集结束时"。

#### Q2: 进程重启后如何保证URL不漏检/重复检？

**三层防护**：
1. `collection_scan_rounds`记录轮次状态，重启后查询该轮是否完成
2. `collection_checkpoints`精确记录中断位置（round_id + bucket + url_hash）
3. 轮次结束前 `ensure_coverage()` 补扫所有未完成的桶

#### Q3: 4小时周期内，某个URL被扫多次还是只扫一次？

**每个URL每轮只扫一次**。分散的是"什么时候扫"，不是"扫几次"：
- scan_bucket 决定该URL在本轮哪个时间片被扫
- scan_count 每轮只+1

#### Q4: full_scan（全量扫描）如何处理？

**full_scan 强制扫描所有桶**，跳过采样逻辑：
- `scan_round.full_mode = True` 时，所有URL都必须扫
- 仍使用桶分散，但每个桶都必须完成
- 完成后 `finished_at` 记录，标记本轮结束

#### Q5: 如何避免同一产品URL集中在同一桶（导致某时刻请求量突增）？

**分组轮转分配**：
- 按 product 分组，组内URL轮转分配到60桶
- 同一产品的URL分散到不同桶，避免请求集中

---

### 4.6 迁移计划

#### Phase 1: 数据准备（不影响现有逻辑）
```sql
-- 新增4张表
-- 运行一次迁移脚本，把现有803 URL分配到60桶
-- 初始化 first_scan_round 和 bucket_index

ALTER TABLE collection_urls ADD COLUMN scan_bucket INTEGER DEFAULT 0;
ALTER TABLE collection_urls ADD COLUMN priority INTEGER DEFAULT 50;
ALTER TABLE collection_urls ADD COLUMN last_scan_at TEXT;
ALTER TABLE collection_urls ADD COLUMN scan_count INTEGER DEFAULT 0;
```

#### Phase 2: 调度改造（新增分片调度器）
```python
# 新增 scheduler_tick() 函数
# 每5分钟触发一次，处理一个bucket
# 兼容现有 run_now() 调用：首次调用时初始化轮次，后续调用处理分片
```

#### Phase 3: 废弃旧逻辑
```python
# 当 collection_scan_rounds 表有活跃轮次时，
# run_now() 不再直接遍历所有URL，而是调用分片处理器
```

---

### 4.7 预期效果

| 指标 | 改造前 | 改造后 |
|------|--------|--------|
| 峰值QPS | ~40 req/s | ~0.3 req/s（每7秒1个请求） |
| 平均QPS | 803×2/1200s ≈ 1.3 req/s | 803×2/14400s ≈ 0.11 req/s |
| 推送实时性 | 立即 | 立即（不受影响） |
| 中断恢复 | 无 | 精确到URL级别 |
| 扫描公平性 | 不均衡（有的URL扫多次，有的少） | 均匀（每URL每轮一次） |

### 4.8 联合优化：桶轮转 × 采样v2

#### 4.8.1 叠加效果

采样v2（策略2）和桶轮转（策略4）是**正交优化**，作用在不同维度，叠加后效果：

```
改造前（一次性全扫，无采样）：
    803 URL × 2 req/URL = 1606 req/轮

桶轮转（每5分钟1桶，全量扫桶内URL）：
    803 × 2 / 60 桶 = ~27 req/轮（每桶扫完）

桶轮转 + 采样v2（50% skip）：
    27 × 50% ≈ ~14 req/轮
```

**叠加后从1606降至~14 req/轮**，减少99%+，且峰值QPS从~40降至~0.3。

#### 4.8.2 联合决策流程

```
scheduler_tick() 每5分钟触发一次
    ↓
获取当前桶的所有URL（约13个）
    ↓
遍历桶内URL：
    ↓
    ┌─ must_scan_now() == True → 必须扫（跳过采样判断）
    ├─ prob >= random() → 扫
    └─ prob < random() → skip（记录skip_count）
    ↓
扫的URL → 发HTTP请求（HEAD + 可选GET）
skip的URL → 更新skip_count、prob
    ↓
完成当前桶 → 更新 checkpoint
    ↓
发现新包 → 立即触发检测+推送（不受桶节奏影响）
```

#### 4.8.3 联合状态表设计

采样v2状态 + 桶轮转状态**共存于同一张表**，避免两套状态不同步：

```sql
CREATE TABLE collection_urls (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,           -- FK → content_sources
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL UNIQUE,        -- md5(url)

    -- ── 桶轮转相关（策略4）───────────────────────
    scan_bucket INTEGER DEFAULT 0,        -- 分桶号 0-59
    last_scan_at TEXT,                    -- 上次扫描时间（UTC）
    last_scan_round INTEGER DEFAULT 0,   -- 上次扫描的轮次编号

    -- ── 采样v2相关（策略2）──────────────────────
    change_count INTEGER DEFAULT 0,       -- 变化次数
    last_changed_at TEXT,                 -- 上次变化时间（UTC）
    consecutive_skips INTEGER DEFAULT 0,  -- 连续跳过次数
    expected_interval_days INTEGER DEFAULT 30, -- 预估更新周期（天）
    scan_priority_score REAL DEFAULT 0,  -- 综合优先级评分 ∈ [0, 1]
    last_prob_update TEXT,               -- 上次prob更新时间

    -- ── 共用字段 ────────────────────────────────
    scan_count INTEGER DEFAULT 0,         -- 累计扫描次数
    priority INTEGER DEFAULT 50,         -- 静态优先级 1-100
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),

    FOREIGN KEY (source_id) REFERENCES content_sources(id)
);
```

#### 4.8.4 must_scan_now 强制触发 vs 桶轮转

**问题**：采样v2的 `must_scan_now()` 强制触发条件（连续skip超标、变化后首次扫描），这些URL要求立即扫，但桶轮转要求它们只能在自己的桶时间片被扫。

**解决方案**：在桶轮转中增加**跨桶强制扫描**逻辑：

```python
def scheduler_tick():
    current_bucket = get_current_bucket()
    urls = get_urls_in_bucket(current_bucket)

    # 分类：普通URL vs 强制URL
    forced_urls = [u for u in urls if must_scan_now(u)]
    normal_urls = [u for u in urls if not must_scan_now(u)]

    # 强制URL：立即处理，不受桶节奏限制
    for url in forced_urls:
        scan_url(url, reason='must_scan')

    # 普通URL：按采样v2概率判断
    for url in normal_urls:
        if url.scan_priority_score >= random.random():
            scan_url(url, reason='sampled')
        else:
            skip_url(url, reason='prob_skip')
```

**但注意**：`must_scan_now()` 触发后，该URL**不参与本轮的skip_count累积**，避免重复惩罚。

#### 4.8.5 桶轮转中采样状态的更新规则

每轮采集结束后，采样v2的状态更新逻辑**不变**，只是触发时机从"一次性"变成"分桶"：

| 事件 | 更新逻辑 |
|---|---|
| URL被扫且发现变化 | `change_count += 1`, `last_changed_at = now`, `consecutive_skips = 0`, `scan_priority_score` 升高 |
| URL被扫但无变化 | `consecutive_skips += 1`, `scan_priority_score` 按v2公式重算 |
| URL被skip | `consecutive_skips += 1`（但不超过阈值，由 `must_scan_now()` 控制上限） |
| 轮次结束 | 桶内所有URL的 `last_scan_round` 更新为当前轮次 |

#### 4.8.6 full_scan 的联合处理

full_scan 时，采样v2的skip逻辑**被完全禁用**：

```python
def scheduler_tick():
    if current_round.mode == 'full':
        # full_scan：所有URL都必须扫，跳过采样
        urls = get_all_urls_in_bucket(current_bucket)
        for url in urls:
            scan_url(url, reason='full_scan')
    else:
        # quick模式：采样v2 + 桶轮转
        ...
```

**但 `must_scan_now()` 保留**：防止连续skip超标的URL在full_scan时被漏扫。

#### 4.8.7 轮次结束判断

桶轮转的轮次结束条件：

```python
def is_round_completed(round_id) -> bool:
    """
    轮次结束 = 所有60桶都已完成 或 时间超过本轮周期上限
    """
    total_buckets = query("""
        SELECT COUNT(DISTINCT bucket) FROM collection_scan_slices
        WHERE round_id=? AND status='completed'
    """)

    # quick模式：4小时内扫完60桶
    # full模式：24小时内扫完60桶
    time_limit = 14400 if mode == 'quick' else 86400

    elapsed = (now_utc() - round_start).total_seconds()
    return total_buckets >= 60 or elapsed > time_limit
```

#### 4.8.8 采样v2参数在分桶场景下的调整

采样v2的半衰期参数**不受分桶影响**，因为采样判断是按URL自身的历史数据，不受时间片划分影响。

但有一个场景需要调整：**连续skip的计数方式**。

当前设计：每个采集周期（4小时）内对每个URL最多检查一次（被分到哪个桶就在那个时间片检查一次）。

**问题**：如果一个URL被分到桶30，而桶30因为故障没被触发（漏扫），这个URL的 `consecutive_skips` 应该+1吗？

**答案**：不应该。`consecutive_skips` 只在URL**实际被检查过但决定skip**时+1。如果桶漏触发了，URL没有被检查过，就不应该累积skip。

**设计**：在 `collection_scan_slices` 中记录每个桶的**实际执行状态**（`skipped` 表示桶未被执行），漏扫的桶不计入任何URL的 `consecutive_skips`。

---

### 4.9 与现有采集流程的兼容

#### 4.9.1 新增调度器 vs 现有 scheduler

```
现有架构：
    APScheduler 每4小时触发 run_now()
    run_now() 一次性遍历所有URL

改造后架构：
    APScheduler 每4小时触发 run_now()（初始化轮次）
    APScheduler 每5分钟触发 scheduler_tick()（分片处理）
```

**兼容方案**：
1. 首次触发 `run_now(mode='quick')` 时，创建 `collection_scan_rounds` 记录（round_id=1）
2. 后续每5分钟 `scheduler_tick()` 根据 `round_id` 继续处理
3. `run_now()` 在已有活跃轮次时，直接调用 `scheduler_tick()` 而非重新初始化

#### 4.9.2 run_now() 的兼容逻辑

```python
def run_now(mode='quick', progress_callback=None):
    # 检查是否有活跃轮次
    active_round = get_active_round()

    if active_round and not active_round.is_expired():
        # 有活跃轮次：继续分片处理
        return continue_slice_processing(active_round, mode)
    else:
        # 无活跃轮次或已过期：初始化新轮次
        return start_new_round(mode, progress_callback)
```

#### 4.9.3 前端进度展示的调整

现有 `/api/collect/progress` 返回当前进度，改造后需要新增字段：

```json
{
  "round_id": 42,
  "mode": "quick",
  "current_bucket": 23,
  "buckets_completed": 23,
  "buckets_total": 60,
  "urls_in_bucket": 13,
  "urls_scanned": 287,
  "urls_skipped": 516,
  "phase": "collecting",
  "started_at": "2026-05-25T14:00:00Z"
}
```

---

### 4.10 迁移步骤

#### Phase 1: 数据准备（不影响现有逻辑）

```sql
-- 1. 新增 collection_urls 表
CREATE TABLE collection_urls (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL UNIQUE,
    scan_bucket INTEGER DEFAULT 0,
    last_scan_at TEXT,
    last_scan_round INTEGER DEFAULT 0,
    change_count INTEGER DEFAULT 0,
    last_changed_at TEXT,
    consecutive_skips INTEGER DEFAULT 0,
    expected_interval_days INTEGER DEFAULT 30,
    scan_priority_score REAL DEFAULT 0,
    last_prob_update TEXT,
    scan_count INTEGER DEFAULT 0,
    priority INTEGER DEFAULT 50,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (source_id) REFERENCES content_sources(id)
);

-- 2. 运行迁移脚本：把现有803 URL从 snapshots 表导入 collection_urls
-- 按 product 分组轮转分配到60桶
-- 初始化 scan_count = 历史采集次数（从 snapshots 估算）

-- 3. 新增 collection_scan_rounds、collection_scan_slices、collection_checkpoints 表
CREATE TABLE collection_scan_rounds (...);
CREATE TABLE collection_scan_slices (...);
CREATE TABLE collection_checkpoints (...);
```

#### Phase 2: 分片调度器（向后兼容）

```python
# 新增 scheduler_tick() 函数
# 首次调用 run_now() 时初始化 round，后续 scheduler_tick() 继续处理

# 切换开关：system_config.sliced_collection = '1' 时启用
# 上线初期默认 '0'，观察稳定后逐步开启
```

#### Phase 3: 全量切换

```python
# 当 sliced_collection = '1' 且运行稳定后
# 将 run_now() 的默认行为切换为分片模式
# 保留旧逻辑作为 fallback
```

---

### 4.11 预期效果（联合优化）

| 指标 | 改造前 | 桶轮转 | 桶轮转+采样v2 |
|------|--------|--------|---------------|
| 峰值QPS | ~40 req/s | ~0.3 req/s | ~0.3 req/s |
| 平均QPS | ~1.3 req/s | ~0.11 req/s | ~0.06 req/s |
| 每轮HTTP请求 | 1606 | ~27 | ~14 |
| 推送实时性 | 立即 | 立即 | 立即 |
| 中断恢复 | 无 | 精确到桶 | 精确到URL |
| 旧URL（>365天） | 每次必扫 | 每次必扫 | 约2%概率skip |

---

## 五、实现优先级建议

### 最优先（立即可做）

1. **HTTP优化 v2 + 桶轮转联合**（文档第三节+第四节）— 减少99%+ HTTP请求，降低峰值QPS
2. **单线程写入者模式**（文档第二节）— 架构层优化，减少DB锁

### 中期（不影响核心功能）

3. **采集链路可视化**（文档第一节）— 运维友好，不改核心逻辑

### 不推荐（成本 > 收益）

- **产品级别轮转**：分散效果不如桶轮转，且实现更复杂

- [ ] 确认前端展示形式（方案 A 或 B）
- [ ] 确认保留历史条数
- [ ] 确认是否需要实时 phase 写入统计
- [ ] 设计 phase 注入机制（最小改动，不侵入现有 phase 逻辑）
- [ ] 实现 `collection_runs` 表 + phases 数据写入
- [ ] 扩展 `/api/collect/progress` 返回 phases
- [ ] 新增 `/api/collect/history` API
- [ ] 前端甘特图 / 时序图展示
- [ ] 产品级耗时 `product_timings` 记录

---

# 采集 HTTP 请求优化 — 分层采样 + 变化记忆

**状态**: 待办
**创建**: 2026-05-25
**背景**: 803 个 URL 每次采集最多 1606 个 HTTP 请求（HEAD+GET），其中 243 个 URL 的 `published_at > 365` 天，极低频更新但仍每轮必扫。delivery_log 显示 2026 年以来 998 条推送中，288 个分组里 271 个只被推送 1-2 次，高频更新（>3次）：极少。

## 现状问题

每个 URL 采集流程：

```
_delay(0.3~0.5s) → HEAD(url)        ← 必发
    ↓ 成功继续
_delay(0.3~0.5s) → GET(url)         ← 必发（无论页面是否变化）
    ↓
compute page_hash
    ↓
if hash == stored_hash:
    skip _extract_table_items()
else:
    parse + extract
```

问题：
- HTTP 请求发生在哈希比对之前，无法提前 skip
- 243 个 URL 的 `published_at > 365` 天，最新包发布于 2011-2019 年，之后页面内容再无变化，但采集员每 4 小时仍去访问它们
- 每个 HTTP 请求附带 0.6~1.0s 的网络延迟 + `_delay()`

## 目标

减少无效 HTTP 请求，优先保证：
1. **不漏新包**（高频更新的 URL 必须每次扫）
2. **不漏回滚检测**（每个 URL 必须定期全量扫一次）
3. **减少资源浪费**（低频沉默 URL 降低采样率）

## 分层优化方案

### 策略 1：Last-Modified 记忆（HTTP 请求数减半）

**原理**：利用 HTTP `Last-Modified` header，跳过未变化的页面 GET 请求。

每个 URL 在 snapshots 中记忆 `last_modified`：

```sql
ALTER TABLE snapshots ADD COLUMN last_modified TEXT;  -- 上次采集的 Last-Modified header
ALTER TABLE snapshots ADD COLUMN scan_count INTEGER DEFAULT 0;  -- 被扫描次数
ALTER TABLE snapshots ADD COLUMN last_scan_round INTEGER;     -- 上次扫描的轮次编号
```

**采集流程变更**：

```
HEAD(url) → 获取 Last-Modified
    ↓
if last_modified == stored_last_modified:
    # 页面根本没变，连 GET 都跳过
    skip GET
    scan_count++, last_scan_round = current_round
    ↓
else:
    GET(url) + page_hash 对比
    if hash changed:
        parse + extract
        update last_modified, scan_count, last_scan_round
    else:
        # hash 没变但 Last-Modified 变了（边缘情况）
        update last_modified
```

**风险控制**：若服务器不返回 `Last-Modified` 或页面变了但 `Last-Modified` 没更新，需要保底机制：

```
# 每个 URL 最多连续跳过 N 次 GET，强制全量扫一次
if consecutive_skip_count >= 3:
    force_full_scan_next_round()
```

**效果**：无变化的 URL 从 2 请求 → 1 请求。803 个 URL 理想情况从 ~1606 请求降到 ~803 请求。

---

### 策略 2：URL 级概率采样（核心优化 v2，改进版）

**原理**：每个 URL 根据 `published_at`、`last_changed_at`、`change_count` 独立计算扫描概率。

**v1 问题（已废弃）**：
- skip_penalty 与 silence_factor 叠加，两因子对抗，逻辑冗余
- change_count = 1 无法区分"今天刚更新"还是"500天前更新过"
- 纯概率 skip 模型，无法表达"哪个 URL 最需要先扫"

**v2 改进：4 项核心优化**

---

**改进 1：去 skip_penalty，改为强制触发条件**

skip_penalty 不再叠加在 prob 上，改为独立判断：

```python
def must_scan_now(url_record: dict) -> bool:
    """满足任一条件 → 本轮必须扫，不走概率采样"""
    # 条件 A: 连续跳过超过阈值（最多容忍 N 轮不扫）
    if url_record['consecutive_skips'] >= MAX_CONSECUTIVE_SKIPS:
        return True
    # 条件 B: 上次变化距今天数 > 预估更新周期 × 2（超过预期更新时间 2 倍，必须扫）
    if url_record['silence_days'] > url_record['expected_interval_days'] * 2:
        return True
    # 条件 C: 该 URL 上轮被 skip 但本轮摇号命中（已经摇号）
    return False
```

---

**改进 2：change_count 时间加权**

```python
def calc_change_recency(url_record: dict) -> float:
    """
    历史变化次数 → 时间加权分数
    近期有变化才算"活跃"，很久前的变化不反映当前状态

    权重设计：
      最近 30 天有变化 × 1.0
      31-90 天前有变化 × 0.5
      91-180 天前有变化 × 0.2
      180 天前 × 0.05
    """
    last_change_days = url_record['silence_days']
    raw_count = url_record['change_count']

    if last_change_days <= 30:
        recency_weight = 1.0
    elif last_change_days <= 90:
        recency_weight = 0.5
    elif last_change_days <= 180:
        recency_weight = 0.2
    else:
        recency_weight = 0.05

    return raw_count * recency_weight  # 范围 [0, ~10]
```

---

**改进 3：delivery_recentness 过滤低价值 URL**

从未被推送过的 URL 说明没有客户订阅，扫了也没意义，直接降为最低采样率：

```python
def calc_delivery_recentness(url_record: dict) -> float:
    """
    最近的 delivery_log 推送距今天数 → 价值分数
    有订阅记录的 URL 才值得高频扫描
    """
    last_delivery_days = url_record.get('last_delivery_days', 9999)

    if last_delivery_days <= 7:
        return 1.0
    elif last_delivery_days <= 30:
        return 0.7
    elif last_delivery_days <= 90:
        return 0.3
    elif last_delivery_days <= 180:
        return 0.1
    else:
        return 0.0  # 从未被推送 → 最低价值
```

---

**改进 4：下次更新时间预估（替代纯概率 skip）**

核心思路从"这轮扫不扫"升级为"这个 URL 距离下次更新还有多久"：

```python
def estimate_next_update_days(url_record: dict) -> float:
    """
    预估该 URL 距下次更新的天数
    用历史更新间隔作为基准，加随机抖动
    """
    history_days = url_record['history_days']  # 该 URL 被监控的总天数
    change_count = url_record['change_count']
    last_change_days = url_record['silence_days']

    if change_count == 0:
        # 从未变化过的 URL，用年龄估算：越老越不可能变化
        # 发布 N 年后还在更新的概率 ≈ exp(-age / 5年)
        return max(7, url_record['age_days'] * math.exp(-url_record['age_days'] / 1825))

    # 从历史拟合平均更新间隔
    avg_interval = history_days / change_count  # 天/次

    # 加随机抖动 ±30%，模拟不确定性
    jitter = random.uniform(0.7, 1.3)

    # 已沉默天数不算在内，预估从今天开始的间隔
    expected = avg_interval * jitter

    return max(1, expected)


def calc_scan_priority(url_record: dict) -> float:
    """
    扫描优先级：值越大 = 越紧迫需要扫描
    基于"距下次更新预估天数"计算
    """
    next_update = estimate_next_update_days(url_record)

    # sigmod 归一化：预估越近，优先级越高
    # sigmod(x) = 1 / (1 + exp(-x))
    # 用 7 天作为半衰期
    priority = 2.0 / (1.0 + math.exp(-(30 - next_update) / 7))

    return priority
```

**调度模型**（替代概率采样）：

```python
def should_scan_now(url_record: dict) -> bool:
    if must_scan_now(url_record):
        return True

    # 计算扫描优先级（0~1 之间，1=最需要扫）
    priority = calc_scan_priority(url_record)

    # 按优先级排序，资源有限时优先扫高优先级 URL
    # 每轮采集前，按 priority 降序取前 N 个 URL 扫描
    # N = min(total_urls, budget)  # budget = 本轮允许的最大扫描数

    # 简化实现：用概率模型近似
    return random.random() <= priority
```

---

**v2 综合概率公式**：

```python
def calc_scan_probability_v2(url_record: dict) -> float:
    """
    prob ∈ [0.02, 1.0]，最低 2% 保底（比 v1 更激进地降采样）
    """
    age_days = url_record['age_days']
    silence_days = url_record['silence_days']
    change_recency = calc_change_recency(url_record)
    delivery_score = calc_delivery_recentness(url_record)

    # 因子 1: 年龄衰减（半衰期 365 天）
    age_factor = math.exp(-age_days / 365) * 0.15

    # 因子 2: 沉默衰减（半衰期 180 天）
    silence_factor = (1 - math.exp(-silence_days / 180)) * 0.10

    # 因子 3: 历史活跃加成（时间加权，封顶 0.40）
    freq_boost = min(0.40, change_recency / 10 * 0.40)

    # 因子 4: 交付价值分数（封顶 0.30）
    value_boost = delivery_score * 0.30

    # 因子 5: 连续沉默惩罚（只加不乘，防止 prob 降到 0）
    skip_penalty_add = min(0.05, url_record['consecutive_skips'] * 0.01)

    prob = 0.02 + age_factor + silence_factor + freq_boost + value_boost + skip_penalty_add
    return min(1.0, prob)
```

---

**v2 概率估算对比**：

| URL 类型 | age | silence | delivery | prob(v1) | prob(v2) |
|---------|-----|---------|---------|---------|---------|
| 今日发布，有订阅 | 0 | 0 | 1.0 | 0.49 | **0.77** |
| 30天前发布，有订阅 | 30 | 30 | 0.7 | 0.40 | **0.53** |
| 90天前发布，从未推送 | 90 | 90 | 0.0 | 0.45 | **0.07** |
| 180天前发布，1次推送 | 180 | 180 | 0.1 | 0.26 | **0.10** |
| 365天前发布，从未推送 | 365 | 365 | 0.0 | 0.15 | **0.02** |
| 730天前发布，从未推送 | 730 | 730 | 0.0 | 0.09 | **0.02** |

v2 的关键改进：**低价值 URL（从未推送）概率降到 2-7%，而高价值 URL 保持在 50-77%**，比 v1 更精准。

---

**v2 漏检反馈强化**：

当 URL 被 skip 多轮后终于被扫到（must_scan_now 触发），如果发现该 URL **有变化**（page_hash 变了），说明这个 URL 不应该被降采样：

```python
# 采集结束后，对本轮 must_scan 的 URL 回溯
for url in must_scan_triggered_urls:
    if url['had_change_this_round']:
        # 漏检了！强化 prob，下次不要降太多
        url['change_count'] += 1          # 增加历史计数
        url['silence_days'] = 0           # 重置沉默天数
        url['consecutive_skips'] = 0     # 重置跳过计数
        # prob 自动通过 calc_scan_probability_v2 升高
```

---

**效果总估算（v2）**：

| URL 类型 | 占比 | v2 prob | 效果 |
|---------|------|---------|------|
| 高价值（30天内发布+有订阅） | ~16% | ~0.75 | 每轮扫，接近全量 |
| 中价值（有订阅但偏旧） | ~22% | ~0.35 | 降采样 65% |
| 低价值（从未推送+偏旧） | ~55% | ~0.07 | 降采样 93% |
| 超低价值（>365天+0推送） | ~7% | ~0.02 | 极低频采样 |

预期总请求数：
- 高价值：803 × 0.16 × 0.75 × 2请求 ≈ **193 请求**
- 中价值：803 × 0.22 × 0.35 × 2请求 ≈ **124 请求**
- 低价值：803 × 0.55 × 0.07 × 2请求 ≈ **62 请求**
- 超低：803 × 0.07 × 0.02 × 2请求 ≈ **2 请求**

**总计约 381 请求/轮**（原始 1606 请求，减少 76%），同时保证高价值 URL 全量覆盖。

---

### 策略 3：产品级轮转保底（防漏）

**原理**：每个产品（source_id）维护轮次计数器，保证即使 URL 长期沉默，也会在采样周期内至少全量扫一次。

```sql
-- collection_scan_policy 表的 url_probabilities 字段存储每个 URL 的详细状态
-- scan_round: 全局轮次编号（每次采集递增）
-- last_full_scan_round: 该产品上次全量扫的轮次
```

**全量触发条件**（满足任一即全量）：

```
条件 A: 产品距上次全量扫已过 N 轮（N = 采样周期，如 7）
条件 B: 该 URL 连续被跳过 ≥ M 次（M = 3）
条件 C: 该 URL 本轮随机摇号命中的概率结果为"扫"
```

**采样周期计算**：

```
采集间隔 = 4 小时
全量扫描周期 = 7 天
全量触发轮次 = 7 × 24 / 4 = 42 轮
```

即：每个产品至少每 42 轮（约 7 天）会触发一次全量扫。

---

### 策略 4：动态调参（自动化）

**原理**：根据每轮采集结果的实际变化率，自动微调各 URL 的采样参数。

```
每轮采集结束后：
    1. 统计本轮被扫 URL 中：
       - 实际变化率 = 有变化的 URL 数 / 被扫 URL 数
    2. 对比期望变化率（来自历史统计）：
       - 若实际 << 期望 → 降低 prob（该 URL 比预期更沉默）
       - 若实际 >> 期望 → 提高 prob（该 URL 比预期更活跃）
    3. Bayesian update:
       prob_new = (prob_old * k + actual_rate) / (k + 1)
       其中 k = 置信度权重（建议 k=3）
```

---

## 落地实施步骤

| 阶段 | 内容 | 风险 |
|------|------|------|
| Phase A | 新增 snapshots 字段（last_modified, scan_count, last_scan_round） | 低（只加字段） |
| Phase B | 新增 collection_scan_policy 表 + 策略 3 产品轮转保底 | 低 |
| Phase C | 实现策略 1（Last-Modified 记忆，GET skip） | 中（需测试 edge case） |
| Phase D | 实现策略 2（概率采样，独立 URL 概率） | 中（参数调优） |
| Phase E | 实现策略 4（动态调参自动化） | 低 |

## 待办

- [ ] 确认新增字段落地方式（migration script）
- [ ] Phase A: `last_modified`, `scan_count`, `last_scan_round` 字段
- [ ] Phase B: `collection_scan_policy` 表 + 产品轮转逻辑
- [ ] Phase C: Last-Modified 记忆 + 连续 skip 计数器 + GET skip
- [ ] Phase D: URL 级概率采样算法实现
- [ ] Phase E: 动态调参（贝叶斯更新）
- [ ] 参数调优：验证衰减半衰期 / 权重系数（上线后观察实际效果微调）

---

# 单线程写入者模式（Single-Writer Pattern）

**状态**: 待办
**创建**: 2026-05-25
**关联**: 作为 database is locked 的根本性架构解决方案

## 现状分析

### 当前并发控制机制

`database.py` 的并发控制依赖三层：

| 层级 | 机制 | 作用 |
|------|------|------|
| SQLite WAL | `PRAGMA journal_mode=WAL` | 读不阻塞写，写不阻塞读 |
| SQLite 超时 | `PRAGMA busy_timeout=30000` | 写锁冲突时等待 30s |
| Python 全局锁 | `_write_lock = threading.RLock()` | 所有 `execute()` 调用串行化 |

### 当前 write_lock 的问题

`_write_lock` 是 Python 层面的全局锁，所有 `execute()` 调用都要抢这把锁：

```
collector thread  → 持有 _write_lock 写 snapshots
heartbeat thread  → 等待 _write_lock（心跳写 DB）
log_scanner thread → 等待 _write_lock（日志写 DB）
APScheduler job    → 等待 _write_lock（定时任务写 DB）
```

**症状**：
- collector 持有锁写大量数据时，其他线程全部 blocked
- WAL 虽支持并发写，但 SQLite 内部只能有一个写操作，其他 writer 被 busytTimeout 挡住
- 当等待超过阈值（或 Python 锁超时），开始出现 `database is locked`

### 已知漏网点（Bug）

`src/collectors/nsfocus.py` 有两处 `execute()` **绕过了 `_write_lock`**：

```python
# line 541 — collector 内部直接调用 execute，未经过 database.py 的锁保护
execute("UPDATE snapshots SET prev_page_hash=page_hash WHERE source_id=? AND source_url=?",
        (source_id, full_url))

# line 597 — 同上
execute("UPDATE snapshots SET prev_page_hash = page_hash, page_hash=? ...")
```

这两处与 `database.py` 的 `_write_lock` 完全无关，是独立的 DB 写操作。

### 根因总结

```
采集高峰（78 产品同时/轮流抓取）
    ↓
collector 线程持续持有 _write_lock + 持续写 DB
    ↓
其他线程（heartbeat/log_scanner/调度器）全部排队等锁
    ↓
等待超时 + WAL checkpoint 失败累积 → database is locked
```

## 解决思路

### 方案对比

| 方案 | 描述 | 优点 | 缺点 |
|------|------|------|------|
| 扩大 busy_timeout | 继续等锁，增加等待时间 | 无代码改动 | 拖延问题，不解决根因 |
| 增加 retry | `execute()` already has 3x retry | 同上 | 同上 |
| **单线程写入者** | 专用写线程消费 queue，所有写路由到队列 | 根治锁竞争，读可真正并发 | 实现复杂，延迟增加 |
| 多进程分离 | 采集进程 vs 通知进程 vs Web 进程分离 | 进程级隔离天然无锁 | 架构大变，DB 文件锁复杂 |

**推荐：单线程写入者模式** — 不改变部署架构，从应用层消除锁竞争。

## 设计方案

### 核心架构

```
┌─────────────────────────────────────────────────────────────┐
│ Producer 线程们（collector / heartbeat / log_scanner / ...) │
│  调用 enqueue_write(sql, params)                            │
│  把任务丢进 Queue，死等 result                               │
└──────────────────┬──────────────────────────────────────────┘
                   │ 线程安全 Queue
                   ▼
┌─────────────────────────────────────────────────────────────┐
│  Writer Thread（唯一，永远只有一个）                         │
│  循环 pop Queue → execute → 回填 result → notify            │
└──────────────────┬──────────────────────────────────────────┘
                   │ 直接 sqlite3.connect() 执行
                   ▼
              SQLite DB（WAL 模式）
```

**读操作**（`query()`）：继续走 `get_db()` + 直接读，无锁，WAL 允许真正并发读。

**写操作**（`execute()` / `executemany()`）：统一路由到 Queue，由 Writer Thread 串行执行。

### Task 对象设计

```python
@dataclass
class WriteTask:
    sql: str
    params: tuple
    result_placeholder: Any  # 用于存放执行结果
    completion_event: threading.Event  # 完成信号
    exception_placeholder: Optional[Exception]  # 异常回传
```

### 实现要点

**1. Queue 选择**

`queue.Queue`（线程安全，无需额外加锁）：
- `maxsize=0`（无界）
- `put()` 阻塞（队满时），`get()` 阻塞（队空时）
- 自带 `threading.Condition`，天然适合生产者-消费者模式

**2. 重入检测（关键）**

Writer Thread 自己也可能调用 `enqueue_write()`（例如通知路由的 DB 操作），必须检测并直接执行，不能再入队：

```python
def enqueue_write(sql, params):
    if threading.current_thread() is _writer_thread:
        # 已经在写线程中，直接执行，不入队避免死锁
        return _direct_execute(sql, params)
    # 正常路径：入队等待
    event, result = _queue_write(sql, params)
    event.wait(timeout=30)
    return result
```

**3. 返回值传递**

`enqueue_write()` 返回 `lastrowid`，通过 `result_placeholder` 回传：
- `put()` 前：创建 `threading.Event` + `result_placeholder = None`
- Writer Thread：执行 SQL，结果写入 `result_placeholder`，`event.set()`
- 调用方：`event.wait()` 超时则抛 `TimeoutError`

**4. 异常处理**

SQL 执行异常不能直接抛给消费者线程，需要捕获后写入 `exception_placeholder` 并设置 event：

```python
try:
    cur = db.execute(sql, params)
    db.commit()
    result_placeholder['value'] = cur.lastrowid
except Exception as e:
    exception_placeholder['value'] = e
finally:
    event.set()
```

调用方 `event.wait()` 返回后检查 `exception_placeholder`，有则 re-raise。

**5. 批量优化（可选增强）**

每个 phase 结束时可以 `executemany()` 批量提交，而非逐条 enqueue：
- collector 采集完一个产品后，批量 enqueue 一批 INSERT
- 减少 Queue 调度次数，降低上下文切换

### 对 nsfocus.py 漏网的处理

`nsfocus.py` 两处 `execute()` 直接调用必须修正。修正后路径：

```
nsfocus.py: execute() → enqueue_write() → Queue → Writer Thread
```

### 渐进式迁移路径

| 阶段 | 内容 | 风险 |
|------|------|------|
| Phase 1 | `database.py` 增加 Queue + Writer Thread，`execute()` 暂时保持原样（加注释说明待迁移） | 低 |
| Phase 2 | `enqueue_write()` 实现并自测（写线程内调用不死锁） | 中 |
| Phase 3 | `execute()` 改为调用 `enqueue_write()`，删除 `_write_lock` | 中
| Phase 4 | 修正 `nsfocus.py` 两处漏网 | 低 |
| Phase 5 | 批量优化（`executemany` 支持） | 低 |

### 监控指标（可复用采集链路监控的 phase 数据）

- Queue 积压深度（`q.qsize()`）
- 单次 write 实际耗时（入队到结果返回）
- Writer Thread 利用率（活跃时间/总时间）

### 优势

1. **根治锁竞争**：所有写操作天然串行，不存在 Python 层锁争用
2. **读真正并发**：`query()` 完全无锁，WAL 允许多读并发
3. **错误隔离**：一个写失败不影响其他写操作（通过 event 通知）
4. **可监控**：Queue 深度 + write 延迟随时可查
5. **批量友好**：写线程空闲时可合并相邻写操作

### 代价

1. **写延迟增加**：非写线程的调用方需要等 Queue 调度（约 1 个 context switch）
2. **实现复杂度**：需要处理重入检测、返回值传递、异常回传
3. **调试复杂度**：调用栈变深，错误堆栈多一层
4. **Queue 积压风险**：采集高峰时 Queue 可能堆积，需要监控 + 告警

## 待办任务（单线程写入者）

- [ ] 确认重入检测逻辑（写线程调用 enqueue_write 时直接执行）
- [ ] Phase 1: 实现 queue.Queue + WriteTask + Writer Thread
- [ ] Phase 1: 实现 enqueue_write() 并自测
- [ ] Phase 1: 修正 nsfocus.py 两处漏网 execute()
- [ ] Phase 2: execute() 路由到 enqueue_write()，删除 `_write_lock`
- [ ] Phase 3: 批量优化（executemany 支持）
- [ ] 监控指标：Queue 积压深度监控 + 告警阈值