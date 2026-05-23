# SQLite 锁竞争问题根因分析

**作者**：Hermes Agent + 用户共同排查
**日期**：2026-05-23
**项目**：nsfocus-monitor
**状态**：已解决

---

## 一、现象

系统出现大量 `database is locked` 警告：
- 采集时每秒持续出现 locked 日志
- 添加客户、修改系统设置等 HTTP 请求返回 500
- 页面长时间无响应

---

## 二、数据库配置现状

```python
# src/models/database.py
DB_PATH = 'data/nsfocus_monitor.db'

# 连接配置
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.execute("PRAGMA busy_timeout=10000")  # 10秒等待
conn.execute("PRAGMA journal_mode=WAL")     # WAL 模式
```

| 参数 | 值 | 说明 |
|------|-----|------|
| `busy_timeout` | 10000ms | 写锁竞争时最多等10秒 |
| `journal_mode` | WAL | Write-Ahead Logging |
| `check_same_thread` | False | 允许多线程使用同一连接 |

---

## 三、SQLite 的并发模型

SQLite 是**串行写入**的数据库——同一时刻只能有**一个写事务**。

```
读操作：任意数量，可并发
写操作：同一时刻只有1个，其余等待 busy_timeout
```

**WAL 模式**的优势：
- 读写不互斥（读不阻塞写，写不阻塞读）
- 多个线程可以同时读
- 写操作串行化，但等待时间极短（毫秒级）

**理论上**：WAL + 10秒 busy_timeout 应该足够应对正常并发写。

---

## 四、系统中的写操作来源

```
┌─────────────────────────────────────────────────────────────┐
│  HTTP 请求线程（Flask，threaded=True 默认）                  │
│  ├─ 添加客户          → 写 customers 表                     │
│  ├─ 修改系统设置      → 写 system_settings 表               │
│  ├─ 订阅规则 CRUD     → 写 subscriptions 表                 │
│  └─ 其他 API 写操作   → 写各类表                            │
├─────────────────────────────────────────────────────────────┤
│  APScheduler 后台调度线程（scheduler.py）                    │
│  ├─ run_now() → save_snapshot() → 写 snapshots 表          │
│  ├─ touch_active_snapshots() → 写 source_snapshots 表      │
│  ├─ update_heartbeat() → 写 user_sessions 表               │
│  └─ reschedule_collect() → 读 system_settings（读锁）      │
├─────────────────────────────────────────────────────────────┤
│  其他后台线程                                                 │
│  ├─ 产品发现 ThreadPoolExecutor → 写 products/discoveries   │
│  └─ 推送通知线程 → 读 subscribers/customers/channels        │
└─────────────────────────────────────────────────────────────┘
```

**核心问题**：多线程并发写库 + WAL auto-checkpoint

---

## 五、真正的根因：三层嵌套

### 第一层：Flask 多线程并发写

```python
# run.py（修复前）
app.run(host='0.0.0.0', port=9999)  # 默认 threaded=True
```

`threaded=True` 时，每个 HTTP 请求在独立线程中执行：
- 请求A 正在写 `system_settings`（INSERT OR REPLACE）
- 请求B 同时写 `snapshots`（采集线程也在写）
- 请求C 同时写 `customers`
- scheduler heartbeat 线程也在同时写 `user_sessions`

4个写者同时竞争 WAL 写锁，但都能在 10秒内拿到锁，所以**不会根本性崩溃**。

### 第二层：finally 块崩溃导致状态机永久卡死

```python
# scheduler.py run_now()（修复前）
def run_now(mode=None):
    try:
        _set_collection_running()
        do_collection()
    finally:
        _clear_collection_running()  # ← 遇到 locked 时崩溃！
        _is_running = False           # ← 从未执行
```

```python
def _clear_collection_running():
    execute("UPDATE collection_running SET status='0', ...")  # ← 遇 locked 抛异常
```

**崩溃链**：
```
finally 遇 locked → sqlite3.OperationalError: database is locked
    ↓
函数异常上冒，finally 中后续代码不执行
    ↓
_is_running 永远卡在 True
    ↓
run_now() 每次进入时检查 _is_running，直接 return 认为"采集中"
    ↓
采集永远不真正运行完成
    ↓
同时 heartbeat 每分钟继续写 user_sessions（正常，不崩溃但积累大量锁等待）
```

### 第三层：touch_active_snapshots 每产品单独 UPDATE

```python
# scheduler.py（修复前）
for source_id in touched_sources:
    touch_active_snapshots(source_id)  # 每次单独 SQL
```

78个 source = 78次单独的 UPDATE 语句，每次持锁时间虽短但串行累积。

---

## 六、修复方案

### 修复 1：Flask 单线程模式

