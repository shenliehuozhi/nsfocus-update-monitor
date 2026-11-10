# CHANGELOG

## [Unreleased]

### 新增
- **推送历史过滤**：页面头部过滤条新增「订阅规则」+「客户」两个下拉（与原有时间范围并列），条件写进 URL hash 可分享；后端 `GET /api/history` 新增 `rule_id` 参数，`subscription.get_history()` 按 `delivery_log.rule_id` 过滤并同时下推到每个快照的 deliveries 子查询（否则列表行会混入其他规则/客户的交付记录）
- **渠道体检**：新增每日邮件渠道凭据探测 `scheduler._channel_health_check`（04:00，只做 SMTP 连接+登录、不发信）。此前邮箱密码/授权码过期只有被动路径（`delivery_log` 留痕 + 仪表盘失败数 + 人工「测试渠道」），若期间无新包发布则完全静默；现在失败会汇总成一条 `channel_error` 系统事件（`emit_channel_error`）经系统事件渠道告警。开关 `channel_health_enabled`（默认开）
- **日志扫描**：`ERROR_PATTERNS['认证错误']` 补 SMTP 凭据失效文案（`SMTP auth failed`、`SMTPAuthenticationError`、163/Microsoft/Gmail/腾讯企业邮各自返回串），使 30 分钟日志扫描能捕获 `[EMAIL] SMTP auth failed: 535 ...`
- **API 文档**：更新路由路径（`/api/settings/scheduler/*`），删除不存在的端面
- **CI 测试门禁**：新增 `.github/workflows/tests.yml`（本仓第一道自动化门禁）—— push/PR/手动触发跑 `python3 -m pytest`，Python 3.10 + `requirements.txt` 安装，另加两条 lint 守住「测试可移植」：`tests/` 里不许出现 `/root/nsfocus-monitor` 绝对路径、不许 `sqlite3.connect` 到生产库。为此先把 6 个绑定开发机的用例改成可离线自足：`test_user_upgrade_auto_migrate` 三个用例自建 `tmp_path` 库 + `monkeypatch` DB_PATH（原来直接读生产 `data/nsfocus_monitor.db`，31 个用例连带 collect 报错），`test_rule_1013_8703_chain` 把生产 chain 值内联并改调真正的 `detector/change._chain_matches`（原来各自复制一份局部实现，测的是副本不是产品代码），`test_subscription_match` 加 autouse fixture 清 `scheduler` 的 chain 缓存并 stub `_get_chain`，`test_about_modal_regression` / `test_scheduler_rollback_chain` / `test_version_info` 的绝对路径改 `Path(__file__).resolve().parents[1]`。离仓验证：仓库副本 tar 到 `/tmp`（排除 `data/` `logs/` `.git` `__pycache__`）+ `MONITOR_DATA_DIR`/`MONITOR_LOG_DIR` 指空目录 + 按 pin 装的依赖，371 passed

