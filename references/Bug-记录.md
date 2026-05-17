# Bug 记录

---

## Bug #007：树形结构 targetId 冲突导致点击错乱

**严重程度**：高

**日期**：2026-05-17

**产品**：绿盟升级监控 Web UI（index.html）

**现象**：
- 网络入侵防护系统(IPS)下 55 条路径，叶子节点"引擎升级包"重复 16 次，点击展开只找到第一个，其余无反应或错乱
- 下一代防火墙(NF-B/NF-D) 等 19 个产品的路径 URL 为空，点击完全无反应

**根因**（两处）：

### 根因 1：targetId 只用叶子短名称，不唯一

代码位置：`buildDataTreeHtml` 的 `renderNode` 函数（index.html，约 line 1344-1350）

```javascript
// 错误写法
targetId = 'pktbl_' + (node.__pkgKey || name)
    .replace(/([^\x00-\x7F])/g, ...)
    .replace(/\W/g, '_');
```

IPS 里"引擎升级包"出现 16 次（不同版本各一份），所有叶子节点共享同一个 `targetId=pktbl_引擎升级包`。浏览器 `getElementById` 只返回第一个匹配的 DOM 元素。

### 根因 2：空 URL 路径不生成 targetId

```javascript
// 错误写法
if (isPkg && node.__pathId) {  // node.__pathId 为空字符串时跳过
    targetId = 'pktbl_' + node.__pathId ...
}
```

NF-B/NF-D 等产品 32 条路径全部 URL 为空，`node.__pathId` 为 `''`，条件不满足，`targetId=null`，`pkgTableToggleById(null)` 直接返回。

**修复方案**：

1. **targetId 生成改用 `__pathId`（URL）**（index.html line 1346-1354）：
   ```javascript
   let targetId = null;
   if (isPkg) {
     const rawId = node.__pathId
       ? node.__pathId
       : ('|' + (node.__chain || []).join('|') + '|idx=' + node.__pathIdx);
     targetId = 'pktbl_' + rawId
         .replace(/[^\x00-\x7F]/g, function(m) { return '_' + m.charCodeAt(0).toString(16) + '_'; })
         .replace(/\W/g, '_');
   }
   ```
   - URL 非空时用 URL：`/update/listNewipsDetail/v/engine5.6.11`（唯一）
   - URL 为空时用 `完整chain路径 + pathIdx`（区分重复 chain）

2. **移除 `node.__pathId` 条件判断**（line 1346）：
   ```javascript
   if (isPkg && node.__pathId)  →  if (isPkg)
   ```

3. **`__pathIdx` 属性**：在树构建时通过 `paths.forEach((p, pathIdx) => ...)` 传入，附加到叶子节点的 `__pathIdx` 字段，确保重复 chain 能区分。

**修复文件**：`src/web/templates/index.html`

**影响范围**：
- 22 个产品有叶子节点名称重复（最严重 IPS ×16）
- 19 个产品有空 URL 路径（最严重 NF-B/NF-D ×32）
- 修复后所有 733 条路径的 targetId 唯一

**验证方法**：
1. 打开「采集数据」页面，展开 IPS → 网络入侵防护系统 5.6.11 → 引擎升级包，确认点击能展开详情
2. 展开 IPS 的"引擎升级包" x16 个不同版本，确认每个都能独立点击
3. 展开 NF-B/NF-D，点击各子路径确认有响应（空包显示"暂无采集数据"提示）

---

## Bug #008：包详情发布时间/发现时间未做时区转换

**严重程度**：中

**日期**：2026-05-17

**产品**：绿盟升级监控 Web UI（index.html）

**现象**：
- 包详情弹窗中「发布: 2026-05-12T09:05:51」比实际时间早 8 小时（未从 UTC 转换到 CST）
- 「发现」字段同样问题

**根因**：

代码位置：`index.html` line 1400-1401

```html
<!-- 错误写法：直接渲染 UTC 时间字符串，未经过 fmtTZ 转换 -->
${s.published_at ? `<b>发布:</b><span>${s.published_at}</span>` : ''}
${s.first_seen_at ? `<b>发现:</b><span>${s.first_seen_at}</span>` : ''}
```

`published_at` 存储在 DB 时已转换为 UTC（`2026-05-12T09:05:51`），前端直接显示成了 UTC 而非 CST。

**修复方案**：

```html
<!-- 正确写法：经过 fmtTZ 转换为本地时间 -->
${s.published_at ? `<b>发布:</b><span>${fmtTZ(s.published_at, true)}</span>` : ''}
${s.first_seen_at ? `<b>发现:</b><span>${fmtTZ(s.first_seen_at, true)}</span>` : ''}
```

**修复文件**：`src/web/templates/index.html` line 1400-1401

**时区规范**（全局铁律）：
- DB 存储 UTC（`datetime('now')` in SQLite = UTC）
- 前端统一经过 `fmtTZ()` 显示本地时间（CST）
- `fmtTZ` 使用 `getHours()/getMinutes()`（非 `toISOString()`）来获取本地时间
- 严禁直接渲染 UTC 时间字符串

**验证方法**：
1. 找一个有 `published_at` 的包，点击展开详情
2. 确认时间显示为「发布: 2026-05-12 17:05:51」（+8小时）而非「2026-05-12 09:05:51」

---

## Bug #009：Session 保活只维护 sessions[0]，discover session 未保活

**严重程度**：高

**日期**：2026-05-17

**产品**：绿盟升级监控核心（scheduler.py）

