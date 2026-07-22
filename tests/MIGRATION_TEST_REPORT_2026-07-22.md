# 迁移测试报告 — 2026-07-22

**目标**：验证 `scripts/migrate_snapshots_to_url_based.py` 在真实迁移前备份上能正确完成
`path_id` 算法迁移 + 按最终四元组去重 + 引用改指 + 索引重建。

**使用备份**：`data/nsfocus_monitor.db.bak.20260716_091445_pre-urlbased`
（10,428,416 字节，对应 `b0f4145` 切到 `MD5(URL)[:12]` 之前的数据库）

**测试方式**：复制备份到临时目录 `/tmp/nsm-migration-old-db-*` 后跑脚本。
源备份、生产数据库、测试副本互相隔离；测试后临时副本已清理。

**对应 commit**：
- `0829e24` fix(migration): keep source boundary and delete duplicates
- `76829db` fix(migration): normalize and dedupe every snapshot status

---

## 1. 迁移前状态

| 项 | 数量 |
|---|---:|
| snapshots 总行数 | 2,222 |
| active | 1,867 |
| superseded | 355 |
| withdrawn | 0 |
| source_url 非空 | 2,219 |
| delivery_log 行数 | 1,938 |
| delayed_queue 行数 | 0 |
| digest_queue 行数 | 0 |
| `snapshots_migration_v3` 标记 | 不存在 |
| `idx_snapshots_unique` | 旧版（不含 source_id） |

## 2. 计划（dry-run 输出）

| 项 | 值 |
|---|---:|
| rows with source_url | 2,219 |
| 最终四元组去重后 | 928 |
| 待删除重复行 | 1,291 |
| 待更新 path_id | 928 |
| 唯一索引需要重建 | True |

被删除行的状态分布：

```text
active 1,173 + superseded 118 = 1,291
```

每组保留目标行状态分布：

```text
active 1,291（保留目标行全部仍为 active）
```

每组按 `last_seen_at DESC, id DESC` 选最新行；保留行的当前 status 不被强制改写。

## 3. 执行结果

```text
deleted: 1,291
references_repointed: 1,454
pathid_updated: 928
unique_index_rebuilt: True
```

## 4. 迁移后状态

| 项 | 数量 |
|---|---:|
| snapshots 总行数 | 931 |
| active | 694 |
| superseded | 237 |
| withdrawn | 0 |
| delivery_log 行数 | 1,938 |

行数差：

```text
-1,291 = 预期删除数 ✓
```

## 5. 数据完整性校验

| 检查项 | 期望 | 实际 |
|---|---|---|
| 总记录差 | -1,291 | -1,291 ✓ |
| `path_id != MD5(URL)[:12]` 数量 | 0 | 0 ✓ |
| 按最终四元组存在重复组 | 0 | 0 ✓ |
| delivery_log 引用孤立 | 0 | 0 ✓ |
| `snapshots_migration_v3` | 1 | 1 ✓ |
| `idx_snapshots_unique` 含 `source_id` | 是 | 是 ✓ |
| 含 `WHERE status='active'` | 是 | 是 ✓ |

delivery_log 引用统计说明：

```text
原始 delivery_log：1,938
references_repointed 计数：1,454
差额 484 是脚本的「已经指向保留行，无需改指」情况
迁移后 delivery_log 总行数：1,938（与原始一致，无丢失）
```

## 6. 单元测试

```text
tests/test_migrate_snapshots_to_url_based.py
3/3 passed
```

用例覆盖：

| # | 场景 | 断言 |
|---|---|---|
| 1 | 同一 source 内去重，跨 source 保留 | 删除只发生在同 source 内的 active 重复 |
| 2 | 多状态混合同一最终身份 | 跨 active/superseded/withdrawn 统一去重，最新行胜 |
| 3 | 引用改指 + 索引重建 + marker | delivery_log/delayed_queue/digest_queue 改指成功 |

## 7. 已知遗留问题（与本次迁移无关）

`PRAGMA foreign_key_check` 报 1,251 条孤儿引用：

```text
delivery_log.rule_id -> subscription_rules.id = 0
```

迁移前 1,251，迁移后 1,251，**数量未变化**。是这份旧备份的既有数据问题，不在本次迁移
负责范围内，脚本不修改 `subscription_rules`。

## 8. 结论

```text
□ 数量变化符合预期
□ 状态保留符合最新 last_seen_at 行
□ path_id 全部统一为 MD5(URL)[:12]
□ 引用 0 丢失
□ 索引形态正确
□ 幂等标记写入
□ 源备份未被改动
□ 单元测试 3/3 通过
```

迁移脚本已可投入实际生产数据库使用。
