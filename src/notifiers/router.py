"""Notification router — orchestrates sending notifications through matched channels."""

import os
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from src.core.logger import get_logger
from src.notifiers.base import NotificationMessage, DeliveryResult, TEMPLATE_NAMES, DEFAULT_TEMPLATE_BY_CHANNEL, resolve_chain_pkg as _resolve_pkg_from_chain, _split_with_marker, _sign_url
from src.notifiers.wecom import WecomNotifier
from src.notifiers.dingtalk import DingtalkNotifier
from src.notifiers.feishu import FeishuNotifier
from src.notifiers.email import EmailNotifier
from src.notifiers.apprise import AppriseNotifier
from src.models.channel import get_by_id, list_active
from src.models.subscription import (
    get_rule_channels, enqueue, get_due_items, mark_pushed, cancel_for_snapshot, get_rule,
    mark_delivery_retry, mark_delivery_terminal,
)
from src.models.snapshot import get_snapshot
from src.detector.change import compute_push_time, is_quiet_time, check_min_interval, is_window_time, compute_next_window_push_time

logger = get_logger('router')

# Push summary accumulator: key = cycle_marker, val = {total, success, failed, items: [], rule_info: {}, channel_info: {}}
_push_summary_accumulator: dict = {}

NOTIFIERS = {
    'wecom': WecomNotifier(),
    'dingtalk': DingtalkNotifier(),
    'feishu': FeishuNotifier(),
    'email': EmailNotifier(),
    'apprise': AppriseNotifier(),
}

# Rate-limit tracker: minimum seconds between sends to same channel type.
# 企微群机器人 webhook 的硬限是 20 条/分钟，3s 间隔正好踩在限额上：一轮集中
# 推 20+ 条时第 21 条起会拿到 45009 (api freq out of limit)。所以除最小间隔外
# 再加一个 60s 滑动窗口，默认 18 条/分钟，留 2 条余量。
_RATE_LIMIT_INTERVAL = float(os.getenv('MONITOR_RATE_LIMIT_SEC', '3'))
_RATE_LIMIT_PER_MINUTE = {
    'wecom': int(os.getenv('MONITOR_RATE_LIMIT_WECOM_PER_MIN', '18')),      # 硬限 20/min
    'dingtalk': int(os.getenv('MONITOR_RATE_LIMIT_DINGTALK_PER_MIN', '18')),  # 硬限 20/min
    'feishu': int(os.getenv('MONITOR_RATE_LIMIT_FEISHU_PER_MIN', '90')),    # 硬限 100/min
}
_last_send: dict[str, float] = {}
_recent_sends: dict[str, list] = {}

# ── Delivery retry policy ──────────────────────────────────────
# 一次发送失败后队列项不再直接标 pushed，而是按退避重试；达到上限后终止并
# 留下 last_error。delayed_queue 的时间戳是服务器本地时间，digest_queue 的是
# UTC（与 created_at 一致），由 utc 参数区分。
_DELIVERY_MAX_ATTEMPTS = max(1, int(os.getenv('MONITOR_DELIVERY_MAX_ATTEMPTS', '6')))
_DELIVERY_BACKOFF_SECONDS = (60, 300, 900, 3600, 3600)
_DIGEST_MAX_ATTEMPTS = max(1, int(os.getenv('MONITOR_DIGEST_MAX_ATTEMPTS', '6')))
_DIGEST_BACKOFF_SECONDS = (300, 900, 1800, 3600, 7200)
_QUEUE_PROCESS_LOCK = threading.Lock()


def _next_attempt_at(attempts: int, schedule: tuple, utc: bool = False) -> str:
    delay = schedule[min(max(attempts - 1, 0), len(schedule) - 1)]
    base = datetime.utcnow() if utc else datetime.now()
    return (base + timedelta(seconds=delay)).strftime('%Y-%m-%d %H:%M:%S')


def _now_str(utc: bool = False) -> str:
    return (datetime.utcnow() if utc else datetime.now()).strftime('%Y-%m-%d %H:%M:%S')


def _throttle_im(channel_type: str) -> None:
    """Enforce min gap + 60s sliding-window cap for IM webhook channels."""
    limit = _RATE_LIMIT_PER_MINUTE.get(channel_type, 18)
    now = time.time()
    last = _last_send.get(channel_type, 0)
    if now - last < _RATE_LIMIT_INTERVAL:
        time.sleep(_RATE_LIMIT_INTERVAL - (now - last))
        now = time.time()

    window = [t for t in _recent_sends.get(channel_type, []) if now - t < 60]
    if len(window) >= limit:
        time.sleep(60 - (now - window[0]) + 0.5)
        now = time.time()
        window = [t for t in window if now - t < 60]
    window.append(now)
    _recent_sends[channel_type] = window
    _last_send[channel_type] = now


def _emit_push_summary() -> list:
    """Emit a single push_summary system event for all accumulated push results.
    Called at the end of a collection cycle to report aggregated push outcomes.

    Returns the summaries list (for embedding into collection summary); emits
    the standalone push_summary notification here as before.

    2026-07-22: 0 推送也发"无推送"汇总通知(让用户知道路径在跑 + 配置生效)。
    累加器无条件清空:采集异常也必须清,否则旧条目会混进下一轮汇总。
    """
    from src.core.event_handler import emit_push_summary
    summaries = list(_push_summary_accumulator.values())
    _push_summary_accumulator.clear()
    try:
        emit_push_summary(summaries)
    except Exception as e:
        logger.error(f'Emit push summary failed: {e}')
    return summaries


def _is_maintenance_mode() -> bool:
    """Check if maintenance mode is enabled (suppress all notifications)."""
    try:
        from src.models.database import query
        rows = query("SELECT value FROM system_settings WHERE key = 'maintenance_mode'")
        return len(rows) > 0 and rows[0]['value'] == '1'
    except Exception:
        return False


