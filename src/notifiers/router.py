"""Notification router — orchestrates sending notifications through matched channels."""

import os
import time
from datetime import datetime, timezone

from src.core.logger import get_logger
from src.notifiers.base import NotificationMessage, DeliveryResult
from src.notifiers.wecom import WecomNotifier
from src.notifiers.dingtalk import DingtalkNotifier
from src.notifiers.feishu import FeishuNotifier
from src.notifiers.email import EmailNotifier
from src.notifiers.apprise import AppriseNotifier
from src.models.channel import get_by_id, list_active
from src.models.subscription import get_rule_channels, enqueue, get_due_items, mark_pushed, cancel_for_snapshot, get_rule
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

# Rate-limit tracker: minimum seconds between sends to same channel type
_RATE_LIMIT_INTERVAL = float(os.getenv('MONITOR_RATE_LIMIT_SEC', '3'))
_last_send: dict[str, float] = {}


def _emit_push_summary():
    """Emit a single push_summary system event for all accumulated push results.
    Called at the end of a collection cycle to report aggregated push outcomes.
    """
    if not _push_summary_accumulator:
        return
    summaries = list(_push_summary_accumulator.values())
    _push_summary_accumulator.clear()

    total = sum(s['total'] for s in summaries)
    success = sum(s['success'] for s in summaries)
    failed = sum(s['failed'] for s in summaries)

    from src.core.event_handler import emit_push_summary
    emit_push_summary(summaries)


def _is_maintenance_mode() -> bool:
    """Check if maintenance mode is enabled (suppress all notifications)."""
    try:
        from src.models.database import query
        rows = query("SELECT value FROM system_settings WHERE key = 'maintenance_mode'")
        return len(rows) > 0 and rows[0]['value'] == '1'
    except Exception:
        return False


