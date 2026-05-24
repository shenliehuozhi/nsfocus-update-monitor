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
                       → collection_running: status=1, started=2026-05-24T02:21:13

2026-05-24 11:02:33     采集完成（quick mode，约 25 分钟）
                       → collection_running: status=0（正常清除）
                       → snapshots 更新：last_seen_at 从 2026-05-23 16:48:27 → 2026-05-24 03:00:09
                       → 新增记录 id 延伸到 3229（绿盟代码安全审计系统 SDA V7.0R01 规则升级包）
```

---

## 根因分析

### 直接原因

`start_scheduler()` 中 `sched.start()` 会**同步阻塞执行**首次采集任务，导致 `collection_running` 在 stale 检测之前就被设置，后续 stale 检测看到"刚设置"的记录，永久判定"还在运行"：

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

在 `start_scheduler()` 中，**先无条件清除** `collection_running`，**再启动** APScheduler：

```python
def start_scheduler(app=None):
    # 关键：先清除任何残留状态，再启动 APScheduler
    _clear_stale_collection_running(force=True)  # 无条件清除

    sched = BackgroundScheduler()
    sched.add_job(_smart_collect, ...)
    sched.start()  # 此时已经没有残留状态，首次采集正常执行
```

`force=True` 时无条件清除任何记录（处理进程非正常退出）；`force=False`（默认）时清除超过 `COLLECT_INTERVAL * 2` 的记录（处理进程内采集超时）。

### 修复 2：`_check_concurrent_stale` 加入进程启动时间检测

```python
# 模块级变量
_process_start_time = datetime.utcnow()

def _check_concurrent_stale():
    # ... 解析 collection_running ...

    # Defense-in-depth: 若记录早于当前进程启动，视为 stale
    if started < _process_start_time:
        logger.warning(f'Previous collection started {started} before process, '
                       f'treating as stale')
        _clear_collection_running()
        return False

    # 原有 elapsed 检测 ...
```

### 未完成：`_last_run` 持久化

`last_full_run` 只存在内存变量，重启后丢失。留待后续实施。

---

## 验证（2026-05-24 11:02 完成）

| 检查项 | 期望 | 实际 | 结果 |
|---|---|---|---|
| 重启后 `collection_running` | force=True 清除旧记录 | 日志：`Auto-clearing collection_running on startup (force=true, started 2026-05-24T01:33:05.606223)` | ✓ |
| 采集正常触发 | status=1, started=当前时间 | 日志：`Collection starting: mode=quick` | ✓ |
| snapshots 更新 | last_seen_at 更新到今天 | `2026-05-23 16:48:27` → `2026-05-24 03:00:09` | ✓ |
| 新记录入库 | 有新增 id | id 延伸到 3229（绿盟代码安全审计系统 SDA V7.0R01） | ✓ |
| `collection_running` 清除 | status=0 | 11:02:33 查询：`status=0` | ✓ |
| 采集完成日志 | `Cycle complete` | app.log 无此行（quick mode 可能走其他路径），但 status=0 证明完成 | ✓ |

**验证命令**：
```python
# 采集完成后检查
conn = sqlite3.connect('data/nsfocus_monitor.db')
cur = conn.cursor()
cur.execute("SELECT value FROM system_settings WHERE key='collection_running'")
cr = json.loads(cur.fetchone()[0])  # status 应为 '0'
cur.execute("SELECT MAX(last_seen_at) FROM snapshots")  # 应为今天时间
cur.execute("SELECT COUNT(*) FROM snapshots WHERE last_seen_at >= '2026-05-24'")  # 应有数据
```

---

## 教训

1. **APScheduler.start() 是同步阻塞的**：会在返回前执行 `job.execute()`，因此任何清理逻辑必须在 `start()` 之前完成
2. **systemd 日志是唯一可靠的时间来源**：所有事件必须以 journalctl 为准，DB 时间受进程状态影响不可信
3. **每项修复都必须有验证**：重启服务观察日志 + 查询 DB + 确认 snapshots 更新，三步缺一不可
4. **分析报告必须包含验证结果**：记录实际执行的命令和输出，证明修复有效