**现象**：
- DB 中存在 2 个 active session（id=9 discover、id=10 collect）
- 但 session 10 从创建到过期只心跳了 1 次，session 9 连续 19 次
- 原因：`_session_heartbeat` 只对 `sessions[0]` 发请求，session 10 排序靠后完全被忽略

**根因**：`_session_heartbeat` 代码（scheduler.py line 827-829）：

```python
cookie = sessions[0]['cookie_value']       # 只取第一个
session_id = sessions[0]['id']
_collector._set_cookie(cookie)              # 只维护第一个 session 的 cookie
```

`get_active_sessions()` 返回按 `last_valid DESC` 排序的所有 session，session 9 排序靠前所以一直保活，session 10 排在后面从未被心跳。

**修复方案**：

1. **遍历所有 active session 发心跳**：
```python
for sess in sessions:
    cookie = sess['cookie_value']
    session_id = sess['id']
    purpose = sess.get('purpose', 'collect')
    # 用 requests 直接发请求，不需要 collector 实例
```

2. **污染检测只针对 collect session**：`downloadsVm/id` 在响应 body 中 → 污染，标记 expired

3. **Session 过期检测针对所有 session**：302 重定向到 /upLic 或 /portal、200 但含"登录"且无 `ser_c_b_con` → 过期

**修复文件**：`src/core/scheduler.py` `_session_heartbeat` 函数（line 808-892）

**教训**：遍历 session 发心跳时，不能假设 `sessions[0]` 包含了所有需要保活的 session。必须遍历全部。

---

## 问题记录 #001：Session 过期判断中的"登录页"误判

**日期**：2026-05-17

**现象**：Session `i110lstkt4ok0e8a2fnt33m3rf` 在心跳检测中被判断为"登录页过期"，但实际该 session 是有效的。

**根因**：heartbeat 过期判断条件：

```python
if resp.status_code == 200 and 'ser_c_b_con' not in resp.text and '登录' in resp.text:
```

绿盟的 BVS 列表页（`/update/listBvsV6/v/bvssys`）返回 200 且页面包含 `ser_c_b_con`（说明是正常的 BVS 格式页面），但 HTML 标题是「售后服务_客户支持_服务与支持_绿盟科技」，页面中也包含"登录"字样（是页面版权信息等非登录框的内容）。因此这个条件被错误触发。

**验证方法**：用有效 session curl 该 URL：
```bash
curl -s "https://update.nsfocus.com/update/listBvsV6/v/bvssys" \
  --cookie "PHPSESSID=i110lstkt4ok0e8a2fnt33m3rf" | grep "downloads/id"
# 返回 97 处匹配，页面是正常的 BVS 包升级页面
```

**结论**：当前判断条件中的 `'登录' in resp.text` 过于宽泛。需要改为更准确的判断——只有当页面确实没有 `ser_c_b_con` 且同时包含"登录"字样时才能判定为登录页（这个条件本身是对的，但在 BVSSYS 页面上因为页面结构原因被误判）。

**正确的过期判断逻辑**：
1. `ser_c_b_con` 不存在 → 可能是登录页（但 BVSSYS 有 `ser_c_b_con`，所以这个条件不会被触发）
2. 当前代码中 `ser_c_b_con` 不存在 AND `登录` 存在才判断为过期，对于 BVSSYS 页面 `ser_c_b_con` 存在，所以不会被判断为过期——**实际当前代码逻辑是正确的，不存在误判**。

**教训**：判断 session 是否过期时，要以页面核心功能字段（`ser_c_b_con`）为准，不能以"登录"字样作为主要判断依据。"登录"字样在绿盟页面中可能出现在版权、页脚等位置，不是登录框的标志。

---

## Bug #008：session 心跳时间未做时区转换

**严重程度**：低

**日期**：2026-05-18

**现象**：前端 session 列表表格中"最近心跳"列显示 UTC 时间（比实际 CST 早 8 小时）。

**根因**：`last_heartbeat_at` 存储 UTC（SQLite `datetime('now')`），前端直接用 `substring(0,16)` 截取原始字符串，未经过 `fmtTZ()` 转换为本地时间。

**涉及文件**：
- `src/web/templates/index.html` 第 428 行：`hbTime = s.last_heartbeat_at ? s.last_heartbeat_at.substring(0,16) : '—'`

**修复**：改为 `s.last_heartbeat_at ? fmtTZ(s.last_heartbeat_at, true) : '—'`

**时区规范（全局铁律）**：
- DB 存储 UTC（`datetime('now')` in SQLite = UTC）
- 前端统一经过 `fmtTZ()` 显示本地时间（CST）
- `fmtTZ` 使用 `getHours()/getMinutes()`（非 `toISOString()`）来获取本地时间
- 严禁直接渲染 UTC 时间字符串

---

## Feature #001：心跳防检测机制

**日期**：2026-05-18

**目标**：降低心跳请求被识别为机器行为的特征。

**实现（三层）**：

| 层级 | 机制 | 参数 |
|------|------|------|
| 1 | 函数入口随机延迟 | 0-30s jitter（`random.uniform(0, 30)`） |
| 2 | 5% 跳过概率 | 模拟人工巡逻间隙（`random.random() < 0.05`） |
| 3 | session 间延迟 | 2-15s 随机间隔（`random.uniform(2, 15)`） |

**效果**：
- 心跳触发时间不再与整点对齐
- 有 5% 概率本轮静默（不像固定任务的刚性执行）
- 多 session 时请求分散在 2-15s 区间，而非瞬间并发

**涉及文件**：
- `src/core/scheduler.py` — `_session_heartbeat()` 函数（第 809-912 行）
- 新增顶层 import：`import random`（第 8 行）