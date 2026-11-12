# CHANGELOG

## [Unreleased]

### 新增
- **导航 · 事件与告警并入「事件与日志」**：主导航「系统日志」改名为「事件与日志」，页面内新增「🔔 事件与告警」「📋 系统日志」两个标签页，默认展示事件列表。事件页复用事件中心的筛选（严重度 / 时间范围 / 仅未确认）、分页、单条已读与按当前筛选批量确认；`events_center.js` 抽出 `EVENTS_SCOPE_MODAL` / `EVENTS_SCOPE_PAGE` 两个挂载面，工具栏、加载、分页、批量确认共用同一套实现，仪表盘卡片弹窗保持不变，避免两处口径漂移。新增 `tests/test_events_center_table.py` 导航/标签页与双挂载面断言
- **Session 采集护栏**：删除最后一个 `active` + `purpose=collect` 的 Session，或把它改为 `discover` 时，前端弹出警告确认框，明确提示之后将没有可用于采集的 Session，定时采集和手动采集都会失败；确认后带 `?force=1` 提交。`DELETE` / `PATCH /api/sessions/:id` 增加服务端兜底：目标是最后一个 active 采集 Session 且未带 `force=1` 时返回 `409`（`code=40901`、`data.requires_confirmation=true`），并限制只能操作当前用户自己的 Session（其他用户返回 404）。`GET /api/sessions` 的 `pool_status` 新增 `active_collect` 供列表判断；点击列表中的类型标签可修改 `purpose` / `collect_mode`，同时修复前端 PATCH 误传 `protocol/mode` 导致类型修改从未生效的问题；移除列表接口未使用的 `get_active_sessions()` 解密调用。新增 `tests/test_session_delete_guard.py` 11 例
- **汇总模式 · 邮箱渠道支持**：此前 `_send_digest_split` 只为企微 / 钉钉 / 飞书构造 payload，规则绑定的邮箱渠道被标记 skipped（"邮件渠道不支持汇总消息"），只绑邮箱的规则会走完重试后一封不发。现在邮箱渠道也参与汇总：收件人沿用单包邮件回退链（`rule_channels.customer_emails` → `subscription_rules.customer_emails` → `customers.email`，并合并渠道 `to_list`），同收件人组的多个邮箱渠道按绑定顺序主备（前一个成功即跳过后面的），汇总 markdown 转为 HTML 正文发送（标题 / 加粗 / 链接 / 代码），主渠道失败但备用成功时不计入队列重试；`delivery_log.recipient` 记录实际收件人。新增 `tests/test_digest_modes.py` 邮箱 3 例与 `tests/test_email_notifier.py` 系统通知 HTML 1 例
- **订阅规则 · 通知对象步骤**：向导第 4 步由「通知渠道」改为「通知对象」。机器人渠道改用 `+` 逐个添加：候选菜单按名称/类型列出未添加渠道，选中后以带模板下拉与删除按钮的行展示（模板选择保留）；邮箱改为「收件人 + 发件渠道」两组，均通过 `+` 逐个添加并以可删除标签展示（收件人支持 Enter 快捷添加与客户邮箱下拉建议）。发件渠道按渠道 `smtp_user` 识别发件地址，`@nsfocus.com` / `@nsfocus.net` 渠道在候选列表标记「优先」并新增时自动排在非 nsfocus 渠道之前；多个发件渠道按选择顺序主备（前一个失败自动用下一个）。多个邮箱渠道共享同一组收件人与附件大小，保存时写入各自 `rule_channels` 行；收件人留空仍回退客户档案邮箱。新增保存前校验（至少一个对象、填写收件人必须有邮箱渠道、无收件人且客户档案无邮箱时拦截、未添加的邮箱草稿自动提交且格式错误时阻止保存）与摘要「对象」项。`tests/test_rule_wizard_ui.py` 扩充到 6 例；无头 Chromium 32 项检查全过：客户弹窗不再拆成「产品下拉 + 版本输入」两列，改为「+ 添加产品」入口。点击后打开订阅同一套路径树弹窗，选中路径即确定产品与当前版本（如 `WAF V6.0.9`、`网络入侵防护系统 5.6.11`），升级包 / 列表容器只用于继续展开；同一产品重复选择时新路径替换旧路径，已选路径以可删除标签展示（`产品@版本`，悬停显示完整路径）。`openChainSelectorModal` 新增 `opts.plain` / `opts.title` / `opts.onConfirm`：客户模式隐藏 leaf/sub 与失效提示、不复用订阅条件状态，确认后经回调写入客户资产；订阅侧行为与父子冲突检测不变。编辑时兼容旧 `"产品"` / `"产品:版本"` 字符串与不在采集目录中的历史产品。新增 `tests/test_customer_assets_ui.py` 7 例
- **订阅规则 · 路径默认跟随客户产品**：新建规则选择关联客户后，自动把客户档案的 `owned_products`（产品 + 当前版本）转换为订阅路径：版本精确命中路径分支时用 `subtree`，无版本时取该产品第一条完整路径（`leaf`），同一产品只保留一条；手动调整过路径或勾选「全部产品」后不再自动覆盖，编辑规则保留已保存条件。订阅「添加路径」弹窗改为携带当前已选路径（取消不再清空，之前每次打开都会把条件重置为空）。新增 `customerAssetChains` / `applyCustomerPaths` 与静态门禁
- **客户月度服务报告**：新增 `GET /api/customers/:id/service-report?month=YYYY-MM`，按 UTC+8 划分月份边界，汇总当月 `delivery_log` 推送明细（时间 / 产品 / 版本 / 类型 / 状态，最多 100 条并标记截断）与成功率。客户列表新增「报告」按钮，弹窗支持上月 / 下月切换（不能进入未来月份）、推送 KPI 与推送明细表。新增 `tests/test_customer_assets_api.py` 4 例与 `tests/test_customer_assets_ui.py` 3 例
- **客户资产台账**：`customers.owned_products` 升级为 `[{"product": "...", "version": "..."}]`（兼容旧 `"产品"` / `"产品:版本"` 字符串，读取时统一规整）；客户列表展示 `产品@版本`，弹窗通过「+ 添加产品」维护持有产品与当前版本（详见上条），客户弹窗加宽到 760px。删除「📦 资产升级建议」卡片与跨产品版本比对逻辑（各产品版本分支差异大、维护成本高），客户版本只在客户信息中维护。新增 `tests/test_customer_assets.py` 与 `tests/test_customer_assets_ui.py`
- **规则库变更接入推送**：采集解析复用 `src.diff` 的 IPS / WAF 规则解析器（支持半角冒号、WAF 三段式规则名），`description_parsed` 新增 `rule_changes`（规则号 + 名称），`added/modified/deleted` ID 列表保持兼容；通知正文（邮件 / IM 模板 A–D）在元信息后增加「📦 规则变更: 新增N条 · 修改N条 · 删除N条」及最多 3 条示例规则名，飞书 `description_summary` 同步带示例规则名。新增 `tests/test_rule_change_notification.py`
- **系统配置页 · 分区卡片网格**：配置页改为与仪表盘一致的 `.cfg-grid` 双列卡片（≤900px 单列），8 个分区各自成卡，标题右侧标注生效时机（即时生效 / 下一轮采集生效 / 保存后生效 / 三项全填才展示）；保存按钮移到页头右侧，删除底部整行按钮与重复的采集提示。长文案提示（预检 URL ⓘ）不再使用 CSS 伪元素 `nowrap` 浮层，改由 `core.js` 的 body 级固定浮窗渲染，避免 3000px 宽隐藏节点把文档撑出横向滚动。新增 `tests/test_settings_page_ui.py` 守住网格、页头保存按钮、分区提示与浮窗接线
- **站点可用性 · 延时曲线**：`nsfocus_site_check` 每轮探测除更新 `system_settings.site_status` 外，还写入新表 `site_probe_history`（保留 30 天）。`GET /api/metrics` 的 `site` 新增 `history`、`history_summary`、`history_hours`，跟随全局 `hours` 窗口；超过 180 个采样时按时间桶平均降采样，失败桶保留 `s=down` / `ms=null`。观测页站点卡片新增内联 SVG 延时曲线：绿色折线 + 面积、不可用时刻红色虚线、峰值/均值/失败次数摘要与首尾时间；`format=prometheus` 不返回历史。新增 `tests/test_site_probe.py` 覆盖写入、时间窗、降采样与 30 天清理
- **可观测页 · 调度任务前移、维护模式与站点可用性**：调度任务卡片移到顶部 KPI 之后，并删除「状态」文字列，状态只由圆点颜色表达（绿运行 / 红停止 / 灰未启用，悬停显示文字）。`GET /api/metrics` 新增 `maintenance`（读取 `system_settings.maintenance_mode`）与 `site`（`update.nsfocus.com` 最近一次探测结果）；维护模式开启时页顶显示橙色告警条，采集健康中标注「已开启（推送静默）」。新增 `src/core/site_monitor.py` 与 `nsfocus_site_check` 任务，每 5 分钟探测 `https://update.nsfocus.com/`（HTTP `< 500` 视为可用），记录 HTTP 状态/耗时/连续失败并持久化到 `system_settings.site_status`；可用 ↔ 不可用切换时写 `site_unavailable` / `site_recovered` 系统事件。站点探测独立于采集调度器，采集关闭时仍继续运行；Prometheus 新增 `nsfocus_maintenance_mode`、`nsfocus_site_up`、`nsfocus_site_http_status`、`nsfocus_site_latency_ms`、`nsfocus_site_consecutive_failures`、`nsfocus_site_last_check_age_minutes`
- **可观测页信息层级优化**：客户推送的「状态」列改为成功率（≥95% 绿 / ≥80% 橙 / 其余红）；顶部新增「调度器」KPI（已启用 / 采集中 / 已关闭），调度器关闭时 KPI 转红并显示页顶红色告警条；「今日更新产品」改为「产品更新」，顶部「今日推送 / 今日成功率」改为「推送 / 成功率」并跟随全局 `hours` 窗口（KPI 读取 `delivery`，`today` 仅保留给 Prometheus 今日指标）；`GET /api/metrics` 新增 `updates`，按 `snapshots.product_name` 产品大类聚合窗口内更新包数并跟随全局 `hours`，卡片只展示产品大类 / 更新包 / 最近更新；精简各卡片冗余说明文字，强化危险/警告态底色与数值对比。调度任务卡不再只显示「调度器已关闭」空态：`scheduler_jobs` 始终返回六个已知任务并附带 `status`（`running` / `stopped` / `disabled`），页面按状态显示绿「运行中」、红「已停止」、灰「未启用」。Prometheus 指标 `nsfocus_products_updated_today` 更名为 `nsfocus_products_updated{window_hours}`
- **可观测页筛选联动与事件下钻**：客户推送卡片移除独立的 7/30/90 天选择，统计窗口跟随页面右上角全局筛选（6h/24h/7d/30d），`GET /api/metrics` 的 `customers` 改为按 `hours` 聚合并返回 `window_hours`；点击客户行下钻推送历史时按窗口换算天数。事件与告警卡片的数字变为下钻入口：点「窗口内」按当前时间窗 + 严重度打开事件中心，点「未确认」按全部时间 + 严重度 + 仅未确认打开；事件中心弹窗新增 6 小时 / 全部时间选项与「仅未确认」勾选。`GET /api/system/events` 新增 `unacked=1` 过滤，`since_hours=0` 表示不限时间，非法参数回退默认值
- **仪表盘并入可观测页**：移除独立仪表盘页面、`src/web/routes/dashboard.py` 与 `/api/dashboard`，登录后默认进入「可观测」。`GET /api/metrics` 新增 `today`（今日推送/成功率/当日更新产品）、`customers`（客户维度推送聚合，跟随全局 `hours` 窗口）、`sessions`、`process`（启动时间/运行时长）并补充对应 Prometheus gauge；可观测页新增今日 KPI、今日更新产品、Session 状态、客户推送卡片（点击下钻推送历史）、进程运行时间与「重启服务」按钮。事件中心从 dashboard.js 拆到 `events_center.js`，仅由可观测页「查看全部」打开，保留分页/过滤、批量已读与失败投递重新入队；清理 dashboard 专属轮询、事件卡与定时器分支，e2e 冒烟导航同步改为可观测/采集数据
- **前端 · ESLint / Prettier 门禁**：新增 `package.json`、`eslint.config.mjs`、`.prettierrc.json`、`.prettierignore`，CI 增加 `frontend-lint` job（Node 22 + `npm ci` + `npm run lint` + `npm run format:check`）。flat config 面向 12 个零构建、跨文件共享全局的经典脚本：关闭 `no-undef`（由 pytest 静态门禁兜底）、`no-unused-vars` 只查局部变量、`no-redeclare` 忽略内建全局（`history()`），空 catch 与常量循环条件放行；Prettier 仅覆盖新增配置与脚本，`src/web/static/{js,css}`、模板、历史文档与 fixture 明确忽略，避免旧格式一次性重排。首轮 41 个 error 已清零
- **前端 · 真实浏览器 e2e 冒烟**：新增 `tests/e2e/test_ui_smoke.py`（Playwright Python），env-gated 只读用例：登录后遍历仪表盘/采集数据/推送历史/产品管理/可观测/系统配置，断言 0 console/page error、0 失败请求、`[data-css]` 为 0、`_applyPctBars` 存在、`data-pct` 的 inline width 与百分比一致、可见进度条有实际像素宽度；缺环境变量或 Playwright 时自动跳过，不影响普通 `pytest`。本机 Chrome 对生产实例实测 6 页全部通过（0 错误，仪表盘 85 个 `data-pct` / 21 个可见，可观测性 1 条 100% / 153.3px，静态资源 25 个请求 200/304）
- **性能 · 静态资源版本化**：`index.html` 与 `/static` 此前都是 `no-cache`，每次进页面 css/js 都要回源校验。现在首页由服务端按文件内容计算 SHA-256 前 12 位，把 `/static/css/app.css` 与 12 个脚本改写成 `?v=<hash>`；带当前哈希的静态请求返回 `Cache-Control: public, max-age=31536000, immutable`，哈希缺失或过期仍是 `no-cache`——滚动升级时旧页面不会把新文件名当成旧内容缓存。首页自身保持 `no-cache` 并用响应体哈希做 ETag，条件请求直接 304。哈希按 `mtime/size/inode` 缓存，对 2 秒内刚改过的文件强制重算，规避文件系统时间戳粒度导致的同尺寸改写漏判。新增 `src/web/asset_versioning.py` 与 7 个回归测试
- **性能 · 采集数据接口条件请求（ETag/304）**：本机真实浏览器实测二次进入采集数据页仍完整传输 199KB gzip（接口 888→260ms），响应没有 validator，浏览器无法复用。`/api/latest/snapshots` 现在以 DB 文件戳生成强 ETag 并返回 `Cache-Control: private, no-cache`：数据未变直接 304（生产库副本实测 1ms、0 字节），任何写库都会改变文件戳 → ETag 变化并重建缓存，不会读到旧数据。同时识别 Flask-Compress 追加 `:gzip` 的 ETag 变体，否则 304 仍要先压缩整包 2MB 才被丢弃。4 个回归测试覆盖 200 / 304 / gzip 变体 / 写后失效 / 空数据
- **性能 · 采集数据接口缓存与体积优化**：`/api/latest/snapshots` 每次进页面都要重建约 2.4MB JSON（实测 TTFB 0.39–0.57s，SQL 75ms / JSON 55ms / 组装 25ms）。新增 `src/web/response_cache.py` 的 `DBStampCache`，以 SQLite 文件戳 `(st_mtime_ns, st_size, st_ino)` 为键缓存响应体——任何连接的任何写入都会改写文件，天然精确失效，无需 TTL 或显式 hook；缓存未命中照常查询，命中直接回放（重复进入 183ms→6ms）。JSON 输出改 `ensure_ascii=False` + 紧凑分隔符，raw 2.38MB→2.03MB（gzip 205KB 不变），并移除前端从未使用的 `source_id` 字段。5 个单测；隔离环境验证首次 183ms、二次 6ms、写库后自动失效
- **运维 · 加密密钥轮换工具**：`scripts/rotate_encryption_key.py` 解决「`MONITOR_SECRET_KEY` 一改，`channels.config`（SMTP/webhook 凭据）与 `user_sessions.cookie_value`（PHPSESSID）就再也解不开」的问题。默认 dry-run 只统计；`--apply --yes` 时先用 SQLite backup API 做一致性备份（保留最近 3 份 `_pre_rotate_enc`），再在 `BEGIN IMMEDIATE` 单事务里逐行用旧密钥解密、新密钥重加密并回读校验，任一行新旧密钥都解不开会列出且退出码 2、不影响其余行；重复执行幂等。`crypto.py` 增加显式密钥的 `encrypt_with_key` / `decrypt_with_key`，原 `encrypt` / `decrypt` 行为不变。6 个单测；生产库副本演练 16/16 迁移、旧密钥全部失效、应用以新密钥启动后渠道 7 条 / 会话 9 条正常解密。运维步骤见 `docs/部署运维.md「密钥轮换」`
- **前端拆分 · data.js 再拆推送弹窗**：`data.js` 2697 行逼近静态门禁 3000 行上限，把推送弹窗整块（`manualPush` / `showPushModal` / 收件人选择 / `confirmPush` / 结果提示 / `showRichToast` / `startBanCountdown` / `resendTargeted`，377 行）移到新经典脚本 `data_push.js`，加载顺序紧跟 `data.js`。纯代码搬移、无逻辑改动，跨文件仍共享全局作用域；`data.js` 降至 2321 行。`tests/test_frontend_assets.py` 的加载顺序门禁同步更新
- **可观测性页面**：主导航新增「可观测」，把 `GET /api/metrics` 的结果直接可视化——投递成功率（分渠道进度条、失败数标红）、即时/汇总队列积压与最久等待、采集健康（上次/下次、近 20 轮均值、数据源新鲜度）、未确认告警、存储体积与表行数、后台任务倒计时；默认最近 24 小时，可切 6h/7d/30d，30 秒自动刷新，切页或退出登录即停表。页面底部给出 Prometheus 抓取地址与 `MONITOR_METRICS_TOKEN` 鉴权说明，只复制地址、不暴露令牌；静态令牌同时接受 `X-Metrics-Token` 与 `Authorization: Bearer <token>`（Prometheus `authorization.credentials_file` 原生只发 Bearer，不再需要反向代理注入自定义头）。新增 `observability.js`（加载顺序在 bootstrap 之前）与 `tests/test_observability_page.py` 门禁
- **投递终止告警**：延迟队列重试耗尽（`retry_exhausted(N)`）或无可用渠道（`no_active_channel`）时，此前只写 logger，网页/事件中心完全看不到丢包。现在 `process_delayed_queue` 每轮把终止项合并写一条 `ERROR` 级 `delivery_error` 系统事件（含队列号、规则/客户/产品、原因、最后错误），系统事件通知开启时同步推送到配置渠道；设置页事件类型新增「投递终止」开关
- **终止队列项手动重试**：事件中心的 `delivery_error` 事件详情列出终止项并提供「重新入队」按钮（`POST /api/history/queue/:id/retry`），清零 `attempts` / `next_attempt_at` 后由 5 分钟队列任务重新投递；只允许 `retry_exhausted*` / `no_active_channel`，`reset_by_new_package` 等业务性取消不会被复活，已送达渠道仍按 `delivery_log` 去重
- **失败交付记录保留期**：`clear_history` 只删已发送行，`failed` 行会无限累积。每日 03:00 清理任务新增一步，删除 `created_at` 早于 180 天的失败记录；没有 `created_at` 的历史行无法判断年龄，保持不动
- **前端拆分 · 单文件 SPA 落地**：`index.html` 从 9899 行降到 63 行，内联样式移到 `static/css/app.css`，8600 行脚本按功能拆成 10 个经典脚本（`core` / `dashboard` / `data` / `pages` / `history` / `products` / `collection` / `account` / `bootstrap` / `tools`）。无构建、跨文件共享全局作用域，加载顺序与原单文件一致；会立即执行登录检查与轮询初始化的 `bootstrap.js` 固定最后加载，避免拆出「调用尚未定义函数」的启动白屏。PyInstaller spec 与 Docker 本来就打包 `src/web/static`，部署方式不变
- **前端静态门禁**：新增 `tests/test_frontend_assets.py`——禁止内联可执行脚本；锁定 10 个脚本及顺序；`index.html` < 80 行、单文件 < 3000 行；跨文件 `const/let` 重声明检查（浏览器会 SyntaxError 白屏）；HTML 与 JS 模板里 163 个内联 `on*` 处理器必须都有定义；`bootstrap.js` 立即调用的函数必须来自更早文件；esprima 全量解析 + CI `node --check`（`.github/workflows/tests.yml` 新增 Frontend syntax gate，ubuntu-latest 自带 node）
- **前端测试读取方式**：新增 `tests/frontend_source.py`，测试按页面真实加载顺序拼接 `static/js`；`test_dashboard_event_table` / `test_about_modal_regression` / `test_inline_attr_escaping` / `test_settings_pkg_track_ui` 不再直接读 `index.html` 里的脚本
- **投递可靠性 · 延迟队列有界重试与退避**：`process_delayed_queue` 此前无论发送成败一律 `mark_pushed`，渠道 4xx/5xx、webhook 失效、规则临时没渠道都会让消息直接消失。`delayed_queue` 新增 `attempts` / `next_attempt_at` / `last_attempt_at` / `last_error`（启动期 `sync_table_columns` 自动补列）；失败行保持 pending，按 1m / 5m / 15m / 1h / 1h 退避，默认最多 6 次（`MONITOR_DELIVERY_MAX_ATTEMPTS`）；无可用渠道直接终态 `cancelled`（`cancelled_reason=no_active_channel`），重试耗尽终态 `retry_exhausted(N)`，`last_error` 保留最后一次错误供排查；已送达渠道由 `delivery_log` 去重，重试只补发失败渠道。单条坏数据（快照已删、`chain_json` 损坏、渠道配置解密失败）只影响自己，不再打断整条队列。`get_due_items` 加 `LIMIT 200` 防止大积压时一轮卡死。回归见 `tests/test_delivery_reliability.py`
- **投递可靠性 · 独立 5 分钟队列任务**：延迟队列原来只在采集轮次结束后处理（默认 4 小时），延迟 1 小时的包实际要等到下一轮采集；现在 `refresh_scheduler_jobs()` 注册 `nsfocus_delivery_queue`（`interval` 5 分钟，启动 30 秒后首跑），与采集轮次共用 `process_delayed_queue`，进程内非重入锁保证两处并发触发时只有一处发送
- **汇总模式 · 部分失败按渠道重试**：汇总发送部分渠道失败 / 全部失败 / 无可用渠道时不再消费队列，`digest_queue` 同样记录 `attempts` / `next_attempt_at` / `last_attempt_at` / `last_error`，按 5m / 15m / 30m / 1h / 2h 退避重试，默认最多 6 次（`MONITOR_DIGEST_MAX_ATTEMPTS`）；`_digest_channel_already_sent` 在重发前检查 `delivery_log`，已完整送达的渠道直接跳过，只补发失败渠道；重试批次用 `id <= retry_max` 锁定，发送期间新入队的包不混入本轮；耗尽后消费队列并写 `ERROR` 级 `digest_summary` 事件（`retry_exhausted=True`），不再无限 pending
- **指标 · 队列重试数**：`GET /api/metrics` 的 `queues.delayed` / `queues.digest` 新增 `retrying`（`attempts > 0` 的 pending 行），Prometheus 输出 `nsfocus_queue_retrying{queue="delayed"|"digest"}`，用于区分「正常积压」与「反复失败」
- **运行指标接口 `GET /api/metrics`**：一次返回投递成功率（按 `?hours=` 窗口、分渠道，含 `undated_failed` 口径说明）、`delayed_queue`/`digest_queue` 积压与最老等待时长（digest 细分到规则）、采集耗时（取最近 20 条 `collection_summary` 事件）与源新鲜度、规则数、未确认告警、库大小/表行数、调度任务下次运行秒数。鉴权沿用 JWT，另支持 `X-Metrics-Token` 静态头（`MONITOR_METRICS_TOKEN`，未配置则不启用）供 Prometheus 抓取；`format=prometheus` 输出 `text/plain` 指标。`hours` 非法或超出 1–720 时回退 24
- **`delivery_log.created_at`**：失败交付行 `sent_at` 恒为 NULL，此前没有任何时间戳，失败率无法按窗口统计。新增 `created_at`（启动期 `sync_table_columns` 自动补列），`log_delivery` 每次写入都打点；指标按 `COALESCE(created_at, sent_at)` 过滤，历史 sent 行回退 `sent_at`，历史失败行单列 `undated_failed`
- **升级包跟踪数量（系统设置）**：设置页「📡 采集设置」新增下拉，可选最新 3 / 5 / 10 个或全部，默认 5，配置键 `pkg_track_limit`。规则历史页会累积上百个包，每轮只入库/推送最新的 N 个；改动在下一轮采集生效，无需重启
- **推送历史过滤**：页面头部过滤条新增「订阅规则」+「客户」两个下拉（与原有时间范围并列），条件写进 URL hash 可分享；后端 `GET /api/history` 新增 `rule_id` 参数，`subscription.get_history()` 按 `delivery_log.rule_id` 过滤并同时下推到每个快照的 deliveries 子查询（否则列表行会混入其他规则/客户的交付记录）
- **渠道体检**：新增每日邮件渠道凭据探测 `scheduler._channel_health_check`（04:00，只做 SMTP 连接+登录、不发信）。此前邮箱密码/授权码过期只有被动路径（`delivery_log` 留痕 + 仪表盘失败数 + 人工「测试渠道」），若期间无新包发布则完全静默；现在失败会汇总成一条 `channel_error` 系统事件（`emit_channel_error`）经系统事件渠道告警。开关 `channel_health_enabled`（默认开）
- **日志扫描**：`ERROR_PATTERNS['认证错误']` 补 SMTP 凭据失效文案（`SMTP auth failed`、`SMTPAuthenticationError`、163/Microsoft/Gmail/腾讯企业邮各自返回串），使 30 分钟日志扫描能捕获 `[EMAIL] SMTP auth failed: 535 ...`
- **API 文档**：更新路由路径（`/api/settings/scheduler/*`），删除不存在的端面
- **CI 测试门禁**：新增 `.github/workflows/tests.yml`（本仓第一道自动化门禁）—— push/PR/手动触发跑 `python3 -m pytest`，Python 3.10 + `requirements.txt` 安装，另加两条 lint 守住「测试可移植」：`tests/` 里不许出现 `/root/nsfocus-monitor` 绝对路径、不许 `sqlite3.connect` 到生产库。为此先把 6 个绑定开发机的用例改成可离线自足：`test_user_upgrade_auto_migrate` 三个用例自建 `tmp_path` 库 + `monkeypatch` DB_PATH（原来直接读生产 `data/nsfocus_monitor.db`，31 个用例连带 collect 报错），`test_rule_1013_8703_chain` 把生产 chain 值内联并改调真正的 `detector/change._chain_matches`（原来各自复制一份局部实现，测的是副本不是产品代码），`test_subscription_match` 加 autouse fixture 清 `scheduler` 的 chain 缓存并 stub `_get_chain`，`test_about_modal_regression` / `test_scheduler_rollback_chain` / `test_version_info` 的绝对路径改 `Path(__file__).resolve().parents[1]`。离仓验证：仓库副本 tar 到 `/tmp`（排除 `data/` `logs/` `.git` `__pycache__`）+ `MONITOR_DATA_DIR`/`MONITOR_LOG_DIR` 指空目录 + 按 pin 装的依赖，371 passed

