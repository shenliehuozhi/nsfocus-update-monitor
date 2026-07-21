# CHANGELOG

## [Unreleased]

### 新增
- **API 文档**：更新路由路径（`/api/settings/scheduler/*`），删除不存在的端面

### 修复
- **代码质量**：清理死代码和过时注释（database.py WAL 注释、subscription.py 死代码、router.py 重复声明、system_routes.py 重复 import）
- **调度器**：`_shutdown_cleanup` 退出时清理 `collection_running` 标志，避免重启后误判
- **日志**：`log_scanner` 异常处理增加 `exc_info=True` 输出完整 traceback
- **采集器**：`NsfocusCollector.collect()` 改为 passive（return [] + DeprecationWarning），避免误触崩溃
- **推送历史**（`resend-targeted`）：补写 `recipient` 字段，前端不再 fallback 到 `channel_name` 显示（commit 48be988）
- **推送历史 UI**：重推期间区分当前按钮（"⏳ 推送中..."）和同 snapshot 其他按钮（灰色禁用保留 "📤 重推"），避免用户以为推了多次（commit 48be988）
- **UI**：`confirmAct` 确认按钮加防双击/防重复触发（同步 disable + delete _confirmCBs 双保险，commit 3d12f1f）

### 改进
- **文档**：API 接口文档、用户手册、部署运维、系统架构全面校准（产品名称、采集模式、订阅字段、路径引用）
- **文档**：删除重复的「采集异常诊断三步法」章节

---

## [2026-07-20] — 1000 系列层级目录识别修复与连锁清理

> 本段从 `f5323a8` 开始追溯，涵盖 10 个 commit，按"问题 → 修复 → 副作用清理"的时间顺序整理。

### 触发问题
- **discover 漏识别**（`f5323a8`）：NSFocus 站点 IPS 等产品在 `/update/ipsIndex/v/5.6.10` 这种含多个 `ser_c_b_tit` 区块的页面里，同一 detail URL 在多个区块出现时，`dict[href -> section_title]` 折叠会让"标准系列升级包列表"丢 3 个子类型到"10000系列升级包列表"（UI 上 5→2、4→5 错位）

### 数据库层（`snapshots` 表去重语义）
- **`5b7f0c6` fix(snapshot)**：`save_snapshot` 的去重查询改按 `source_url` 而非 `source_id`（commit 前 5 元组缺 source_id，会让跨 sid 同 URL 的包被误判为同一行）
- **`708f955` fix(snapshots)**：把 `source_id` 加进 `UNIQUE INDEX` 和 `save_snapshot` SELECT 命中键（`source_id, source_url, path_id, file_name, md5_hash` WHERE status='active'）

### 文档
- **`b431a4e` docs(reference)**：补 `references/2026-07-20-session-summary.md`（126 行），记录 sid-in-UNIQUE、dedupe 清理、refactor 决策的来龙去脉

### Chain 长度治理（`ser_c_b_tit` 单段不进 chain）
- **`8c41709` nsfocus**：当某子页的 `ser_c_b_tit` 区块数 ≤1 时，该段 sec_title **不进** chain（之前所有 sec 都进，导致 WAF 海光 V6.0.9.1 Bot特征升级这种页面 chain 出现冗余段，path_id 与 `_chainToUrlMap` 全部失效，必须用户主动「刷新包类型」重采）

### 订阅系统（应对 chain 变长/变化后的静默失效）
- **`1870b6b` UI**：订阅条件里失效的 chain 加红 ⚠️ 角标（一眼看到哪条链不再有效）
- **`8499816` UI tooltip**：红 ⚠️ 上挂诊断化 tooltip，列出 4 种失效原因（产品消失 / 产品无 paths / sec 整段没了 / sec 还在但精确 chain 没了）

### Discover 性能（避免 vendor 重跑）
- **`b9e9114` perf(discover)**：`confirm_apply` 走本地 JSON diff（temp file `added/deleted_paths`）而非重跑 `discover_package_types`；实测 73 产品 × 60s/vendor = 70+ 分钟 → 本地 JSON diff 4 秒

### API 路径一致性
- **`4d2ed03` fix(api)**：前端 `_srcUrlChainMap` 用的 path_id 算法 (`MD5(BASE_URL+url+JSON(chain))[:12]`) 与后端 `save_snapshot` 写入的 (`MD5(url)[:12]`) 不一致，导致 `dataBuildFeed` path 查找失效；统一为 `MD5(url)[:12]`，匹配 `scheduler._compute_path_id`

### 采集性能（本会话衍生优化）
- **`4297840` perf(collect)**：`NsfocusCollector._collect_quick` 新增 `shared_url_cache` 参数；scheduler 在产品循环外建一次 dict 注入，所有产品共用同一 cache。NSFocus 的 N chain → 1 URL 共享结构 + 跨 sid 共享 URL 都能命中，省去重复 fetch + parse + old_snaps SQL。实测白天 cycle 耗时均值 725s → 672s（中位节省 18s / 2.6%），网络波动大时收益被网络本身掩盖

