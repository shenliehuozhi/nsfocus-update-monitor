# Bug 记录

---

## Bug #001 — 路由跳转页污染 session 上下文

**严重级别**: 高
**状态**: 已识别，已修复（部分）
**发现时间**: 2026-05-15
**编号**: BUG-001

### 现象
discover 阶段递归访问 `/update/selectPro/id/*`（NCSS-I 产品入口）后，绿盟服务器将 session 上下文切换至"虚拟化版本"。后续所有 BVS 请求返回的下载链接从 `downloads/id`（标品）变成 `downloadsVm/id`（虚拟化），导致 BVS 标品数据采集为 0。

### 根因
- `/update/selectPro/id/2` 是服务器端路由跳转页，非 HTTP 302 重定向
- 访问后服务器 rewrite `resp.url` 为 `/update/upLic`（上传证书页）
- 服务器端 session 上下文被永久切换至虚拟化版本，直到新 cookie
- `_fetch` 只检测 HTTP 状态码，未检测 URL 路径变化

### 修复措施

#### 已实施
1. **Session 分组**：数据库 `user_sessions` 新增 `purpose`（discover/collect）和 `collect_mode`（standard/vm）字段，discover 和采集使用独立 session cookie，互相隔离
2. **Discover 独立 Session**：collector 新增 `_discover_session`（独立 `requests.Session`），discover 全程使用该 session，被污染不影响采集 session
3. **RedirectToLicenseError**：`_fetch` 和 `_fetch_discover` 检测到 `resp.url` 含 `/update/upLic` 时抛出 `RedirectToLicenseError`，recurse 捕获后跳过该 path，记录日志
4. **健康检查 URL 可配置**：默认改为 `/update/listBvsV6/v/bvssys`（BVS 专用页），同时检测 `/update/upLic` 跳转，验证 session 有效性时能发现上下文污染

#### 待实施
- [ ] `verify_session` 对 discover session 检测 `/update/upLic` 时，提示用户需要标品 session
- [ ] 采集 session 只接受 standard mode session，vm mode session 用于采集虚拟化数据

### 验证方法
```bash
# 使用 discover session 访问污染源，应跳过而非污染
curl -b "PHPSESSID=<discover_cookie>" "https://update.nsfocus.com/update/selectPro/id/2"
# 应返回 RedirectToLicenseError，日志显示"跳过（/upLic 跳转）"

# 使用 collect standard session 访问 BVS，应仍是标品链接
curl -b "PHPSESSID=<collect_standard_cookie>" "https://update.nsfocus.com/update/listBvsV6/v/bvssys"
# resp.text 应含 downloads/id（非 downloadsVm/id）
```

### 污染源列表（已知）
| URL | 产品 | 影响 |
|-----|------|------|
| `/update/selectPro/id/2` | NCSS-I | BVS 从标品变虚拟化 |

---

## Bug #002 — full/delta 模式历史遗留

**严重级别**: 低
**状态**: 待确认
**发现时间**: 2026-05-16
**编号**: BUG-002

### 现象
scheduler 的 `run_now(mode='full')` 调用 `_collect_full` + `_check_package_types_fresh`，其中 `_check_package_types_fresh` 会再次调用 `discover_package_types`。但用户表示产品管理 UI 的"自动发现"功能已接管 discover 逻辑，full 模式的 discover 部分是冗余的。

### 待确认事项
1. scheduler full 模式的 `_check_package_types_fresh` 是否可删除？
2. delta 模式直接 redirect 到 `_collect_quick`，是否仍有存在价值？
3. 当前 `mode='delta'` 在 UI 上是否还有入口？

### 处理建议
- 确认 full 模式采集部分（`_collect_full`）是否仍被使用
- 确认后清理 `_check_package_types_fresh` 相关调用
- delta 模式如有独立入口，可保留或合并至 quick

---

## Bug #003 — discover/confirm/status SSE 锁未初始化导致 500

**严重级别**: 高
**状态**: 已修复
**发现时间**: 2026-05-16
**编号**: BUG-003

### 现象
点击「确认应用变更」后，按钮显示「处理中…」但进度永远不更新，一直卡住直到超时。前端无报错，后端 `/api/system/products/discover/confirm/status` 返回 HTTP 500。

### 根因
`discover_confirm_status()` (GET `/products/discover/confirm/status`) 是 SSE 端点，Flask 用 `stream_with_context(generate())` 懒执行。SSE 接入时生成器还未启动，真正执行到 `with _confirm_lock:` 时已经是第一次 `yield` 之后了——此时 `_confirm_lock` 还是模块级 `None`，导致 `TypeError: cannot access 'NoneType' with 'with'`。

POST `/products/discover/confirm` 有 lazy-init 保护：
```python
if _confirm_lock is None:
    _confirm_lock = threading.Lock()
```
但 GET 没有。

### 修复措施
在 `discover_confirm_status()` 的 `generate()` 函数开头加同等保护：
```python
global _confirm_lock
if _confirm_lock is None:
    import threading
    _confirm_lock = threading.Lock()
```

### 验证方法
```bash
# 重启服务后
curl -H "Authorization: Bearer <token>" \
  'http://127.0.0.1:9999/api/system/products/discover/confirm/status' \
  --max-time 3
# 应返回 200 + SSE stream，不应返回 500
```