### 修复
- **事件中心 · 批量确认未遵守当前筛选**：原「一键已读」只按严重度批量标记，忽略时间范围，未选严重度时还会连带标记 INFO 与全部历史事件，与按钮提示「按当前过滤器」不一致。现在按钮改为「批量确认」，`POST /api/system/events/ack-all` 新增 `since_hours`（0 = 全部时间），只标记当前严重度 + 时间范围内仍未确认的事件；前端一次请求提交 `severity` / `since_hours`，不再按 4 个 severity 循环，确认框明确显示当前范围并说明已读与范围外事件不受影响；完成后回到第一页。新增 `tests/test_events_api_filter.py` 3 例与 `tests/test_events_center_table.py` 静态门禁
- **前端 · 树路径名过长把行折成两行**：树行 `.tree-row` 带 `flex-wrap:wrap`，标签 `flex-shrink:0`，根目录下的长路径名（如「V6.0R04F04及以前（RSASNX3-X/P/S/A/E/HHA/VM）」）会折成 2 行（实测 1451 个节点里 96 行高 57px）。可点击树行改为 `flex-wrap:nowrap`，名称用 `.tree-name` 单行省略并带 `title` 完整提示，包数/活跃徽标移入不收缩的 `.tree-meta`，采集数据树、订阅路径选择树与产品管理路径树三处统一
- **前端 · 采集数据左侧产品名过长换行**：卡片头 `flex-wrap:wrap`，长产品名（如「绿盟智能安全运营平台(ISOP)」）会把「活跃/低频」徽标和「采」按钮挤到第二行，卡片头高度从 49px 涨到 78px。产品名改为 `flex-1 min-w0 ellipsis` 单行省略，完整名称保留在 `title` 悬浮提示；新增 `tests/test_data_page_ui.py` 两例门禁
- **前端 · 订阅「添加路径」与采集数据产品树只能展开到根路径**：`data-css` 重构把树节点折叠从内联 `style.display` 改成 `.hidden` 类，但 `app.css` 里遗留的 `.tree-children{display:none}` 仍然无条件生效——展开时只是移除 `.hidden`，计算样式仍为 `none`，所以两层树的箭头点了都没反应，只能看到根节点。删除该遗留规则，折叠统一由 `.hidden{display:none!important}` 控制；同时修复树容器 id 由节点名转义生成导致中文名（如「系统升级包」/「规则升级包」）被压成同一串下划线、同层不同分支撞 id 而展开到别的分支的问题，改为递增序号（`tcd_*/cst_*`）。新增 `tests/test_frontend_assets.py` 两例静态门禁
- **前端 · 采集数据跳转详情「大小」显示 `NaN K`**：列表接口已裁掉 `file_size`（只有详情接口返回），`dataSnapDetailHtml` 仍无条件按数值换算，`null/1024` 得 `NaN`。现在 `file_size` 为空时不渲染该行，详情懒加载补齐后再显示
- **运维 · stderr 日志无轮转把磁盘吃到 95%**：`logger.py` 的 console handler 固定 `INFO`，systemd `StandardError=append:` 又把 stderr 原样追加到 `logs/service_error.log`，5 个月累积 390M，叠加 journal 480M 与缓存后根分区到 95%。console handler 改为按输出目标取级别：交互终端 `INFO`，重定向到文件/管道时 `WARNING`（`MONITOR_CONSOLE_LOG_LEVEL` 可显式覆盖）；新增 `deploy/nsfocus-monitor.logrotate`（daily / 50M / rotate 7 / compress / copytruncate，适配 systemd 持有的 fd）与 `tests/test_logger_console_level.py`。文件日志仍按 `MONITOR_LOG_LEVEL` 写 `app.log`（10MB × 10 轮转），不影响排查
- **前端 · 页面渲染异常静默**：`showPage()` 直接调用页面函数，`async` 函数内部抛错只留下未处理的 Promise rejection，界面表现为「点击没反应」（本次 `data-args` 占位符事故正是如此）。现在统一走 `_pageRenderError()`：控制台输出页面名与堆栈，界面弹「页面加载失败：…」；401 仍由 `api()` 统一处理，不重复提示。门禁见 `tests/test_frontend_lifecycle.py`
- **前端 · 导航四页点击无反应（`data-args` 占位符被写成裸标识符）**：`$el` / `$value` / `$checked` 只有作为字符串才会被 `_actArg()` 解析成元素/值；迁移内联处理器时 34 处写成 `_dataArgs($el)`，页面函数在构造模板时直接抛 `ReferenceError`，而异常发生在写 `#app` 之前、`async` 页面函数又无人 catch，表现就是「采集数据 / 推送历史 / 系统配置 / 产品管理」点击后停在原页面（接口其实都 200）。全部改为 `_dataArgs('$el')` 等字符串占位符；另修 2 处 `el.style = '…'` 静默无效赋值（`style` 是只读对象）改 `.style.cssText`。新增静态门禁 `test_data_args_placeholders_are_strings` / `test_no_style_property_assignment`
- **前端 · 登录凭证改 HttpOnly Cookie + CSRF**：JWT 此前存在 `localStorage`，任何 XSS 都能直接读走 24 小时有效的凭证。登录改为下发 `nsm_session`（HttpOnly + SameSite=Lax，HTTPS 下可加 `MONITOR_COOKIE_SECURE=1` 启用 Secure）与可读的 `nsm_csrf` 双提交 Cookie，登录响应不再回传 token；`require_auth` 对 Cookie 鉴权的 `POST/PUT/PATCH/DELETE` 校验 `X-CSRF-Token`，缺失/不匹配返回 403。`Authorization: Bearer` 继续接受（旧标签页与脚本客户端不受影响，且不要求 CSRF）；旧标签页首次 `/auth/me` 时服务端顺带换发 Cookie，前端随后清掉 localStorage 副本。新增 `POST /api/auth/logout` 清除 Cookie，登出改为服务端+客户端双清；`/api/metrics`、包类型刷新 SSE 同时接受 Cookie。前端 `api()` 统一附带 CSRF 头，SSE/导出等 4 处裸 `fetch` 不再手拼 Bearer。回归见 `tests/test_auth_cookie.py`（8 例）与 `test_frontend_lifecycle.py` 新增门禁
- **前端 · HTML 文本上下文转义缺口（存储型 XSS）**：拆分时只处理了内联 `on*` 属性位，普通文本位仍有多处直接插值数据库内容。`product_name` / `customer_name` / `rule_name` / 渠道名与类型 / 文件名 / MD5 / 描述 / 日志路径 / 事件渠道等 30+ 处改为经 `escHtml()` 输出，日志详情按钮的 JSON 参数改走 `escAttr()`（双引号不再截断属性）。新增 `tests/test_html_text_escaping.py`：扫全部已加载 JS 的模板行，危险字段（`c|ch|s|h|snap|src|p|f|r|d|x|g`）出现在 `<...>` 模板里未经 `escHtml/escAttr/_opt/fmtTZ/typeCN/chTypeChip` 等包装即失败，并校验 `escHtml` 覆盖 `&<>"'`
- **前端 · 定时器泄漏与登出后 401 轮询**：仪表盘/日志/可观测/事件中心/采集/发现/封禁等 10 余个 `setInterval` 只在切页时零散清理，登出后仍在后台打 `/api`，Token 失效时每个请求各自弹 toast 并反复跳登录。现在 `core.js` 集中管理：`_clearPageTimers(page)` 处理切页，`_clearAllTimers()` 处理登出与 401；`products.js` 三个轮询点统一登记 `_productsPollCleanup`（含 SSE `abort`），`showPage()` 先清上一页再切。`api()` 加全局 401 拦截：除 `/auth/login`（密码错误不能清 token）外，只处理一次——清 token、停全部定时器、单条提示、回登录页
- **前端 · 慢请求返回后覆盖当前页面**：页面渲染函数 `await` 后才写 `#app`，用户中途切页时旧响应会把新页面盖掉。新增导航代际 `_navSeq` / `navStale(seq)`：`dashboard` / `dataPage` / `logs` / `history` / `settings` / `products` / `sessions` / `channels` / `customers` / `subscriptions` 全部在入口记 `_seq`，写 DOM 前 `if(navStale(_seq))return`
- **前端 · CSS 自定义属性自引用**：`--success-border`、`--gray-bg`、`--snap-ver-bg`、`--snap-ver-border`、`--snap-pkg-type-badge/color` 被定义成 `var(自己)`，按规范在 computed-value 阶段失效（继承或初始值），snapshot 版本徽标等回落成透明/继承色。改为真实调色板值或指向 `--gray-bg` / `--border` / `--primary-light`；门禁禁止自引用
- **前端 · 裸 `fetch` 绕过统一鉴权/401 处理**：`dashboard`（推送汇总、系统健康）、`data`（调度状态、采集进度、日志文件）、`tools`（diff 抓取/缓存/对比）、`collection` 等 9 处直接 `fetch`，不带 Token 也不走 401 拦截。全部改走 `api()`（错误提示统一 `j.message`）；仅保留 4 处必要例外：`api()` 自身、`products` 的 SSE 流、`tools` 两处 CSV 文本/blob 导出。新增 `tests/test_frontend_lifecycle.py` 钉住 401 排除登录、定时器清理清单、`_navSeq` 守卫、裸 fetch 白名单与 CSS 自引用
- **汇总失败事件 · 开机不足 1 小时时首次失败被静默吞掉**：`_log_digest_failure` 用 `time.monotonic()`（起点是开机时间）做每小时去重，却把「从未记录」的默认值写成 `0`；进程/容器启动不足 3600 秒时 `monotonic - 0 < 3600` 恒成立，第一次失败直接 return，`digest_summary` 事件不落库。默认值改为 `None`，只有真实记录过才参与去重；回归见 `tests/test_digest_modes.py::test_digest_first_failure_logs_event_on_fresh_clock`
- **延迟推送 · 本地时间与 UTC 混比导致晚 8 小时**：`push_after` 由 `compute_push_time()` 用 `datetime.now()` 写本地时间，`get_due_items()` 却用 `datetime('now')`（UTC）比较，CST 服务器上所有延迟推送被硬拖后 8 小时（延迟 1 小时实际等 9 小时）。改为统一本地时间比较（`mark_pushed` / `mark_delivery_retry` / `mark_delivery_terminal` 时间戳同口径），`/api/metrics` 的 `overdue` 与系统页队列统计也改按本地时间
- **渠道 · 历史坏密文触发 `ValueError` 中断推送**：`channel._decrypt_config` 只捕获 `JSONDecodeError` / `TypeError`，密钥轮换或早期写入格式不同导致的 `ValueError`（如 nonce 长度校验）会冒泡到发送链路，该条推送直接异常。现在一并捕获并回退空配置，由发送侧按「无可用渠道」走终态
- **指标 · Prometheus 输出规范**：窗口统计是 Gauge 且会回落，名字却带 `_total`（Prometheus 约定 `_total` 仅用于单调 Counter，会误导 `rate()`）；同时缺 `# TYPE` 声明、标签值未转义换行。现在统一 `# TYPE <name> gauge` 去重输出，指标改为 `nsfocus_delivery_events` / `nsfocus_delivery_channel_{events,sent,failed}` / `nsfocus_delivery_undated_failed` / `nsfocus_collection_sources`，`_label()` 补 `\n` 转义；回归新增格式校验（每行可解析、family 有且仅有 TYPE、无重复序列、无 `_total` Gauge）
- **指标/仪表面板 · 服务重启后 `last_run` 显示为空**：`get_status()` 只读模块内存态，重启后到首轮采集结束前 `last_run/last_quick_run` 全是 null，尽管 `system_settings.last_quick_scan_at` 里有值；`_load_last_full_scan()` 定义了但从未被调用。现在内存为空时回落到 `system_settings`（新增 `_load_last_quick_scan()`），`/api/metrics` 的 `next_run` 改取 `nsfocus_collect` 任务的实际下次执行时间，采集中 `current.duration_s` 按 `started_at` 实时计算（原来恒为 0）
- **仪表盘 · 事件中心表头渲染成 `"+ "+ "+ "+ ...` 垃圾文本**：`renderEventsCenterTable` / `renderEventsTable` 的 `const head = \`...\`` 模板里混入了旧的字符串拼接片段（`'<thead ...>'+\n '<tr ...>'+ ...`），反引号内不再执行拼接而是原样输出，仪表盘卡片表头与事件中心弹窗都多出一串引号和加号。两处表头改回纯模板字面量；新增 `tests/test_dashboard_event_table.py` 守住模板内不得出现单引号拼接片段，并校验 7 个表头列齐全
- **汇总模式 · 上期汇总后才到货/漏发的包永远不投递**：发送侧只取「当前 period_key」的 pending 行，同一周期汇总发出之后才检测到的包（如周日 02:00 发完、08:00 来了新包）入队后永远不会再被取走；触发日漏跑、维护模式、时间窗太窄造成整期漏发时同理——这些包既没即时推送，也不进任何一期汇总。现在每次到期投递该规则**全部** pending（含往期），按本次最大队列 id 标记已发（发送期间新入队的留到下一期）；`digest_last_sent` 记最早一行的周期，补发往期内容不占用本周期发送名额（否则「周日漏发、周一补发」会把此后发送日永久挪到周一）
- **汇总模式 · 错过触发日/时间窗整期丢失**：周/月目标日原来用 `==` 严格相等，目标日进程没跑（或维护模式、窗口内没有检查点）就整期作废。现在 ①周/月改 `>=`，同周期后续检查可补；②队列里有往期 pending 时无视星期立即补发；③检查频率 6h → 15min——6 小时固定相位遇到 <6h 的时间窗（如 09:00–12:00，刻度 02/08/14/20）会每周都跳过
- **汇总模式 · 发送失败仍标记「已发送」**：`_send_digest_split` 不返回结果，`mark_digest_sent` 无条件执行 → 渠道 4xx、webhook 失效、规则没绑渠道、渠道被停用、只绑邮箱等情况下队列被静默消费，网页/事件中心都看不到。现在返回 `{sent, failed, skipped}`：全失败/无可用渠道不消费队列（下轮自动重试，日志和 `system_event_log` 每小时最多一条）；部分成功才标记并对失败渠道告警；每次汇总（成功/部分失败/全失败）都写 `system_event_log`（`event_type='digest_summary'`）
- **汇总模式 · ISO 周边界 key 对不上**：周 key 用日历年拼（`now.year`），2026-12-31 入队 `2026-W53`、2027-01-03 检查得到 `2027-W53`，跨年那周的包永远 pending。抽出 `subscription.digest_period_key()`（周用 ISO 年），入队与到期判定共用
- **汇总模式 · 同一包多 chain 重复计数**：一个包匹配多条 chain 时队列里有多行，汇总显示「本期新增 N 个」并把同一包列 N 遍。展示按 `snapshot_id` 去重，标记范围仍覆盖全部队列行
- **汇总模式 · 直发 webhook 不走加签**：汇总自己 `requests.post`，不读 `config.secret`，钉钉加签/飞书签名渠道会 310000/19021 失败。现在按渠道调 `_sign_url`（钉钉 ms + url quote、飞书秒级 + raw）
- **汇总模式 · 飞书显示 markdown 原文**：飞书 text 消息不渲染 markdown，汇总里的 `##`/`**`/`<u>`/`[x](url)` 原样露出。改为 `post` 富文本，标题去标记、链接转 `a` 标签
- **汇总模式 · 文案**：季度标题拼成「季度度升级汇总」；每条明细补「版本: branch version」，产品列表排序稳定
- **汇总模式 · 手动「重推」假成功**：`/resend` 对汇总规则只会再入队一次，本周期已发过就永远发不出去，接口仍返回「已重新推送」。`route_notifications` 新增 `force_immediate`，重推直接走即时发送
- **汇总模式 · 队列无清理**：`digest_queue` 不在 DB 清理范围，sent/cancelled/孤儿行永久累积；snapshots 清理还会因 FK 引用 pending 行失败。新增 `cleanup_digest_queue()`（只清 sent/cancelled 90 天，pending 是未投递 outbox 不能删）+ `(rule_id,status)`/`period_key` 索引；snapshots 清理跳过仍被 `digest_queue` 引用的行
- **UI · 汇总模式提示**：规则弹窗注明汇总只发企微/钉钉/飞书，邮箱渠道会被跳过
- **推送汇总 · 采集完成汇总被去重「吃掉」**：`emit_collection_summary` / `emit_cleanup_summary` 的防重用 `created_at >= date('now')` 做字符串比较，2026-09-02 起时间戳写入格式从 `YYYY-MM-DD HH:MM:SS` 变成 ISO `YYYY-MM-DDTHH:MM:SS.ffffff`，而 `'T'（0x54）> ' '（0x20）`，同一天已存在一条旧记录就让「今天是否发过」恒为真——线上 9/1 还有 8 条，9/2 之后每天固定 1 条（当天首次）。改为 SQL 端 `datetime(created_at) >= datetime('now', '-N minutes')` 同时归一化两种格式，清理汇总 1 分钟、采集汇总 5 分钟窗口
- **推送汇总 · 时间显示成 UTC**：`pushed_at` 直接输出 ISO UTC（`00:10:10` 实际是北京时间 08:10），改用 `base._utc_to_cst_display` 转 CST 渲染
- **推送汇总 · 计数口径与邮件故障转移**：`total` 原本是渠道交付次数（一个包绑 3 个渠道记 3），标题却像在说包数，改为明确写「共 N 条交付」；邮件主渠道失败、备用渠道补发成功的场景以前记成 1✓ 1✗（看起来失败了），现在 `_send_immediate` 对同收件人组回标：主渠道 `failed-1 / fallback+1`，条目注明「已由备用渠道补发」，组头用 `{success}✓ {failed}✗ {fallback}↻`，标题追加「已补发」
- **系统通知 · 钉钉/飞书/邮件渲染成产品模板**：汇总、清理、渠道体检这类 `product_name` 为空的 `NotificationMessage` 仍走产品模板，钉钉/飞书会出现空标题 + 空元数据占位，邮件会带「MD5 校验」段和下载按钮。三个 notifier 加无产品分支：钉钉直接发 description_full 并分片、飞书发 `msg_type=text`、邮件主题取 `message.title`、正文纯文本转义、跳过 MD5/下载提示
- **推送汇总 · 静默失败与不可观测**：`emit_push_summary` 不落库，系统事件渠道发失败只写一行 warning，网页上查不到任何痕迹；汇总里失败明细也不折叠，24 条突发时单条消息逼近企微 4096 字节硬限。现在 ①每 cycle 的汇总写入 `system_event_log`（`event_type='push_summary'`，有失败升 WARNING）；②统一走 `_deliver_event`：IM 渠道先过 `_throttle_im` + 返回失败重试一次（间隔 3s），email 渠道明确报错「系统事件渠道不能是邮件渠道」；③每组成功明细上限 12 条、失败上限 20 条，超出折叠为「另有 N 个（详见推送历史）」
- **推送汇总 · 累加器生命周期**：`_emit_push_summary` 先 `emit` 再 `clear`，发送链上一旦抛异常，本轮条目会污染下一轮；改为先快照 + 清空再发送并捕获异常。`scheduler.run_collection` 的异常分支也补发一次，避免收集中途失败时本轮推送结果完全丢失；手动「重推」走 `accumulate=False`，不再混入下一轮采集汇总
- **推送汇总 · digest 忽略返回码**：`_send_digest_split` 把 HTTP 2xx 当成功，企微 `40058/45009`、飞书 `code!=0` 都记 `sent`；email/未知渠道直接静默跳过。现在校验 `status_code` + `errcode`/`code`，失败写 `delivery_log(status='failed', error=...)` 并停止后续分片；不支持的渠道类型一律 warning 后跳过。另删除死代码 `_write_push_summary_from_delivery_log`（70 行）与 `_split_text`
- **邮件 · 同一邮箱绑定多个渠道会重复发送**：去重键 `(snapshot_id, channel_id, rule_id, chain_json)` 带 `channel_id`，同一规则绑两个 email 渠道发给同一邮箱时各发一封，收件人层面没有任何去重。现在 `_send_immediate` 预解析每个 email 绑定的收件人（`rule_channels.customer_emails` → `subscription_rules.customer_emails` → `customers.email`，与 `email.send` 同优先级），按收件人集合分组，并按 `rule_channels.id`（绑定顺序）做故障转移：前一个渠道发送成功就不再发后面的，失败才继续下一个；组内任一渠道此前已送达（`delivery_log` 有 `sent`）则整组跳过，避免换轮次后重复。不同收件人的 email 渠道互不影响；机器人渠道行为不变。回归见 `tests/test_email_channel_failover.py`
- **企微 · 推送汇总超长被拒（40058）**：`2026-11-11 00:10:10 Push summary failed: Wecom error [1/1]: markdown.content exceed max length 4096`。00:09 一分钟内集中推了 24 条（20 成功 + 4 失败），汇总按 `(rule, channel, chain)` 分 14 段、每段固定 3 行头（30 个 `─` + 规则/客户/渠道 + 时间/成功/失败），段头就占近 3KB，整条 ≈4.3KB。`wecom.py` 只有产品推送走分片，系统通知分支（推送汇总/清理汇总等）是 `bodies = [content]` 单条硬发，所以必然 40058。现在 ①系统通知也走 `_split_with_marker(content, 4000)`（字节安全 + `(i/N)`）；②`_build_push_summary_message` 每组压成一行头（`─ 规则 / 客户 / 渠道 · 20✓ 4✗ · 时间`），每组成功明细上限 `SUMMARY_MAX_ITEMS_PER_GROUP=12`，超出折叠为「另有 N 个成功」，失败条目全量保留。同一批数据实测 4300B → 2571B；回归见 `tests/test_push_summary_limits.py`
- **推送 · IM 渠道节流卡在企微 20 条/分钟硬限上**：原来只有 `MONITOR_RATE_LIMIT_SEC=3` 的最小间隔（恰好 20 条/分钟），一轮突发 24 条时第 21 条起全部 `45009 api freq out of limit`。`router._throttle_im()` 在最小间隔之外加 60 秒滑动窗口，默认企微/钉钉 18 条/分钟、飞书 90 条/分钟（`MONITOR_RATE_LIMIT_{WECOM,DINGTALK,FEISHU}_PER_MIN`），窗口满时睡到最早一条滑出后再发；`_send_immediate` 与 digest 推送共用
- **清理 · 失败交付记录每次清理都被删**：`clear_history` 的 `OR sent_at IS NULL` 把 `delivery_status='failed'`（`sent_at` 恒为 NULL）无条件删光，推送失败在历史里查不到。改为只按 `sent_at` 删除已发送的过期记录，失败行保留
- **采集 · 历史包回填被当成新包推送**：全表解析上线后，规则历史页里旧解析器漏掉的历史包被一次性 INSERT，`first_seen == last_seen` 让 detector 判 NEW，客户收到十几年前「新包」的推送（线上 id 10354：发布 2025-09-08、首次发现 2026-11-10，同一轮回填 139 行、229 条交付记录）。现在 `save_snapshot` 按路径（`source_id + source_url + path_id`）计算两个锚点：`MIN(first_seen_at)`（开始监控时间，新路径退回当前时间）与当前最新 active 包的 `published_at`——发布时间早于锚点 7 天以上的以 `superseded` 入库且 `first_seen_at` 回填为发布时间（detector 因此判 UNCHANGED），首次接入某路径时整页历史包也不会再被当新包推（首轮实测仍有 2010 年的包被推，故宽限期对无路径锚点的新路径同样生效）；比最新 active 包早超过 7 天的 active 行降级为 superseded；保存最新包时同路径更老的 active 行一并收口（track window 之外的老 active 也覆盖）。同批发布（间隔 ≤7 天，一次发 3 个包）仍全部 active + NEW。detector 改为「先全部入库、再统一判 NEW」并加 `status=='active'` 门禁，避免同一轮里被更新的同路径包降级后仍被误判。回归见 `tests/test_snapshot_backfill_status.py`
- **采集 · save_snapshot 第二查参数错位**：`status=?` 占位符排在 `page_hash/source_url` 之前，但参数顺序没同步，`status` 实际收到 page_hash（hex）→ `CHECK constraint failed: status IN ('active','superseded','withdrawn')`，superseded/withdrawn 行重现时整条 UPDATE 失败（H03/SP04 长期停在旧状态）。参数顺序已对齐，并改为按当前批次决定是否复活
- **仪表盘 · 「异常 (2h内)」永远为空**：正则与 `app.log` 实际格式有两处不匹配——时间戳是空格分隔不是 ISO `T`，级别是 `[ERROR]` 带方括号不是裸 `ERROR`，生产日志里 24 条 WARNING/ERROR 一条都进不来。改为 `_scan_recent_log_alerts()` 统一解析（兼容两种时间格式），并把尾部采样从「readlines 整个 10MB 再取 500 行（≈30 分钟，覆盖不了 2h 口径）」改成只 seek 文件尾部 1MB / 4000 行；修好后当天 2 条 `Failed to save snapshot` 恢复可见。回归见 `tests/test_health_log_scan.py`
- **仪表盘 · 产品配色三处不一致**：趋势曲线按产品名排序取色、图例按包数量排序取色、产品分布表又用另一套 `PIE_COLORS`，同一产品在图例/曲线/分布表是三种颜色（勾选图例里的产品可能高亮错曲线）。改为单一 `_buildProdColorMap()` 按名称索引，图例固定按名称排序；产品分布表配色与趋势图一致
- **采集 · 一页多包漏采**：绿盟规则历史页是「一个升级包一张 `<table>`」（WAF `rule6070` 实测同一页 247 张表），旧解析器只读第一张表，一次发布多个包时只有最新那个能被看见，其余包永远不入库、不推送。现在逐表逐包解析，按 `published_at` 只跟踪最新 N 个；撤回/取代检测仍用全量 key，滑出窗口的老包不会被误标 withdrawn/superseded。快速采集同步改为读取完整页面（原来截断 50KB，第 20 个之后的表根本读不到）。实测 247 包全量解析 ≈722ms（每张表 4 行、单遍遍历）；回归见 `tests/test_multipkg_track_window.py`（2 表 4 包、一次 3 新包全判 NEW、窗口外不误撤回、描述正文含「名称：」不拆包）
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