### 受影响文件
```
src/collectors/nsfocus.py            chain 提取 + discover 修复
src/core/scheduler.py                性能 patch
src/models/snapshot.py               UNIQUE INDEX + dedupe 语义
src/web/routes/api_routes.py         path_id 算法对齐
src/web/routes/system_routes.py      confirm_apply 重构
src/web/templates/index.html         订阅失效 UI
tests/test_nsfocus_section_links.py  f5323a8 配套测试
references/2026-07-20-session-summary.md  会话总结
```

---

## [0.1.0] - 2026-05-29

### 新增
- **升级包描述关键词高亮**：支持 markdown 和 html 两种格式
  - 🔴 P0红：版本前置约束（"仅支持V5.6R11F08及以上"、"依赖插件包"等）
  - 🟠 P1橙：重启设备/引擎、影响功能（「重启引擎生效」、「会话中断」等）
  - 🟢 P2绿：正面安全信号（「不影响当前配置」、「不会造成会话中断」等）
  - 否定语境跳过（「不会造成会话中断」等否定表达不触发P0红）
  - 长语义单元优先匹配（避免子串碎片高亮）
- **邮件超限附件提示**：超过10MB的附件增加MD5校验建议区块，含 Linux/macOS 和 Windows 双平台命令
- **邮件下载地址按钮**：HTML邮件正文底部增加蓝色下载按钮
- **运维健康视图**：`/api/system/health` 展示服务健康状态、最后采集时间、推送今日统计
- **手动采集二次确认**：手动采集前增加确认弹窗
- **CHANGELOG 文档**：新增采集异常诊断三步法与快速恢复指南
- **部署运维文档**：补充采集异常应急处置 SOP

### 修复
- fix(email): SMTP发送改为后台线程执行，避免连接阻塞导致API超时
- fix(email): 超限提示嵌入HTML正文，避免QQ邮箱无法显示附件说明
- fix(email): 超限提示MD5校验区块底色改为黄色与上方警告一致
- fix(email): MD5校验建议改为红色强调 + 增加Windows certutil命令
- fix(api): `resend-targeted` 重推接口FK约束失败时捕获异常，不阻断推送成功返回
- fix(frontend): 推送历史时间使用 `fmtTZ()` 转换为 CST 时区
- fix(frontend): 推送弹窗手动邮箱模式输入框加 padding/圆角/聚焦高亮
- fix(highlight): 升级包高亮重叠时低程度颜色优先；P0关键词在否定语境内跳过
- fix(highlight): 升级包高亮重叠去重：长语义单元优先于子串碎片
- fix(notifiers): 展开详情收起后再展开字段变-的问题
- fix(collect): 手动采集中途重按提示「正在采集中请等待」而非无响应
- fix(dashboard): 失败数0显示灰色 + 运维健康/最近推送双栏等高对齐
- fix(dashboard): P0修复失败数红色误报 + P1最近推送Feed + P2状态条合并
- fix(dashboard): '最近推送' → '最近更新' 名称修正
- fix(dashboard): 删除最近更新卡片中的心跳信息
- fix(health): 修复 push_today 在 push_success_rate 前引用导致 500 错误
- fix(运维健康): 修复健康状态图标判断逻辑
- fix(db): 消除采集过程中 database is locked 错误（WAL + busy_timeout）
- fix(log_scanner): DB-independent alert delivery via HTTP callback
- fix(collector): strip leading '>' from section title
- fix(system_routes): discard discover result 清除内存状态
- fix(syntax): remove stray backtick before div.card
- fix(scheduler): Flask threaded=True 解决请求超时 + scheduler._check_concurrent_stale 非dict类型守卫
- fix(scheduler): 回退 last_run/last_mode 内存写入逻辑（现从DB读取 MAX(last_collected_at)）
- fix(health): 采集间隔/心跳间隔可配置化

### 改进
- ui: remove duplicate static collection status bar at dashboard top
- ui: 运维健康标题区加浅蓝底色+底边分隔线，增强标题与内容区块感
- ui: 手动采集/重启按钮移至运维健康卡片标题行右侧
- ui: 统一运维健康按钮尺寸 + 重启服务改为红色danger按钮
- ui: move action buttons into 运维健康 card, remove duplicate row above
- ui: dashboard button layout - info-bar above buttons, two-row stack
- ui: move /restart endpoint to system_routes（was in api_routes where bp not defined）
- enhance(scheduler): 采集完成后持久化 last_run/mode 到 DB
- enhance(health): 采集和推送为核心的信息展示
- api: `/settings/scheduler` 返回 last_run（MAX last_collected_at from content_sources）
- dashboard: add 重启服务 button with SIGTERM graceful shutdown
- refactor: 移除手动全量采集，始终为增量采集（Quick扫描）

### 重构
- refactor(collect): 移除手动全量采集，始终为增量采集
- refactor(dashboard): P0修复失败数红色误报 + P1最近推送Feed + P2状态条合并

### 废弃
- disable(full_scan): 暂时禁用全量扫描自动触发
- remove(full_scan): 删除全量扫描相关的所有UI和配置项

---

## [0.0.x] - 2026-05-10 ~ 2026-05-25

Initial development phase. See git log for details.