```python
# run.py（修复后）
app.run(host='0.0.0.0', port=9999, threaded=False)
```

**原理**：所有 HTTP 请求在同一线程中串行执行，请求之间不会并发写库。

**效果**：HTTP 请求和 scheduler 线程之间仍然并发（因为 scheduler 跑在独立 APScheduler 线程），但 HTTP 请求之间不再竞争。

### 修复 2：finally 块加 try-except

```python
# scheduler.py（修复后）
finally:
    try:
        _clear_collection_running()
    except Exception as e:
        logger.warning(f"Failed to clear collection_running: {e}")
    finally:
        _is_running = False
```

**原理**：防止 `_clear_collection_running()` 崩溃导致 `_is_running` 永远卡死。

### 修复 3：批量 touch_active_snapshots

```python
# snapshot.py（修复后）
def touch_active_snapshots(source_ids: list):
    execute("""
        UPDATE source_snapshots
        SET last_active_at = datetime('now')
        WHERE source_id IN ({})
    """.format(','.join('?' * len(source_ids))), source_ids)
```

```python
# scheduler.py（修复后）
touched_source_ids = []
for source_id in touched_sources:
    touched_source_ids.append(source_id)
# 批量一次更新
touch_active_snapshots(touched_source_ids)
```

**原理**：78次 UPDATE → 1次 UPDATE，锁持有时间从秒级降到毫秒级。

---

## 七、验证结果

```
# 重启前（threaded=True + finally崩溃）
database is locked 日志：6504 条
最后一条：2026-05-23 19:51:59

# 重启后（threaded=False + finally修复）
database is locked 日志：0 条（19:59 重启至今）
```

---

## 八、配置参数修改记录

| 参数 | 原值 | 新值 | 修改方式 | 说明 |
|------|------|------|----------|------|
| `collect_interval` | 1小时 | 24小时 | API PUT | 减少采集频率 |
| `heartbeat_interval` | 30分钟 | 10分钟 | API PUT | 更快感知 session 存活 |
| Flask `threaded` | True | False | run.py | 请求串行化 |

> **注意**：之前用 Python 脚本直接改 DB，但路径错误（`src/../data/` vs `data/`），改到了错误文件。通过 API 改才是正确方式。

---

## 九、经验教训

### 1. 数据库锁竞争的诊断方法

```
grep "database is locked" logs/app.log | wc -l
grep "database is locked" logs/app.log | tail -5  # 看最新条目时间
```

- 如果时间集中在重启前 → 历史积累
- 如果持续增长 → 仍有锁竞争

### 2. finally 块必须用 try-except 保护

任何在 finally 中调用数据库写操作的代码，都必须用 try-except 包裹：

```python
# 错误
finally:
    write_to_db()  # 如果这里崩溃，后续清理代码永远不会执行

# 正确
finally:
    try:
        write_to_db()
    except Exception as e:
        logger.warning(f"Cleanup write failed: {e}")
```

### 3. Flask threaded 模式的取舍

- `threaded=True`（默认）：适合低并发开发环境，但多线程写库可能竞争
- `threaded=False`：请求串行化，消除 HTTP 请求间并发，throughput 降低但稳定性提高
- 生产环境更好的方案：gunicorn/uwsgi 多进程 + 单 worker 线程

### 4. WAL + busy_timeout 的正确理解

- `busy_timeout=10000` 只解决**短暂锁等待**
- 不能依赖它解决**长时间持锁**（如采集崩溃、慢查询持锁）
- 真正的根因是**持锁时间过长**或**锁永远不释放**

### 5. 批量操作减少锁竞争

频繁的单条 UPDATE 应改为批量操作：

```python
# 错误：N次持锁
for item in items:
    execute("UPDATE ... WHERE id=?", (item,))

# 正确：1次持锁
execute("UPDATE ... WHERE id IN ({})".format(','.join('?'*len(items))), items)
```

---

## 十、相关代码文件

| 文件 | 关键改动 |
|------|----------|
| `run.py` | `threaded=False` |
| `src/models/database.py` | `busy_timeout=10000`，WAL 模式 |
| `src/models/snapshot.py` | `touch_active_snapshots(source_ids: list)` 批量 |
| `src/core/scheduler.py` | finally try-except，批量 touch，stale 检测 |
| `docs/analysis/sqlite-locking-root-cause.md` | 本文档 |

---

## 十一、后续优化方向

1. **gunicorn 多进程部署**：用 `gunicorn -w 4 --threads 1` 替代 Flask 内置多线程
2. **heartbeat 合并写入**：多 session 合并为一次批量 UPDATE
3. **采集结果缓冲写入**：内存中缓冲多个快照，一次事务写入
