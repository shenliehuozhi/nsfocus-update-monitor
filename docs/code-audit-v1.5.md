# 代码质量审计报告

> 版本：v1.5-security
> 日期：2026-05-24
> 审计范围：src/ 下所有 Python 文件

---

## 一、严重问题（必须修复）

### S1. scheduler.py:483 — 未定义变量 `emit` 引用

```python
_check_package_types_fresh(existing_sources, cookie, emit)
```

运行时错误：`emit` 未定义，会导致崩溃。

**修复方案**：删除此行或改为 `_emit`（若存在）。

---

### S2. dashboard.py:9 — 缺少 `@require_auth` 装饰器

```python
@bp.route('/dashboard', methods=['GET'])
def get_dashboard():  # ← 无认证保护
```

任何未登录用户可直接访问 `/api/dashboard`，获取系统敏感数据。

**修复方案**：添加 `@require_auth` 装饰器。

---

### S3. nsfocus.py:516 — 裸 `except:` 子句

```python
except: desc_parsed = {}
```

捕获所有异常（包括 `KeyboardInterrupt`、`SystemExit`），可能导致进程无法正常退出。

**修复方案**：改为 `except Exception:`。

---

### S4. change.py:183-184 — 时区不一致

```python
if valid_until and valid_until < datetime.now(timezone.utc):
```

`datetime.now(timezone.utc)` 返回 UTC 时间，但 `valid_until` 存储格式未确认时区，可能导致过期判断错误。

**修复方案**：统一使用 UTC 时间比较。

---

### S5. user.py:46 — 时区混用

```python
if datetime.now().isoformat() > banned_until:
```

其他模块使用 `datetime.utcnow()`，此处混用 localtime，若服务器时区非 UTC 会导致封禁时间判断错误。

**修复方案**：改为 `datetime.utcnow()`。

---

### S6. customer.py:43-44 — 动态 UPDATE SET 未验证列名

```python
sets = ', '.join(f'{k} = ?' for k in kwargs)
execute(f"UPDATE customers SET {sets} WHERE id = ?", ...)
```

若 `kwargs` 包含非列名字段会导致 SQL 错误。当前无白名单校验。

**修复方案**：添加列名白名单校验。

---

## 二、中等问题（应修复）

### M1. scheduler.py:298 — `execute.__self__` hack 永远返回 None

```python
existing = execute.__self__ if hasattr(execute, '__self__') else None
```

`execute` 是模块级函数，无 `__self__`，永远为 `None`，`enqueue()` 去重逻辑失效。

**影响**：重复推送。

---

### M2. subscription.py:298 — 同上

```python
existing = execute.__self__ if hasattr(execute, '__self__') else None
```

去重逻辑失效。

---

### M3. snapshot.py:42-44 — 查询不存在的列 `entry_url`

```python
rows = query("SELECT * FROM content_sources WHERE entry_url = ?", ...)
```

`content_sources` 表无 `entry_url` 列，查询永远返回空。

---

### M4. snapshot.py:333 — INSERT 引用不存在的列 `path_id`

```python
INSERT INTO snapshots ... (..., path_id) VALUES (..., ?)
```

`snapshots` 表无 `path_id` 列，会导致 SQLite 错误。

---

### M5. dashboard.py:9 — 缺少 `@require_auth`（同 S2）

---

### M6. system_routes.py:154 — 日志文件名校验不够严谨

```python
if not filename.endswith('.log') or '..' in filename or '/' in filename:
```

虽有基础校验，但可通过特殊字符绕过（如空字节注入）。

---

### M7. nsfocus.py:438,730 — 函数内嵌套 import

```python
import hashlib  # 在函数内部
```

违反 PEP8，应提到文件顶部。

---

## 三、轻微问题（建议修复）

### L1. apprise.py:20 — 冗余状态码

```python
if resp.status_code in (200, 200, 201):  # 200 重复
```

---

### L2. base.py:150 — Python 3.10+ 语法

```python
def send_confirmation(self, message: NotificationMessage, results: list[DeliveryResult], config: dict)
```

`list[]` 简写需 Python 3.9+，若项目支持更早版本会导致 `SyntaxError`。

---

### L3. rate_limiter.py:35 — Python 3.10+ 语法

```python
def _parse_iso(s: str | None):
```

`str | None` 语法需 Python 3.10+。

---

### L4. nsfocus.py — 重复 import

```python
from src.models.database import execute  # 出现3次
```

---

### L5. change.py — 每次调用重新 import

```python
_get_chain = None
try:
    from src.core.scheduler import _get_chain
except ImportError:
    pass
```

每次 `get_new_for_subscription` 调用都执行，且失败时静默降级。

---

### L6. notifiers — 静默吞掉错误

```python
except Exception: pass  # dingtalk.py:74, wecom.py:54, feishu.py:56, apprise.py:25
```

推送失败时静默，不记录日志。

---

## 四、汇总

| 等级 | 数量 |
|------|------|
| 严重 | 6 |
| 中等 | 7 |
| 轻微 | 6 |

---

## 五、修复优先级

| 优先级 | 问题 | 文件 |
|--------|------|------|
| P0 | `emit` 未定义崩溃 | scheduler.py:483 |
| P0 | 缺少认证 | dashboard.py:9 |
| P0 | 裸 except | nsfocus.py:516 |
| P0 | 时区混用 | change.py:183-184, user.py:46 |
| P1 | `execute.__self__` 去重失效 | scheduler.py:298, subscription.py:298 |
| P1 | 动态 UPDATE 未验证列名 | customer.py:43-44 |
| P2 | snapshot 查询不存在的列 | snapshot.py:42-44, 333 |
| P2 | 函数内嵌套 import | nsfocus.py |
| L | 其他轻微问题 | ... |

---

## 六、待确认

1. `snapshot.py` 的 `entry_url` 和 `path_id` 列是否真的不存在，还是迁移未执行？
2. `execute.__self__` hack 的实际意图是什么？是未完成的重构吗？
3. Python 版本要求是什么？（3.9/3.10+？）