def route_notifications(snapshot_id: int, rule_id: int, is_rollback: bool = False):
    """Route a snapshot through its matched channels.

    For delayed pushes, enqueue; for immediate (rollback or no delay), send now.
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

    # Rollback notifications are always immediate
    if is_rollback:
        _send_immediate(snap, rule, is_rollback=True)
        return

    # Digest mode: enqueue to digest queue instead of sending
    digest_mode = rule.get('digest_mode', '')
    if digest_mode:
        from datetime import datetime
        now = datetime.now()
        if digest_mode == 'weekly':
            period_key = f"{now.year}-W{now.isocalendar()[1]:02d}"
        elif digest_mode == 'monthly':
            period_key = now.strftime('%Y-%m')
        else:  # quarterly
            period_key = f"{now.year}-Q{(now.month - 1) // 3 + 1}"

        from src.models.subscription import enqueue_digest
        enqueue_digest(rule_id, snapshot_id, period_key)
        logger.info(f'Rule {rule["name"]}: enqueued for {digest_mode} digest ({period_key})')
        return

    # Check quiet time
    if is_quiet_time(rule):
        logger.info(f'Rule {rule["name"]}: in quiet time, delaying')
        push_after = compute_push_time(1)  # At least 1 hour
        enqueue(snapshot_id, rule_id, push_after)
        return

    # Check min interval
    if not check_min_interval(rule, snap.get('product_name', '')):
        logger.info(f'Rule {rule["name"]}: min interval not met, delaying')
        push_after = compute_push_time(1)
        enqueue(snapshot_id, rule_id, push_after)
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
            enqueue(snapshot_id, rule_id, push_after)
            logger.info(f'Rule {rule["name"]}: outside window, enqueued for {push_after}')
            return
        # Fall through to send immediately when in window

    if delay_days > 0:
        # Each package has its own independent delay timer — no reset.
        push_after = compute_push_time(delay_days)
        enqueue(snapshot_id, rule_id, push_after)
        logger.info(f'Rule {rule["name"]}: delayed {delay_days}d')
        return

    # No delay — send immediately
    _send_immediate(snap, rule)


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


def _send_immediate(snap: dict, rule: dict, is_rollback: bool = False):
    """Send notification immediately through all channels associated with the rule."""
    message = NotificationMessage.from_snapshot(snap, is_rollback=is_rollback)

    bindings = get_rule_channels(rule['id'])
    if not bindings:
        logger.warning(f'Rule {rule["name"]}: no channels bound')
        return

    results = []

    for binding in bindings:
        channel_id = binding.get('channel_id')
        if not channel_id:
            continue

        channel = get_by_id(channel_id)
        if not channel or not channel.get('is_active'):
            continue

        # Deduplication: skip if THIS rule already pushed this snapshot through this
        # channel successfully. Multiple rules sharing the same channel (e.g. ch_id=3
        # bound to both rule 14 "邮箱推送" and rule 25 "华兴银行") must each push their
        # own recipient list — to_list comes from rule.customer_emails, not channel,
        # so cross-rule dedup incorrectly silences other rules' recipients.
        # (failed records don't block — rate-limit failures are transient)
        from src.models.database import query
        existing = query(
            """SELECT id FROM delivery_log
               WHERE snapshot_id = ? AND channel_id = ? AND rule_id = ?
                     AND delivery_status = 'sent'
               LIMIT 1""",
            (snap['id'], channel_id, rule['id'])
        )
        if existing:
            logger.info(f'Skip duplicate: snapshot {snap["id"]} already sent to '
                        f'channel {channel_id} by rule {rule["id"]}')
            continue

        notifier = NOTIFIERS.get(channel['type'])
        if not notifier:
            logger.warning(f'Unknown channel type: {channel["type"]}')
            continue

        # Rate-limit IM channels to avoid API throttling
        if channel['type'] in ('wecom', 'dingtalk', 'feishu'):
            last = _last_send.get(channel['type'], 0)
            elapsed = time.time() - last
            if elapsed < _RATE_LIMIT_INTERVAL:
                time.sleep(_RATE_LIMIT_INTERVAL - elapsed)
            _last_send[channel['type']] = time.time()

        # Build channel config with rule-level overrides
        ch_config = dict(channel['config'])
        # NOTE: template 是订阅规则级别配置(每个 rule 可独立选通知模板)。
        # 通过 ch_config['_template'] 注入到 notifier.send()。默认 'full'(行为不变)。
        # 兼容矩阵详见 base.py:TEMPLATE_NAMES 周围注释。
        template = rule.get('template', '') or 'full'
        ch_config['_template'] = template
        if channel['type'] == 'email':
            if rule.get('customer_emails'):
                ch_config['rule_emails'] = rule['customer_emails']
            # Attachment size: read from customer only (per-customer setting).
            # Falls back to global default when customer is unset (0).
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

        # Send
        result = notifier.send(message, ch_config)
        results.append(result)

        # Calculate recipient string for delivery_log (push history UI).
        # email 渠道: 拼接 to_list + rule_emails(去重),跟 email.py 内部逻辑保持一致
        # 机器人渠道 (wecom/dingtalk/feishu): 推送目标是 webhook,没有"收件人"概念,
        # 留空,前端回退到 channel_name 当"收件人"展示
        recipient_str = ''
        if channel['type'] == 'email':
            to_list = list(ch_config.get('to_list') or [])
            rule_emails = (ch_config.get('rule_emails') or '').strip()
            if rule_emails:
                extras = [e.strip() for e in rule_emails.split(',') if e.strip()]
                to_list = list(dict.fromkeys(to_list + extras))
            recipient_str = ','.join(to_list)

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
        )

        # Accumulate for push summary (replaces per-delivery emit_push)
        key = (rule['id'], channel_id)
        if key not in _push_summary_accumulator:
            _push_summary_accumulator[key] = {
                'total': 0, 'success': 0, 'failed': 0,
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
        accum['items'].append({
            'file_name': snap.get('file_name', ''),
            'product_name': snap.get('product_name', ''),
            'package_type': snap.get('package_type', ''),
            'success': result.success,
            'error': result.error_message or '',
            'pushed_at': datetime.now(timezone.utc).isoformat(),
        })

    logger.info(f'Rule {rule["name"]}: {len(results)} deliveries, '
                f'success={sum(1 for r in results if r.success)}')


def process_delayed_queue():
    """Check delayed_queue for items ready to push."""
    if _is_maintenance_mode():
        return
    items = get_due_items()
    for item in items:
        snap = get_snapshot(item['snapshot_id'])
        if not snap:
            mark_pushed(item['id'])
            continue
        # P1-2 fix: skip if snapshot is no longer active (package was withdrawn
        # during the delay window — cancelled_reason is set by mark_rollback_pending)
        if snap.get('status') != 'active':
            logger.info(f'Skip delayed push {item["id"]}: snapshot status={snap.get("status")}')
            mark_pushed(item['id'])
            continue
        rule = get_rule(item['rule_id'])
        if not rule:
            mark_pushed(item['id'])
            continue
        # C1-2: 重放时也要检查静默期和窗口时间，不在窗口内则跳过
        if is_quiet_time(rule):
            logger.info(f'Skip delayed push {item["id"]}: quiet time')
            continue
        if is_window_time(rule) is False:
            logger.info(f'Skip delayed push {item["id"]}: outside window time')
            continue
        _send_immediate(snap, rule, is_rollback=False)
        mark_pushed(item['id'])


def process_digests():
    """Check and send due digest summaries."""
    if _is_maintenance_mode():
        return
    from src.models.subscription import (
        get_rules_due_for_digest, get_digest_snapshots,
        mark_digest_sent, mark_digest_rule_sent, cancel_digest_item,
    )
    from src.notifiers.base import NotificationMessage

    due_rules = get_rules_due_for_digest()
    if not due_rules:
        return

    for rule in due_rules:
        period_key = rule['_period_key']
        snaps = get_digest_snapshots(rule_id=rule['id'], period_key=period_key)
        if not snaps:
            # No snapshots accumulated, mark as sent to prevent re-checking
            mark_digest_rule_sent(rule['id'], period_key)
            continue

        # P1-2 fix: filter out snapshots that are no longer active (package was
        # withdrawn/rolled back during the digest window), cancel their queue entries
        still_active = []
        for s in snaps:
            if s.get('snapshot_status') != 'active':
                logger.info(f'Removing withdrawn snapshot {s["snapshot_id"]} from digest '
                            f'queue item {s["id"]} for rule {rule["name"]}')
                cancel_digest_item(s['id'])
            else:
                still_active.append(s)
        snaps = still_active
        if not snaps:
            mark_digest_rule_sent(rule['id'], period_key)
            continue

        # C1-3: 汇总发送前检查窗口时间，不在窗口内则跳过（不标记为已发送，下次继续检查）
        if is_window_time(rule) is False:
            logger.info(f'Skip digest for rule {rule["name"]}: outside window time')
            continue

        # Filter: for full packages, only keep the latest per group
        snaps = _filter_full_packages(snaps)

        # Build digest message
        mode_label = {'weekly': '周', 'monthly': '月', 'quarterly': '季度'}.get(rule['digest_mode'], '')
        title = f'📊 {rule["name"]} — {mode_label}度升级汇总'
        product_names = list(set(s.get('product_name', '') for s in snaps))
        period_display = period_key

        # Build summary markdown
        lines = [
            f'## {title}',
            '',
            f'**周期**: {period_display}',
            f'''**产品**: {', '.join(product_names)}''',
            f'**本期新增**: {len(snaps)} 个升级包',
            f'🔺 <u>请查看每个升级包的详情/升级描述，了解具体变更内容</u>',
            '',
        ]

        # Group by source_url (each source_url = one distinct upgrade flow)
        from collections import defaultdict
        grouped = defaultdict(list)
        for s in snaps:
            key = s.get('source_url') or '其他'
            grouped[key].append(s)

        idx = 0
        for source_key, items in grouped.items():
            # Human label: last segment of URL path (e.g. 'rule' from .../v/rule)
            source_label = source_key.split('/')[-1] if source_key.startswith('http') else source_key

            lines.append(f'### {source_label}')
            for s in items:
                idx += 1
                urgency_icon = {'critical': '🔴', 'high': '⚠️', 'normal': 'ℹ️'}.get(s.get('urgency', ''), '')
                type_label = s.get('package_type', '')
                is_full = _is_full_package(s)
                full_tag = ' [全量]' if is_full else ''
                lines.append(f'{idx}. {urgency_icon} **{type_label}**{full_tag}')
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

        # Send via rule's channels with auto-split for long messages
        digest_text = '\n'.join(lines)
        _send_digest_split(rule, digest_text, snaps)

        mark_digest_sent(rule['id'], period_key)
        mark_digest_rule_sent(rule['id'], period_key)
        logger.info(f'Digest sent: rule={rule["name"]}, period={period_key}, '
                    f'{len(snaps)} packages')


def _send_digest_split(rule: dict, digest_text: str, snaps: list):
    """Send a digest summary, splitting into multiple messages if too long."""
    from src.models.subscription import get_rule_channels

    bindings = get_rule_channels(rule['id'])
    if not bindings:
        return

    # Split into parts of ~3800 bytes each
    parts = _split_text(digest_text, 3800)
    total = len(parts)

    for binding in bindings:
        channel_id = binding.get('channel_id')
        if not channel_id:
            continue
        channel = get_by_id(channel_id)
        if not channel or not channel.get('is_active'):
            continue

        payload_fn = {
            'wecom': lambda t: {'msgtype': 'markdown', 'markdown': {'content': t}},
            'dingtalk': lambda t: {'msgtype': 'markdown', 'markdown': {'title': '升级汇总', 'text': t}},
            'feishu': lambda t: {'msg_type': 'text', 'content': {'text': t}},
        }.get(channel['type'])

        if not payload_fn:
            continue

        import requests
        webhook = channel['config'].get('webhook_url', '')
        if not webhook:
            continue

        for i, part in enumerate(parts):
            if total > 1:
                part = f'({i+1}/{total})\n{part}'

            # Rate-limit IM channels
            if channel['type'] in ('wecom', 'dingtalk', 'feishu'):
                last = _last_send.get(channel['type'], 0)
                elapsed = time.time() - last
                if elapsed < _RATE_LIMIT_INTERVAL:
                    time.sleep(_RATE_LIMIT_INTERVAL - elapsed)
                _last_send[channel['type']] = time.time()

            try:
                requests.post(webhook, json=payload_fn(part), timeout=15)
            except Exception as e:
                logger.warning(f'Digest send failed for {channel["type"]} part {i+1}: {e}')

        # Log each snapshot delivery
        for snap in snaps:
            from src.models.subscription import log_delivery
            log_delivery(
                snapshot_id=snap['snapshot_id'],
                rule_id=rule['id'],
                channel_id=channel_id,
                channel_type=channel['type'],
                channel_name=channel.get('name', ''),
                # 同 _send_immediate:用 rule.customer_id,不是 binding 的(永远是 NULL)
                customer_id=rule.get('customer_id') or binding.get('customer_id'),
                status='sent',
                # digest 是机器人渠道,recipient 留空,前端回退到 channel_name
                recipient='',
            )


def _split_text(text: str, max_bytes: int) -> list[str]:
    """Split text into chunks of at most max_bytes each, at line boundaries."""
    parts = []
    lines = text.split('\n')
    current = ''
    for line in lines:
        candidate = current + ('\n' if current else '') + line
        if len(candidate.encode('utf-8')) > max_bytes:
            if current:
                parts.append(current)
            # If single line exceeds limit, chunk it
            if len(line.encode('utf-8')) > max_bytes:
                encoded = line.encode('utf-8')
                offset = 0
                while offset < len(encoded):
                    chunk = encoded[offset:offset + max_bytes]
                    try:
                        parts.append(chunk.decode('utf-8'))
                    except UnicodeDecodeError:
                        # Back up to last clean character boundary
                        parts.append(chunk.decode('utf-8', errors='ignore'))
                    offset += max_bytes
                current = ''
            else:
                current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts if parts else [text]


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
    pkg_type = snap.get('package_type', '')
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
