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

## 后续议题 (本议题不做)

- 9 个历史 FK 违反 (delivery_log→customers customer_id=0, rule_channels→subscription_rules)
- 时区混乱 (log CST / DB UTC / system CST)
- UNIQUE 索引无 status 维度
- last_seen_at 同秒 89 条
- 0 items 无告警

## 5 个待办最终结论 (本议题收尾)

| # | 议题 | 状态 | 备注 |
|---|---|---|---|
| #2 | delivery_log WARNING | ✅ 完成 (commit cbdab0b) | 本议题主任务 |
| #3+#7 | UNIQUE 索引 | ❌ 不做 | 实际无累积问题,业务正确 |
| #4 | 时区混乱 | ❌ 不修 | 主要是排查困扰,业务影响小(8h 差对天级判断无影响) |
| #5 | last_seen_at 同秒 89 条 | ❌ 不修 | 不是 bug,只是显示误导(last_seen_at 是 save_snapshot 写的,cdc2a43 不动它) |
| #6 | 0 items 无告警 | ❌ 不修 | 用户偏好"宁可空跑,绿盟恢复时能自动抓到" |

## 反思 — 本议题踩坑教训

### 误判 1:认为 89 条 superseded 是 WITHDRAWN

**错的事实**:
- 89 条 `all_versions` 只返回 1 行 → 错误推断"无 active 版本 = 绿盟下架"

**实际**:
- 89 条都是 `◄ OLD`(被新版本取代),不是 WITHDRAWN
- 绿盟升级模式:**不撤回重发**,直接发新版本(用新文件名)
- 例如 `V7-IPS-1.0.407.dat` superseded,`V7-IPS-1.0.409.dat` active(file_name 不同)
- 用 (source_id, version_branch) 查询就能看到 409 active 行

**差点导致**:
- 修改 4a52d14 的 WITHDRAWN 判定逻辑,把 89 条 `OLD` 改成 `WITHDRAWN`
- 业务语义从"被取代"变"被下架",前端展示/推送策略都可能错
- 幸亏你反问"你是怎么判断的",我重新查证才发现误判

### 误判 2:4a52d14 逻辑有 bug

**错的事实**:
- 4a52d14 那个 WITHDRAWN 判定分支在绿盟升级模式下不会触发
- 不是 bug,只是**绿盟几乎不用撤回重发**
- 89 条都是合法 `OLD`,标签完全正确

### 误判 3:误报 "12 个 source 0 items 是 session 过期"

**错的事实**:
- session 实际正常,所有 source 都有 last_collected_at 更新
- 实际是 **绿盟页面本身没 table**(curl 验证确认)
- 不是本会话 commit 引入

### 误判 4:误算数据规模

**错的事实**:
- 之前说"37 NEW" / "570 UNCHANGED" / "89 superseded" 都是基于混乱时间窗的统计
- 实际本 cycle:0 NEW, 26 UNCHANGED, 0 OLD(cdc2a43 23:30 cycle 标的 89 条不在本 cycle 时间窗内)
- 时区问题:log CST / DB UTC 混用导致我多次时间推算错

### 误判 5:误读字段值

**错的事实**:
- 之前说"4 条 isop-vulnDict 字段错位 bug"
- 实际**字段值完全正常**,是我自己 `c.execute()` 读 tuple 索引错了
- 4 条 snap 数据 100% 正常,本 cycle 已经刷新过 last_seen_at

## 反思 — 以后怎么避免

### 原则

1. **完整图,再下结论**:看数据先看"全貌",再局部。**只查局部就下结论 = 推断**
2. **同义词换查询验证**:换 1-2 个查询方式验证同一事实,别只用 1 个查询
3. **数据"少"未必异常**:可能是业务自然形态,先想"正常情况下数据应该长什么样"
4. **动手前 dry-run 验证**:如果准备改逻辑,**先 dry-run 一下新逻辑会怎么处理现有数据**

### 实战做法

1. **判断 DB 数据状态时,先查全貌**(skill `nsfocus-snapshot-status-judgment` 详述)
2. **对结论做"反向验证"**:推论"被下架"→ 反向 curl 绿盟页面验证
3. **动手前 sanity check**:SELECT id, file_name 看跟推断是否一致
4. **时区问题**:DB 存 UTC,log 写 CST,排查时先确认 server 当前时间 + UTC/CST 转换

### 本次实践改进

1. **migrations/ 目录** 启用 — 每个 schema 改动一个文件,含 SQL + dry-run + 回滚 SQL + 反思
2. **.gitignore 加 `data/*.db.bak.*` 规则** — 防止 DB 备份误带 commit
3. **commit body 必含"反思"段** — 防止下次再犯同样错误
4. **skill `nsfocus-snapshot-status-judgment`** — 防止误判 89 条 status 场景再发生

## 议题最终状态

- ✅ #2 delivery_log WARNING 修复 (commit `cbdab0b`)
- ❌ #3+#7 / #4 / #5 / #6 不修(已列理由)
- ✅ 反思落 commit 文档 + skill 备忘
- ✅ DB 备份在文件系统(.gitignore 自动忽略)
