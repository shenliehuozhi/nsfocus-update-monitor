# 模块六：采集数据 (Data)

## 功能说明

采集数据页面展示系统从绿盟升级站点采集到的所有升级包快照。每一次采集会抓取产品页面的包列表，解析出文件名、版本号、MD5、发布时间等信息，存入 `snapshots` 表。

## 数据模型

### snapshots 表

```sql
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER REFERENCES content_sources(id),  -- 所属产品
    product_name TEXT,           -- 产品名称
    version_branch TEXT,         -- 版本分支，如 "V6.0.9"
    package_type TEXT,           -- 包类型：sys/rule/nti/av/apprule/url 等
    file_name TEXT,             -- 文件名
    package_version TEXT,        -- 包版本号（如 "2025061201"）
    md5_hash TEXT,              -- MD5 值
    file_size INTEGER,          -- 文件大小（字节）
    description_raw TEXT,       -- 原始描述文本
    urgency TEXT DEFAULT '',     -- 紧急程度：critical/high/normal
    download_id TEXT,          -- 下载ID（用于构造下载 URL）
    published_at TEXT,          -- 发布时间
    min_sys_version TEXT,      -- 最低系统版本要求
    source_url TEXT,           -- 来源 URL
    status TEXT DEFAULT 'active',  -- active=有效, rollback=已撤回
    created_at TEXT,
    last_seen_at TEXT,         -- 最后一次出现在采集结果中的时间
    rollback_from_id INTEGER   -- 如果是撤回，指向被撤回的 snapshot ID
)
```

## content_sources 表（产品/采集源）

```sql
CREATE TABLE content_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,               -- 产品名称
    display_name TEXT,               -- 显示名称
    source_type TEXT,                -- nsfocus/manual
    entry_url TEXT,                  -- 入口页面相对路径
    config TEXT DEFAULT '{}',         -- 采集配置（JSON）
    is_active INTEGER DEFAULT 1,      -- 是否启用采集
    health_status TEXT DEFAULT 'unknown', -- ok/error/unknown
    last_collected_at TEXT,          -- 最后一次采集时间
    strategy TEXT DEFAULT 'standard', -- standard/recursive
    is_manual INTEGER DEFAULT 0,     -- 是否手动添加
    package_type TEXT,               -- 包类型（聚合显示）
    force_type TEXT,
    package_type_discovered TEXT,
    package_type_changed INTEGER DEFAULT 0,
    created_at TEXT
)
```

## 包类型说明

| type | 说明 |
|------|------|
| sys | 系统升级包 |
| rule | 规则升级包 |
| nti | 威胁情报包 |
| av | 病毒库升级包 |
| apprule | 应用规则包 |
| url | URL 分类库 |
| wcs | 恶意站点库 |
| geo | 地理库 |
| judge | 研判规则库 |
| interface | 接口升级包 |
| special | 特殊升级包 |
| merge | 合并升级包 |
| client | 客户端 |
| engine | 引擎升级包 |

## 采集流程

```
scheduler 触发采集
        ↓
加载 Session cookie（从 user_sessions）
        ↓
nsfocus.collector 访问绿盟升级页面
        ↓
解析 HTML，提取包信息（文件名、版本、MD5等）
        ↓
与现有快照比较（MD5 对比）
        ↓
  ├── 新包（MD5 不存在）→ status='active'，判断是否为撤回
  ├── 相同（MD5+内容一致）→ 跳过
  └── 变化（MD5 同但内容变）→ 更新快照，标记 package_type_changed=1
        ↓
写入 snapshots 表
        ↓
如果包被撤回（URL 不再出现）→ status='rollback'
```

## 撤回检测

当一个快照在下一次采集中不再出现在产品页面中时：
- 原快照 `status` 设为 `'rollback'`
- `rollback_from_id` 指向被撤回的快照 ID

订阅规则可以配置 `notify_rollback=1` 来通知客户包被撤回。

## API 设计

### GET /api/data

获取所有快照（active 状态）。

**查询参数**：

| 参数 | 说明 |
|------|------|
| product | 按产品名过滤 |
| source_id | 按采集源 ID 过滤 |
| status | active/rollback，默认 active |

**响应**：
```json
{
  "code": 0,
  "data": {
    "snapshots": [
      {
        "id": 100,
        "product_name": "WEB应用防护系统(WAF)",
        "version_branch": "V6.0.9",
        "package_type": "rule",
        "file_name": "WAF_V6.0.9_规则升级包_2025061201.tar.gz",
        "package_version": "2025061201",
        "md5_hash": "abc123...",
        "file_size": 5242880,
        "urgency": "high",
        "published_at": "2026-06-12",
        "status": "active",
        "created_at": "2026-06-12T10:00:00"
      }
    ],
    "total": 254,
    "sources": { "WAF": 50, "IPS": 80, ... }
  }
}
```

### GET /api/options/products

返回已激活的产品列表（供前端下拉选择）。

## 前端展示

产品树结构：
```
▼ WAF（V6.0.9）
    ▼ 规则包（rule）
        ├── WAF_V6.0.9_规则升级包.tar.gz  v2025061201  2026-06-12
        └── WAF_V6.0.9_规则升级包.tar.gz  v2025061001  2026-06-10
▼ IPS（V5.6.11）
    ▼ 系统包（sys）
        ...
```

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/models/snapshot.py` | 快照数据访问层 |
| `src/models/subscription.py` | 订阅规则（匹配快照） |
| `src/collectors/nsfocus.py` | 采集器（抓取+解析） |
| `src/core/scheduler.py` | 采集调度器 |
