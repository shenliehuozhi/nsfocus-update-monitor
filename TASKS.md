# 任务看板

> 更新时间：2026-11-11　｜　全量测试：`522 passed, 1 skipped`　｜　生产服务：`active`（127.0.0.1:9999）
>
> 规则：每完成/新增任务都更新本文件；命令、验证结果和改动文件写在这里，不依赖聊天记录。

## 已完成（前端技术债收口）

- [x] **前端拆分**：`index.html` 9899 → 63 行，CSS/JS 全部落到 `src/web/static/`，12 个经典脚本无构建加载
- [x] **data.js 拆分（2026-11-11）**：`data.js` 2697 行逼近 3000 行门禁，把推送弹窗整块（`manualPush` / `showPushModal` / 收件人选择 / `confirmPush` / 结果提示 / `showRichToast` / `startBanCountdown` / `resendTargeted`，377 行）移到新脚本 `data_push.js`，加载顺序 `core → dashboard → data → data_push → pages → …`；`data.js` 降至 2321 行，纯搬移无逻辑改动
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

- [ ] **服务 stderr 日志轮转**：systemd `StandardError=append:logs/service_error.log`（以及 `service.log`）没有轮转，console handler 把每条 INFO 都写一份，5 个月累积 390M。给 `logs/*.log` 加 logrotate（daily / 50M / rotate 7 / compress），或把生产环境 console handler 级别提到 WARNING
- [ ] **语义化 CSS 重构**：把 1114 处 `data-css` 逐步收敛为组件/工具类（门禁会卡住增长），全部清空后删除 `_applyDataCss` 过渡层与 `data-css` 机制
- [ ] **ESLint / Prettier + 浏览器 e2e**：目前只有 esprima/`node --check` 语法门禁，没有 lint 与真实浏览器回归
- [ ] **静态资源版本化与 ESM 迁移**：给 `index.html` 的 css/js 加内容哈希或版本参数（当前 `no-cache` 靠每次回源，缓存击穿风险仍在）；后续再评估经典脚本 → ESM
- [ ] **Prometheus 对接**：用户明确暂缓（`/api/metrics` 与静态令牌已就绪，缺抓取配置与告警规则）

## 环境 / 运维备注

- 生产端口 **9999**（不是 29999）；服务单元 `nsfocus-monitor.service`，启动命令 `/usr/bin/python3 run.py`
- 跑测试必须指定 `TMPDIR`，否则会用到 161M 的 tmpfs（`/run/user/0/...`）并报 `database or disk is full`：
  ```bash
  cd /root/nsfocus-monitor && TMPDIR=/tmp/pytest-of-root python3 -m pytest -q
  ```
- 磁盘 2026-11-11 再次告急（95%）：清理 `logs/service_error.log` 390M（5 月至今的 INFO，无轮转，保留末尾 2000 行到 `.recent`）、`journalctl --rotate && --vacuum-size=200M`（480M → 120M）、uv/pip 缓存约 1.1G；当前 2.9G 可用（93%）。`/root/files`（9.8G）与 `/root/.hermes`（6.1G）为其他用途，未动
- **不要在生产机跑全量 pytest**：机器仅 1.6G 内存、无 swap，与 Qoder runtime（约 700M）并跑会触发整机卡死/重启；全量测试留给 CI 或空闲时段，日常只跑受影响的子集
- 测试数据一律走 `MONITOR_DATA_DIR` / `MONITOR_LOG_DIR` 隔离，不碰生产 `data/nsfocus_monitor.db`
- 重启生产服务、`git push` 均需用户明确同意
