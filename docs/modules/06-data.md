# 模块六：采集数据 (Data)

## 功能说明

采集数据页面展示系统从绿盟升级站点（update.nsfocus.com）采集到的所有升级包快照。每一次采集会抓取产品页面的包列表，解析出文件名、版本号、MD5、发布时间等信息，存入 `snapshots` 表。

---

## 采集流程总览

```
用户/定时触发采集
        ↓
scheduler.run_now(mode)
        ↓
① 预检所有活跃 Session（verify_session）
        ↓  session 失效则标记 expired，跳过
        ↓
② 获取 is_active=1 的产品列表（content_sources）
        ↓
③ 执行采集（_collect_quick 或 _collect_full）
        ├── HEAD/GET 每个已知包页面 URL
        ├── 比较 page_hash（MD5）
        ├── hash 未变化 → 跳过
        ├── hash 变化 → 重新解析表格，提取包信息
        └── 返回 UnifiedContentItem 列表
        ↓
④ 变更检测（run_detection）
        ├── 新包 → 插入 snapshots，status='active'
        ├── 撤回包 → status='rollback'
        └── 无变化 → 跳过
        ↓
⑤ 推送通知（route_notifications）
        ├── 匹配订阅规则（filter_conditions）
        ├── 应用延迟/摘要策略
        └── 发送通知
```

---

## 采集模式：delta（快速）vs full（深度）

### delta 模式（默认）

调用 `_collect_quick()`，只检查已知页面 URL 是否变化：

1. 从 `content_sources.package_type_discovered` 读取该产品的所有已知最终页面 URL
2. 对每个 URL 发 HEAD 请求（探测服务器是否支持）
3. 对每个 URL 发 GET 请求，读取前 50KB HTML
4. 计算页面内容的 MD5（page_hash），与 `snapshots.page_hash` 比较
5. hash 相同 → 页面无变化，复用已有快照
6. hash 变化 → 重新解析 HTML 表格，提取包列表
7. 对比新旧快照列表：新增 / 变化 / 消失

**耗时**：约 20 秒（5 个产品 × 若干页面）

### full 模式（深度）

调用 `_collect_full()`，重新遍历产品所有页面：

1. 从产品入口 URL 开始
2. 递归向下抓取每个子链接（版本分支 → 包类型 → 最终页面）
3. 对每个最终页面执行 `_extract_table_items()`
4. 全量写入/更新 `snapshots` 表

**耗时**：约 15-20 分钟

### quick 模式

与 delta 完全相同（代码中 delta 就是调用 `_collect_quick`）。

---

## 核心数据结构

### snapshots 表

```sql
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER REFERENCES content_sources(id),
    product_name TEXT,
    version_branch TEXT,          -- "V6.0.9"
    package_type TEXT,           -- "rule" / "sys" / "av" / "nti" 等
    file_name TEXT,             -- "WAF_V6.0.9_规则升级包.tar.gz"
    package_version TEXT,        -- "2025061201"（8位日期版本号）
    md5_hash TEXT,              -- 文件 MD5（去重主键）
    file_size INTEGER,          -- 文件大小（字节）
    description_raw TEXT,       -- 原始描述文本
    urgency TEXT DEFAULT 'normal',  -- critical/high/normal
    download_id TEXT,          -- 下载 ID（构造下载 URL 用）
    published_at TEXT,          -- 发布时间（UTC）
    min_sys_version TEXT,      -- 最低系统版本要求
    source_url TEXT,           -- 来源页面 URL
    status TEXT DEFAULT 'active',  -- active/rollback/rollback_pending
    created_at TEXT,
    last_seen_at TEXT,         -- 最后一次出现在采集结果中的时间
    rollback_from_id INTEGER,  -- 指向被撤回的 snapshot ID
    -- 哈希追踪（用于判断页面是否变化）
    page_hash TEXT,            -- 页面内容的 MD5
    prev_page_hash TEXT,        -- 上一次 page_hash
)
```

### content_sources 表

```sql
CREATE TABLE content_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,               -- 产品名称
    entry_url TEXT,                  -- 入口页面相对路径，如 "/update/wafIndex"
    is_active INTEGER DEFAULT 1,      -- 1=参与采集，0=跳过
    health_status TEXT DEFAULT 'unknown', -- ok/error/unknown
    last_collected_at TEXT,          -- 最后一次采集时间
    -- 包类型发现结果（JSON）
    package_type TEXT,                -- 聚合的包类型描述
    package_type_discovered TEXT,    -- JSON，发现的所有路径和类型
    package_type_changed INTEGER DEFAULT 0,  -- 发现结果是否变化
    ...
)
```

