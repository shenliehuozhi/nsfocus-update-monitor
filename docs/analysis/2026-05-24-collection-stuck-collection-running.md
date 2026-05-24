# 事故分析：采集卡死 (2026-05-24 更新)

## 结论

本次不是 DB 锁竞争复发。`collection_running` 在进程启动时过早设置，导致 `_check_concurrent_stale()` 永久判定"还在运行"，所有后续采集被跳过。

---

## 正确时间线（systemd 日志 + 数据库）

```
2026-05-23 16:22-16:26  服务在 4 分钟内崩溃重启 7471 次
2026-05-23 16:26:34     终于启动成功，开始正常运行

                       -- 此后服务正常运行约 1 小时 --

2026-05-23 17:33:05     某进程触发了一次采集
                       → _set_collection_running(status=1) 写入 DB
                       → 采集没有完成（snapshots 0 条更新）
                       → finally 未执行，collection_running 永久卡住

2026-05-23 17:35        手动重启服务
                       → start_scheduler() 被调用
                       → APScheduler.start() 内部同步执行 _smart_collect
                       → _smart_collect → run_now → _set_collection_running(17:33:05 的记录仍存在)
                       → _check_concurrent_stale 发现 elapsed ≈ 0.0h < 8h，判定"还在运行"
                       → 采集被跳过，collection_running 再次写入（仍为 17:33:05 的状态）

2026-05-23 16:29:31     服务被停止（systemctl stop，Deactivated successfully）

                       -- 中间间隔约 17 小时（服务停止状态）--

2026-05-24 01:33-01:55  heartbeat_log 正常（2个 session 均写入）
                       → 说明心跳线程运行正常，问题仅在采集

2026-05-24 09:33       当前会话开始（用户连接 SSH）
                       → 查询 DB 发现 collection_running 卡住
                       → 添加 _clear_stale_collection_running 函数
```

**关键发现**：09:33 UTC 是当前会话开始时间，不是 `collection_running` 写入时间。`collection_running` 的 `started_at = 01:33:05 UTC = 09:33:05 CST` 是 05-23 下午的采集记录，不是 05-24 凌晨。

---

## 根因分析

### 直接原因

`start_scheduler()` 中 `sched.start()` 会**同步阻塞执行**首次采集任务，导致 `collection_running` 在 stale 检测之前就被设置：

```
APScheduler 启动流程：
1. _clear_stale_collection_running() 检查（如果存在）
2. sched.start() 被调用
3. APScheduler 内部发现 next_run_time=datetime.utcnow()，立即同步执行 _smart_collect
4. _smart_collect → run_now → _set_collection_running(status=1)
   → 此时 collection_running 已存在（卡在 status=1）
   → _check_concurrent_stale 检查：elapsed = 0.0h < 8h，判定"还在运行"
   → 但这个检查发生在 sched.start() 返回之后（APScheduler 内部）
5. sched.start() 返回
6. 后续所有定时采集触发时，_check_concurrent_stale 判定 elapsed < 8h，跳过
```

### 核心缺陷

`run_now()` 在 `sched.start()` 之前被调用，设置了 `collection_running`，但后续的 `sched.start()` 触发 `_smart_collect` 时，`_check_concurrent_stale` 已经看到"刚设置"的记录，判定还在运行。

### 两次事件的区别

| | 第一次（05-23 19:36 前） | 本次（05-23 17:33） |
|---|---|---|
| 根因 | `threaded=True` 多线程并发写 + 78次单独 UPDATE + finally 崩溃 | 启动时采集先于 stale 检测，_check_concurrent_stale 永久判定"还在运行" |
| 触发条件 | 真正的 DB 锁竞争 | APScheduler 启动行为 |
| 症状 | 6542 次 database locked 错误 | 0 次 lock，但采集被跳过 |
| 修复 | `threaded=False` + finally 修复 + 批量 touch | 调整启动顺序 |

两次是完全不同的根因。

---

## 修复方案

### 修复 1：先强制清除，再启动 APScheduler

在 `start_scheduler()` 中，**先无条件清除** `collection_running`，**再启动** APScheduler：

```python
def start_scheduler(app=None):
    # 关键：先清除任何残留状态，再启动 APScheduler
    _clear_stale_collection_running(force=True)  # force=True: 无条件清除

    sched = BackgroundScheduler()
    # ... add jobs ...
    sched.start()
    # 此时首次采集会正常执行（因为已经没有残留状态）
```

`force=True` 时无条件清除任何 `collection_running` 记录（处理进程非正常退出）；`force=False`（默认）时仅清除超过 `COLLECT_INTERVAL * 2` 的记录。

### 修复 2：`_check_concurrent_stale` 加入进程启动时间检测

即使 `collection_running` elapsed 时间短，如果进程启动时间早于 `started_at`，也视为 stale：

```python
# 新增：记录进程启动时间
_process_start_time = datetime.utcnow()

# 在 _check_concurrent_stale 中：
if _process_start_time > started:
    # 进程是后来启动的，说明上次采集是在当前进程启动前崩溃的
    logger.warning('collection_running set before process start, treating as stale')
    _clear_collection_running()
    return False
```

### 修复 3：`_last_run` 持久化

`last_full_run` 只存在内存变量，重启后丢失。仿照 `_save_last_full_scan()` 在 `run_now()` 成功结束时写入 `system_settings` 表。

---

## 验证

修复后：
- [ ] 重启服务，`collection_running` 被无条件清除
- [ ] 下次定时采集正常触发，snapshots 有更新
- [ ] 采集完成后 `collection_running` 正常清除
- [ ] Dashboard `last_run` 正常显示

---

## 教训

1. **APScheduler.start() 是同步阻塞的**：会在返回前执行 `job.execute()`，因此任何清理逻辑必须在 `start()` 之前完成
2. **04-23 修复后 lock 错误为 0**：`threaded=False` + 批量写 + `busy_timeout=10000ms` 完全正确，不是频率问题
3. **WAL 4MB 的含义**：WAL 活跃说明最近有大量写入（heartbeat_log 每 10min 写一次），checkpoint 后 WAL 会归零，不是问题
4. **systemd 日志是唯一可靠的时间来源**：所有事件必须以 journalctl 为准，DB 时间受进程状态影响不可信