### 修复
- **采集 · 一页多包漏采**：绿盟规则历史页是「一个升级包一张 `<table>`」（WAF `rule6070` 实测同一页 247 张表），旧解析器只读第一张表，一次发布多个包时只有最新那个能被看见，其余包永远不入库、不推送。现在逐表逐包解析，按 `published_at` 只跟踪最新 N 个；撤回/取代检测仍用全量 key，滑出窗口的老包不会被误标 withdrawn/superseded。快速采集同步改为读取完整页面（原来截断 50KB，第 20 个之后的表根本读不到）。实测 247 包页面全量解析 ≈722ms（每张表 4 行、单遍遍历）；回归见 `tests/test_multipkg_track_window.py`（2 表 4 包、一次 3 新包全判 NEW、窗口外不误撤回、描述正文含「名称：」不拆包）
- **仪表盘 · 客户推送分布**：不再隐藏近 N 天 0 推送的客户（此前 `items.filter(c => c.total>0)` 把 5 个客户砍成 3 个）。后端 `LEFT JOIN customers` 本就返回全量，现在 0 推送客户置灰沉底、状态列显示「无推送」
- **代码质量**：清理死代码和过时注释（database.py WAL 注释、subscription.py 死代码、router.py 重复声明、system_routes.py 重复 import）
- **调度器**：`_shutdown_cleanup` 退出时清理 `collection_running` 标志，避免重启后误判
- **日志**：`log_scanner` 异常处理增加 `exc_info=True` 输出完整 traceback
- **采集器**：`NsfocusCollector.collect()` 改为 passive（return [] + DeprecationWarning），避免误触崩溃
- **推送历史**（`resend-targeted`）：补写 `recipient` 字段，前端不再 fallback 到 `channel_name` 显示（commit 48be988）
- **推送历史 UI**：重推期间区分当前按钮（"⏳ 推送中..."）和同 snapshot 其他按钮（灰色禁用保留 "📤 重推"），避免用户以为推了多次（commit 48be988）
- **UI**：`confirmAct` 确认按钮加防双击/防重复触发（同步 disable + delete _confirmCBs 双保险，commit 3d12f1f）
- **通知模板**（`base._format_template_strip` Stage 2）：desc 里没有「注意事项」时不再退化成一个只有空标题的「注意事项」头 —— 原先无条件走 stage 2，会让正文整段消失。现在仅在真的取到注意段时才用，否则继续下探到 brief 档补「详情见 URL」
- **通知模板**（`base._fmt_meta_line`）：`MD5` 这行标签此前硬编码 `'MD5' + 6 空格`，比 `_pad_label` 的 4 字符对齐多出 4 列，冒号与其余 meta 行不同列（e2e34bc 统一对齐时漏改的第二个调用点）。现统一交给 `_pad_label`
- **推送历史分页**：后端每页固定 200 个包，前端原先不传 `page`，第 2 页之后的数据永远看不到；且 `total` 用的是 `COUNT(dl.*)` 交付行数而非包数，同包推 3 个客户会被算成 3。现在 `total` 改 `COUNT(DISTINCT s.id)`（实测 7124 → 1187），页头新增「共 N 个包 · 第 x/y 页」+ 上/下一页，过滤条件变更自动回到第 1 页
- **前端 · 内联事件属性转义**：`on*="foo('${esc(X)}')"` 这类写法里的 `esc()` 挡不住单引号。HTML 解析器先对属性值整体做实体解码、再交给 JS 编译，所以 `&#39;` 会被还原成裸 `'`（等于 `escHtml` 在属性里没起作用），而 `esc()` 干脆不处理 `'` —— 字段里一个撇号就让该按钮静默失效，`');代码//` 则是在页面全局作用域里执行 JS（`localStorage.token` 就在手边）。新增 `escAttr()`（`\` 与 `'` 出反斜杠形式，`& < > "` 出实体，`\n\r` 出字面量，单次遍历避免实体被二次编码），替换 4 个 handler 的 13 处调用点（客户列表「编辑」、数据页与下钻的「推送」弹窗）；另补 4 处完全裸插的远端版本串（rd 对比页 `data-ver` / `onchange` / 版本 chip —— `data-*` 用 `escHtml`，JS 实参用 `escAttr`）。全量校验：277 个 handler 过 V8 编译 0 失败，12 个对抗样本走「emit → HTML 解码 → JS 求值」完整管线还原一致
- **前端 · 死代码**：删掉两个同名 `togglePkgType`（一个 `// Deprecated - no-op`、一个空体 `{}`，后者静默覆盖前者），全仓 0 调用点
- **前端 · 内联事件属性转义（第二批）**：fbdfc2c 只换了 4 个 handler，复查 `index.html` 发现属性内 JS 字符串实参位还有 **12 处**在用 `escHtml()`（分布在 8 行）。`escHtml` 把 `'` 转成 `&#39;`，但属性值会先被 HTML 解析器实体解码再交给 JS 编译，等于没起作用。实测 13 个对抗样本：旧写法 6 个写坏 —— `It's`、`'''`、`');alert(1);//` 和 CRLF 触发 JS 语法错误（按钮静默失效），`back\slash` 变成 `backslash`、JSON 串 `["a\"b","c"]` 变成 `["a"b","c"]`（**静默改坏数据**，不报错）。其中两处输入是可控的：渠道名 `c.name`（用户在系统设置里填，`showTestLog`）和远端 `change.entry_url`（`togglePkgDetail`）。另把 L2110 手写的 `${p.replace(/'/g,"\\'")}`（只管 `'`，不管 `"`/反斜杠/换行）也收敛到 `escAttr`。验证：2 个 `<script>` 块共 9329 行整块过 V8 语法检查 0 失败，13/13 样本经「emit → 实体解码 → JS 求值」逐字节还原
- **前端 · 转义防线**：新增 `tests/test_inline_attr_escaping.py` —— 扫 `on*` 属性里 JS 字符串实参位的 `esc`/`escHtml` 与手写 `'` 转义并给出行号，另用 node 跑上面那条完整管线做属性验证（找不到 node 则 skip）。此前这类问题靠人眼复查，两批之间就漏了 12 处
- **仪表盘**：`dashboard()` 每次渲染都抛 `ReferenceError: rng is not defined`（f592134 引入范围按钮高亮时用了不存在的变量），异常点在函数中段，导致后面的「采集进行中启动进度轮询」整段被跳过。改为按按钮所在卡片取各自范围（`#tlRange` → `_dashRanges.tl`，`#pieRange` → `_dashRanges.pie`）。顺带修好了范围按钮高亮——此前这条链一抛错，任何卡片的高亮都没生效

