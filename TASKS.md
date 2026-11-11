# 任务看板

> 更新时间：2026-11-11　｜　全量测试：`547 passed, 1 skipped`　｜　生产服务：`active`（127.0.0.1:9999）
>
> 规则：每完成/新增任务都更新本文件；命令、验证结果和改动文件写在这里，不依赖聊天记录。

## 已完成（前端技术债收口）

- [x] **前端拆分**：`index.html` 9899 → 63 行，CSS/JS 全部落到 `src/web/static/`，12 个经典脚本无构建加载
- [x] **data.js 拆分（2026-11-11）**：`data.js` 2697 行逼近 3000 行门禁，把推送弹窗整块（`manualPush` / `showPushModal` / 收件人选择 / `confirmPush` / 结果提示 / `showRichToast` / `startBanCountdown` / `resendTargeted`，377 行）移到新脚本 `data_push.js`，加载顺序 `core → dashboard → data → data_push → pages → …`；`data.js` 降至 2321 行，纯搬移无逻辑改动
- [x] **本机环境端到端验收（2026-11-11）**：经 Qoder 本机工作区对 `http://10.10.10.109:29999` 做只读 HTTP 验收——`data_push.js` 200/20106B、首页 12 个脚本顺序正确、登录 `code=0`、`latest/snapshots`、`history?days=30`、`settings/config`、`settings/scheduler`、`system/products`、`metrics?hours=24` 全部 200 且 `code=0`；静态资源字节与仓库逐一相同（data.js 122732 / data_push.js 20106 / pages.js 110128 / history.js 52362 / products.js 83179 / core.js 22328）
- [x] **事件委托替代内联处理器**：282 处 `on*="…"` 清零；`data-on-click/change/input/keydown` + `data-args`（`_dataArgs` 统一转义）；`data-stop/self/key/prevent` 语义保留
- [x] **CSP 上线**（2026-11-11 两次重启后均已验证）：
  `script-src 'self'` / `style-src 'self'` / `object-src 'none'` / `base-uri 'self'` / `form-action 'self'` / `frame-ancestors 'none'` / `img-src 'self' data:` / `connect-src 'self'`
  验证：`curl -I http://127.0.0.1:9999/` 返回完整 Header；静态资源 200；未登录 API/`/auth/me` 均 401；启动日志无异常
- [x] **内联样式清零**（2026-11-11）：1114 处 `style="…"` 改为 `data-css`，`core.js` 用 `_applyDataCss()` + `MutationObserver` 经 CSSOM 应用；删除运行时注入的 `<style>`；`style-src` 已改为 `'self'`（重启后生效）
- [x] **样式门禁**：禁止新内联 `style` 属性、禁止运行时注入 `<style>`（`createElement('style')` / `setAttribute('style')` / `insertRule` / `adoptedStyleSheets`），`data-css` 数量设“只减不增”上限（1114）
- [x] **静态门禁重写**：零内联处理器扫描、177 个 `data-on-*` 动作名必须已定义、事件类型白名单、`data-args` 只能用 `_dataArgs`/JSON
- [x] **CSP 单测**：`tests/test_csp_headers.py`（含 `script-src` 不得含 `'unsafe-inline'` 断言）
- [x] **HTML 文本/属性转义审计**：`tests/test_html_text_escaping.py`；`_dataArgs` 替代会破坏 JSON 的 `escAttr(JSON.stringify(...))`
- [x] **HttpOnly Cookie + CSRF 双提交**：JWT 不再落 localStorage；非安全方法回传 `X-CSRF-Token`；脚本/API 仍可用 Bearer
- [x] **登录态生命周期**：全局 401 失效处理、统一定时器注册、登出/切页清理、异步渲染导航代际保护
- [x] **observability 页面**：投递/队列/采集/事件/存储/调度分区，30 秒自动刷新，切页停表
- [x] **导航四页点击无反应修复**（2026-11-11）：迁移内联处理器时把 `data-args` 占位符写成裸标识符（`_dataArgs($el)` 等 34 处），模板求值抛 `ReferenceError`，页面函数在写 `#app` 前中断且无人 catch → 采集数据 / 推送历史 / 系统配置 / 产品管理点击后停在原页面（接口实际已 200）。改为字符串占位符 `'$el'` / `'$value'` / `'$checked'`；顺带修 2 处 `el.style = '…'` 静默无效赋值 → `.style.cssText`
- [x] **页面渲染异常不再静默**（2026-11-11）：`showPage()` 统一捕获页面函数同步/异步异常，控制台留栈 + 界面提示「页面加载失败」，避免再次出现「点击没反应」式故障
- [x] **占位符/样式赋值门禁**：`test_data_args_placeholders_are_strings`、`test_no_style_property_assignment`（`tests/test_frontend_assets.py`）
- [x] **前端其他修复**：裸 `fetch` 迁移到 `api()`、CSS 自引用变量、重复顶层函数清理、`javascript:` 链接清除

