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