### 改进
- **UI · 下拉框统一**：此前只有 `.form-group select` 关了原生外观（`appearance:none` + 灰三角），散在工具条/标签行/弹窗里的 select 还是 Windows Chrome 的系统箭头 + 系统边框，同屏两套长相；内联样式里还各自重复写 border/radius/font-size（11/12/13px、4/6/8px 混杂）。现在加全局 `select` 基线（同一枚灰三角 + `var(--gray-border)` + 8px 圆角 + 13px 字号 + hover/focus/disabled 三态），新增 `select.sel-sm`（6px 圆角/12px）给密集工具条，并清掉 15 处内联装饰样式、事件中心两个下拉去掉 `btn btn-xs btn-outline` 类。Chromium 实测 14 个可见下拉 computed style 全部一致（appearance=none / 自定义箭头 / padding-right≥24），0 例外。⚠️ 原生 `<option>` 弹层由操作系统绘制，CSS 改不了，要统一那层得换自定义下拉组件（已在下面试点）
- **UI · 自定义下拉浮层（全站 33 个 `<select>`）**：先在推送历史（规则/客户/时间）+ 系统日志（文件/级别/行数）6 个试点，验证通过后推广到全部 33 个 `<select>`——指针展开时改为自绘选项列表（8px 圆角、轻阴影、6px 选项圆角、当前项主色加 ✓），不再用系统的直角蓝条。**渐进增强，不改交互契约**：原生 `<select>` 仍留在 DOM 里负责 `value` / `onchange` / 表单语义，选中后照常派发 `change`（日志页仍是"选完点查询"，没有凭空改成自动提交）；禁用、`multiple`、`size>1` 的列表式控件不接管；触屏设备（`pointer:coarse`）不接管，仍用系统 popup。选中放在 `click` 而不是 `mousedown`，按下后拖出选项再松手不会误选；浮层挂在 `body` 上，弹窗内选中不会连带关闭弹窗。键盘操作未展开时完全走原生（方向键改值、输入首字母跳转），展开后才由浮层接管方向键/Enter/Esc；空间不足自动向上翻转，滚动/缩放/点外部/重复点同一控件都会收起。试点阶段 Chromium 实测 18 项交互断言全过（选项数、回显、定位、宽度、不越界、value 更新、change 触发、三种关闭路径、键盘两条路径、查询带 `level=ERROR`、无 pageerror）；本次推广完成静态校验（33/33 带 `dd`、组件 JS 语法通过）与全量单测（371 passed）
- **性能 P0**：`delivery_log` 此前零索引，推送历史/客户推送分布/仪表盘 recent 全走全表扫。新增 `SCHEMA_DELIVERY_LOG_INDEXES`（`sent_at`、`(customer_id,sent_at)`、`(rule_id,sent_at)`、`snapshot_id`），启动期 `IF NOT EXISTS` 幂等建索引。实测（生产库副本）：`customers/push-summary` 2.77ms→0.02ms、dashboard `recent_deliveries` 3.02ms→0.04ms
- **性能 P0**：`subscription.get_history()` 去掉 N+1 —— 原来每个快照行单查一次 deliveries（`limit=200` 时 202 条 SQL / ~750ms），改为一次 `snapshot_id IN (...)` 批量取再分组，实测 3 条 SQL / ~75ms；`deliveries` 仍按 `customer_id`/`rule_id` 过滤，语义不变，回归防线见 `tests/test_history_filters.py`
- **文档**：API 接口文档、用户手册、部署运维、系统架构全面校准（产品名称、采集模式、订阅字段、路径引用）
- **文档**：删除重复的「采集异常诊断三步法」章节
- **死代码**：删除 rollback 相关的永不命中分支 —— `snapshots.status` 的 CHECK 枚举只有 `active/superseded/withdrawn`，因此 `status IN ('active','rollback','rollback_pending')`（scheduler 2 处）、清理任务里的 6 个 `status != 'rollback'` 谓词、`snapshot.get_rollback_snapshots()` 全都等价于无条件版本，保留只会误导阅读。⚠️ 设置页「回退确认次数」`rollback_confirm` 仍是死开关：值一路传进 `run_detection(rollback_confirm=…)` 但函数体从不读取（改数字无效果），已在 docstring 标注，是否摘掉 UI 开关待定
- **数据治理**：`scripts/delete_snapshot.py` 每次 `--apply` 都会 `shutil.copy2` 整库副本（16.8MB/份）且从无轮转，`data/` 已积到 12 份 / 151.2MB。现在新增 `rotate_backups()`，备份落盘后只保留最近 `MONITOR_DB_BAK_KEEP`（默认 3）份，glob 刻意收窄到 `{db}.bak.*_pre_delete_*` —— 别人手工建的 `nsfocus_monitor.db.bak.1784655161`、`..._before-retry` 以及任何非本脚本产物都不会被误删；`keep<=0` 关闭轮转；顺手把硬编码的 `Path('/root/nsfocus-monitor/data/...')` 改成 `MONITOR_DATA_DIR` 优先、否则按脚本位置推导（脚本此前在测试副本上无法自足运行）。另新增只读盘点脚本 `scripts/data_hygiene_report.py`（不删任何东西，只打印建议命令）：备份轮转候选、散落空库/整库副本、`_snapshots_backup_pre_*` / `_deleted_placeholders_*` 一类残渣表的行数与 dbstat 实际占用、被 git 跟踪的数据文件（`data/scheduler.log`、`src/nsfocus.db`、两个 `*.bak*`）。生产首跑：合计可回收 ≈ 123.9MB（备份候选 99.5MB + 残渣表 7.7MB + 副本/空库 16.8MB+）。删除与 `DROP` 均需人工逐条确认后再执行，脚本本身不动数据
- **可诊断性**：`detector/change.py` 保存快照失败的 except 现在带上 `file_name / md5_hash / source_id` 与完整 traceback（`exc_info=True`）。生产日志里出现过一次 `Failed to save snapshot: CHECK constraint failed: status IN ('active','superseded','withdrawn')`，原先只有一行异常文本、无包标识无栈，事后无法定位是哪个条目；当前代码所有 status 写入都是合法字面量，故只加取证手段、不臆测修法
- **测试**：`python3 -m pytest` 由 7 failed / 359 passed 恢复到 **367 passed 全绿**。5 个用例是测自己的假前提或已腐化的 fixture：`test_history_chain_package_type` 硬编码 `sent_at='2026-08-21'` 配 `days=7`，日历一过必挂（改相对时间 helper）；`test_template_strip` 有两个同名函数互相遮蔽，且拿含汉字的 `'英文 1'` 当纯英文行样本；`test_router_decisions` 断言的是 `6dbdea3` 之前的旧契约（0 推送不发汇总），现补上用例守住"0 推送也要发「本次无推送」"这条产品语义

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