## 待排期

- [x] **服务 stderr 日志轮转（2026-11-11）**：console handler 在 stderr 被重定向时默认只输出 `WARNING+`（交互终端仍为 `INFO`，`MONITOR_CONSOLE_LOG_LEVEL` 可覆盖），避免每条 INFO 双写；新增 `deploy/nsfocus-monitor.logrotate`（daily/50M/7 份/compress/copytruncate）与 `tests/test_logger_console_level.py`。生产已安装 `/etc/logrotate.d/nsfocus-monitor` 并重启验证：`active`、首页 200、重启后 stderr 仅 6 行且全部为 WARNING，INFO 只进轮转的 `app.log`
- [ ] **语义化 CSS 重构**：把 1114 处 `data-css` 逐步收敛为组件/工具类（门禁会卡住增长），全部清空后删除 `_applyDataCss` 过渡层与 `data-css` 机制。第一批已完成（1114 → 1064）：模板里重复的整段内联样式收敛为具名组件类（`modal-overlay` / `modal-card-sm|md|scroll` / `modal-close` / `icon-btn` / `modal-actions` / `modal-form-actions` / `modal-ft` / `about-*` / `badge-xs` / `label.cfg-event-label`），`account.js` 的 14 处 `data-css` 清零；`cfg-event-label` 用 `label.cfg-event-label` 写法压过 `.form-group label` 的 `display`，避免标签竖排。保留 `.tree-children` 的 `display:none` 内联值（展开逻辑读 `style.display`，换类会让首次点击失效）。门禁上限下调到 1064，全量 `547 passed, 1 skipped`。生产已于 2026-11-11 13:11 重启部署 `d122f69` 并验证：`active`、首页 200 / `no-cache` / ETag `c7211228827e28d1`、13 个资源均带新 `?v=`（CSS `0e579919c0b8`）、当前哈希 CSS 为 `immutable`（39,449B）、过期哈希仍 `no-cache`、首页条件请求 304（4ms / 0B）、登录 `code=0`、关键接口全部 200、`app.log` 无 ERROR；本机 headless Chrome 实测关于 / 改密 / 添加 Session 弹窗与事件类型标签的 computed style 与迁移前一致（复选标签仍横排；flex 容器内 `inline-flex` 会被 blockify 为 `flex`，属预期）。第二批（1064 → 898）：新增文本/间距工具类（`text-*`、`fs10`–`fs14`、`muted-10/11/12`、`ml-auto`、`flex-1`、`mt14`/`mr10`/`mb4/8/10`、`p8`/`p16`）并置于 `app.css` 末尾，替换 166 处重复 `data-css`；只动单值/静态组合，`display:none` 与动态颜色表达式保留。生产读盘即生效：CSS 新哈希 `d7fe876e9d3a`（40,381B，immutable）、`pages.js` 新哈希 `25dbca467388`，无需重启；门禁 898，全量 `547 passed, 1 skipped`。第三批（898 → 772）：`.evt-th` / `.data-th` / `.metric-label` / `.cfg-note` 组件类 + `title-15` / `empty-icon` / `text-primary` / `pointer` / `fw600` / `ellipsis` / `m0` 等小工具类，复用 `.flex1` / `.muted-12` / `font-mono`，共 126 处；动态写回元素（产品行透明度、动态颜色）未动。生产 CSS `7a8c32f19a54`（42,023B）、`dashboard.js` `d845d671e2a3`、`tools.js` `a30f0e0e20fe`；门禁 772，全量 `547 passed, 1 skipped`。第四批（772 → 672）：22 个宽度工具类 `w36`–`w420` 收掉 45 处固定宽度（`<col>` / 表头 / 输入框 / 卡片），`.table-base` / `.table-fixed` / `.ta-center` / `.box-border` 表格工具类，`.evt-thead` / `.evt-head-row` / `.data-stats-*` 表格组件（13 处），`.confirm-box-lg`、`.banner-chip` / `.banner-chip-lit` / `.banner-close`、`.rd-ver-line` + 颜色工具类（14 处），以及 `.subtitle` / `.tree-box` / `.tree-indent` / `.feed-arrow-w` / `.flex-center-10` / `.max-w380` / `.text-cyan`，共 100 处；`w36` 首次 dry-run 漏配后补齐 2 处。JS 只对进度条与可拖拽 `[data-col]` 单元格写宽度，静态宽度无读回；`.feed-arrow-w` 末尾 28px 压过 `.feed-arrow` 24px，与内联一致。生产读盘即生效：CSS `74706e413098`（44,092B，immutable）、`dashboard.js` `a0f19134ea27`、`pages.js` `74f9fad3b850`，无需重启；门禁 672，全量 `547 passed, 1 skipped`。第五批（672 → 506）：新增一组小工具类（间距/字号/字重/宽度下限/透明度/内边距）与组件类（`.progress-track` / `.diff-box` / `.pick-card` / `.log-viewer` / `.btn-purple-outline` / `.overlay-998|1000` / `.seg` / `.seg-btn-active` / `.warn-strong` / `.chip-warn` / `.status-row` / `.list-indent` / `.sec-title` / `.modal-body` / `.input-lg` / `.warn-box` / `.empty-success` / `.pane-*` / `.list-compact` / `.tree-toggle` / `.icon-plain` / `.h30-pill`），共 166 处；`display:none` 相关 13 处因 JS 读写 `style.display` 保留，用途/格式切换按钮激活态改类后仍由 `syncModalButtons()` 内联写回，行为不变。生产 CSS `0625953ac1d9`（48,281B）、`dashboard.js` `54bdd39d5d3d`、`data.js` `7292f9969a7b`、`pages.js` `c0649a378da4`；门禁 506，全量 `547 passed, 1 skipped`
- [ ] **ESLint / Prettier + 浏览器 e2e**：目前只有 esprima/`node --check` 语法门禁，没有 lint 与真实浏览器回归
- [x] **首屏接口优化（2026-11-11）**：实测 `/api/latest/snapshots` raw 2.38MB / gzip 205KB、TTFB 0.39–0.57s（SQL 75ms、JSON 55ms）；`/api/system/products` raw 956KB / gzip 51KB、85ms。已做：DB 文件戳缓存（重复进入页面 183ms→6ms，任何写入自动失效）、`ensure_ascii=False` + 紧凑分隔符（raw 2.38MB→2.03MB）、移除前端未使用的 `source_id`；顺带修复跳转详情 `file_size` 被裁后显示 `NaN K` 的回归。生产已于 2026-11-11 重启部署并验证：`active`、首页 200、接口 2,034,502B（gzip 199KB）、缓存命中 17–88ms、响应中 `source_id` 已消失
- [x] **接口条件请求 ETag/304（2026-11-11）**：本机真实浏览器实测（Chrome headless + CDP，实例 `a860937`）二次进入采集数据页仍完整传输 199KB gzip、接口 888→260ms，响应没有 validator。现在以 DB 文件戳生成强 ETag + `Cache-Control: private, no-cache`，数据未变回 304（生产库副本实测 1ms / 0B），任何写库后 ETag 变化并重新构建；同时识别 Flask-Compress 的 `:gzip` ETag 变体，避免为回 304 仍压缩 2MB。4 个回归测试覆盖 200 / 304 / gzip 变体 / 写后失效 / 空数据。生产已重启 `b6995f5` 验证：gzip 条件请求 304 为 5–6ms / 0B（未短接时 184ms）、identity 缓存命中 18ms、首页 200。本机浏览器复测（`b6995f5`）：首次 200 / 199,540B / 530ms，第二、三次为真实 HTTP 304（已发送 If-None-Match、transferSize 300B、46–48ms、服务端 1–8ms），fetch 透明拿到完整数据，50 行 / 61 页 / 登录后 0 错误；采集写库窗口内 ETag 会变化并退化为 200 全量，属预期设计。ETag 稳定性跟随写库：30s HEAD 轮询中观察到 25.2s 连续不变，也观察到 2.5s 内变化；要稳定复现 304，需取 ≥3.6s 的无写入窗口。同次实测确认 NaN 修复生效（详情“文件大小”5.5MB、无字面量 NaN）、50 行 feed / 10,017 DOM 节点 / 页面自报 167–212ms
- [ ] **首屏客户端渲染 / 历史懒加载**：真实浏览器实测页面自报 167–185ms、`.feed-row` 仅 50 行、DOM 约 1 万节点，客户端分组不是当前瓶颈；若后续数据量再翻倍，再考虑按源懒加载历史并由服务端返回统计
- [x] **资源版本化与 ESM（2026-11-11）**：`index.html` 里的 css/js 改由服务端按内容哈希输出 `?v=<sha256前12位>`（`src/web/asset_versioning.py`），哈希命中当前内容时 `/static` 返回 `public, max-age=31536000, immutable`，缺失/过期仍 `no-cache`；首页保持 `no-cache` 并带响应体 ETag，条件请求 304。哈希缓存按文件戳失效，2 秒内刚改过的文件强制重算（规避同尺寸快速改写撞时间戳粒度）。7 个测试覆盖哈希内容变化、路径逃逸、首页 URL、静态缓存头、首页 304；全量 `547 passed, 1 skipped`。生产已于 2026-11-11 13:00 重启部署 `64af1b2` 并验证：`active`、首页 200 / `no-cache` / ETag `2797b52955f0bfe7`、13 个资源均带 `?v=`、当前哈希 CSS 为 `immutable`、过期/无版本仍 `no-cache`、首页 gzip 条件请求 304（4ms / 0B）、`latest/snapshots` 304（4ms / 0B）且冷请求 200 / 199,188B / 94ms、登录 `code=0`、`app.log` 无 ERROR；ESM 未迁移：经典脚本零构建且跨文件共享全局作用域，切换需同时重构加载顺序与全局依赖，留待后续单独评估
- [ ] **Prometheus 对接**：用户明确暂缓（`/api/metrics` 与静态令牌已就绪，缺抓取配置与告警规则）
- [x] **密钥轮换工具（2026-11-11）**：`scripts/rotate_encryption_key.py` 默认 dry-run，`--apply --yes` 时先做一致性备份、再单事务把 `channels.config` / `user_sessions.cookie_value` 从旧密钥重加密到新密钥，逐行校验并用新密钥回读；`src/core/crypto.py` 增加显式密钥的 `encrypt_with_key` / `decrypt_with_key`。6 个单测通过；在生产库副本上演练 16/16 行迁移成功、旧密钥全部失效、应用以新密钥启动后渠道 7 条与会话 9 条均正常解密
- [x] **生产密钥轮换（2026-11-11 11:57）**：`.env` 原先未定义 `MONITOR_JWT_SECRET` / `MONITOR_SECRET_KEY`，且 `CORS_ORIGINS` 为空（允许所有来源）。已停服务 → 用 `scripts/rotate_encryption_key.py` 把 16 条密文（渠道 7 + Session 9）从开发兜底密钥迁移到新密钥（备份 `data/nsfocus_monitor.db.bak.20261111_115715_pre_rotate_enc`）→ 生成 JWT 新密钥 → `.env` 补齐两密钥并 `chmod 600`，`CORS_ORIGINS` 限制为 `http://10.10.10.109:29999,http://127.0.0.1:9999,http://localhost:9999` → 启动。验证：`active`、首页/静态资源 200、登录 `code=0`、渠道 7 条与会话 9 条正常解密、旧密钥 16/16 失效、新密钥 16/16 可读、非白名单 Origin 不返回 CORS 头、重启后无 fallback 告警。本机（Qoder 电脑）只读复验同样通过：登录/channels/sessions/latest-snapshots/data_push.js 均 200，`code=0`，渠道 7、会话 9、快照 80 个源。`/root/.env.before-secret-rotate` 为改前备份（chmod 600）

