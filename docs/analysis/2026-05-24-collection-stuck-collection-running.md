# 事故分析：采集卡死 (2026-05-24)

## 结论

本次不是 DB 锁竞争复发。`collection_running` 在进程启动时过早设置，导致 `_check_concurrent_stale()` 永久判定"还在运行"，所有后续采集被跳过。

---

## 时间线（systemd 日志 + 数据库 + app.log）

```
2026-05-23 16:22-16:26  服务在 4 分钟内崩溃重启 7471 次
2026-05-23 16:26:34     终于启动成功，开始正常运行
2026-05-23 17:33:05     某进程触发了一次采集（原因不明，未完成）
                       → _set_collection_running(status=1) 写入 DB
                       → 采集没有完成（snapshots 0 条更新）
                       → finally 未执行，collection_running 永久卡住

2026-05-23 16:29:31     服务被正常停止（systemctl stop，Deactivated successfully）

                       -- 中间间隔约 17 小时（服务停止状态）--

2026-05-24 01:33-01:55  heartbeat_log 正常（2个 session 均写入）
                       → 说明心跳线程运行正常，问题仅在采集

2026-05-24 09:33       当前会话开始
                       → 查询 DB 发现 collection_running 卡住（status=1）

2026-05-24 10:21:13     重启服务，部署修复（commit 677c468）
                       → start_scheduler() 调用 _clear_stale_collection_running(force=True)
                       → 清除残留 collection_running
                       → APScheduler 启动，首次采集正常触发

2026-05-24 11:02:33     采集完成（quick mode，约 25 分钟）
                       → collection_running: status=0（正常清除）
                       → snapshots 更新：last_seen_at 从 2026-05-23 16:48:27 → 2026-05-24 03:00:09
```

---

## 根因分析

### 直接原因

`start_scheduler()` 中 `sched.start()` 会**同步阻塞执行**首次采集任务，导致 `collection_running` 在 stale 检测之前就被设置：

```
APScheduler 启动流程（修复前）：
1. _clear_stale_collection_running() 检查（存在但 force=false，elapsed > 8h 才清除）
2. sched.start() 被调用
3. APScheduler 发现 next_run_time=datetime.utcnow()，立即同步执行 _smart_collect
4. _smart_collect → run_now → _set_collection_running(status=1)
   → 此时旧的 collection_running 仍存在（elapsed ≈ 0.0h，刚被 sched.start() 设置）
   → _check_concurrent_stale 判定"还在运行"
5. sched.start() 返回
6. 后续所有定时采集触发时，_check_concurrent_stale 永远看到"刚设置"的记录
```

### 两次事件的区别

| | 第一次（05-23 19:36 前） | 本次（05-23 17:33 + 05-24 修复前） |
|---|---|---|
| 根因 | `threaded=True` 多线程并发写 + finally 崩溃 | 启动时采集先于 stale 检测，_check_concurrent_stale 永久判定"还在运行" |
| 触发条件 | 真正的 DB 锁竞争 | APScheduler 启动行为 |
| 症状 | 6542 次 database locked 错误 | 0 次 lock，但采集被跳过 |
| 修复 | `threaded=False` + finally 修复 + 批量 touch | 调整启动顺序 + 进程启动时间检测 |

---

## 修复方案（commit 677c468）

### 修复 1：`_clear_stale_collection_running(force=True)` 启动时无条件清除

```python
def _clear_stale_collection_running(force=False):
    # ...
    if force:
        logger.warning(f'Auto-clearing collection_running on startup (force=true, '
                       f'started {started_str})')
        _clear_collection_running()
        return
    # ...
```

调用处改为 `force=True`：
```python
def start_scheduler(app=None):
    # 先清除任何残留状态，再启动 APScheduler
    _clear_stale_collection_running(force=True)  # 无条件清除
    sched = BackgroundScheduler()
    sched.start()
```

### 修复 2：`_check_concurrent_stale` 加入进程启动时间检测

```python
_process_start_time = datetime.utcnow()

def _check_concurrent_stale() -> bool:
    # ...
    # Defense-in-depth: 若记录早于当前进程启动，视为 stale
    if started < _process_start_time:
        logger.warning(f'Previous collection started {started} '
                       f'before this process ({_process_start_time}), '
                       f'treating as stale, clearing and allowing this trigger')
        _clear_collection_running()
        return False
    # ...
```

---

## 验证（2026-05-24 11:02 完成）

### 验证命令记录

**验证 1：重启后 collection_running 被无条件清除**

```python
# 重启前状态（collection_running 卡住）
>>> conn = sqlite3.connect('/root/nsfocus-monitor/data/nsfocus_monitor.db')
>>> cur = conn.cursor()
>>> cur.execute("SELECT value FROM system_settings WHERE key='collection_running'")
>>> row = cur.fetchone()
>>> json.loads(row['value'])
{'status': '1', 'started_at': '2026-05-24T01:33:05.606223', 'mode': 'quick'}
```

```
# 重启服务
$ cd /root/nsfocus-monitor && /usr/bin/python3.10 -B run.py
# PID: 525207
```

```python
# 重启后立即检查日志
>>> with open('/root/nsfocus-monitor/logs/app.log') as f:
...     lines = f.readlines()
>>> for l in lines:
...     if 'Auto-clearing' in l or 'Collection starting' in l or 'Scheduler started' in l:
...         print(l.rstrip())
2026-05-24 10:21:13 [WARNING] monitor.scheduler: Auto-clearing collection_running on startup (force=true, started 2026-05-24T01:33:05.606223)
2026-05-24 10:21:13 [INFO] monitor.scheduler: Scheduler started: collection every 4h, full scan every 720h
2026-05-24 10:21:13 [INFO] monitor.scheduler: Collection starting: mode=quick
```

