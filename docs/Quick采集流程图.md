# Quick 采集详细流程图

## 目录
1. [整体架构](#1-整体架构)
2. [Scheduler.run_now() 完整步骤](#2-schedulerrun_now-完整步骤)
3. [_collect_quick 详细流程](#3-_collect_quick-详细流程)
4. [run_detection 详细流程](#4-run_detection-详细流程)
5. [mark_rollback_pending 详细流程](#5-mark_rollback_pending-详细流程)
6. [save_snapshot 匹配逻辑](#6-save_snapshot-匹配逻辑)
7. [包级别 diff 日志生成逻辑](#7-包级别-diff-日志生成逻辑)
8. [Session 轮换与失效处理](#8-session-轮换与失效处理)
9. [ROLLBACK_CONFIRM 防抖动机制](#9-rollback_confirm-防抖动机制)
10. [完整数据流时序图](#10-完整数据流时序图)

---

## 1. 整体架构

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 产品管理（外部系统）                                      │
│   在 content_sources 表中配置 package_type_discovered（JSON 数组）                       │
│   每个元素：{ url: "/update/listWafV6/v/6.0.7", chain: ["WAF V6.0.7","WAF V6.0系统升级包"] } │
└────────────────────────────────────────────┬───────────────────────────────────────────┘
                                             │ 运营人员配置一次
                                             ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              content_sources 表（产品目录）                              │
│  ┌──────────────┬──────────────────────────────────────────────────────────────────┐   │
│  │ id=101       │ name="WEB应用防护系统(WAF)"                                         │   │
│  │ url         │ "https://update.nsfocus.com/update/listWafV6"                     │   │
│  │ package_    │ {                                                                    │   │
│  │ type_       │   "paths": [                                                        │   │
│  │ discovered  │     { "url": "/update/listWafV6/v/6.0.7",                           │   │
│  │             │       "chain": ["WAF V6.0.7","WAF V6.0系统升级包"] },               │   │
│  │             │     { "url": "/update/listWafV6/v/rule6.0.7",                      │   │
│  │             │       "chain": ["WAF V6.0.7","WAF V6.0规则升级包"] },               │   │
│  │             │   ]                                                                 │   │
│  │             │ }                                                                    │   │
│  └──────────────┴──────────────────────────────────────────────────────────────────┘   │
└────────────────────────────┬───────────────────────────────────────────────────────────┘
                             │ 采集时读取
                             ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              Scheduler.run_now('quick')                                 │
│                                                                                         │
│   ① 获取 session                                                          ┌────────────┐ │
│   ② Session 预检（verify_session）                                       │ 定时触发   │ │
│   ③ 遍历每个 source_id，执行 _collect_quick                               │ (cron/4min)│ │
│   ④ 遍历每个 source，执行 run_detection                                   │            │ │
│   ⑤ 匹配订阅规则，route_notifications                                    │ 手动触发   │ │
│   ⑥ WAL checkpoint                                                        └────────────┘ │
└────────────────────────────┬───────────────────────────────────────────────────────────┘
                             │
          ┌──────────────────┴──────────────────┐
          ▼                                      ▼
┌─────────────────────┐               ┌─────────────────────────┐
│ NsfocusCollector    │               │ change.run_detection     │
│ ._collect_quick()   │               │ .save_snapshot()        │
│  ├─ 遍历已知 URLs   │               │ .mark_rollback_pending()│
│  ├─ HTTP GET        │──────────────▶│ ._confirm_rollbacks()   │
│  ├─ page_hash 对比  │   items[]     └─────────────────────────┘
│  ├─ SAME → 反查DB   │
│  ├─ CHANGE → 解析   │
│  └─ seen_ids 填充   │
└─────────────────────┘
```

---

## 2. Scheduler.run_now() 完整步骤

```
输入：mode = 'quick' | 'full'

─────────────────────────────────────────────────────────────
Step 0: 并发保护
─────────────────────────────────────────────────────────────
  if _is_running: return { status: 'skipped', reason: 'Already running' }
  _is_running = True
  设置 _progress 状态：phase='init'

─────────────────────────────────────────────────────────────
Step 1: 获取有效 Session
─────────────────────────────────────────────────────────────
  sessions = get_active_sessions()
  cookie = sessions[0]['cookie_value']
  _collector._set_cookie(cookie)

  预检：verify_session(HEALTH_URL)
    └─ 失败 → update_status(session[0], 'expired')
    └─ 用 sessions[1] 重试
    └─ 全部失败 → 中止采集，返回 error

─────────────────────────────────────────────────────────────
Step 2: 确保 content_sources 已注册（Bootstrap）
─────────────────────────────────────────────────────────────
  existing_sources = { name: row for row in list_content_sources('nsfocus') }
  for name, entry_url in _collector_products().items():
      if name not in existing_sources:
          upsert_source(name, 'nsfocus', entry_url, 'standard', category='security')
  existing_sources 刷新

─────────────────────────────────────────────────────────────
Step 3: 执行采集
─────────────────────────────────────────────────────────────
  if mode == 'quick':
      all_items = _collect_quick(existing_sources, cookie, _emit)
  elif mode == 'delta':   # 兼容旧版，内部同 quick
      all_items = _collect_quick(existing_sources, cookie, _emit)
  else:                   # full 模式
      all_items = _collect_full(existing_sources, sessions, cookie, _emit)

  _progress['products_done'] = len(existing_sources)

  if not all_items:
      # 空采集不触发 rollback（可能是网络抖动）
      _is_running = False
      return summary with status='warning'

─────────────────────────────────────────────────────────────
Step 4: 按 source_id 分组，执行变更检测
─────────────────────────────────────────────────────────────
  for each (name, src) in existing_sources:
      4a. 按 source_id 过滤 items
      4b. 读取 before_snaps（所有 status in ('active','rollback','rollback_pending')）
          SQL: SELECT id, file_name, md5_hash FROM snapshots
               WHERE source_id=? AND status IN ('active','rollback','rollback_pending')
      4c. 调用 run_detection(src['id'], src_items, ROLLBACK_CONFIRM,
                             seen_ids={s['id'] for s in before_snaps})
      4d. 汇总日志：【产品】采集完成：本次提取 N 个 | 新增 M | 回滚 K
      4e. _progress 更新：total_new, total_rollback

─────────────────────────────────────────────────────────────
Step 5: 推送通知
─────────────────────────────────────────────────────────────
  if result.new_items:
      rules = get_enabled_rules()
      for rule in rules:
          matched = get_new_for_subscription(rule, result.new_items)
          for sid, snap in matched:
              route_notifications(sid, rule['id'])

  # 回滚通知（仅 notify_rollback=1 的规则）
  for rule in rules:
      if rule.get('notify_rollback', 1):
          for sid, snap in result.rollback_items:
              route_notifications(sid, rule['id'], is_rollback=True)

─────────────────────────────────────────────────────────────
Step 6: 处理延迟队列
─────────────────────────────────────────────────────────────
  process_delayed_queue()

─────────────────────────────────────────────────────────────
Step 7: WAL checkpoint（防止 WAL 文件无限增长）
─────────────────────────────────────────────────────────────
  execute('PRAGMA wal_checkpoint(PASSIVE)')

  _is_running = False
  _last_run = datetime.utcnow()
  if mode == 'full':
      _last_full_run = datetime.utcnow()
  返回 summary
```

---

## 3. _collect_quick 详细流程

```
输入：source_id, product_name
返回：List[UnifiedContentItem]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
内部初始化
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  items = []          # 最终返回的所有包
  seen_ids = set()    # 保护快照不被 rollback 误标（Bug #006）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 1: 从 content_sources 读取已知 URL 列表
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SQL: SELECT package_type_discovered FROM content_sources WHERE id=?
  解析 JSON，提取 paths 数组：
    for p in paths:
        url      = p['url']                          # 如 "/update/listWafV6/v/6.0.7"
        chain    = p['chain']                         # 如 ["WAF V6.0.7","WAF V6.0系统升级包"]
        ver      = chain[-2] if len(chain)>=2 else '' # "WAF V6.0.7"
        pkg_type = chain[-1]  if chain      else ''  # "WAF V6.0系统升级包"
        paths_urls.append((url, ver, pkg_type))

  【备选】若无 paths_urls：
    从 snapshots 表反查：
      SELECT DISTINCT source_url, version_branch, package_type
      FROM snapshots
      WHERE source_id=? AND source_url != ''
        AND status IN ('active','rollback','rollback_pending')

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 2: 遍历每个 URL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  total = len(urls)
  for (url, ver, pkg_type) in urls:
      checked += 1

      ┌─ 2a. 补全完整 URL ─────────────────────────────────────┐
      │  full_url = f'{BASE_URL}{url}' if url.startswith('/') │
      │           = "https://update.nsfocus.com" + url         │
      └─────────────────────────────────────────────────────────┘

      ┌─ 2b. 请求延迟 ─────────────────────────────────────────┐
      │  self._delay()  # 读取 MONITOR_DELAY 配置，sleep      │
      └─────────────────────────────────────────────────────────┘

      ┌─ 2c. HTTP GET 请求 ────────────────────────────────────┐
      │  resp = session.get(full_url, timeout=TIMEOUT,         │
      │                    stream=True)                       │
      │  检测重定向到登录页：                                  │
      │    if '/portal/' in resp.url                          │
      │       or '/login' in resp.url                         │
      │       or '登录' in resp.text[:200]:                  │
      │        → raise SessionExpiredError                    │
      │  读取 HTML（前 50KB）：html = resp.text[:50000]        │
      └─────────────────────────────────────────────────────────┘

      ┌─ 2d. 登录页 title 二次检测 ────────────────────────────┐
      │  if '<title>' in html:                                 │
      │      title = html[html.find('<title>')+7: ...]       │
      │      if '登录' in title:                              │
      │          raise SessionExpiredError                    │
      └─────────────────────────────────────────────────────────┘

      ┌─ 2e. 计算 page_hash ──────────────────────────────────┐
      │  page_hash = MD5(html[:50000].encode()).hexdigest()   │
      │  查询 DB：stored_hash, prev_page_hash                  │
      │  SQL: SELECT page_hash, prev_page_hash FROM snapshots │
      │       WHERE source_id=? AND source_url=? LIMIT 1      │
      └─────────────────────────────────────────────────────────┘

      ┌─ 2f. 记录 hash 对比日志 ──────────────────────────────┐
      │  stored_hash == None → "【产品】NEW  url  无 → hash"  │
      │  stored_hash == page_hash → "SAME  prev → hash"      │
      │  stored_hash != page_hash → "CHANGE prev → hash"     │
      └─────────────────────────────────────────────────────────┘

      ┌─ 2g. Hash 对比分支判断 ───────────────────────────────┐
      │                                                   │  │
      │   stored_hash == page_hash?                      │  │
      │   ├── YES → SAME path（页面无变化）                │  │
      │   └── NO  → CHANGE path（页面有变化）               │  │
      └───────────────────────────────────────────────────┘  │
      │                                                      │
      ▼                                                      ▼
┌─────────────────────┐                    ┌─────────────────────────────────────────┐
│ 【SAME PATH】        │                    │ 【CHANGE PATH】                         │
│ page_hash 未变       │                    │ page_hash 变了                         │
│                     │                    │                                         │
│ 1. 验证非登录页碰撞：  │                    │ 1. changed += 1                         │
│    查询 known_md5s：  │                    │ 2. 计算旧包 map（查 DB active 快照）      │
│      SELECT md5_hash  │                    │    old_map = {(file_name, pkg_type): s} │
│      FROM snapshots   │                    │ 3. 调用 _extract_table_items(html)      │
│      WHERE source_id   │                    │    → 返回 List[UnifiedContentItem]    │
│        AND source_url  │                    │ 4. 打印包级别 diff 日志                │
│        AND md5_hash != ''│                   │    ► NEW  fname vver (size bytes)     │
│                         │                    │      type=pkg_type  md5=md5...        │
│    如果 html 中找不到   │                    │    ► CHANGE fname vver (size bytes)    │
│    任何 known_md5：    │                    │      old_fname md5=old→new_md5         │
│    → 强制走 CHANGE     │                    │      type=pkg_type                    │
│    （登录页 hash 碰撞）│                    │    ◄ REMOVED fname (size bytes)         │
│                     │                    │      type=pkg_type  md5=md5...          │
│ 2. 重建 items：       │                    │ 5. items.extend(table_items)           │
│    查询 DB active 快照│                    │ 6. 批量更新 page_hash：                 │
│    构建 UnifiedContent│                    │    UPDATE snapshots SET               │
│    Item，加入 items   │                    │      prev_page_hash=page_hash,         │
│                     │                    │      page_hash=?                        │
│ 3. seen_ids 保护：    │                    │      WHERE source_id=? AND source_url=? │
│    seen_ids.add(id)   │                    │                                         │
│    （防 rollback 误标）│                    │                                         │
│                     │                    │                                         │
│ 4. 更新 prev_page_hash│                    │                                         │
│    UPDATE snapshots   │                    │                                         │
│    SET prev_page_hash=│                    │                                         │
│      page_hash        │                    │                                         │
│    WHERE source_id=?   │                    │                                         │
│      AND source_url=? │                    │                                         │
└─────────────────────┘                    └─────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 3: 汇总返回
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  logger.info(f'Quick {product_name}: {changed}/{total} pages changed, {len(items)} items')
  return items
```

---

## 4. run_detection 详细流程

```
输入：
  source_id      # 产品 ID
  items           # List[UnifiedContentItem]，采集到的包
  rollback_confirm # ROLLBACK_CONFIRM（默认2）
  check_rollback  # True
  seen_ids        # set[int]，所有已知快照 ID（来自 scheduler 传入的 before_snaps）

输出：DetectionResult(source_id, new_items, rollback_items, unchanged_items, unchanged_count, errors)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 1: seen_ids 初始化
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  seen_ids = set() if seen_ids is None else set(seen_ids)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 2: 提前返回判断
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  if not items and not seen_ids:
      # 既没有新采集到 items，DB 里也没有已知快照
      # → 可能是 session 失效，勿触发大量 rollback
      logger.warning(f'Source {source_id}: collected 0 items')
      return empty result

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 3: 遍历 items，调用 save_snapshot 写入 DB
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  for item in items:
      3a. snap_dict = item.to_snapshot_dict()
          # UnifiedContentItem → dict，包含所有字段
          # source_id, product_name, version_branch, package_type,
          # file_name, md5_hash, file_size, package_version, ...

      3b. sid = save_snapshot(snap_dict)
          # 匹配键：(source_id + product_name + version_branch + package_type + md5_hash)
          # → 已有相同 md5 → UPDATE last_seen_at = now, status='active'
          # → 无相同 md5   → INSERT，返回新 id

      3c. seen_ids.add(sid)

      3d. 判断是否新包：
          snap = get_snapshot(sid)
          if snap['first_seen_at'] == snap['last_seen_at']:
              result.new_items.append((sid, snap))
          else:
              result.unchanged_count += 1
              result.unchanged_items.append((sid, snap))

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 4: mark_rollback_pending（防误标保护）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  if check_rollback:
      snap_db.mark_rollback_pending(seen_ids, source_id)

  作用：active 快照不在 seen_ids → rollback_pending
        （seen_ids = 采集到的 + before_snaps 预填充）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 5: _confirm_rollbacks（确认回滚）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  if check_rollback:
      confirmed = _confirm_rollbacks(source_id, rollback_confirm)
      result.rollback_items = confirmed

  条件：rollback_cycles >= ROLLBACK_CONFIRM（默认2个周期才确认）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 6: 返回结果
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  return result
    {
      new_items:      [(sid, snap), ...],  # 本次新发现的包
      rollback_items: [(sid, snap), ...],  # 确认回滚的包
      unchanged_items: [(sid, snap), ...],  # 无变化的包
      unchanged_count: N,
      errors: []
    }
```

---

## 5. mark_rollback_pending 详细流程

```
输入：seen_ids: set[int], source_id: int

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
分支一：active 快照不在 seen_ids 中
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  查询：SELECT id FROM snapshots WHERE source_id=? AND status='active'
  for row in active:
      if row['id'] not in seen_ids:
          UPDATE snapshots
          SET status='rollback_pending', rollback_cycles=1
          WHERE id=?
          → 首次出现：rollback_cycles=1

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
分支二：已在 rollback_pending 的快照仍不在 seen_ids
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  查询：SELECT id FROM snapshots WHERE source_id=? AND status='rollback_pending'
  for row in pending:
      if row['id'] not in seen_ids:
          UPDATE snapshots
          SET rollback_cycles = rollback_cycles + 1
          WHERE id=?
          → 每多缺席一个周期，cycles += 1

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
关键：seen_ids 的构成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  seen_ids 由以下三部分构成：
    ① Scheduler 传入的 before_snaps（所有 active/rollback/rollback_pending ID）
       SQL: SELECT id FROM snapshots
            WHERE source_id=? AND status IN ('active','rollback','rollback_pending')
    ② _collect_quick SAME path 中，反查 DB 重建的 active 快照 ID
       seen_ids.add(s['id'])  ← 在 SAME path 内
    ③ _collect_quick CHANGE path 中，_extract_table_items 返回的 item 们
       → 最终由 save_snapshot 写入后 add 到 seen_ids

  结果：正常采集的快照，三重保护下绝不会进入 rollback_pending
```

---

## 6. save_snapshot 匹配逻辑

```
匹配键（5个字段联合唯一）：
  source_id    # 内容源 ID
  product_name # 产品名（如 "WEB应用防护系统(WAF)"）
  version_branch # 版本分支（如 "6.0.7"）
  package_type # 包类型（如 "WAF V6.0系统升级包"）
  md5_hash     # 文件 MD5（精确匹配）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
场景 A：md5_hash 已存在（幂等更新）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SELECT id FROM snapshots
  WHERE source_id=? AND product_name=? AND version_branch=?
    AND package_type=? AND md5_hash=?

  → 命中 → UPDATE（更新时间、status 恢复为 active）
  → UPDATE 字段：
      file_name, package_version, file_size,
      description_raw, description_parsed,
      min_sys_version, restart_required, urgency,
      download_id, published_at,
      last_seen_at = now(),
      status = 'active',
      rollback_cycles = 0,
      page_hash, source_url

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
场景 B：md5_hash 是新值（新增快照）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  → INSERT INTO snapshots（全部字段）
  → first_seen_at = last_seen_at = now()（由 DB 默认值）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
关键结论
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  包是否"新增"由 md5_hash 决定，而非 source_url 决定
  同一个 URL、同一 package_type，MD5 变了 → 视为新包
  同一个 URL、同一 package_type，MD5 没变 → 幂等更新，跳过
```

---

## 7. 包级别 diff 日志生成逻辑

```
时机：_collect_quick 的 CHANGE path
位置：nsfocus.py 第 609~647 行

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 1: 查询该 URL 在 DB 中的旧快照
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  old_snaps = snap_query("""
      SELECT file_name, md5_hash, package_version, package_type, file_size
      FROM snapshots
      WHERE source_id=? AND source_url=? AND status='active'""",
      (source_id, full_url))
  old_map = {(s['file_name'], s['package_type']): s for s in old_snaps}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 2: 遍历本次采集的 table_items，逐个对比
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  for ti in table_items:
      key = (ti.file_name, ti.package_type)
      old_s = old_map.get(key)

      if old_s is None:
          # 旧包里没有 → NEW
          logger.info(f'  ► NEW  {ti.file_name} v{ti.package_version} ({ti.file_size} bytes)')
          logger.info(f'    {ti.package_type}  md5={ti.md5_hash[:12]}...')
      else:
          # 旧包里有，但 md5 可能变了 → CHANGE
          old_md5 = old_s['md5_hash'] or ''
          new_md5 = ti.md5_hash or ''
          logger.info(f'  ► CHANGE {ti.file_name} v{ti.package_version} ({ti.file_size} bytes)')
          logger.info(f'    {old_s["file_name"]} md5={old_md5[:12]}... → md5={new_md5[:12]}...')
          logger.info(f'    {ti.package_type}')

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 3: 检测被移除的包（旧包有，新包里没有）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  new_keys = {(ti.file_name, ti.package_type) for ti in table_items}
  for (fname, ptype), old_s in old_map.items():
      if (fname, ptype) not in new_keys:
          old_md5 = old_s['md5_hash'] or ''
          logger.info(f'  ◄ REMOVED {fname} ({old_s["file_size"] or 0} bytes)')
          logger.info(f'    type={ptype}  md5={old_md5[:12]}...')

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
日志输出示例
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【WEB应用防护系统(WAF)】CHANGE listWaf/v/6.0.7  abc123 → def456
  ► CHANGE waf-sys-V6.0R03F03SP07.dat v6.0.7 (52428800 bytes)
    waf-sys-V6.0R03F03SP06.dat md5=abc123... → md5=def456...
    WAF V6.0系统升级包
  ► NEW waf-rule-V6.0.8.dat v6.0.8 (12345678 bytes)
    WAF V6.0规则升级包  md5=789abc...
  ◄ REMOVED waf-old-special.dat (987654 bytes)
    type=WAF V6.0特殊升级包  md5=321bca...
```

---

## 8. Session 轮换与失效处理

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Session 存储结构
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  user_sessions 表：
    id, user_id, cookie_value（AES-256-GCM 加密）,
    purpose ('collect' | 'discover'),
    heartbeat_count, last_heartbeat_at, status

  获取有效 session：
    get_active_sessions() → status='active' 的记录解密后返回

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 1: 预检（verify_session）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  目标 URL：HEALTH_URL = /update/listBvsV6/v/bvssys

  GET full_url（带 PHPSESSID cookie）
  检测：
    if '/portal/index' in resp.url → 登录页，session 失效
    if '/update/upLic' in resp.url → 许可证页面，session 上下文切换

  失败处理：
    update_status(session[0]['id'], 'expired')
    如果有 session[1] → 切换用它重试验证
    全部失败 → 采集中止

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 2: _collect_quick 中的 SessionExpiredError 处理
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  在 URL 请求阶段检测到登录页：
    → raise SessionExpiredError(f'Session invalid for {product_name}')

  nsfocus.py 捕获后：
    → 捕获 SessionExpiredError，打印警告
    → 该 URL 不加入 items（视为采集失败）
    → 继续处理下一个 URL（不中止整个产品采集）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Step 3: 请求延迟
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  每个 URL 请求前：_delay()
  读取 MONITOR_DELAY 配置（默认 0.5s）
  两层随机抖动：0.5 + random()*0.5 = 0.5~1.0s
  目的：避免高频请求被服务端封锁
```

---

## 9. ROLLBACK_CONFIRM 防抖动机制

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
快照状态机
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    ┌──────────────────────────────────────────────────────────────┐
    │                                                              │
    │  ┌─────────┐   hash变了+     ┌──────────────────┐            │
    │  │ active  │   items里没它   │ rollback_pending │            │
    │  │         │ ─────────────▶ │ cycles=1          │            │
    │  │         │   (首次缺席)    └────────┬─────────┘            │
    │  │         │                           │ cycles++            │
    │  │         │                           │ (连续缺席)           │
    │  │         │                           ▼                     │
    │  │         │   ┌──────────────────────────────┐             │
    │  └─────────┘   │ cycles >= ROLLBACK_CONFIRM   │             │
    │     ▲          │     (默认2个周期)              │             │
    │     │          └──────────────┬───────────────┘             │
    │     │                         │ 确认回滚                     │
    │     │                         ▼                             │
    │     │               ┌──────────────────┐                    │
    │     │               │     rollback     │                    │
    │     │               │ (标记为消失)      │                    │
    │     │               └──────────────────┘                    │
    │     │                                                        │
    │     │  (hash 恢复/快照重新出现)                               │
    │     │  save_snapshot 匹配到相同的 md5_hash                    │
    │     │  → UPDATE status='active', rollback_cycles=0          │
    └─────┴────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ROLLBACK_CONFIRM = 2（默认值，可配置）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  周期 1：快照消失 → rollback_pending, cycles=1
  周期 2：快照仍消失 → cycles=2，cycles >= 2 → confirm → rollback
  周期 3+：快照仍消失 → 保持 rollback，不再重复 confirm

  优点：防止网络抖动、单次页面临时无数据导致的误判
  配置项：MONITOR_ROLLBACK_CONFIRM（DB system_settings 表）
```

---

## 10. 完整数据流时序图

```
产品管理                    Scheduler                   NsfocusCollector              DB(snapshots)              ChangeDetector              Notifiers
   │                           │                              │                            │                          │                           │
   │ 配置 paths                 │                              │                            │                          │                           │
   │ ─────────────────────────▶│                              │                            │                          │                           │
   │                           │                              │                            │                          │                           │
   │                    ┌──────┴───────┐                      │                            │                          │                           │
   │                    │ run_now()    │                      │                            │                          │                           │
   │                    │ mode=quick   │                      │                            │                          │                           │
   │                    └──────┬───────┘                      │                            │                          │                           │
   │                           │                              │                            │                          │                           │
   │                    ┌──────▼───────┐                      │                            │                          │                           │
   │                    │ ①session    │                      │                            │                          │                           │
   │                    │ get_active_ │                      │                            │                          │                           │
   │                    │ sessions()  │                      │                            │                          │                           │
   │                    └──────┬───────┘                      │                            │                          │                           │
   │                           │                              │                            │                          │                           │
   │                           │ set_cookie(cookie)           │                            │                          │                           │
   │                           │─────────────────────────────▶│                            │                          │                           │
   │                           │                              │                            │                          │                           │
   │                           │                    ┌─────────┴──────────┐                  │                          │                           │
   │                           │                    │ _collect_quick()  │                  │                          │                           │
   │                           │                    └─────────┬──────────┘                  │                          │                           │
   │                           │                              │                            │                          │                           │
   │                           │                    ┌─────────▼──────────┐                  │                          │                           │
   │                           │                    │ 读 package_type_   │                  │                          │                           │
   │                           │                    │ discovered (paths) │                  │                          │                           │
   │                           │                    └─────────┬──────────┘                  │                          │                           │
   │                           │                              │                            │                          │                           │
   │                           │                    ┌─────────▼──────────┐                  │                          │                           │
   │                           │                    │ for each url:     │                  │                          │                           │
   │                           │                    │   HTTP GET        │                  │                          │                           │
   │                           │                    │   page_hash 计算   │                  │                          │                           │
   │                           │                    └─────────┬──────────┘                  │                          │                           │
   │                           │                              │                            │                          │                           │
   │                           │                    ┌─────────▼──────────┐                  │                          │                           │
   │                           │                    │ stored == current? │                  │                          │                           │
   │                           │                    └──┬────────┬───────┘                  │                          │                           │
   │                           │                       │YES     │NO                       │                          │                           │
   │                           │                       ▼        ▼                           │                          │                           │
   │                           │                  SAME path  CHANGE path                  │                          │                           │
   │                           │                       │        │                           │                          │                           │
   │                           │                       │   ┌────▼──────────────┐           │                          │                           │
   │                           │                       │   │ _extract_table_  │           │                          │                           │
   │                           │                       │   │ items(html)      │           │                          │                           │
   │                           │                       │   └────┬──────────────┘           │                          │                           │
   │                           │                       │        │                           │                          │                           │
   │                           │                       │   ┌────▼──────────────┐           │                          │                           │
   │                           │                       │   │ 打印 ► CHANGE    │           │                          │                           │
   │                           │                       │   │ ► NEW / ◄ REMOVED│           │                          │                           │
   │                           │                       │   └────┬──────────────┘           │                          │                           │
   │                           │                       │        │                           │                          │                           │
   │                           │                       │◀───────┴────────────────────────▶│                          │                           │
   │                           │                              │                            │                          │                           │
   │                           │◀──────────────────────────────┘                            │                          │                           │
   │                           │   items[]（UnifiedContentItem[]）                         │                          │                           │
   │                           │                              │                            │                          │                           │
   │                    ┌──────▼───────┐                      │                            │                          │                           │
   │                    │ 遍历每个 src │                      │                            │                          │                           │
   │                    │ 读 before_  │                      │                            │                          │                           │
   │                    │ snaps       │                      │                            │                          │                           │
   │                    └──────┬───────┘                      │                            │                          │                           │
   │                           │                              │   SELECT id, file_name,    │                          │                           │
   │                           │──────────────────────────────────────────────▶│ md5_hash                  │                          │
   │                           │                              │   WHERE status IN         │                          │
   │                           │                              │   ('active','rollback',    │                          │
   │                           │                              │    'rollback_pending')     │                          │
   │                           │                              │◀─────────────────────────────                        │                           │
   │                           │   seen_ids (before)          │                            │                          │                           │
   │                           │─────────────────────────────▶│                            │                          │                           │
   │                           │                              │                            │                          │                           │
   │                           │                    ┌─────────▼──────────┐                  │                          │                           │
   │                           │                    │ run_detection()   │                  │                          │                           │
   │                           │                    │  for item in items│                  │                          │                           │
   │                           │                    └─────────┬──────────┘                  │                          │                           │
   │                           │                              │                            │                          │                           │
   │                           │                              │   ┌────────────────────────▼───────────────┐   │
   │                           │                              │   │ save_snapshot(snap_dict)             │   │
   │                           │                              │   │   匹配键: src_id+name+ver+type+md5   │   │
   │                           │                              │   │   已有 → UPDATE last_seen_at=now     │   │
   │                           │                              │   │   新增 → INSERT (first=last=now)     │   │
   │                           │                              │   └───────────────────────────────────────┘   │
   │                           │                              │                            │                          │                           │
   │                           │                              │                            │   ┌────────────────────▼──────────┐
   │                           │                              │                            │   │ mark_rollback_pending(seen_ids) │
   │                           │                              │                            │   │   active 不在 seen_ids →        │
   │                           │                              │                            │   │   rollback_pending (cycles=1)   │
   │                           │                              │                            │   │   rollback_pending 仍缺席 →   │
   │                           │                              │                            │   │   cycles++                     │
   │                           │                              │                            │   └────────────────────▲──────────┘
   │                           │                              │                            │                          │
   │                           │                              │                            │   ┌────────────────────▼──────────┐
   │                           │                              │                            │   │ _confirm_rollbacks            │
   │                           │                              │                            │   │   cycles>=2 → rollback        │
   │                           │                              │                            │   └────────────────────▲──────────┘
   │                           │◀──────────────────────────────│◀───────────────────────────┼──────────────────────────┘
   │                           │   DetectionResult             │                            │
   │                           │                              │                            │                          │                           │
   │                           │                    ┌─────────▼──────────┐                  │                          │                           │
   │                           │                    │ 推送通知            │                  │                          │                           │
   │                           │                    │ route_notifications│◀─────────────────│──────────────────────────┘
   │                           │                    └────────────────────┘                  │
   │                           │                              │                            │
   │                    ┌──────▼───────┐                      │                            │
   │                    │ WAL checkpoint│                      │                            │
   │                    └──────────────┘                      │                            │
   │                           │                              │                            │
```

---

## 附：关键配置项

| 配置项 | 来源 | 默认值 | 说明 |
|--------|------|--------|------|
| MONITOR_COLLECT_INTERVAL | DB / env | 4（分钟） | Quick 采集间隔 |
| MONITOR_FULL_SCAN_INTERVAL | DB / env | 24（小时） | Full Scan 间隔 |
| MONITOR_ROLLBACK_CONFIRM | DB / env | 2（周期） | 回滚确认阈值 |
| MONITOR_DELAY | DB / env | 0.5（秒） | 请求间隔基础值 |
| MONITOR_HEARTBEAT_URL | DB / env | /update/listBvsV6/v/bvssys | Session 预检 URL |