### 安全
- **启用 CSP + 零内联处理器**：`index.html` 与 11 个静态 JS 共 282 处内联 `on*="…"` 全部移除，改为统一事件委托——`data-on-click/change/input/keydown="fn"` 声明动作，`data-args='${_dataArgs(...)}'` 传 JSON 实参（`_dataArgs = escHtml(JSON.stringify(args))`，修掉 `escAttr` 给引号加反斜杠导致 `JSON.parse` 失败的问题），占位符 `$el/$event/$value/$checked` 与 `data-stop / data-self / data-key / data-prevent` 修饰符保留原有阻止冒泡、只响应自身、回车触发、阻止默认行为语义；`href="javascript:void(0)"` 改为 `href="#" data-prevent`。服务端 `after_request` 统一下发 `CSP_POLICY`：`script-src 'self'`（不再允许内联脚本/处理器）、`object-src 'none'`、`base-uri 'self'`、`form-action 'self'`、`frame-ancestors 'none'`、`img-src 'self' data:`、`connect-src 'self'`；静态门禁同步改为零内联检查：扫描全部 JS + HTML 禁止 `on*=` 属性，提取 177 个 `data-on-*` 动作名并校验全部已定义、事件类型限定在 click/change/input/keydown、`data-args` 只允许 `_dataArgs(...)`/JSON 字面量；新增 `tests/test_csp_headers.py` 固化策略。⚠️ 需重启服务后 Header 才生效
- **CSP 收紧到 `style-src 'self'`**：1114 处内联 `style="…"` 全部改写为 `data-css="…"`，由 `core.js` 的 `_applyDataCss()` + `MutationObserver` 在节点插入后通过 CSSOM 应用（CSP 只拦截 HTML 内联样式，CSSOM 合法）。选择过渡层而非直接改 CSS 类的原因：现有代码大量依赖 `el.style.display` 读写（约 20 处判断、190 处赋值），改成类会破坏可见性判断；`data-css` 保留“模板样式在前、脚本覆盖在后”的原有优先级，零视觉改动。同时删除 `data.js` 运行时注入的 `<style>`（`table thead th` 规则移到 `app.css`）。新增门禁：禁止新的内联 `style` 属性、禁止 `createElement('style')`/`setAttribute('style')`/`insertRule`/`adoptedStyleSheets`，并给 `data-css` 数量加“只减不增”上限（当前 1114）。后续把 `data-css` 逐步收敛为语义化 CSS 类后即可删除过渡层。⚠️ 需重启服务后 `style-src` 才变为 `'self'`