## 环境 / 运维备注

- 生产端口 **9999**（不是 29999）；服务单元 `nsfocus-monitor.service`，启动命令 `/usr/bin/python3 run.py`
- 跑测试必须指定 `TMPDIR`，否则会用到 161M 的 tmpfs（`/run/user/0/...`）并报 `database or disk is full`：
  ```bash
  cd /root/nsfocus-monitor && TMPDIR=/tmp/pytest-of-root python3 -m pytest -q
  ```
- 磁盘 2026-11-11 再次告急（95%）：清理 `logs/service_error.log` 390M（5 月至今的 INFO，无轮转，保留末尾 2000 行到 `.recent`）、`journalctl --rotate && --vacuum-size=200M`（480M → 120M）、uv/pip 缓存约 1.1G；当前 3.0G 可用（93%）。`/root/files`（9.8G）与 `/root/.hermes`（6.1G）为其他用途，未动
- **不要在生产机跑全量 pytest**：机器仅 1.6G 内存、无 swap，与 Qoder runtime（约 700M）并跑会触发整机卡死/重启；全量测试留给 CI 或空闲时段，日常只跑受影响的子集
- 测试数据一律走 `MONITOR_DATA_DIR` / `MONITOR_LOG_DIR` 隔离，不碰生产 `data/nsfocus_monitor.db`
- 重启生产服务、`git push` 均需用户明确同意