def route_notifications(snapshot_id: int, rule_id: int, is_rollback: bool = False,
                          user_chain: list | None = None, accumulate: bool = True,
                          force_immediate: bool = False):
    """Route a snapshot through its matched channels.

    For delayed pushes, enqueue; for immediate (rollback or no delay), send now.

    user_chain: 订阅条件里匹配到的 chain(从 get_new_for_subscription 返回),
    推送消息内容用这个 chain 构造,不读 snap 行反查。
    chain_json (序列化后) 会被写入 delayed_queue / digest_queue /
    delivery_log,作为同 chain 维度的去重 key。

    accumulate: 是否计入本采集周期的推送汇总。网页「重新推送」传 False,
    避免手动补推混进下一轮采集汇总。

    force_immediate: 跳过延迟/时间窗/汇总队列,当场发送。手动重推用 ——
    汇总规则的「重推」如果只入队,本期已发过就永远发不出去。
    """
    if _is_maintenance_mode():
        logger.info(f'Maintenance mode: suppressed notification for snapshot {snapshot_id}')
        return

    snap = get_snapshot(snapshot_id)
    if not snap:
        logger.warning(f'Snapshot {snapshot_id} not found')
        return

    from src.models.subscription import get_rule
    rule = get_rule(rule_id)
    if not rule:
        logger.warning(f'Rule {rule_id} not found')
        return

    # 把 user_chain 序列化成 JSON,作为下游 enqueue / enqueue_digest /
    # log_delivery / dedup 的 chain 维度 key。空 chain 用 '[]'。
    chain_json = json.dumps(user_chain or [], ensure_ascii=False)

    # Rollback notifications are always immediate
    if is_rollback:
        _send_immediate(snap, rule, is_rollback=True, user_chain=user_chain,
                        accumulate=accumulate)
        return

    # 网页「重新推送」:不走延迟/窗口/汇总队列,当场发
    if force_immediate:
        _send_immediate(snap, rule, user_chain=user_chain,
                        accumulate=accumulate)
        return

    # Digest mode: enqueue to digest queue instead of sending
    digest_mode = rule.get('digest_mode', '')
    if digest_mode:
        from src.models.subscription import enqueue_digest, digest_period_key
        period_key = digest_period_key(digest_mode)
        enqueue_digest(rule_id, snapshot_id, period_key, chain_json=chain_json)
        logger.info(f'Rule {rule["name"]}: enqueued for {digest_mode} digest ({period_key})')
        return

    # Check quiet time
    if is_quiet_time(rule):
        logger.info(f'Rule {rule["name"]}: in quiet time, delaying')
        push_after = compute_push_time(1)  # At least 1 hour
        enqueue(snapshot_id, rule_id, push_after, chain_json=chain_json)
        return

    # Check min interval
    if not check_min_interval(rule, snap.get('product_name', '')):
        logger.info(f'Rule {rule["name"]}: min interval not met, delaying')
        push_after = compute_push_time(1)
        enqueue(snapshot_id, rule_id, push_after, chain_json=chain_json)
        return

    # Apply delay strategy
    delay_days = rule.get('delay_days', 0)
    # 汇总模式才使用窗口策略。即时模式无窗口策略。
    window_config = rule.get('window_config') or {}
    has_window = bool(window_config.get('start') and window_config.get('end'))  # 汇总模式窗口

    if has_window:
        # Window strategy: only send when inside time window
        if not is_window_time(rule):
            push_after = compute_next_window_push_time(rule)
            enqueue(snapshot_id, rule_id, push_after, chain_json=chain_json)
            logger.info(f'Rule {rule["name"]}: outside window, enqueued for {push_after}')
            return
        # Fall through to send immediately when in window

    if delay_days > 0:
        # Each package has its own independent delay timer — no reset.
        push_after = compute_push_time(delay_days)
        enqueue(snapshot_id, rule_id, push_after, chain_json=chain_json)
        logger.info(f'Rule {rule["name"]}: delayed {delay_days}d')
        return

    # No delay — send immediately
    _send_immediate(snap, rule, user_chain=user_chain, accumulate=accumulate)


def _get_email_rate_settings() -> dict:
    """Read email rate limit settings from system_settings (with defaults)."""
    from src.models.database import query
    rows = query("SELECT key, value FROM system_settings WHERE key LIKE 'email_%'")
    cfg = {r['key']: r['value'] for r in rows}
    return {
        'cust_hourly': int(cfg.get('email_customer_hourly_limit', 10)),
        'cust_daily': int(cfg.get('email_customer_daily_limit', 50)),
        'global_hourly': int(cfg.get('email_global_hourly_limit', 100)),
        'global_daily': int(cfg.get('email_global_daily_limit', 500)),
    }


def _resolve_email_recipients(binding: dict, rule: dict,
                              channel_config: dict) -> tuple[str, list[str]]:
    """解析 email 渠道的收件人，优先级与 email.send 一致。

    rule_channels.customer_emails → subscription_rules.customer_emails
    → customers.email，再与渠道 config.to_list（legacy）合并去重。
    返回 (rule_emails, 去重后的收件人列表)。
    """
    from src.models.database import query
    rc_emails = (binding.get('customer_emails') or '').strip()
    if not rc_emails and rule.get('customer_emails'):
        rc_emails = rule['customer_emails']
    if not rc_emails:
        cust_id = binding.get('customer_id', 0)
        if cust_id:
            rows = query("SELECT email FROM customers WHERE id=?", (cust_id,))
            if rows:
                rc_emails = (rows[0].get('email') or '').strip()
    to_list = list(channel_config.get('to_list') or [])
    extras = [e.strip() for e in rc_emails.split(',') if e.strip()]
    return rc_emails, list(dict.fromkeys(to_list + extras))