### package_type_discovered JSON 结构

```json
{
  "types": ["规则升级包", "系统升级包", "威胁情报升级包"],
  "paths": [
    {
      "chain": ["WEB应用防护系统(WAF)", "V6.0.9", "规则升级包"],
      "types": ["规则升级包"],
      "url": "/update/wafIndex/v/6.0.9/rule"
    },
    {
      "chain": ["WEB应用防护系统(WAF)", "V6.0.9", "系统升级包"],
      "types": ["系统升级包"],
      "url": "/update/wafIndex/v/6.0.9/sys"
    }
  ],
  "modes": {
    "规则升级包": "auto",
    "系统升级包": "auto"
  }
}
```

`chain` 数组：[产品名, 版本分支, 包类型]

---

## 页面哈希机制（page_hash）

### 什么是 page_hash

page_hash 是绿盟产品列表页面的 **HTML 内容**的 MD5（取前 50KB）。用于判断页面内容是否发生变化。

```
page_hash = MD5(html[:50000])
```

### 比较流程

```
采集开始
        ↓
GET https://update.nsfocus.com/update/wafIndex/v/6.0.9/rule
        ↓
计算 page_hash = MD5(html[:50000])
        ↓
查 DB: SELECT page_hash FROM snapshots WHERE source_url = ? AND status = 'active'
        ↓
  ├── page_hash == stored_hash → 页面无变化，复用已有快照（seen_ids）
  ├── page_hash != stored_hash → 页面已变化，重新解析
  └── stored_hash IS NULL → 新页面，插入 placeholder 后重新解析
```

### prev_page_hash 追踪链

每次页面 hash 变化时：
- `prev_page_hash` ← 旧 `page_hash`（记录上一次的哈希值）
- `page_hash` ← 新哈希值

这样可以追溯：`prev_page_hash → page_hash → ?`，了解页面的变化历史。

### 批量写入优化

原来每检查一个 URL 就写一次 DB（频繁 SQLite 锁竞争）。优化后改为**先收集，再批量写入**：

```python
pending_pagehash_inserts = []   # 首次出现的 URL，插入 placeholder
pending_pagehash_updates = []   # hash 变化的 URL，更新 prev_page_hash
# HTTP 请求循环结束后，统一执行一次批量写入
```

---

## 包信息解析：_extract_table_items

### HTML 结构

绿盟产品页面是标准表格结构：

```html
<table>
  <tr>
    <td>名称：</td><td><a href="/update/downloads/id/123">WAF_V6.0.9_规则升级包.tar.gz</a></td>
  </tr>
  <tr>
    <td>版本：</td><td>2025061201</td>
  </tr>
  <tr>
    <td>MD5：</td><td>abc123def456...</td>
  </tr>
  <tr>
    <td>大小：</td><td>5.2MB</td>
  </tr>
  <tr>
    <td>发布时间：</td><td>2026-06-12 17:05:51</td>
  </tr>
  <tr>
    <td>描述：</td>
    <td>
      一、新增：高危漏洞规则 (20250601) 100条<br/>
      二、修改：中等危规则 (20250520) 50条<br/>
      三、升级建议：需重启设备
    </td>
  </tr>
</table>
```

### 解析流程（_parse_kv_row）

对每个表格行用正则提取键值对：

| 正则模式 | 字段 |
|---------|------|
| `名称[：:]\s*(.+?)(?=\s*(?:版本\|MD5\|大小\|描述\|发布\|$))` | `file_name` |
| `版本[：:]\s*(.+?)(?=\s*(?:MD5\|大小\|描述\|发布\|名称\|$))` | `package_version` |
| `MD5[：:]\s*([a-fA-F0-9]{32})` | `md5_hash` |
| `大小[：:]\s*([\d.]+[KMGT]?B?)` | `file_size_raw` |
| `发布时间[：:]\s*(.+?)$` | `published_at` |
| `描述[：:](.*)` | `description_raw` |

### 描述解析：parse_description

从描述文本提取结构化信息：

