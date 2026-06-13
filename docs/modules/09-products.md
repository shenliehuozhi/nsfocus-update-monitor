# 模块九：产品管理与自动发现 (Products)

## 功能说明

产品管理用于控制哪些绿盟产品参与采集，以及自动发现每个产品下有哪些版本分支和包类型。

---

## 产品入口地址（内置静态配置）

系统内置 6 个产品的入口路径：

```python
PRODUCTS = {
    'WAF':  '/update/wafIndex',
    'IPS':  '/update/listIps',
    'IDS':  '/update/listIds',
    'RSAS': '/update/listAuroraIndex',
    'NF':   '/update/ListNf',
    'UTS':  '/update/bsaUtsIndex',
}
```

但这只是入口，实际运行时从 `content_sources` 表读取 `entry_url` 和 `is_active` 状态。

---

## 自动发现流程（discover_package_types）

### 调用时机

1. **首次确认时**：用户在 UI 点击"确认"一个新发现结果
2. **手动触发**：通过 API `POST /api/system/discover` 触发
3. **产品首次启用时**：scheduler 发现某产品 `package_type_discovered` 为空，自动触发

### 递归抓取算法

```
discover_package_types(source_id, session_cookie)
        ↓
抓取入口页面 HTML（/update/wafIndex）
        ↓
提取页面中的 section 标题（ser_c_b_tit 标记）
        示例："WEB应用防护系统(WAF)列表"
        ↓
提取顶级链接（排除侧边栏）：
  _extract_content_links(html)
        ↓
  排除 _is_sidebar_link() 和 _is_stopped() 的链接
        ↓
对每个顶级链接开始递归（depth ≤ 6）：
        ↓
  recurse(page_url, chain=[], depth=0)
        │
        ├── depth > 6 → 返回（防止无限递归）
        ├── 已访问过 → 返回（防止环形链接）
        │
        ├── 获取页面 HTML
        │
        ├── 判断是否为最终包页面：
        │   _extract_table_items(html) → 有记录？
        │       ├── 有 → 这是最终页，记录 chain[-1] 为包类型名
        │       │      paths.append({chain, types: [type_name], url})
        │       └── 无 → 继续递归
        │
        ├── 提取子链接（排除侧边栏和停止链接）
        │
        └── 对每个子链接递归：
            recurse(sub_url, chain + [sub_text], depth+1)
```

### 递归深度示例

WAF 的典型结构（3层）：

```
入口：/update/wafIndex
  ├── V6.0.9（顶级链接）
  │     ├── 规则升级包（子链接）
  │     │     └── 表格页（最终页）→ 包类型="规则升级包"，url="/update/wafIndex/v/6.0.9/rule"
  │     └── 系统升级包（子链接）
  │           └── 表格页（最终页）→ 包类型="系统升级包"，url="/update/wafIndex/v/6.0.9/sys"
  └── V6.0.8（顶级链接）
        └── 规则升级包
              └── 表格页 → 包类型="规则升级包"，url="/update/wafIndex/v/6.0.8/rule"
```

RSAS/NF 的深层结构（4层，变量深度）：

```
入口：/update/listAuroraIndex
  ├── RSAS V6.0（顶级）
  │     ├── Web漏洞扫描（第二层）
  │     │     ├── 规则库（第三层）
  │     │     │     └── 表格页 → url
  │     │     └── 升级包
  │     │           └── 表格页 → url
  │     └── 系统卷（第二层）
  │           └── 表格页 → url
  └── RSAS V5.6（顶级）
        └── ...
```

### /upLic 重定向处理

有些页面需要虚拟机环境，访问时 302 重定向到 `/update/upLic`：

```python
except RedirectToLicenseError:
    # 记录为 VM 类型（url=None），采集时会跳过
    paths.append({
        'chain': chain,
        'types': [type_name],
        'url': None,
        'vm': True
    })
```

`_collect_quick` 会自动跳过 `url=None` 的路径。

### 发现结果存储

发现结果存入 `content_sources.package_type_discovered`：

```json
{
  "types": ["规则升级包", "系统升级包", "威胁情报升级包"],
  "paths": [
    {
      "chain": ["WEB应用防护系统(WAF)", "V6.0.9", "规则升级包"],
      "types": ["规则升级包"],
      "url": "/update/wafIndex/v/6.0.9/rule"
    }
  ],
  "modes": {
    "规则升级包": "auto",
    "系统升级包": "auto"
  }
}
```

### section 标题提取

页面 HTML 中包含 `ser_c_b_tit` 标记的区域标识产品分类区块：