def _send_immediate(snap: dict, rule: dict, is_rollback: bool = False,
                    user_chain: list | None = None, accumulate: bool = True) -> dict:
    """Send notification immediately through all channels associated with the rule.

    Email 故障转移：同一订阅规则下多个 email 渠道解析出的收件人集合完全相同时
    （典型：主/备两个 SMTP 渠道发给同一个邮箱），按 rule_channels.id 顺序发送，
    前一个成功就不再发后面的；前一个失败才用下一个。不同收件人的渠道互不影响。

    accumulate=False 时不写推送汇总累加器（手动重推场景）。

    Returns a delivery summary used by the delayed queue to decide retry:
    sent / failed / skipped / unusable / no_channels。失败被备用邮箱补发的
    不计入 failed（收件人实际已收到）。
    """
    from src.models.database import query

    summary = {
        'attempted': 0, 'sent': 0, 'failed': 0, 'skipped': 0,
        'unusable': 0, 'fallback': 0, 'no_channels': False, 'errors': [],
    }

    # chain_json: dedup 维度 + log_delivery 持久化字段
    chain_json = json.dumps(user_chain or [], ensure_ascii=False)

    message = NotificationMessage.from_snapshot(snap, is_rollback=is_rollback,
                                                user_chain=user_chain)

    bindings = get_rule_channels(rule['id'])
    if not bindings:
        logger.warning(f'Rule {rule["name"]}: no channels bound')
        summary['no_channels'] = True
        return summary

    # 预解析每个 email 绑定的收件人集合：同集合的渠道互为备用，只有前一个失败
    # 才会继续下一个；不同收件人的 email 渠道各发各的。
    channel_cache: dict = {}
    email_plan: dict = {}        # binding idx -> {rule_emails, recipients, target}
    target_channels: dict = {}   # frozenset(收件人) -> {channel_id}
    for idx, binding in enumerate(bindings):
        channel_id = binding.get('channel_id')
        if not channel_id:
            continue
        if channel_id not in channel_cache:
            channel_cache[channel_id] = get_by_id(channel_id)
        channel = channel_cache[channel_id]
        if not channel or not channel.get('is_active') or channel.get('type') != 'email':
            continue
        rc_emails, recips = _resolve_email_recipients(
            binding, rule, channel.get('config') or {})
        if not recips:
            continue
        target = frozenset(e.lower() for e in recips)
        email_plan[idx] = {
            'rule_emails': rc_emails,
            'recipients': recips,
            'target': target,
        }
        target_channels.setdefault(target, set()).add(channel_id)

    succeeded_targets: set = set()
    # 同一收件人组里失败的尝试:等后续备用渠道成功时,回头标记为"已补发"
    attempts_by_target: dict = {}
    # 队列重试判定:只统计"最终仍未送达"的失败
    queue_failed_by_target: dict = {}
    unresolved_failed = 0

    for idx, binding in enumerate(bindings):
        channel_id = binding.get('channel_id')
        if not channel_id:
            summary['unusable'] += 1
            continue

        channel = channel_cache.get(channel_id)
        if not channel or not channel.get('is_active'):
            summary['unusable'] += 1
            continue

        plan = email_plan.get(idx)
        target = plan['target'] if plan else None

        # 同一邮箱的备用渠道：本组已有任一渠道发送成功 → 跳过后续渠道
        if target is not None:
            if target in succeeded_targets:
                logger.info(f'Skip fallback email channel {channel_id}: snapshot {snap["id"]} '
                            f'already delivered to {",".join(sorted(target))} by rule {rule["id"]}')
                summary['skipped'] += 1
                continue
            members = sorted(target_channels[target])
            delivered = query(
                """SELECT channel_id FROM delivery_log
                   WHERE snapshot_id = ? AND rule_id = ? AND chain_json = ?
                         AND delivery_status = 'sent'
                         AND channel_id IN ({})""".format(','.join('?' * len(members))),
                (snap['id'], rule['id'], chain_json, *members))
            if delivered:
                succeeded_targets.add(target)
                logger.info(f'Skip fallback email channel {channel_id}: snapshot {snap["id"]} '
                            f'already delivered to {",".join(sorted(target))} by rule {rule["id"]}')
                summary['skipped'] += 1
                continue

        # Deduplication: skip if THIS rule already pushed this snapshot through this
        # channel successfully for THIS chain. Multiple rules sharing the same channel
        # (e.g. ch_id=3 bound to both rule 14 "邮箱推送" and rule 25 "华兴银行") must each
        # push their own recipient list — to_list comes from rule.customer_emails, not
        # channel, so cross-rule dedup incorrectly silences other rules' recipients.
        # chain_json 维度:同一物理 snap + rule + channel,如果 chain-A 已经推送过,
        # chain-B 仍可独立推送。同一 chain 重复才算重复。
        # (failed records don't block — rate-limit failures are transient)
        # email 有 target 时上面已按整个收件人组查过，这里不再重复查。
        if target is None:
            existing = query(
                """SELECT id FROM delivery_log
                   WHERE snapshot_id = ? AND channel_id = ? AND rule_id = ?
                         AND chain_json = ?
                         AND delivery_status = 'sent'
                   LIMIT 1""",
                (snap['id'], channel_id, rule['id'], chain_json)
            )
            if existing:
                logger.info(f'Skip duplicate: snapshot {snap["id"]} already sent to '
                            f'channel {channel_id} by rule {rule["id"]} (chain={chain_json})')
                summary['skipped'] += 1
                continue

        notifier = NOTIFIERS.get(channel['type'])
        if not notifier:
            logger.warning(f'Unknown channel type: {channel["type"]}')
            summary['unusable'] += 1
            continue

        # Rate-limit IM channels to avoid API throttling
        if channel['type'] in ('wecom', 'dingtalk', 'feishu'):
            _throttle_im(channel['type'])

        # Build channel config with rule-level overrides
        ch_config = dict(channel['config'])
        # 2026-07-11 per-channel 字段 fallback 链 (rule_channels → subscription_rules → customers → DEFAULT)
        # binding = rule_channels 那行 (包含 per-channel 字段)
        # rule = subscription_rules 那行 (含全局 customer_emails / template 字典)
        # rule_template 是 _parse_rule 后可能为 dict 或 str
        # ── 模板选择 ──
        rc_template = (binding.get('template') or '').strip()
        rule_tpl_field = rule.get('template')
        if isinstance(rule_tpl_field, dict):
            rule_ch_tpl = rule_tpl_field.get(channel_id) or rule_tpl_field.get(str(channel_id)) or ''
        elif isinstance(rule_tpl_field, str) and rule_tpl_field:
            rule_ch_tpl = rule_tpl_field  # 老标量形态:所有渠道共用
        else:
            rule_ch_tpl = ''
        ch_type_default = DEFAULT_TEMPLATE_BY_CHANNEL.get(channel['type'], 'full')
        template = rc_template or rule_ch_tpl or ch_type_default
        if template not in TEMPLATE_NAMES:
            template = 'full'
        ch_config['_template'] = template

        if channel['type'] == 'email':
            # 收件人 fallback 链在预解析阶段已算好（与 email.send 同优先级），
            # 这里只把 rule_emails 注入 channel config。
            if plan and plan.get('rule_emails'):
                ch_config['rule_emails'] = plan['rule_emails']
            # 附件大小: per-channel (rule_channels.attachment_max_mb)
            #          → per-customer (customers.attachment_max_mb)
            #          → 不设 (用 email.py 默认 ATTACHMENT_MAX_SIZE)
            rc_attach = int(binding.get('attachment_max_mb') or 0)
            if rc_attach > 0:
                ch_config['attachment_max_mb'] = str(rc_attach)
            else:
                cust_id = binding.get('customer_id', 0)
                if cust_id:
                    from src.models.database import query as _q
                    _crow = _q("SELECT attachment_max_mb FROM customers WHERE id=?", (cust_id,))
                    if _crow:
                        customer_attach_mb = int(_crow[0].get('attachment_max_mb') or 0)
                        if customer_attach_mb > 0:
                            ch_config['attachment_max_mb'] = str(customer_attach_mb)
            # Inject rate-limit context
            ch_config['_channel_id'] = channel_id
            ch_config['_customer_id'] = binding.get('customer_id', 0)
            ch_config['_email_hourly_limit'] = channel.get('email_hourly_limit', 0)
            ch_config['_email_daily_limit'] = channel.get('email_daily_limit', 0)
            # Inject global/customer limits from system_settings
            limits = _get_email_rate_settings()
            ch_config['_cust_hourly_limit'] = limits['cust_hourly']
            ch_config['_cust_daily_limit'] = limits['cust_daily']
            ch_config['_global_hourly_limit'] = limits['global_hourly']
            ch_config['_global_daily_limit'] = limits['global_daily']

        # Send — 单渠道异常不能中断同规则的其他渠道
        try:
            result = notifier.send(message, ch_config)
        except Exception as e:
            result = DeliveryResult(False, channel['type'], channel.get('name', ''),
                                    f'{type(e).__name__}: {e}')
            logger.error(f'Channel {channel_id} send raised: {e}')

        summary['attempted'] += 1
        if result.success:
            summary['sent'] += 1
            if target is not None:
                # 备用渠道补发成功 → 先前的失败不再算"未送达"
                succeeded_targets.add(target)
                resolved = queue_failed_by_target.pop(target, 0)
                if resolved:
                    unresolved_failed -= resolved
                    summary['fallback'] = summary.get('fallback', 0) + resolved
        else:
            unresolved_failed += 1
            summary['errors'].append(
                f'{channel["type"]}({channel.get("name", "")}): {result.error_message}'[:300])
            if target is not None:
                queue_failed_by_target[target] = queue_failed_by_target.get(target, 0) + 1

        # Calculate recipient string for delivery_log (push history UI).
        # 机器人渠道 (wecom/dingtalk/feishu): 推送目标是 webhook,没有"收件人"概念,
        # 留空,前端回退到 channel_name 当"收件人"展示
        recipient_str = ''
        if channel['type'] == 'email':
            recipient_str = ','.join((plan or {}).get('recipients') or [])

        # Log delivery
        from src.models.subscription import log_delivery
        log_delivery(
            snapshot_id=snap['id'],
            rule_id=rule['id'],
            channel_id=channel_id,
            channel_type=channel['type'],
            channel_name=channel.get('name', ''),
            # Fix: subscription_rules.customer_id 是推送客户的唯一来源
            # (rule_channels.customer_id 在前端表单里永远不会被填,因为后端
            # 期望的字段名是 'customers' (复数 list),前端却传 'customer_id' (单值))。
            # 不再用 binding.get('customer_id') —— 改用 rule.get('customer_id'),
            # 兼容老 binding 行 (None) 不会写 NULL,旧历史也保持原样不动。
            customer_id=rule.get('customer_id') or binding.get('customer_id'),
            status='sent' if result.success else 'failed',
            error=result.error_message,
            recipient=recipient_str,
            sender=getattr(result, 'sender', '') or '',
            chain_json=chain_json,
        )

        # Accumulate for push summary (replaces per-delivery emit_push).
        # key 加 chain_json 维度:同一 rule + channel 下,chain-A 和 chain-B 的
        # 推送汇总分开计数,避免跨 chain 混淆。
        if accumulate:
            key = (rule['id'], channel_id, chain_json)
            if key not in _push_summary_accumulator:
                _push_summary_accumulator[key] = {
                    'total': 0, 'success': 0, 'failed': 0, 'fallback': 0,
                    'rule_name': rule.get('name', ''),
                    'customer_name': rule.get('customer_name', ''),
                    'channel_type': channel['type'],
                    'channel_name': channel.get('name', ''),
                    'items': [],   # list of {file_name, product_name, package_type, success, error, pushed_at}
                    'pushed_at': datetime.now(timezone.utc).isoformat(),
                }
            accum = _push_summary_accumulator[key]
            accum['total'] += 1
            if result.success:
                accum['success'] += 1
            else:
                accum['failed'] += 1
            item = {
                'file_name': snap.get('file_name', ''),
                'product_name': snap.get('product_name', ''),
                # 2026-08-08: 从 chain 派生 package_type,不读 snap.package_type
                # (URL 去重后 save_snapshot UPDATE 会把 snap.package_type
                # 互相覆盖,数据脏)。content_sources.package_type.paths[].chain
                # 才是真相源。
                'package_type': _resolve_pkg_from_chain(snap),
                'success': result.success,
                'error': result.error_message or '',
                'pushed_at': datetime.now(timezone.utc).isoformat(),
            }
            accum['items'].append(item)

            if target is not None:
                if result.success:
                    # 备用渠道补发成功:同组的失败尝试不再算作"没送到"
                    for prev_accum, prev_item in attempts_by_target.get(target, []):
                        if not prev_item.get('failover'):
                            prev_item['failover'] = True
                            prev_accum['failed'] -= 1
                            prev_accum['fallback'] += 1
                else:
                    attempts_by_target.setdefault(target, []).append((accum, item))

    summary['failed'] = unresolved_failed
    logger.info(f'Rule {rule["name"]}: {summary["attempted"]} attempts, '
                f'sent={summary["sent"]}, failed={summary["failed"]}, '
                f'skipped={summary["skipped"]}, unusable={summary["unusable"]}')
    return summary


