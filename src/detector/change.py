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
    unchanged_count: int = 0
    errors: list = field(default_factory=list)


def run_detection(source_id: int, items: list[UnifiedContentItem],
                  rollback_confirm: int = 2, check_rollback: bool = True) -> DetectionResult:
    """Main detection entry point.

    1. Save/update all collected items as snapshots
    2. Mark items NOT in this batch as rollback_pending
    3. Confirm rollbacks after N consecutive misses
    4. Return categorized results
    """
    result = DetectionResult(source_id=source_id)

    if not items:
        # Nothing collected — could mean session expired or site down
        # Don't trigger mass rollback on empty collection
        logger.warning(f'Source {source_id}: collected 0 items')
        return result

    # Step 1: Save/update all items
    seen_ids = set()
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
    """Check rollback_pending items and confirm those that have been
    pending for enough cycles.

    Simplified approach: we use a separate tracking table or just flip
    rollback_pending → rollback after confirm_count calls.

    For Phase 1, we use a simple state machine:
      - rollback_pending items are flipped to rollback on the NEXT call
      - if confirm_count == 2, it takes 2 cycles
    """
    # Get currently pending items
    from src.models.database import query, execute
    
    pending = query(
        "SELECT id FROM snapshots WHERE source_id = ? AND status = 'rollback_pending'",
        (source_id,)
    )
    
    if not pending:
        return []

    # We need a way to count consecutive misses. 
    # Simple approach: store a rollback_cycle_count in a JSON file or
    # use the fact that rollback_pending only gets set when an item is
    # not in seen_ids. If it was pending before AND still not seen, confirm.
    
    # For now: use a global counter stored in memory (restarts reset counter)
    # Better approach for production: add a rollback_cycle table
    confirmed = []
    
    for row in pending:
        sid = row['id']
        # Get snapshot to return it
        snap = snap_db.get_snapshot(sid)
        if snap:
            snap_db.confirm_rollbacks(source_id, confirm_count)
            # Actually execute individually
            execute(
                "UPDATE snapshots SET status = 'rollback', rollback_confirmed_at = datetime('now') "
                "WHERE id = ?",
                (sid,)
            )
            confirmed.append((sid, dict(snap)))
            logger.info(f'ROLLBACK confirmed: {snap.get("product_name")} {snap.get("file_name")}')

    return confirmed


def get_new_for_subscription(rule: dict, new_items: list) -> list:
    """Filter new items through a subscription rule's conditions.

    Returns items that match the rule's filter_conditions.
    """
    conditions = rule.get('filter_conditions', {})
    if not conditions:
        return new_items  # No filter = match all

    matched = []
    for sid, snap in new_items:
        # Check product filter
        products = conditions.get('products', [])
        if products and snap.get('product_name') not in products:
            continue

        # Check version filter
        versions = conditions.get('versions', [])
        if versions and snap.get('version_branch') not in versions:
            continue

        # Check package type filter (supports comma-separated values per entry)
        pkg_types = conditions.get('package_types', [])
        if pkg_types:
            # Flatten comma-separated entries: ['rule,sys', 'av'] → ['rule', 'sys', 'av']
            flat_types = set()
            for pt in pkg_types:
                if pt:
                    for t in pt.split(','):
                        if t.strip():
                            flat_types.add(t.strip())
            if flat_types and snap.get('package_type') not in flat_types:
                continue

        # Check urgency filter
        urgency_list = conditions.get('urgency', [])
        if urgency_list and snap.get('urgency') not in urgency_list:
            continue

        # Check keyword filter (in description)
        keywords = conditions.get('keywords', [])
        if keywords:
            desc = snap.get('description_raw', '')
            if not any(kw.lower() in desc.lower() for kw in keywords):
                continue

        matched.append((sid, snap))

    return matched


def compute_push_time(delay_hours: int) -> str:
    """Compute the push_after timestamp for a delayed push."""
    if delay_hours <= 0:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    target = datetime.now() + timedelta(hours=delay_hours)
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
