# Database Is Locked 问题分析报告

**项目**：nsfocus-monitor（绿盟升级监控平台）
**分析时间**：2026-05-25
**编写人**：Hermes Agent

---

## 一、问题现象

系统日志中频繁出现以下错误：

```
Job "Session Heartbeat ..." raised an exception
sqlite3.OperationalError: database is locked

Job "Log Scanner..." raised an exception
sqlite3.OperationalError: database is locked
```

心跳线程日志显示：

```
[HEARTBEAT] _session_heartbeat invoked, _is_running=False
ERROR: database is locked
```

系统事件通知中出现大量 **Session 错误** 告警，但实际 session 状态正常。

---

## 二、根本原因分析

### 2.1 SQLite WAL 机制基础

SQLite 有三种日志模式，系统采用 **WAL（Write-Ahead Logging）** 模式：

```
正常模式（DELETE）：写操作 → 锁住整个数据库 → 写完释放
WAL 模式：写操作 → 写入独立的 WAL 文件 → 定期合并回主数据库
          ↑ 写操作不锁数据库，并发能力大幅提升
```

**WAL 文件里的数据最终要"合并回"主数据库（page by page）**，这个合并动作就叫 **checkpoint**。

WAL 文件如果一直只写不合并，会越来越大（`-wal` 文件可能几百 MB）。

### 2.2 checkpoint 的锁机制

绿盟采集配置的 `TRUNCATE` 模式：

```sql
PRAGMA wal_checkpoint(TRUNCATE)
```

含义：把 WAL 里未合并的内容全部写回主数据库，然后**把 WAL 文件清零**。

**问题所在**：checkpoint 过程中 SQLite 需要持有**独占锁**（Exclusive lock），把所有未提交的 WAL 内容一页一页地写回主数据库。这个过程可能持续数秒，期间任何其他写操作都会报 `database is locked`。

### 2.3 采集流程中的锁持有时间线

```
Stage 5 收尾：
  1. touch_active_snapshots (批量UPDATE)      ← 快速，毫秒级
  2. update_source_health (UPDATE)            ← 快速，毫秒级
  3. INSERT OR REPLACE last_full_scan_at       ← 快速，毫秒级
  4. PRAGMA wal_checkpoint(TRUNCATE)           ← ⚠️ 持独占锁，数秒
  5. INSERT OR REPLACE collection_running=0   ← ⚠️ 等锁释放后执行
```

心跳线程在第 4 步持锁期间执行 DB 写操作 → 触发 `database is locked`。

### 2.4 心跳函数的脆弱性

```python
# 心跳函数中，心跳结果写入DB时没有任何保护
update_heartbeat(session_id, '正常')      # ← 失败则整函数崩溃
log_heartbeat(session_id, '正常', ...)     # ← 未包裹 try-except
```

心跳函数在执行 HTTP 检查后，需要写 DB 记录心跳状态。未捕获 `database is locked` 异常，导致**整个心跳函数崩溃**，后续所有心跳均无法记录 `_last_heartbeat_success`，系统日志出现"从未有成功心跳"的误报。

---

## 三、修复过程（时间线）

### Phase 1：根除多进程并发（commit e14ef47）

**问题**：APScheduler 默认 `max_workers=10`，多个采集进程同时写 DB。
**修复**：设置 `max_workers=1`，确保同时只有一个任务执行。

### Phase 2：禁用 Flask 多线程（commit nea4cc7b）

**问题**：Flask 开发服务器多线程模式下，HTTP 请求处理和采集任务并发写 DB。
**修复**：Flask 禁用多线程模式。

### Phase 3：批量更新减少锁竞争（commit 8f328de）

**问题**：`touch_active_snapshots` 每个包单独 UPDATE，频繁锁表。
**修复**：改为批量 UPDATE，一条 SQL 更新所有产品。

### Phase 4：引入全局写锁 + busy_timeout（commit 1b0f60b）

**问题**：多线程无锁争抢，导致 database is locked。
**修复**：
- 引入 `threading.RLock()` 全局写锁 `_write_lock`，所有 DB 写操作串行化
- 设置 `busy_timeout=5000ms`，锁等待超时 5 秒

### Phase 5：WAL checkpoint TRUNCATE（commit 833c442）

**问题**：WAL 文件无限增长，导致 checkpoint 时间变长。
**修复**：采集结束时执行 `PRAGMA wal_checkpoint(TRUNCATE)`，将 WAL 清零，控制文件大小。

### Phase 6：心跳函数加 try-except（commit 5edbb71）

**问题**：心跳函数 DB 操作未捕获异常，崩溃导致 `database is locked` 错误扩散。
**修复**：四类心跳结果（污染/过期/正常/网络错误）的 DB 更新全部包在 try-except 中。

### Phase 7：execute() 增加重试机制（commit ab9d3ef）

**问题**：即使有全局锁，checkpoint 期间的独占锁仍会导致其他写操作被拒。
**修复**：`execute()` 增加 3 次重试机制（100ms/200ms 递增间隔），根本解决采集结束后 DB 锁未释放导致的写入失败。

---

## 四、技术方案总结

| 层次 | 措施 | 效果 |
|------|------|------|
| 架构层 | `max_workers=1` | 消除多进程并发写 |
| 架构层 | Flask 禁用多线程 | 消除多线程并发写 |
| 应用层 | `RLock()` 全局写锁 | 所有写操作强制串行化 |
| 驱动层 | `busy_timeout=30s` | 锁等待时间从 5s 提升到 30s |
| 应用层 | 批量 UPDATE | 减少锁持有次数和时间 |
| 运维层 | `PRAGMA wal_checkpoint(TRUNCATE)` | 控制 WAL 文件大小 |
| 应用层 | 心跳函数 try-except | DB 异常不导致函数崩溃 |
| **驱动层** | **`execute()` 重试 3 次** | **彻底消除 database is locked** |

---

## 五、经验教训

1. **SQLite WAL 不是万能的**：WAL 提升了并发读能力，但 checkpoint 期间的独占锁仍是瓶颈。
2. **busy_timeout 要足够大**：30s 才能应对采集结束时的极端锁等待场景。
3. **所有 DB 写操作必须包裹异常处理**：即使有重试机制，调用方仍应有防御性代码。
4. **WAL checkpoint 应与采集流程解耦**：理想方案是定时 checkpoint，不在采集结束时同步执行。

---

## 六、后续优化建议

### 建议 1：分离 WAL checkpoint（方案 B）
把 `PRAGMA wal_checkpoint(TRUNCATE)` 移出采集结束流程，改为独立定时任务（如每小时一次），避免在采集收尾时阻塞。

### 建议 2：心跳表异步写入
心跳 `heartbeat_log` 改用内存队列 + 独立后台线程批量写入，不在心跳主流程中等待 DB 锁。

### 建议 3：监控 WAL 文件大小
增加 WAL 文件大小监控，超过阈值时强制触发 checkpoint，防止 WAL 文件无限膨胀导致 checkpoint 时间不可控。

---

## 七、相关 commit 一览

| commit | 描述 |
|--------|------|
| `e14ef47` | APScheduler max_workers=1 根除 database locked |
| `nea4cc7b` | 禁用 Flask 多线程 + 批量 touch_active_snapshots |
| `1b0f60b` | 全局写锁 + busy_timeout + log_scanner 等待采集完成 |
| `833c442` | RLock + WAL checkpoint TRUNCATE |
| `5edbb71` | 心跳函数所有 DB 更新包裹 try-except |
| `ab9d3ef` | execute() 重试机制应对采集结束时的 database is locked |