def process_delayed_queue():
    """Deliver due delayed_queue items with bounded retry and backoff.

    Previously every due row was marked pushed even when all channels failed,
    which silently dropped notifications. Now a failed delivery keeps the row
    pending, records attempts/last_error, and retries with backoff until it
    either succeeds or hits MONITOR_DELIVERY_MAX_ATTEMPTS.

    Runs from the collection cycle and from a dedicated 5-minute scheduler job;
    the process-wide lock prevents the two from double-sending the same row.
    """
    if _is_maintenance_mode():
        return
    if not _QUEUE_PROCESS_LOCK.acquire(blocking=False):
        logger.info('Delayed queue is already being processed; skip this run')
        return
    terminals = []
    try:
        items = get_due_items()
        for item in items:
            try:
                terminal = _process_delayed_item(item)
                if terminal:
                    terminals.append(terminal)
            except Exception as e:
                # 单条坏数据不能中断整条队列；尽量落下重试状态
                logger.error(f'Delayed queue item {item.get("id")} failed unexpectedly: {e}')
                try:
                    attempts = int(item.get('attempts') or 0) + 1
                    error = f'{type(e).__name__}: {e}'
                    if attempts >= _DELIVERY_MAX_ATTEMPTS:
                        mark_delivery_terminal(item['id'], 'retry_exhausted', error)
                        terminals.append({
                            'queue_id': item.get('id'),
                            'snapshot_id': item.get('snapshot_id'),
                            'rule_id': item.get('rule_id'),
                            'reason': 'retry_exhausted',
                            'attempts': attempts,
                            'error': error[:300],
                        })
                    else:
                        mark_delivery_retry(
                            item['id'], attempts,
                            _next_attempt_at(attempts, _DELIVERY_BACKOFF_SECONDS),
                            error)
                except Exception as inner:
                    logger.error(f'Failed to persist retry state for item '
                                 f'{item.get("id")}: {inner}')
    finally:
        _QUEUE_PROCESS_LOCK.release()
    # 通知可能被 IM 节流阻塞，放到锁外发送，避免拖住下一轮队列处理
    if terminals:
        try:
            from src.core.event_handler import emit_delivery_error
            emit_delivery_error(terminals)
        except Exception as e:
            logger.error(f'Emit delivery_error event failed: {e}')


