# 模块九：产品管理 (Products)

## 功能说明

产品管理用于控制哪些绿盟产品参与采集。系统内置了78个产品定义，但默认只启用5个（WAF/IPS/NF/RSAS/UTS）。用户可以通过产品管理界面启用/禁用单个或批量产品。

## 数据模型

### content_sources 表

```sql
CREATE TABLE content_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                   -- 产品名称
    display_name TEXT,                    -- 显示名称
    source_type TEXT,                      -- nsfocus/manual
    entry_url TEXT,                       -- 入口页面相对路径
    config TEXT DEFAULT '{}',             -- 采集配置（JSON）
    is_active INTEGER DEFAULT 1,           -- 1=启用采集, 0=禁用
    health_status TEXT DEFAULT 'unknown',  -- ok/error/unknown
    last_collected_at TEXT,               -- 最后一次采集时间
    strategy TEXT DEFAULT 'standard',     -- standard/recursive
    is_manual INTEGER DEFAULT 0,           -- 是否手动添加
    package_type TEXT,                    -- 包类型（聚合显示）
    force_type TEXT,
    package_type_discovered TEXT,
    package_type_changed INTEGER DEFAULT 0,
    created_at TEXT
)
```

## 内置产品列表（仅列出默认启用的5个）

| 产品 | name | is_active |
|------|------|-----------|
| WEB应用防护系统(WAF) | WEB应用防护系统(WAF) | 1 |
| 网络入侵防护系统(IPS) | 网络入侵防护系统(IPS) | 1 |
| 下一代防火墙(NF) | 下一代防火墙(NF) | 1 |
| 绿盟远程安全评估系统(RSAS) | 绿盟远程安全评估系统(RSAS) | 1 |
| 威胁预警系统(UTS) | 威胁预警系统(UTS) | 1 |

其余73个产品的 `is_active` 默认为 0。

## API 设计

### GET /api/options/products/all

返回所有产品（含 is_active 状态）。

**响应**：
```json
{
  "code": 0,
  "data": [
    {
      "id": 1,
      "name": "WEB应用防护系统(WAF)",
      "display_name": "WEB应用防护系统(WAF)",
      "is_active": 1,
      "health_status": "ok",
      "last_collected_at": "2026-06-12T10:00:00",
      "package_type": "rule"
    },
    {
      "id": 2,
      "name": "网络入侵防护系统(IPS)",
      "is_active": 1,
      "health_status": "unknown",
      "last_collected_at": null,
      "package_type": "sys"
    }
  ]
}
```

### PATCH /api/options/products/:id

单个启用/禁用产品。

**请求体**：
```json
{
  "is_active": 0
}
```

### POST /api/options/products/batch

批量启用/禁用产品。

**请求体**：
```json
{
  "ids": [1, 2, 3],
  "action": "enable"
}
```

或：

```json
{
  "ids": [4, 5, 6],
  "action": "disable"
}
```

## 字段说明

| 字段 | 说明 |
|------|------|
| is_active | 控制是否参与采集（=0 时 scheduler 跳过该产品） |
| health_status | 上次采集结果：ok=成功 / error=失败 / unknown=从未采集 |
| last_collected_at | 最后一次成功采集的时间（UTC） |
| package_type | 采集时发现的包类型（JSON），供前端树状展示 |

## 采集流程中的角色

`content_sources.is_active` 在 scheduler 中使用：

```python
# scheduler.py — 加载采集源时过滤
sources = list_sources('nsfocus')
active_sources = [s for s in sources if s.get('is_active')]
```

禁用产品不会删除已采集的快照数据，只是跳过新的采集。

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/models/content_source.py` | content_sources 数据访问层 |
| `src/web/routes/api_routes.py` | 产品管理 API |
| `data/initial_sources.json` | 初始产品数据（78个产品定义） |
| `src/collectors/nsfocus.py` | 采集器（根据 is_active 决定是否采集） |