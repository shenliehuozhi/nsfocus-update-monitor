# VM 路径（空URL）标记问题 — 待办

**状态**: 待处理
**优先级**: 中
**创建时间**: 2026-05-19
**标签**: `nsfocus` `vm` `discover` `前端标记`

---

## 问题描述

302 跳转到 `/upLic` 的路径（VM 虚拟化路径）需要在前端标记为 `🖥 VM`，目前有 4 处空 URL 判断逻辑，但根本问题是：**discover 阶段 VM 分支从未被触发**，导致 DB 里 `vm=True` 的记录数为 0。

---

## 现状梳理

### 1. 三处空 URL 判断逻辑

#### discover（nsfocus.py 行 356-358）
```python
for p in pkg_data.get('paths', []):
    url = p.get('url')
    if not url:
        continue   # 跳过，不加入 discovered paths
```

#### _collect_quick（nsfocus.py 行 355-358）
```python
for p in pkg_data.get('paths', []):
    url = p.get('url')
    if not url:
        continue   # 跳过，不采集此路径
    paths_urls.append((url, ver, pkg_type))
```

#### 前端 _chainToUrlMap（index.html 行 1513-1527）
```javascript
if (p.url) {
    _chainToUrlMap[key] = { url: p.url, vm: p.vm || false };
} else if (p.vm) {
    // VM path: store with null url
    _chainToUrlMap[key] = { url: null, vm: true };
}
```

---

### 2. discover 阶段 VM 分支代码（从未触发）

**nsfocus.py 行 196-204**（`discover_package_types` 内的 `recurse`）：
```python
except RedirectToLicenseError:
    _log('  ' * (depth + 2) + f'  ► /upLic 重定向，标记为 VM 类型')
    type_name = chain[-1] if chain else self._clean_version(name) or name
    if type_name not in all_types:
        all_types.append(type_name)
    paths.append({'chain': [name] + list(chain), 'types': [type_name], 'url': None, 'vm': True})
    return
```

**抛出条件**（`_fetch_discover` 行 604-605）：
```python
if '/update/upLic' in resp.url:
    raise RedirectToLicenseError('Redirected to /update/upLic — session context switched')
```

---

### 3. 实际数据

- discover.log 最新记录：无 `► /upLic` 日志
- 历史记录（5月16日）：34 条 `跳过（/upLic 跳转，session 上下文污染）`（旧代码）
- DB 查询（所有 content_sources）：
  - `vm=true` 路径数：**0**
  - `url=None` 路径数：**0**

**结论**：新代码的 VM 分支从未被触发过。

---

### 4. 前端已完成的修改

以下修改已完成，但依赖后端实际产生 `vm=true` 数据才生效：

| 位置 | 修改内容 | 行号 |
|------|----------|------|
| `buildDataTreeHtml` | 叶子节点存储 `node.__isVm = p.vm \|\| false` | 4867 |
| `buildDataTreeHtml` | 叶子节点渲染加 `🖥` badge | 5102 |
| `_chainToUrlMap` | 存 `{url, vm}` 对象 | 1513-1527 |
| `_chainToUrlMap` | VM 路径存 `{url: null, vm: true}` | 1526 |
| `buildChainLinks` | 新增 `leafVm` 参数，叶子返回 `isVm` | 1950-1990 |
| `dataRenderRight` header | 链路最后节点加 `🖥` | 1811-1825 |
| `dataRightToggleRow` | 详情行叶子节点加 `🖥` | 2053-2067 |
| `matchedPath` | URL匹配失败时按 hints 回退查找 | 2055-2073 |

---

## 待办事项

### 高优：确认 VM 路径 redirect 检测逻辑

**问题**：302 跳转后 `resp.url` 是否包含 `/update/upLic`？

- 如果包含 → 检测应工作，需确认为何从未触发
- 如果不包含 → 需要检测 `Location` 响应头

**验证方法**：手动对已知 VM 产品执行一次 discover，观察日志

### 中优：前端移除中间节点 vmBadge

当前代码中 `propagateHints` 已在叶子存储 `__isVm`，但中间节点无 vm 传播逻辑（正确）。**无需改动**。

### 低优：前端 header/详情行 vmBadge 样式

当前使用 `🖥` 纯文本 emoji，用户未提出异议，**保持现状**。

---

## 相关文件

- `/root/nsfocus-monitor/src/collectors/nsfocus.py` — 收集器，discover + _collect_quick
- `/root/nsfocus-monitor/src/core/scheduler.py` — 调度器，调用 discover
- `/root/nsfocus-monitor/src/web/templates/index.html` — 前端模板
- `/root/nsfocus-monitor/data/discover.log` — 发现日志
- `/root/nsfocus-monitor/data/nsfocus_monitor.db` — SQLite 数据库