def _process_delayed_item(item: dict):
    snap = get_snapshot(item['snapshot_id'])
    if not snap:
        mark_pushed(item['id'])
        return
    # P1-2 fix: skip if snapshot is no longer active (包在延迟窗口内被撤回,
    # 由 collector 当场把 status 标成 withdrawn/superseded)
    if snap.get('status') != 'active':
        logger.info(f'Skip delayed push {item["id"]}: snapshot status={snap.get("status")}')
        mark_pushed(item['id'])
        return
    rule = get_rule(item['rule_id'])
    if not rule:
        mark_pushed(item['id'])
        return
    # C1-2: 重放时也要检查静默期和窗口时间，不在窗口内则跳过
    if is_quiet_time(rule):
        logger.debug(f'Skip delayed push {item["id"]}: quiet time')
        return
    if is_window_time(rule) is False:
        logger.debug(f'Skip delayed push {item["id"]}: outside window time')
        return
    # 2026-08-21: 从 delayed_queue 行恢复 user_chain,确保推送消息内容
    # 用原始匹配 chain 派生(package_type 等),不重新查表。
    # 空 / 异常 chain_json 回退到 None(legacy 兼容)。
    try:
        chain_json_raw = item.get('chain_json') or '[]'
        user_chain = json.loads(chain_json_raw) if chain_json_raw else None
        if not isinstance(user_chain, list):
            user_chain = None
    except (json.JSONDecodeError, TypeError):
        user_chain = None

    try:
        result = _send_immediate(snap, rule, is_rollback=False, user_chain=user_chain)
    except Exception as e:
        result = {
            'attempted': 0, 'sent': 0, 'failed': 1, 'skipped': 0,
            'unusable': 0, 'fallback': 0, 'no_channels': False,
            'errors': [f'{type(e).__name__}: {e}'],
        }

    # 兼容测试 / 旧调用方：无返回值视为已处理
    if result is None:
        mark_pushed(item['id'])
        return

    failed = int(result.get('failed') or 0)
    sent = int(result.get('sent') or 0)
    skipped = int(result.get('skipped') or 0)
    unusable = int(result.get('unusable') or 0)

    if failed == 0 and (sent > 0 or skipped > 0):
        mark_pushed(item['id'])
        return
    if failed == 0:
        # 没有任何可用渠道（未绑定/停用/类型不支持）：重试不会自愈
        error = f'no deliverable channel (unusable={unusable})'
        mark_delivery_terminal(item['id'], 'no_active_channel', error)
        logger.error(f'Delayed push {item["id"]} terminated: {error}')
        return {
            'queue_id': item['id'],
            'snapshot_id': item['snapshot_id'],
            'rule_id': item['rule_id'],
            'rule_name': rule.get('name') or '',
            'customer_name': rule.get('customer_name') or '',
            'product_name': snap.get('product_name') or '',
            'version': snap.get('version_branch') or '',
            'reason': 'no_active_channel',
            'attempts': int(item.get('attempts') or 0),
            'error': error[:300],
        }

    attempts = int(item.get('attempts') or 0) + 1
    error = '; '.join(result.get('errors') or [])[:1000] or 'unknown delivery error'
    if attempts >= _DELIVERY_MAX_ATTEMPTS:
        mark_delivery_terminal(item['id'], f'retry_exhausted({attempts})', error)
        logger.error(f'Delayed push {item["id"]} exhausted after {attempts} attempts: {error}')
        return {
            'queue_id': item['id'],
            'snapshot_id': item['snapshot_id'],
            'rule_id': item['rule_id'],
            'rule_name': rule.get('name') or '',
            'customer_name': rule.get('customer_name') or '',
            'product_name': snap.get('product_name') or '',
            'version': snap.get('version_branch') or '',
            'reason': f'retry_exhausted({attempts})',
            'attempts': attempts,
            'error': error[:300],
        }
    else:
        next_at = _next_attempt_at(attempts, _DELIVERY_BACKOFF_SECONDS)
        mark_delivery_retry(item['id'], attempts, next_at, error)
        logger.warning(f'Delayed push {item["id"]} failed '
                       f'(attempt {attempts}/{_DELIVERY_MAX_ATTEMPTS}), '
                       f'retry at {next_at}: {error}')


# 汇总发送失败/无可用渠道时,每个规则每小时只记一次日志和系统事件,
# 否则 15 分钟一轮会刷 96 条。
_DIGEST_FAIL_LOG: dict = {}

_DIGEST_MODE_LABEL = {'weekly': '每周', 'monthly': '每月', 'quarterly': '每季度'}