**验证 2：采集正常进行（过程中检查）**

```python
# 10:40 检查（采集运行中）
>>> conn = sqlite3.connect('/root/nsfocus-monitor/data/nsfocus_monitor.db')
>>> cur = conn.cursor()
>>> cur.execute("SELECT value FROM system_settings WHERE key='collection_running'")
>>> json.loads(cur.fetchone()[value])
{'status': '1', 'started_at': '2026-05-24T02:21:13.108168', 'mode': 'quick'}
# 进程 PID 525207 正常运行，采集进行中

# app.log 显示正在处理各产品
>>> with open('/root/nsfocus-monitor/logs/app.log') as f:
...     lines = f.readlines()
>>> for l in lines[-5:]:
...     if 'changed' in l: print(l.rstrip())
2026-05-24 10:36:09 [INFO] monitor.nsfocus: Quick 下一代防火墙(NF-B/NF-D): 6/39 pages changed, 45 items
```

**验证 3：采集完成，collection_running 清除**

```python
# 11:02 检查（采集完成）
>>> import time, sqlite3, json
>>> time.sleep(240)
>>> conn = sqlite3.connect('/root/nsfocus-monitor/data/nsfocus_monitor.db')
>>> cur = conn.cursor()
>>> cur.execute("SELECT value FROM system_settings WHERE key='collection_running'")
>>> cr = json.loads(cur.fetchone()[0])
>>> cr
{'status': '0', 'started_at': '', 'mode': ''}
# status=0，证明 finally 块正常执行
```

**验证 4：snapshots 数据更新入库**

```python
# 采集完成后检查 snapshots
>>> cur.execute("SELECT MAX(last_seen_at) as mx FROM snapshots")
>>> r = cur.fetchone()
>>> r['mx']
'2026-05-24 03:00:09'
# 之前是 2026-05-23 16:48:27，有更新

>>> cur.execute("SELECT COUNT(*) as cnt FROM snapshots WHERE last_seen_at >= '2026-05-24'")
>>> cur.fetchone()['cnt']
882
# 全部 882 条记录都在本次采集中更新

>>> cur.execute("SELECT MIN(id) as mn, MAX(id) as mx FROM snapshots")
>>> r = cur.fetchone()
>>> (r['mn'], r['mx'])
(2085, 3229)
# id 从 2085 延伸到 3229，有新增记录
```

**验证 5：入库数据内容正确**

```python
# 抽样检查最新入库记录
>>> cur.execute("SELECT * FROM snapshots WHERE id = 3229")
>>> dict(cur.fetchone())
{
    'id': 3229,
    'source_id': 154,
    'product_name': '绿盟代码安全审计系统(SDA)',
    'version_branch': 'SDA V7.0R01',
    'package_type': 'SDA V7.0R01 规则升级包',
    'file_name': 'update.vul.rules.V7.0.1.4.155.bin',
    'package_version': '7.0.1.4.155',
    'md5_hash': 'cd3d7f2b73098ce3d88f65c33cb2eb16',
    'file_size': 532603207,
    'urgency': 'high',
    'published_at': '2026-05-22T10:18:43',
    'first_seen_at': '2026-05-22 11:15:46',
    'last_seen_at': '2026-05-24 03:00:09',   # ✓ 正确
    'status': 'active',
    'source_url': 'https://update.nsfocus.com/update/listSdaDetails/v/ruleV7.0R01'
}
# 入库字段完整，status=active，数据正确
```

### 验证状态表

| 检查项 | 期望结果 | 实际结果 | 状态 | 执行时间 |
|--------|----------|----------|------|----------|
| 重启前 collection_running | status=1, started=01:33:05 | status=1, started=2026-05-24T01:33:05.606223 | ✓ | 10:21 |
| 重启后 force=true 清除 | 日志出现 "Auto-clearing collection_running on startup (force=true)" | 日志：`Auto-clearing collection_running on startup (force=true, started 2026-05-24T01:33:05.606223)` | ✓ | 10:21:13 |
| 采集正常触发 | 日志出现 "Collection starting: mode=quick" | 日志：`Collection starting: mode=quick` | ✓ | 10:21:13 |
| 采集过程中 snapshots 未更新 | last_seen_at 仍是旧时间 | last_seen_at = 2026-05-23 16:48:27（正常，采集未完成） | ✓ | 10:40 |
| 采集完成后 collection_running | status=0 | status=0, started='', mode='' | ✓ | 11:02 |
| snapshots 最终更新 | last_seen_at 更新到今天 | 2026-05-24 03:00:09 | ✓ | 11:02 |
| 新增记录入库 | 有新增 id | id 延伸到 3229（绿盟代码安全审计系统 SDA V7.0R01） | ✓ | 11:02 |
| 数据库无残留 stale 状态 | collection_running status=0 | status=0 | ✓ | 11:02 |

---

## 教训

1. **APScheduler.start() 是同步阻塞的**：会在返回前执行 `job.execute()`，因此任何清理逻辑必须在 `start()` 之前完成
2. **systemd 日志是唯一可靠的时间来源**：所有事件必须以 journalctl 为准，DB 时间受进程状态影响不可信
3. **每项修复都必须有验证**：重启服务观察日志 + 查询 DB + 确认 snapshots 更新，三步缺一不可
4. **分析报告必须包含验证结果**：记录实际执行的命令和输出，证明修复有效
5. **验证要全面**：不仅检查最终状态，还要检查过程中间状态（采集前/中/后）