```python
# 从入口页面提取 section 标题
for sec_match in re.finditer(r"ser_c_b_tit['\">]\s*([^<]+?)\s*</div>", html):
    sec_title = sec_match.group(1).strip()
    # 找到该 section 内所有链接，建立 url→section 映射
    section_titles[link_url] = sec_title
```

---

## 包类型变化检测（diff_package_types）

每次发现完成后，对比新旧 `package_type_discovered`：

```python
diff = NsfocusCollector.diff_package_types(old_discovered, new_discovered)

# added_paths: 新增的路径（产品线新增了某个版本或包类型）
# deleted_paths: 消失的路径（某个版本下线）
# modified_paths: 路径存在但包类型变化
```

**比较主键**：`chain + types` 的组合字符串（不用 URL，因为同一 URL 可能有细微变化）

---

## 侧边栏链接过滤（_is_sidebar_link）

很多链接指向其他产品入口侧边栏，需要排除：

```python
# 需要排除的侧边栏 URL 模式
IS_SIDEBAR_PATTERNS = [
    r'/bmgIndex$', r'/cdgIndex$', r'/bsaIndex$', r'/bsaUtsIndex$',
    r'/listEspcL$', r'/listDms$', r'/DsitIndex$', r'/DsdbIndex$',
    r'/listIds$', r'/listIps$', r'/listTac', r'/listScm',
    r'/wafIndex$', r'/ListNf$', r'/listAuroraIndex$',
    ...
]
```

判断逻辑：

```python
def _is_sidebar_link(url: str) -> bool:
    return any(re.search(p, url) for p in IS_SIDEBAR_PATTERNS)
```

---

## 停止链接过滤（_is_stopped）

有些链接虽然格式正确但指向"已停止维护"的版本（页面中有 `default` 标记）：

```python
def _is_stopped(url: str, html: str) -> bool:
    pos = html.find(url)
    context = html[max(0, pos-100):pos+len(url)+50]
    return 'default' in context  # 有 "default" 标记说明该链接已停止
```

---

## 产品管理 API

### GET /api/options/products/all

返回所有产品（含 is_active、health_status、package_type_discovered）：

```json
{
  "code": 0,
  "data": [
    {
      "id": 1,
      "name": "WEB应用防护系统(WAF)",
      "display_name": "WEB应用防护系统(WAF)",
      "entry_url": "/update/wafIndex",
      "is_active": 1,
      "health_status": "ok",
      "last_collected_at": "2026-06-12T10:00:00",
      "package_type": "rule,sys",
      "package_type_discovered": "{\"types\":[\"规则升级包\"],\"paths\":[...]}"
    }
  ]
}
```

### PATCH /api/options/products/:id

单个启用/禁用产品：

```json
// 禁用
{ "is_active": 0 }

// 启用
{ "is_active": 1 }
```

### POST /api/options/products/batch

批量操作：

```json
// 批量启用
{ "ids": [1, 2, 3], "action": "enable" }

// 批量禁用
{ "ids": [4, 5, 6], "action": "disable" }
```

### POST /api/system/discover

触发自动发现（后台执行）：

```json
{
  "source_id": 1,
  "session_id": 2
}
```

---

## 数据模型

### content_sources 表

```sql
CREATE TABLE content_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    entry_url TEXT,                     -- 如 "/update/wafIndex"
    is_active INTEGER DEFAULT 1,         -- 采集开关
    health_status TEXT DEFAULT 'unknown', -- ok/error/unknown
    last_collected_at TEXT,              -- UTC 时间戳
    -- 包类型发现
    package_type TEXT,                   -- 逗号分隔的包类型（聚合显示）
    package_type_discovered TEXT,        -- JSON，发现的完整路径和类型
    package_type_changed INTEGER DEFAULT 0,  -- 每次发现后是否变化
    ...
)
```

---

## 健康状态（health_status）

| 值 | 含义 | 触发条件 |
|----|------|---------|
| `ok` | 健康 | 采集该产品时所有页面返回 200 且有内容 |
| `error` | 异常 | 采集时遇到网络错误或 Session 失效 |
| `unknown` | 未知 | 尚未采集过，或首次启用 |

---

## 相关文件

| 文件 | 说明 |
|------|------|
| `src/collectors/nsfocus.py` | `discover_package_types()` 递归发现算法 |
| `src/web/routes/system_routes.py` | discover/confirm API 路由 |
| `src/models/content_source.py` | content_sources 数据访问层 |
| `src/core/scheduler.py` | 调度器调用发现流程 |