def _dedupe_digest_snaps(snaps: list) -> list:
    """同一包匹配多条 chain 时队列里有多行,汇总按 snapshot 去重。"""
    seen = set()
    result = []
    for s in snaps:
        sid = s.get('snapshot_id')
        if sid in seen:
            continue
        seen.add(sid)
        result.append(s)
    return result


def _build_digest_text(rule: dict, period_key: str, snaps: list) -> tuple[str, list]:
    mode_label = _DIGEST_MODE_LABEL.get(rule.get('digest_mode', ''), '')
    title = f'📊 {rule["name"]} — {mode_label}升级汇总'
    product_names = sorted({s.get('product_name', '') for s in snaps if s.get('product_name')})

    lines = [
        f'## {title}',
        '',
        f'**周期**: {period_key}',
        f'''**产品**: {', '.join(product_names)}''',
        f'**本期新增**: {len(snaps)} 个升级包',
        '🔺 <u>请查看每个升级包的详情/升级描述，了解具体变更内容</u>',
        '',
    ]

    from collections import defaultdict
    grouped = defaultdict(list)
    for s in snaps:
        grouped[s.get('source_url') or '其他'].append(s)

    idx = 0
    for source_key, items in grouped.items():
        source_label = source_key.split('/')[-1] if source_key.startswith('http') else source_key
        lines.append(f'### {source_label}')
        for s in items:
            idx += 1
            urgency_icon = {'critical': '🔴', 'high': '⚠️', 'normal': 'ℹ️'}.get(s.get('urgency', ''), '')
            type_label = s.get('package_type', '')
            full_tag = ' [全量]' if _is_full_package(s) else ''
            lines.append(f'{idx}. {urgency_icon} **{type_label}**{full_tag}')
            version = ' '.join(x for x in (s.get('version_branch'), s.get('package_version')) if x)
            if version:
                lines.append(f'   版本: {version}')
            fname = s.get('file_name') or '（无文件名）'
            lines.append(f'   文件名: {fname}')
            dl_id = s.get('download_id')
            if dl_id:
                dl_url = f'https://update.nsfocus.com/update/downloads/id/{dl_id}'
                lines.append(f'   下载: [{fname}]({dl_url})')
            md5 = s.get('md5_hash')
            if md5:
                lines.append(f'   MD5: `{md5}`')
            src_url = s.get('source_url', '')
            if src_url:
                lines.append(f'   详情: [{src_url}]({src_url})')
            lines.append('')

    return title, lines


def _log_digest_failure(rule: dict, period_key: str, detail: str):
    now = time.monotonic()
    # monotonic 的起点是开机时间；默认值取 0 会让「启动不足 1 小时」的机器
    # 把首次失败误判成刚发过，事件直接静默丢掉。用 None 表示从未记录。
    last = _DIGEST_FAIL_LOG.get(rule['id'])
    if last is not None and now - last < 3600:
        return
    _DIGEST_FAIL_LOG[rule['id']] = now
    logger.error(f'Digest NOT sent: rule={rule.get("name")}, period={period_key}, {detail}')
    try:
        from src.models.event_log import log_event
        log_event(event_type='digest_summary', severity='ERROR',
                  rule_id=rule['id'],
                  message={'period': period_key, 'error': detail})
    except Exception as e:
        logger.warning(f'Digest failure event log failed: {e}')


def process_digests():
    """Check and send due digest summaries."""
    if _is_maintenance_mode():
        return
    from src.models.subscription import (
        get_rules_due_for_digest, get_digest_snapshots,
        mark_digest_sent, mark_digest_rule_sent, cancel_digest_item,
        defer_digest_items,
    )

    due_rules = get_rules_due_for_digest()
    if not due_rules:
        return

    for rule in due_rules:
        period_key = rule['_period_key']
        # 所有 pending 行都要投递:过期周期漏发的、上期汇总后才到货的
        snaps = get_digest_snapshots(rule_id=rule['id'])
        if not snaps:
            mark_digest_rule_sent(rule['id'], period_key)
            continue

        still_active = []
        for s in snaps:
            if s.get('snapshot_status') != 'active':
                logger.info(f'Removing withdrawn snapshot {s["snapshot_id"]} from digest '
                            f'queue item {s["id"]} for rule {rule["name"]}')
                cancel_digest_item(s['id'])
            else:
                still_active.append(s)
        if not still_active:
            mark_digest_rule_sent(rule['id'], period_key)
            continue

        # 部分失败重试时锁定上一轮批次：发送期间新入队的行 attempts=0，
        # 不能混进本轮，否则已成功渠道会因快照集合变化而重复收到整期汇总。
        retry_max = max((s['id'] for s in still_active
                         if int(s.get('attempts') or 0) > 0), default=None)
        if retry_max is not None:
            still_active = [s for s in still_active if s['id'] <= retry_max]
        if not still_active:
            continue

        # 去重/全量过滤只影响展示,标记上限必须覆盖本次取到的全部队列行,
        # 否则被去重掉的多 chain 行会永远 pending,下一轮重复投递
        max_dq_id = max(s['id'] for s in still_active)

        # 上一轮部分失败后的退避未到：本 tick 不重发
        retry_at = max((s.get('next_attempt_at') or '' for s in still_active), default='')
        if retry_at and retry_at > _now_str(utc=True):
            logger.debug(f'Skip digest for rule {rule["name"]}: retry backoff until {retry_at}')
            continue

        snaps = _dedupe_digest_snaps(still_active)

        # 不在窗口内就跳过,不标记已发,下个 tick(15 分钟)继续检查
        if is_window_time(rule) is False:
            logger.info(f'Skip digest for rule {rule["name"]}: outside window time')
            continue

        snaps = _filter_full_packages(snaps)

        title, lines = _build_digest_text(rule, period_key, snaps)
        result = _send_digest_split(rule, '\n'.join(lines), snaps)

        sent_channels = result.get('sent') or []
        failed_channels = result.get('failed') or []
        skipped_channels = result.get('skipped') or []
        already_channels = result.get('already') or []

        exhausted = False
        attempts = 0
        detail = ''
        if failed_channels or (not sent_channels and not already_channels):
            # 部分/全部渠道失败，或没有任何渠道可用：不消费队列，按退避重试。
            # 已成功的渠道由 _send_digest_split 在下一轮自动跳过。
            detail = '; '.join(failed_channels + skipped_channels) or '无可用渠道'
            prev_attempts = max((int(s.get('attempts') or 0) for s in still_active), default=0)
            attempts = prev_attempts + 1
            if attempts < _DIGEST_MAX_ATTEMPTS:
                next_at = _next_attempt_at(attempts, _DIGEST_BACKOFF_SECONDS, utc=True)
                defer_digest_items(rule['id'], max_dq_id, attempts, next_at, detail)
                _log_digest_failure(rule, period_key, detail)
                logger.warning(f'Digest retry scheduled: rule={rule["name"]}, '
                               f'attempt {attempts}/{_DIGEST_MAX_ATTEMPTS}, '
                               f'next={next_at}, detail={detail}')
                continue
            # 达到上限：终止重试并消费队列，避免无限 pending
            exhausted = True
            logger.error(f'Digest retry exhausted: rule={rule["name"]}, '
                         f'attempts={attempts}, detail={detail}')

        # 只标记本次真正打包进汇总的行(id <= max),避免把发送期间新入队的包吞掉。
        # last_sent 记最早一行的周期:补发往期内容时不占用本周期的发送名额,
        # 否则"周日漏发、周一补发"会把此后每周的发送日永久挪到周一。
        sent_period = min(s['period_key'] for s in still_active)
        mark_digest_sent(rule['id'], max_id=max_dq_id)
        mark_digest_rule_sent(rule['id'], sent_period)
        _DIGEST_FAIL_LOG.pop(rule['id'], None)

        try:
            from src.models.event_log import log_event
            message = {'title': title, 'period': period_key,
                       'packages': len(snaps),
                       'sent_channels': sent_channels,
                       'failed_channels': failed_channels,
                       'skipped_channels': skipped_channels,
                       'already_channels': already_channels}
            if exhausted:
                message['error'] = detail
                message['retry_exhausted'] = True
            log_event(event_type='digest_summary',
                      severity='ERROR' if exhausted else ('WARNING' if failed_channels else 'INFO'),
                      rule_id=rule['id'], message=message)
        except Exception as e:
            logger.warning(f'Digest event log failed: {e}')

        if exhausted:
            logger.error(f'Digest abandoned: rule={rule["name"]}, period={period_key}, '
                         f'{len(snaps)} packages, detail={detail}')
        else:
            logger.info(f'Digest sent: rule={rule["name"]}, period={period_key}, '
                        f'{len(snaps)} packages, channels={sent_channels + already_channels}')


