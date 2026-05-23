"""Notification router — orchestrates sending notifications through matched channels."""

import os
import time

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
    delay_hours = rule.get('delay_hours', 0)
    strategy = rule.get('delay_strategy', 'reset')

    if strategy == 'window':
        # Window strategy: only send when inside time window
        if not is_window_time(rule):
            push_after = compute_next_window_push_time(rule)
            enqueue(snapshot_id, rule_id, push_after)
            logger.info(f'Rule {rule["name"]}: outside window, enqueued for {push_after}')
            return
        # Fall through to send immediately when in window

    if delay_hours > 0:
        if strategy == 'reset':
            from src.models.subscription import reset_timer_for_rule
            reset_timer_for_rule(rule_id)

        push_after = compute_push_time(delay_hours)
        enqueue(snapshot_id, rule_id, push_after)
        logger.info(f'Rule {rule["name"]}: delayed {delay_hours}h, strategy={strategy}')
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
    im_channels_used = set()

    for binding in bindings:
        channel_id = binding.get('channel_id')
        if not channel_id:
            continue

        channel = get_by_id(channel_id)
        if not channel or not channel.get('is_active'):
            continue

        # Deduplication: skip if this snapshot+channel already sent successfully
        # (failed records don't block — rate-limit failures are transient)
        from src.models.database import query
        existing = query(
            """SELECT id FROM delivery_log
               WHERE snapshot_id = ? AND channel_id = ? AND delivery_status = 'sent'
               LIMIT 1""",
            (snap['id'], channel_id)
        )
        if existing:
            logger.info(f'Skip duplicate: snapshot {snap["id"]} already sent to channel {channel_id}')
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
        if channel['type'] == 'email':
            if rule.get('customer_emails'):
                ch_config['rule_emails'] = rule['customer_emails']
            if rule.get('attachment_max_mb'):
                ch_config['attachment_max_mb'] = str(rule['attachment_max_mb'])
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

        if channel['type'] in ('wecom', 'dingtalk', 'feishu'):
            im_channels_used.add(channel['type'])

        # Log delivery
        from src.models.subscription import log_delivery
        log_delivery(
            snapshot_id=snap['id'],
            rule_id=rule['id'],
            channel_id=channel_id,
            channel_type=channel['type'],
            channel_name=channel.get('name', ''),
            customer_id=binding.get('customer_id'),
            status='sent' if result.success else 'failed',
            error=result.error_message
        )

    # Send confirmation to IM channels
    for ch_type in im_channels_used:
        for binding in bindings:
            channel_id = binding.get('channel_id')
            if not channel_id:
                continue
            channel = get_by_id(channel_id)
            if channel and channel['type'] == ch_type:
                notifier = NOTIFIERS.get(ch_type)
                if notifier:
                    notifier.send_confirmation(message, results, channel['config'])
                break  # One confirmation per IM type

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
        rule = get_rule(item['rule_id'])
        if not rule:
            mark_pushed(item['id'])
            continue
        _send_immediate(snap, rule, is_rollback=False)
        mark_pushed(item['id'])


def process_digests():
    """Check and send due digest summaries."""
    if _is_maintenance_mode():
        return
    from src.models.subscription import (
        get_rules_due_for_digest, get_digest_snapshots,
        mark_digest_sent, mark_digest_rule_sent,
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
            '',
        ]

        # Group by product
        from collections import defaultdict
        grouped = defaultdict(list)
        for s in snaps:
            grouped[s.get('product_name', '') or '其他'].append(s)

        idx = 0
        for prod, items in grouped.items():
            lines.append(f'### {prod}')
            for s in items:
                idx += 1
                urgency_icon = {'critical': '🔴', 'high': '⚠️', 'normal': 'ℹ️'}.get(s.get('urgency', ''), '')
                type_label = s.get('package_type', '')
                is_full = _is_full_package(s)
                full_tag = ' [全量]' if is_full else ''
                lines.append(f'{idx}. {urgency_icon} **{type_label}**{full_tag} — {s.get("file_name", "")}')
                if s.get('package_version'):
                    lines.append(f'   版本: {s.get("package_version")}')
                if s.get('description_raw'):
                    desc = (s.get('description_raw') or '')[:120]
                    lines.append(f'   {desc}')
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
                customer_id=binding.get('customer_id'),
                status='sent',
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
                        parts.append(encoded[offset:offset + max_bytes - 3].decode('utf-8', errors='ignore'))
                    offset += max_bytes - 3
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
