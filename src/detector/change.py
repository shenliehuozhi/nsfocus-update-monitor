"""Change detection engine.

Compares collected items against the snapshot database to identify:
  - NEW packages (never seen before)
  - ROLLBACK packages (disappeared for N consecutive checks)
  - UNCHANGED packages (still present)

Also manages the delayed push queue.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from src.core.logger import get_logger
from src.models import snapshot as snap_db
from src.collectors.base import UnifiedContentItem

logger = get_logger('detector')


@dataclass
class DetectionResult:
    source_id: int
    new_items: list = field(default_factory=list)       # (snapshot_id, snapshot_dict)
    rollback_items: list = field(default_factory=list)   # (snapshot_id, snapshot_dict)
    unchanged_items: list = field(default_factory=list)  # (snapshot_id, snapshot_dict)
    unchanged_count: int = 0
    errors: list = field(default_factory=list)


def run_detection(source_id: int, items: list[UnifiedContentItem],
                  rollback_confirm: int = 2, check_rollback: bool = True,
                  seen_ids: set = None) -> DetectionResult:
    """Main detection entry point.

    1. Save/update all collected items as snapshots
    2. Mark items NOT in this batch as rollback_pending
    3. Confirm rollbacks after N consecutive misses
    4. Return categorized results
    """
    result = DetectionResult(source_id=source_id)

    # Build seen_ids: pre-populate with known active snapshot IDs (unchanged pages
    # that were filtered by dedup in scheduler._collect_quick) so they aren't
    # incorrectly marked rollback_pending.
    seen_ids = set() if seen_ids is None else set(seen_ids)

    if not items and not seen_ids:
        # Nothing collected — could mean session expired or site down
        # Don't trigger mass rollback on empty collection
        logger.warning(f'Source {source_id}: collected 0 items')
        return result

    # Step 1: Save/update all collected items as snapshots
    for item in items:
        try:
            snap_dict = item.to_snapshot_dict()
            sid = snap_db.save_snapshot(snap_dict)
            seen_ids.add(sid)

            # Check if this is NEW (first_seen_at == last_seen_at after save)
            snap = snap_db.get_snapshot(sid)
            if snap and snap.get('first_seen_at') == snap.get('last_seen_at'):
                result.new_items.append((sid, snap))
            else:
                result.unchanged_count += 1
                result.unchanged_items.append((sid, snap))

        except Exception as e:
            logger.error(f'Failed to save snapshot: {e}')
            result.errors.append(str(e))

    # Step 2: Mark missing items as rollback_pending (only in full-scan mode)
    if check_rollback:
        snap_db.mark_rollback_pending(seen_ids, source_id)

    # Step 3: Confirm rollbacks (only in full-scan mode)
    if check_rollback:
        confirmed = _confirm_rollbacks(source_id, rollback_confirm)
        result.rollback_items = confirmed

    return result


def _confirm_rollbacks(source_id: int, confirm_count: int) -> list:
    """Confirm rollback for snapshots that have been pending for N consecutive cycles.

    Only snapshots with rollback_cycles >= confirm_count are confirmed.
    Pending snapshots with fewer cycles stay pending for the next check.
    """
    from src.models.database import query, execute

    # Only confirm snapshots that have reached the threshold
    pending = query(
        """SELECT id FROM snapshots
           WHERE source_id = ? AND status = 'rollback_pending'
           AND rollback_cycles >= ?""",
        (source_id, confirm_count)
    )

    if not pending:
        return []

    confirmed = []
    for row in pending:
        sid = row['id']
        snap = snap_db.get_snapshot(sid)
        if snap:
            execute(
                "UPDATE snapshots SET status = 'rollback', rollback_confirmed_at = datetime('now') "
                "WHERE id = ?",
                (sid,)
            )
            confirmed.append((sid, dict(snap)))
            logger.info(f'ROLLBACK confirmed (cycles={snap.get("rollback_cycles",0)}/{confirm_count}): '
                       f'{snap.get("product_name")} {snap.get("file_name")}')

    return confirmed


# ── Subscription rule matching ──────────────────────────────────────────────


def _chain_matches(snap_chain: list, rule_chains: list) -> bool:
    """判断 snapshot 的 chain 是否匹配规则中的任意一条 chain 条目。

    匹配模式：
      - leaf:    snap_chain 必须与 rule_chain 完全相等（精确到具体包类型）
      - subtree: snap_chain 以 rule_chain 为前缀（订阅该节点下全部包）

    Args:
        snap_chain:   snapshot 从 source_url 反查得到的完整 chain
        rule_chains:  filter_conditions['chains'] 列表

    Returns:
        True if any rule_chain entry matches, False otherwise.
    """
    if not rule_chains:
        return True  # 无 chains 条件 = 全部匹配

    for entry in rule_chains:
        rc = entry.get('chain', [])
        mode = entry.get('match', 'leaf')

        if not rc:
            continue

        if mode == 'leaf':
            if snap_chain == rc:
                return True
        elif mode == 'subtree':
            # subtree: snap_chain 的前缀必须等于 rule_chain
            if len(snap_chain) >= len(rc) and snap_chain[:len(rc)] == rc:
                return True

    return False


def get_new_for_subscription(rule: dict, new_items: list) -> list:
    """Filter new items through a subscription rule's conditions.

    支持新旧两种 filter_conditions 结构：
      - 新结构（chains）: 按链路径匹配，通过 scheduler._get_chain 反查
      - 旧结构（products/versions/package_types）: 维持向后兼容的独立字段匹配

    空 conditions = 匹配全部（等效订阅全部产品）。

    Args:
        rule:      subscription_rules 表行（含 filter_conditions JSON 解析后 dict）
        new_items: [(snapshot_id, snapshot_dict), ...]

    Returns:
        匹配的 [(snapshot_id, snapshot_dict), ...]
    """
    conditions = rule.get('filter_conditions', {})
    if not conditions:
        return new_items  # 空条件 = 匹配全部

    # ── 新结构：chains（链路径匹配）──────────────────────────────────────
    chains = conditions.get('chains', [])

    if chains:
        # 从 scheduler 获取 chain 反查函数（延迟 import 避免循环）
        _get_chain = None
        try:
            from src.core.scheduler import _get_chain as _resolve_chain
            _get_chain = _resolve_chain
        except ImportError:
            logger.warning('scheduler._get_chain not available, skipping chain filter')

        if _get_chain and chains:
            matched = []
            for sid, snap in new_items:
                snap_chain = _get_chain(
                    snap.get('source_id', 0),
                    snap.get('source_url', '')
                )
                if not _chain_matches(snap_chain, chains):
                    continue

                # Chain 匹配通过后，再检查 urgency 和 keywords
                urgency = conditions.get('urgency', [])
                if urgency and snap.get('urgency') not in urgency:
                    continue

                keywords = conditions.get('keywords', [])
                if keywords:
                    desc = snap.get('description_raw', '')
                    if not any(kw.lower() in desc.lower() for kw in keywords):
                        continue

                matched.append((sid, snap))
            return matched

    # ── 旧结构：products / versions / package_types（向后兼容）───────────
    products = conditions.get('products', [])
    versions = conditions.get('versions', [])
    pkg_types = conditions.get('package_types', [])
    urgency = conditions.get('urgency', [])
    keywords = conditions.get('keywords', [])

    matched = []
    for sid, snap in new_items:
        # Check product filter
        if products and snap.get('product_name') not in products:
            continue

        # Check version filter
        if versions and snap.get('version_branch') not in versions:
            continue

        # Check package type filter (supports comma-separated values per entry)
        if pkg_types:
            flat_types = set()
            for pt in pkg_types:
                if pt:
                    for t in pt.split(','):
                        t = t.strip()
                        if t:
                            flat_types.add(t)
            if flat_types and snap.get('package_type') not in flat_types:
                continue

        # Check urgency filter
        if urgency and snap.get('urgency') not in urgency:
            continue

        # Check keyword filter (in description)
        if keywords:
            desc = snap.get('description_raw', '')
            if not any(kw.lower() in desc.lower() for kw in keywords):
                continue

        matched.append((sid, snap))

    return matched


def compute_push_time(delay_days: int) -> str:
    """Compute the push_after timestamp for a delayed push (unit: days)."""
    if delay_days <= 0:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    target = datetime.now() + timedelta(days=delay_days)
    return target.strftime('%Y-%m-%d %H:%M:%S')


def is_quiet_time(rule: dict) -> bool:
    """Check if current time falls within the rule's quiet period."""
    quiet_start = rule.get('quiet_start', '')
    quiet_end = rule.get('quiet_end', '')
    if not quiet_start or not quiet_end:
        return False

    now = datetime.now().strftime('%H:%M')
    if quiet_start <= quiet_end:
        return quiet_start <= now <= quiet_end
    else:
        # Crosses midnight (e.g., 22:00 - 08:00)
        return now >= quiet_start or now <= quiet_end