# 汇总直发 webhook 也要按渠道加签,否则钉钉/飞书后台开了签名校验就必失败
_DIGEST_SIGN_PARAMS = {
    'dingtalk': ('ms', True),
    'feishu': ('s', False),
}


def _digest_signed_url(channel: dict) -> str:
    config = channel.get('config') or {}
    webhook = config.get('webhook_url', '')
    params = _DIGEST_SIGN_PARAMS.get(channel.get('type', ''))
    if not webhook or not params:
        return webhook
    return _sign_url(webhook, config.get('secret', ''),
                     timestamp_unit=params[0], url_quote=params[1])


def _digest_feishu_post(text: str) -> dict:
    """飞书 text 消息不渲染 markdown,把汇总转成 post 段落(链接转 a 标签)。"""
    import re
    paras = []
    for raw in text.split('\n'):
        line = raw.strip()
        if not line:
            continue
        line = line.lstrip('#').strip()
        line = line.replace('<u>', '').replace('</u>', '').replace('**', '').replace('`', '')
        parts = []
        pos = 0
        for m in re.finditer(r'\[([^\]]+)\]\(([^)]+)\)', line):
            if m.start() > pos:
                parts.append({'tag': 'text', 'text': line[pos:m.start()]})
            parts.append({'tag': 'a', 'text': m.group(1), 'href': m.group(2)})
            pos = m.end()
        if pos < len(line):
            parts.append({'tag': 'text', 'text': line[pos:]})
        if parts:
            paras.append(parts)
    return {'title': '升级汇总', 'content': paras}


def _digest_channel_already_sent(rule_id: int, channel_id: int,
                                 snapshot_ids: list) -> bool:
    """True when every snapshot in this digest has a sent row for the channel.

    Lets a retry skip channels that succeeded on an earlier attempt instead of
    re-sending the whole summary to them.
    """
    if not snapshot_ids:
        return False
    from src.models.database import query
    placeholders = ','.join('?' * len(snapshot_ids))
    rows = query(
        f"""SELECT COUNT(DISTINCT snapshot_id) AS c FROM delivery_log
            WHERE rule_id = ? AND channel_id = ? AND delivery_status = 'sent'
              AND snapshot_id IN ({placeholders})""",
        (rule_id, channel_id, *snapshot_ids))
    return bool(rows) and (rows[0]['c'] or 0) >= len(snapshot_ids)