```python
# 输入 description_raw
"""
一、新增：高危漏洞规则 (20250601) 100条
二、修改：中等危规则 (20250520) 50条
三、升级建议：需重启设备，前置版本 V6.0.8
"""

# 输出 parsed
{
    "added": ["20250601"],
    "modified": ["20250520"],
    "deleted": [],
    "other": "...",
    "min_sys_version": "V6.0.8",      # 从"前置版本"提取
    "restart_required": True           # 有"重启"且无"无需重启"
}
```

### 紧急程度判断：urgency

```python
if any(kw in desc_lower for kw in ['高危', '严重', 'critical', '远程代码执行', '紧急']):
    urgency = 'critical'
elif any(kw in desc_lower for kw in ['中危', 'high', '漏洞', '绕过']):
    urgency = 'high'
else:
    urgency = 'normal'
```

---

## 撤回检测（Rollback Detection）

### 机制

每次 delta 采集（`_collect_quick`）结束后，对比：

```
old_snapshot_set = {file_name, package_type}  ← 上一次采集的快照
new_snapshot_set = {file_name, package_type}  ← 本次采集到的快照

gone_files = old_snapshot_set - new_snapshot_set
# gone_files 中的快照 → status 改为 'rollback'
```

### 三态流转

```
active（活跃）←──首次新增
        │
   下次采集消失
        ↓
rollback_pending（撤回待确认）
        │
   连续2次确认消失
        ↓
rollback（确认撤回）
```

### 确认机制（ROLLBACK_CONFIRM）

```python
ROLLBACK_CONFIRM = 2   # 连续2次采集都消失才标记为 rollback
```

`rollback_cycles` 字段记录连续消失次数，达到 ROLLBACK_CONFIRM 才最终标记为 `rollback`。

---

## 包类型代码映射

绿盟页面上显示的是中文名称，系统内部存储为英文缩写：

| 中文名称（页面） | 代码（DB） |
|----------------|-----------|
| 系统升级包 / 引擎升级包 | `sys` |
| 规则升级包 / 规则库升级包 | `rule` |
| 威胁情报升级包 / NTI威胁情报升级包 | `nti` |
| 病毒特征库升级包 | `av` |
| 流式病毒库升级包 | `av_stream` |
| 应用规则库升级包 | `apprule` |
| URL分类库（升级包） | `url` |
| 恶意站点库升级包 | `wcs` |
| 地理库升级包 | `geo` |
| 研判规则库升级包 | `judge` |
| 接口升级包 | `interface` |
| 特殊升级包 | `special` |
| 其他升级包 | `other` |
| 合并升级包 | `merge` |
| 客户端 | `client` |

---

## 时间处理：CST → UTC

绿盟站点显示的时间是 **中国标准时间（CST，UTC+8）**，存入数据库前需要转换：

```python
# nsfocus.py
def _cst_to_utc(raw_time: str) -> str:
    # '2026-05-12 17:05:51' (CST) → '2026-05-12T09:05:51' (UTC)
    # '2026-05-12' (CST) → '2026-05-12T00:00:00' (UTC)
```

---

## 新增检测流程（run_detection）

```python
def run_detection(source_id, items, rollback_confirm, check_rollback, seen_ids):
    existing = query("SELECT * FROM snapshots WHERE source_id=?", (source_id,))
    existing_map = {(s.file_name, s.package_type): s for s in existing}

    new_items = []
    rollback_items = []

    for item in items:
        key = (item.file_name, item.package_type)
        if key not in existing_map:
            new_items.append(item)  # 新包
        else:
            old = existing_map[key]
            if old.md5_hash != item.md5_hash:
                new_items.append(item)  # 内容变化（重新入库）

    # 撤回检测
    if check_rollback:
        new_keys = {(it.file_name, it.package_type) for it in items}
        for (fname, ptype), old_snap in existing_map.items():
            if (fname, ptype) not in new_keys:
                rollback_items.append(old_snap)

    return DetectionResult(new_items, rollback_items)
```

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/collectors/nsfocus.py` | 采集器核心（HTML 解析、hash 比较、描述提取） |
| `src/core/scheduler.py` | 调度器（采集流程编排、Session 预检、推送触发） |
| `src/models/snapshot.py` | snapshots 表数据访问层 |
| `src/models/content_source.py` | content_sources 表数据访问层 |
| `src/core/detector.py` | 变更检测逻辑 |
