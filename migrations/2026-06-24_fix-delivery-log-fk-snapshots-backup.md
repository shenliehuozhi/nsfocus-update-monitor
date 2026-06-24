# 修复 delivery_log / delayed_queue / digest_queue FK 引用 snapshots_backup

## 根因

历史上某次 schema 迁移(推测是 P9 path_id 修复阶段),开发者:

1. RENAME snapshots TO snapshots_backup (备份用)
2. 改 schema 后 CREATE TABLE snapshots (新表)
3. INSERT INTO snapshots SELECT * FROM snapshots_backup
4. DROP TABLE snapshots_backup

**第 4 步没运行 `PRAGMA foreign_key_check` 验证外键**,导致 `delivery_log` / `delayed_queue` / `digest_queue` 三个表的 `snapshot_id` FK 永远指向已 DROP 的 `snapshots_backup` 表。

bug 隐藏的原因:
- SQLite 默认 `PRAGMA foreign_keys=OFF`,DROP TABLE 不检查 FK 引用
- 大多数 query/execute 函数用的连接不强制 FK
- INSERT/SELECT/UPDATE 不触发 FK check,**只有 DELETE 触发**
- 平时 cleanup 没跑过 DELETE FROM delivery_log,bug 一直隐藏
- 2026-06-24 凌晨 `_run_db_cleanup` 第一次跑 DELETE → 暴露 `no such table: main.snapshots_backup` WARNING

## 修复

重建 3 个表,FK 引用从 `snapshots_backup` 改回 `snapshots`,数据完整保留。

### SQL 流程(标准 SQLite schema 迁移)

```sql
PRAGMA foreign_keys=OFF;

-- 对 delivery_log / delayed_queue / digest_queue 各自:
CREATE TABLE _<table>_tmp AS SELECT * FROM <table>;
DROP TABLE <table>;
-- 新建 schema (FK 引用 snapshots 不是 snapshots_backup)
CREATE TABLE <table> (...);
INSERT INTO <table> SELECT * FROM _<table>_tmp;
DROP TABLE _<table>_tmp;

PRAGMA foreign_keys=ON;
```

## 验证

- `PRAGMA integrity_check`:ok
- `PRAGMA foreign_key_check`:31 个违反 → 9 个违反(减 22 个 = 修好 delivery_log→snapshots_backup 22 行)
- data 完整:delivery_log 22 条 / delayed_queue 0 条 / digest_queue 0 条
- `clear_history(older_than_days=90)` 跑通,删除 22 条(sent_at IS NULL 的孤儿)
- 剩余 9 个 FK 违反是历史遗留(delivery_log→customers customer_id=0、rule_channels→subscription_rules),跟本议题无关,后续单独处理

## 反思 — 以后如何避免

### 工程原则

1. **DROP TABLE 必查 FK**:跑 `PRAGMA foreign_key_check` 确认没外键引用被 DROP 的表
2. **Schema 字符串跟 DB 同步**:不允许 `SCHEMA_*` 字符串跟 `sqlite_master.sql` 实际表结构漂移(本议题附带发现 delivery_log 列顺序不一致,代码预期 rule_id 在 channel_id 前,实际表里 rule_id 在 retry_count 后)
3. **FK 改动要双向**:改 snapshots 表时,所有引用 snapshots 的 FK 都要同步检查
4. **Schema 迁移要测试**:改完跑 PRAGMA foreign_key_check + dry-run 关键 query

### 实战改进(后续议题,本议题不做)

1. **加 `migrations/` 目录** — 每个 schema 改动一个文件,内容含 SQL + dry-run 验证 + 回滚 SQL(本次修复的 migration 文件就是第 1 个)
2. **加 `check_fk_safe()` 自动化函数** — schema 改动前后跑,确保 FK 引用全部一致
3. **加 `validate_schema_match()` 自动化函数** — 对比 `sqlite_master.sql` 跟代码里的 `SCHEMA_*` 字符串,防止漂移
4. **DB 改完 commit message 模板 checklist**:
   - [ ] PRAGMA foreign_key_check 通过
   - [ ] PRAGMA integrity_check 通过
   - [ ] 关键 query dry-run 通过
   - [ ] 列数 / 列顺序对齐(代码字符串 vs 实际)

## 不处理(留到后续议题)

- 9 个历史 FK 违反(delivery_log→customers 3 行 customer_id=0、rule_channels→subscription_rules 6 行)
- 时区混乱(log CST / DB UTC / system CST)
- UNIQUE 索引无 status 维度
- last_seen_at 同秒 89 条
- 0 items 无告警