def _send_digest_split(rule: dict, digest_text: str, snaps: list) -> dict:
    """Send a digest summary, splitting into multiple messages if too long.

    机器人渠道直发 webhook:必须自己看返回码,否则 4xx/errcode 也会被当成成功
    记进 delivery_log。邮件渠道暂不支持汇总消息(历史 recipient 在渠道级已废弃),
    跳过并留日志,不再静默。

    返回 {'sent': [渠道...], 'failed': [...], 'skipped': [...], 'already': [...]},
    调用方据此决定重试/消费队列。already 是"上次已成功、本次跳过"的渠道。
    """
    from src.models.subscription import get_rule_channels, log_delivery
    import requests

    result = {'sent': [], 'failed': [], 'skipped': [], 'already': []}

    bindings = get_rule_channels(rule['id'])
    if not bindings:
        result['skipped'].append('规则未绑定渠道')
        logger.warning(f'Digest 跳过: rule={rule.get("name")} 未绑定任何渠道')
        return result

    snapshot_ids = sorted({s['snapshot_id'] for s in snaps})
    # 和 IM 通知共用一套分片(4000 字节 + (i/N) 标记)
    parts = _split_with_marker(digest_text, 4000)
    total = len(parts)
    im_types = ('wecom', 'dingtalk', 'feishu')

    for binding in bindings:
        channel_id = binding.get('channel_id')
        if not channel_id:
            continue
        channel = get_by_id(channel_id)
        if not channel:
            result['skipped'].append(f'渠道 {channel_id}: 不存在')
            continue
        label = f'{channel.get("name", "")}({channel.get("type", "")})'
        if not channel.get('is_active'):
            result['skipped'].append(f'{label}: 已停用')
            logger.warning(f'Digest 跳过渠道 {channel_id}({channel.get("name", "")}): 渠道已停用')
            continue
        # 部分失败重试时，已成功的渠道不再重复收到同一期汇总
        if _digest_channel_already_sent(rule['id'], channel_id, snapshot_ids):
            result['already'].append(label)
            logger.info(f'Digest 跳过已投递渠道 {channel_id}({channel.get("name", "")})')
            continue

        payload_fn = {
            'wecom': lambda t: {'msgtype': 'markdown', 'markdown': {'content': t}},
            'dingtalk': lambda t: {'msgtype': 'markdown', 'markdown': {'title': '升级汇总', 'text': t}},
            'feishu': lambda t: {'msg_type': 'post',
                                 'content': {'post': {'zh_cn': _digest_feishu_post(t)}}},
        }.get(channel['type'])

        if not payload_fn:
            reason = ('邮件渠道不支持汇总消息,请在该规则上改用企微/钉钉/飞书渠道'
                      if channel['type'] == 'email'
                      else f'不支持的渠道类型 {channel["type"]}')
            result['skipped'].append(f'{label}: {reason}')
            logger.warning(f'Digest 跳过渠道 {channel_id}({channel.get("name", "")}): {reason}')
            continue

        config = channel.get('config') or {}
        if not config.get('webhook_url'):
            result['skipped'].append(f'{label}: webhook_url 为空')
            logger.warning(f'Digest 跳过渠道 {channel_id}: webhook_url 为空')
            continue

        url = _digest_signed_url(channel)
        send_error = ''
        for i, part in enumerate(parts):
            if channel['type'] in im_types:
                _throttle_im(channel['type'])
            try:
                resp = requests.post(url, json=payload_fn(part), timeout=15)
                body = resp.json() if resp.content else {}
                errcode = body.get('errcode')
                code = body.get('code')
                if (resp.status_code != 200
                        or (errcode is not None and errcode != 0)
                        or (code is not None and code != 0)):
                    send_error = f'part {i+1}/{total}: HTTP {resp.status_code} {body}'
                    break
            except Exception as e:
                send_error = f'part {i+1}/{total}: {type(e).__name__}: {e}'
                break

        if send_error:
            logger.warning(f'Digest send failed for {channel["type"]} '
                           f'({channel.get("name", "")}): {send_error}')
            result['failed'].append(f'{label}: {send_error}')
        else:
            result['sent'].append(label)

        # Log each snapshot delivery
        for snap in snaps:
            # digest_queue 行带 chain_json (同 rule + period 下按 chain 分桶)。
            try:
                chain_json_raw = (snap.get('chain_json') or '[]') if isinstance(snap, dict) else '[]'
            except Exception:
                chain_json_raw = '[]'
            log_delivery(
                snapshot_id=snap['snapshot_id'],
                rule_id=rule['id'],
                channel_id=channel_id,
                channel_type=channel['type'],
                channel_name=channel.get('name', ''),
                # 同 _send_immediate:用 rule.customer_id,不是 binding 的(永远是 NULL)
                customer_id=rule.get('customer_id') or binding.get('customer_id'),
                status='failed' if send_error else 'sent',
                error=send_error,
                # digest 是机器人渠道,recipient 留空,前端回退到 channel_name
                recipient='',
                chain_json=chain_json_raw,
            )

    return result


def _is_full_package(snap: dict) -> bool:
    """Check if a snapshot is a full/complete package (全量包).
    
    Priority (highest first):
    1. Config override (product|type → full/incremental)
    2. Explicit incremental keywords → not full
    3. Explicit full keywords → full
    4. Default: not full
    """
    # Load classification config
    config = _load_classification_config()

    product = snap.get('product_name', '')
    # 2026-08-08: pkg_type 用 chain 派生,不读 snap.package_type
    # (URL 去重后 save_snapshot UPDATE 互相覆盖,snap.package_type 脏)。
    pkg_type = _resolve_pkg_from_chain(snap)
    override_key = f'{product}|{pkg_type}'

    # 1. Check explicit overrides first
    overrides = config.get('overrides', {})
    if override_key in overrides:
        return overrides[override_key] == 'full'
    wildcard_key = f'*|{pkg_type}'
    if wildcard_key in overrides:
        return overrides[wildcard_key] == 'full'

    # 2-4. Keyword-based detection
    desc = (snap.get('description_raw') or '').lower()
    name = (snap.get('file_name') or '').lower()

    # 2. Explicit incremental keywords → not full
    incr_keywords = config.get('incremental_keywords', ['增量', '差异包', '补丁包', '增量更新'])
    if any(kw.lower() in desc or kw.lower() in name for kw in incr_keywords):
        return False

    # 3. Explicit full keywords → full
    full_keywords = config.get('full_keywords', ['全量', '完整包', '离线升级包', '离线包'])
    if any(kw.lower() in desc or kw.lower() in name for kw in full_keywords):
        return True

    # 4. Default: not full
    return False


def _load_classification_config() -> dict:
    """Load package classification config from system_settings."""
    try:
        from src.models.database import query
        rows = query("SELECT value FROM system_settings WHERE key = 'package_classification'")
        if rows:
            import json
            return json.loads(rows[0]['value'])
    except Exception:
        pass
    # Default config
    return {
        'full_keywords': ['全量', '完整包', '离线升级包', '离线包'],
        'incremental_keywords': ['增量', '差异包', '补丁包', '增量更新'],
        'overrides': {},
    }


def _filter_full_packages(snaps: list) -> list:
    """Filter snapshots: for full packages, only keep the latest per group.
    
    Groups by: (product_name, version_branch, package_type)
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for s in snaps:
        key = (s.get('product_name', ''), s.get('version_branch', ''), s.get('package_type', ''))
        groups[key].append(s)

    result = []
    for key, items in groups.items():
        full_items = [s for s in items if _is_full_package(s)]
        incr_items = [s for s in items if not _is_full_package(s)]

        if full_items:
            # Only keep the latest full package (by published_at)
            full_items.sort(key=lambda s: s.get('published_at', ''), reverse=True)
            result.append(full_items[0])
            logger.debug(f'Full package filtered: {key} kept 1/{len(full_items)}')
        # Always keep all incremental packages
        result.extend(incr_items)

    return result