### 改进
- **订阅规则 · 创建/编辑改为 4 步向导**：规则弹窗原先把基本信息、订阅条件、推送配置、通知渠道、模板说明一次铺开，字段多且需要长滚动。现在拆成「基本信息 → 订阅条件 → 推送策略 → 通知渠道」四步，顶部步骤条可回退、点击前进时逐级校验（规则名称、关联客户、至少一条路径或「全部产品」、汇总窗口时段），底部按步骤显示「上一步 / 下一步 / 保存规则」。最后一步显示客户 / 条件 / 渠道数 / 推送模式摘要，模板说明默认收起；保存时若校验失败会自动跳回对应步骤。编辑规则仍从第 1 步开始并保留已保存条件。步骤条与摘要随后做了视觉强化：渐变面板、当前步骤白底浮起 + 圆点光环、完成步骤显示绿色对勾与进度连线；摘要改为左侧主色强调条 + 「客户 / 订阅 / 渠道 / 推送」标签 pill。新增 `tests/test_rule_wizard_ui.py` 5 例；无头浏览器 32 项检查覆盖前进校验、摘要、保存载荷与编辑回显
- **通知渠道 / 订阅规则 · 创建按钮与 Session 页对齐**：「+ 添加渠道」与「+ 创建规则」从卡片正文移到卡片头部右侧，复用 Session 页的 `btn btn-primary ml-auto px5-14 fs12`，表格去掉多余上边距。纯静态资源改动，生产读盘即生效、无需重启
- **页面命名：可观测 → 仪表盘**：主导航、页面标题与采集完成弹窗按钮统一改回「仪表盘」，与用户既有叫法一致；内部路由 `data-page="observability"`、`observability.js` 与 `/api/metrics` 保持不变，避免破坏书签和定时器清理逻辑。同步更新页面门禁、e2e 导航断言与用户手册 / API 文档 / 模块文档中的页面名称
- **前端 · data-css 机制移除（7 → 0）**：最后 7 处动态进度条宽度（可观测性投递成功率、数据页产品分布/客户失败率、采集与发现横幅、单产品采集横幅、采集进度弹窗）不再写 `data-css="width:X%"`，改为模板输出 `data-pct="X"`，渲染完成后调用 `_applyPctBars(root)` 一次性通过 CSSOM 写入 `el.style.width`——CSP 的 `style-src 'self'` 只拦 HTML 内联样式，CSSOM 合法。随后删除 `core.js` 的 `_applyDataCss()` 与全局 `MutationObserver` 过渡层（`history.js` 里与样式无关的 `_discObs` 保留）。门禁 `DATA_CSS_MAX` 从 7 降到 0，`test_data_css_applier_present` 改为 `test_data_css_mechanism_removed`，断言源码中不再出现 `data-css`/`_applyDataCss` 且存在 `_applyPctBars`。全量 `547 passed, 1 skipped`
- **前端 · data-css 组件化第八批（97 → 7）**：把显隐切换统一改为 `.hidden` 类、动态颜色统一改为状态类。`core.js` 的 `_showById/_hideById/_setDisplay` 与登录/登出、用户菜单、事件中心、弹窗、树折叠、产品/工具页步骤、采集与发现横幅全部改走 `classList`，不再读写 `style.display`；动态颜色收敛为 `.sev-critical/error/warning/info`、`.sev-text-*`、`.evt-row-*`、`.pal-0..23`、`.pie-c0..9`、`.log-lv-*`、`.hlth-badge` + `text-*`、`.sched-*`、`.ch-type-*`、`.match-*`、`.tpl-badge-*`、`.rd-group-new/updated/deleted`、`.push-tab(-active)`、`.seg-btn-active`/`.tab-flat(-off)`、`.disc-confirm-phase.err`、`.collect-banner.done` / `.disc-banner.done` 等；树缩进用 `.tree-row`/`.ti-0..16`，产品行禁用态用 `.row-disabled`/`.btn-disabled-look`，弹窗统一 `.modal-box(-disc)`。顺带修复浮动日志窗口：删除 `FLOAT_LOG_CSS` 里的内联 `display:none`，改由 `.float-log hidden` + `classList` 控制，避免“移除 hidden 仍被内联样式压住”的回归。剩余 7 处 `data-css` 全部是动态进度条宽度（`width:${pct}%`），作为过渡层最后用途保留；门禁 97 → 7。同步更新 `test_about_modal_regression.py` 断言为 `.hidden` 语义。纯静态资源改动，生产读盘即生效、无需重启：CSS `43123afc6538`（84,563B，immutable）、`account.js` `0868e1181b7b`、`bootstrap.js` `c0ccc2e531a3`、`collection.js` `ed9c59ec347d`、`core.js` `20d679be0cdf`、`dashboard.js` `70a734910de4`、`data.js` `1a38e5edc668`、`data_push.js` `0cfe6045a51a`、`history.js` `418ec9de5dd5`、`observability.js` `7a5641a1ecf3`、`pages.js` `4f5d3d4eef78`、`products.js` `6c86365e416f`、`tools.js` `4d6b6bf61e61`；全量 `547 passed, 1 skipped`
- **前端 · data-css 组件化第七批（160 → 97）**：清理剩余静态组合，新增 31 条规则：数据页统计表头收成 `.data-th-num` + `ta-right`/`ta-left`/`w160`，数值单元格收成 `.data-num-pad`；采集数据 feed 表头与行宽度改用 `w72/84/116/180`、`shrink0`、`muted-11`、`pl16`、`ellipsis` 组合；事件中心弹窗筛选条/分页脚收成 `.evt-tools-bar` / `.evt-foot` / `.evt-foot-end`，事件详情单元格收成 `.detail-cell(-lg)`，原始 JSON 面板与时间列收成 `.log-panel(-tall)` / `.log-ts(-lg)`；仪表盘健康卡宽度改用 `min-w80/100/110/130/160`，无 Session 告警收成 `.alert-danger-panel`，空态收成 `.empty-success-lg`，紫色提示条收成 `.info-purple-wide`；历史页表头工具条、分页收件人列、发现弹窗标题与进度条（`.progress-8` / `.banner-bar-3-tight`）一并迁移，共替换 63 处。动态 `${}` 值、`display:none` 显隐组合与进度条宽度继续保留。纯静态资源改动，生产读盘即生效，无需重启：CSS `99e5821c9b32`（70,935B，immutable）、`collection.js` `a3a4f6f06c67`、`dashboard.js` `7d0bb7145fd9`、`data.js` `40a6cb31d161`、`history.js` `bc330445dcc9`、`observability.js` `726382cc11ef`、`pages.js` `f171df1db2ad`、`products.js` `6d2526d67650`。门禁 160 → 97，全量 `547 passed, 1 skipped`
- **前端 · data-css 组件化第六批（506 → 160）**：本批集中清理跨文件重复的弹窗/横幅/徽标/表格与日志视图样式，新增 244 条规则：通用工具类（`p12`/`py12–32`/`mt1–20`/`mb0–20`/`gap2–16`/`min-w60–200`/`max-w130–320`/`fs9–32`/`lh14-15`/`d-block`/`justify-center`/`items-end` 等）与组件类（`.confirm-card-purple`/`.confirm-head`/`.dialog-icon`/`.dialog-x`/`.center-screen`/`.overlay-999`/`.modal-card-420/560/600/760/860/960`、`.push-tabs`/`.push-tab`/`.push-tab-active`/`.push-textarea`/`.push-foot`/`.pick-row(-lg)`/`.push-meta-row`/`.push-dim`、`.banner-warn`/`.banner-danger`/`.banner-bar(-3)`/`.warn-banner`/`.info-purple-note`、`.diff-sec`/`.cnt-success|danger|purple|disabled`/`.diff-list-box`/`.diff-actions`/`.diff-danger-box`/`.diff-warn-box`、`.code-chip(-muted)`/`.log-dark`/`.json-pre`/`.table-hd-row`、`.tag-primary|warn|success|danger|purple`/`.badge-xs-*`/`.pill-*`、`.snap-filter-bar`/`.list-box-240|320`/`.tree-row`/`.tree-tag`/`.split-*`/`.ops-*`/`.discover-row`），共替换 346 处。两处因与既有 CSS 同名冲突而改名：`.badge-warn` → `.flag-warn-badge`、`.data-filters` → `.snap-filter-bar`；`display:none` 组合、含 `${}` 的动态值与进度条宽度按规则全部跳过，另跳过 `data-css` 存于 JS 字符串变量（`colStyle`/`selClass`）的片段与一处既有的非法值 `color:var(--text)827`。纯静态资源改动，生产读盘即生效，无需重启：CSS `e956fb7152c7`（68,414B，immutable）、`collection.js` `f7e48a4caf9c`、`core.js` `7d2adf1aa02f`、`dashboard.js` `f30332e5c4ab`、`data.js` `1e65fb5d2c0d`、`data_push.js` `39f757a2d3f8`、`history.js` `9ae00a2e2920`、`observability.js` `68afb9295e6a`、`pages.js` `1772f5a5b500`、`products.js` `70a103aac340`、`tools.js` `acfd8791d595`。门禁 506 → 160，全量 `547 passed, 1 skipped`
- **前端 · data-css 组件化第五批（672 → 506）**：继续收敛重复的静态组合，新增一组小工具类（`mt2/6/10/20`、`mb2/3/12`、`mr4/8`、`mx4`、`gap4/24`、`wrap`、`col`、`shrink0`、`min-w0/36/140`、`fw400/500/700`、`fs16/115`、`lh1/17`、`ta-right`、`underline`、`no-select`、`text-white/body`、`bg-subtle`、`muted-13/14`、`gray-muted-13`、`disabled-12`、`opacity-90/50`、`p6/20/30/40`、`px2`、`p8x12`、`py30`、`mt-neg6` 等）与组件类（`.progress-track`、`.diff-box`、`.pick-card`、`.log-viewer`、`.btn-purple-outline`、`.overlay-998/1000`、`.seg`、`.seg-btn-active`、`.warn-strong`、`.chip-warn`、`.status-row`、`.list-indent`、`.sec-title`、`.modal-body`、`.input-lg`、`.warn-box`、`.empty-success`、`.pane-scroll/clip/list`、`.list-compact`、`.tree-toggle`、`.icon-plain`、`.h30-pill`），共替换 166 处。`display:none` 相关组合（13 处）因 JS 读写 `style.display` 全部保留；新增弹窗用途/格式切换按钮的激活态改类后，`syncModalButtons()` 仍在打开时写回内联样式，行为不变。纯静态资源改动，生产读盘即生效：CSS `0625953ac1d9`（48,281B，immutable）、`dashboard.js` `54bdd39d5d3d`、`data.js` `7292f9969a7b`、`pages.js` `c0649a378da4`。门禁 672 → 506，全量 547 passed
- **前端 · data-css 组件化第四批（772 → 672）**：新增 22 个宽度工具类（`w36`–`w420`）收掉 45 处固定列宽 / 输入框宽度（`<col>`、表头 `th`、弹窗 `.card`），新增 `.table-base` / `.table-fixed` / `.ta-center` / `.box-border` 复用表格基础样式（5 处），事件中心表头与统计表收敛为 `.evt-thead` / `.evt-head-row` / `.data-stats-thead` / `.data-stats-td` / `.data-stats-row`（13 处），日志弹窗大号确认框收成 `.confirm-box-lg`（2 处），采集/发现横幅收成 `.banner-chip` / `.banner-chip-lit` / `.banner-close`（8 处），版本差异日志行收成 `.rd-ver-line` + 既有颜色工具类（14 处），另有 `.subtitle` / `.tree-box` / `.tree-indent` / `.feed-arrow-w` / `.flex-center-10` / `.max-w380` / `.text-cyan` 等（14 处），共替换 100 处。审计确认 JS 只对进度条与可拖拽 `[data-col]` 单元格写宽度，被替换的静态宽度均无读回；`.feed-arrow-w` 置于文件末尾以 28px 压过 `.feed-arrow` 的 24px，与原先内联优先级一致。纯静态资源改动，生产读盘即生效：CSS `74706e413098`（44,092B，immutable）、`dashboard.js` `a0f19134ea27`、`pages.js` `74f9fad3b850`。门禁 772 → 672，全量 547 passed
- **前端 · data-css 组件化第三批（898 → 772）**：事件中心表头（12 处）与数据页统计表头（3 处）收成 `.evt-th` / `.data-th`，运维健康卡指标标签收成 `.metric-label`（6 处），设置页提示收成 `.cfg-note`（7 处）；复用已有 `.flex1`（9 处）、`.muted-12`，并新增 `font-mono` / `title-15` / `empty-icon` / `empty-text` / `text-primary` / `text-purple` / `pointer` / `fw600` / `ellipsis` / `w-full` / `m0` 等小工具类，共替换 126 处。审计确认被替换元素没有动态 JS 写回：`opacity:0.6` 只用于静态文本，产品行的透明度仍由 JS 直接设置（未替换其动态 `data-css`）。生产读盘生效：CSS `7a8c32f19a54`（42,023B，immutable）、`dashboard.js` `d845d671e2a3`、`tools.js` `a30f0e0e20fe`。门禁 898 → 772，全量 547 passed
- **前端 · data-css 组件化第二批（1064 → 898）**：新增一组文本 / 间距工具类并集中定义在 `app.css` 末尾（`text-muted` / `text-danger` / `text-success` / `text-success-text` / `text-gray-muted` / `text-disabled`、`fs10`–`fs14`、`muted-10/11/12`、`gray-muted-12`、`disabled-11`、`ml-auto`、`flex-1`、`mt14` / `mr10` / `mb4/8/10`、`p8` / `p16`），同特异性平局时靠后生效，等价于原来的内联样式。共替换 166 处重复 `data-css`（13 处 `font-size:12.5px`、11 处 `margin-left:auto`、10 处 `flex:1`、22 处 `color:var(--muted)` 等）。只替换单值/纯静态组合：`display:none`（JS 读 `style.display`）和含模板表达式的动态颜色（如 `active-badge`）原样保留。纯静态资源改动，生产服务直接从磁盘读取，无需重启；首页自动生成新哈希（CSS `d7fe876e9d3a`）。门禁上限 1064 → 898，全量 547 passed
- **前端 · data-css 组件化第一批（1114 → 1064）**：把模板里重复的整段内联样式收敛成具名组件类——`modal-overlay`（此前全站 3 处各抄一遍，CSS 里反而没有定义）、`modal-card-sm/md/scroll`、`modal-close`、`icon-btn`、`modal-actions`、`modal-form-actions`、`modal-ft`、`about-*`（关于弹窗整页）、`badge-xs`、`cfg-event-label`。`account.js` 的 14 处 `data-css` 全部清零，dashboard/pages/products/history/tools 的重复弹窗、关闭按钮、表单动作区、事件类型复选标签同步迁移；顺带删掉 `.rd-picker-product` 上 CSS 已完全覆盖的冗余声明。逐类校验新类声明与删除的 `data-css` 逐项一致，并处理特异性（`label.cfg-event-label` 压过 `.form-group label`）。保留 `.tree-children` 的 `display:none` 内联值：展开逻辑读 `style.display`，换成类会让首次点击失效。门禁上限从 1114 下调到 1064，全量 547 passed
- **前端 · 重复顶层函数清理**：拆分后残留 6 组同名顶层 `function`（`showRichToast`、`escHtml`、`_groupPkgTypes`、`setPurpose`、`setMode`、`onWinToggle`），经典脚本后加载的定义会静默覆盖前一份，前一份成为永远执行不到的死代码。保留运行时实际生效的实现，`escHtml` 移到首个加载的 `core.js`（单引号一并转义，全站只保留一份）；新增 `test_no_duplicate_top_level_functions` 门禁，重复声明直接失败
- **仓库卫生**：`monitor.db`、`nsfocus.db`、`nsfocus_monitor.db`、`src/nsfocus.db`、`data/scheduler.log` 取消 git 跟踪（磁盘文件保留，生产库不受影响），`.gitignore` 增加全局 `*.db` / `*.db-*`
- **仪表盘 · 运维视角收口**：今日推送卡的大字从「成功数」改为「成功率」，失败数单独标红；「最近更新」按 snapshot 聚合，一个包推给 N 个客户只占一行（右侧显示客户数，悬浮看逐客户结果），无推送时显示空态而不是空白卡；事件已读后的卡片刷新改走 `/api/dashboard?events_only=1`，不再为刷新 10 条事件把 timeline / product_stats / recent_deliveries 全部重算；去掉首屏重复的 `/settings/scheduler` 请求，直接复用 `/system/health` 返回的 scheduler 状态（4 个请求 → 3 个）。回归见 `tests/test_dashboard_events_only.py`：此前只有 `.form-group select` 关了原生外观（`appearance:none` + 灰三角），散在工具条/标签行/弹窗里的 select 还是 Windows Chrome 的系统箭头 + 系统边框，同屏两套长相；内联样式里还各自重复写 border/radius/font-size（11/12/13px、4/6/8px 混杂）。现在加全局 `select` 基线（同一枚灰三角 + `var(--gray-border)` + 8px 圆角 + 13px 字号 + hover/focus/disabled 三态），新增 `select.sel-sm`（6px 圆角/12px）给密集工具条，并清掉 15 处内联装饰样式、事件中心两个下拉去掉 `btn btn-xs btn-outline` 类。Chromium 实测 14 个可见下拉 computed style 全部一致（appearance=none / 自定义箭头 / padding-right≥24），0 例外。⚠️ 原生 `<option>` 弹层由操作系统绘制，CSS 改不了，要统一那层得换自定义下拉组件（已在下面试点）
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