def is_window_time(rule: dict) -> bool:
    """Check if current time falls within the rule's push window.

    window_config: {days: [0-6], start: "HH:MM", end: "HH:MM"}
    Returns True if inside window, False if outside.

    Semantic for empty days (digest mode):
      - Only time-of-day restriction applies (any day is allowed)
      - e.g. {start: "09:00", end: "18:00"} = only send during business hours
    """
    wc = rule.get('window_config') or {}
    days = wc.get('days', [])
    start = wc.get('start', '')
    end = wc.get('end', '')
    if not start or not end:
        return True  # No window configured = always OK

    now = datetime.now()
    current_time = now.strftime('%H:%M')

    # Time-of-day check (always applies)
    if start <= end:
        in_range = start <= current_time <= end
    else:
        # Crosses midnight
        in_range = current_time >= start or current_time <= end

    # Day-of-week check (only applies when days is non-empty)
    if not in_range:
        return False
    if days:
        today_weekday = now.weekday()  # 0=Mon ... 6=Sun
        if today_weekday not in days:
            return False

    return True


def compute_next_window_push_time(rule: dict) -> str:
    """Compute the next window opening time for when outside the window.

    For backward compatibility: if rule has delay_strategy='window' but no window_config,
    use default window (Mon-Fri 09:00-18:00).

    For digest mode (days=[]): finds next time window opens today/tomorrow, ignoring day.
    """
    wc = rule.get('window_config') or {}
    # Backward compat: delay_strategy='window' without window_config gets default window
    if not wc and rule.get('delay_strategy') == 'window':
        wc = {'days': [1, 2, 3, 4, 5], 'start': '09:00', 'end': '18:00'}
    days = wc.get('days', [])
    start = wc.get('start', '')
    if not start:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    now = datetime.now()
    current_time = now.strftime('%H:%M')

    if not days:
        # Digest mode (days=[]): only time-of-day restriction, find next opening
        if current_time < start:
            # Window opens today
            target = now.replace(hour=int(start.split(':')[0]), minute=int(start.split(':')[1]), second=0, microsecond=0)
        else:
            # Window already passed today, open tomorrow at start
            target = now.replace(hour=int(start.split(':')[0]), minute=int(start.split(':')[1]), second=0, microsecond=0) + timedelta(days=1)
        return target.strftime('%Y-%m-%d %H:%M:%S')

    days_sorted = sorted(days)
    today_weekday = now.weekday()

    # Find next window day
    next_day = None
    days_ahead = None
    for d in days_sorted:
        if d > today_weekday:
            next_day = d
            days_ahead = d - today_weekday
            break

    if next_day is None:
        # Wrap to next week
        next_day = days_sorted[0]
        days_ahead = 7 - today_weekday + next_day

    # Compute target datetime
    target = now + timedelta(days=days_ahead)
    h, m = map(int, start.split(':'))
    target = target.replace(hour=h, minute=m, second=0, microsecond=0)
    return target.strftime('%Y-%m-%d %H:%M:%S')


def check_min_interval(rule: dict, product_name: str) -> bool:
    """Check if minimum interval has passed since last notification for this product/rule.

    Returns True if OK to notify, False if too soon.
    """
    min_hours = rule.get('min_interval_hours', 0)
    if min_hours <= 0:
        return True

    from src.models.database import query

    cutoff = (datetime.now() - timedelta(hours=min_hours)).strftime('%Y-%m-%d %H:%M:%S')
    rows = query(
        """SELECT COUNT(*) as cnt FROM delivery_log dl
           JOIN snapshots s ON dl.snapshot_id = s.id
           WHERE s.product_name = ? AND dl.sent_at > ? AND dl.delivery_status = 'sent'""",
        (product_name, cutoff)
    )
    count = rows[0]['cnt'] if rows else 0
    return count == 0
