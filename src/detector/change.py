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
from src.core.timeutil import CST, DB_FMT, cst_now, to_utc
from src.models import snapshot as snap_db
from src.collectors.base import UnifiedContentItem

logger = get_logger('detector')


@dataclass
class DetectionResult:
    source_id: int
    new_items: list = field(default_factory=list)       # (snapshot_id, snapshot_dict)
    new_chain_items: list = field(default_factory=list)   # (snapshot_id, snapshot_dict, chain)
    rollback_items: list = field(default_factory=list)   # (snapshot_id, snapshot_dict)
    unchanged_items: list = field(default_factory=list)  # (snapshot_id, snapshot_dict)
    unchanged_count: int = 0
    errors: list = field(default_factory=list)


def run_detection(source_id: int, items: list[UnifiedContentItem],
                  rollback_confirm: int = 2, check_rollback: bool = True,
                  seen_ids: set = None,
                  withdrawn_items: list = None) -> DetectionResult:
    """Main detection entry point.

    1. Save/update all collected items as snapshots
    2. 按 first_seen == last_seen 判 NEW / UNCHANGED
    3. collector 报的撤回行进 result.rollback_items → scheduler 推 ⚠️ 撤回通知

    2026-07-22 改动 3: withdrawn_items 是 collector 报告的"刚被标 withdrawn 的行",
    这些行需要进 result.rollback_items 让 scheduler 推 ⚠️ 撤回通知。

    ⚠️ rollback_confirm / check_rollback 是 2026-07-27 删掉"连续 N 次确认回退"路径后
    留下的死参数,函数体从不读取 —— 但设置页「回退确认次数」仍把值传进来,
    改了那个数字不会有任何效果。要恢复该语义需重新实现,或直接摘掉 UI 开关。
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
    # 先全部入库、再统一判定 NEW：同一轮的包可能互相影响状态（同路径最新包会把
    # 更老的 active 降级为 superseded），边存边判会把已被降级的包误推成 NEW。
    processed_physical_keys: set = set()
    saved_sids = []
    for item in items:
        # to_snapshot_dict 自己也可能炸,先给 snap_dict 兜个空值让 except 里能安全引用
        snap_dict = {}
        try:
            snap_dict = item.to_snapshot_dict()
            physical_key = (
                snap_dict.get('source_id', 0),
                snap_dict.get('source_url', ''),
                snap_dict.get('path_id', ''),
                snap_dict.get('file_name', ''),
                snap_dict.get('md5_hash', ''),
            )
            if physical_key in processed_physical_keys:
                continue
            processed_physical_keys.add(physical_key)
            sid = snap_db.save_snapshot(snap_dict)
            seen_ids.add(sid)
            saved_sids.append(sid)

        except Exception as e:
            # 带上是哪个包 + 完整 traceback: 原先只 log 一行 e, 像
            # "CHECK constraint failed: status IN (...)" 这种就完全无法定位到具体条目
            logger.error('Failed to save snapshot: file=%s md5=%s source_id=%s: %s',
                         snap_dict.get('file_name', ''), snap_dict.get('md5_hash', ''),
                         snap_dict.get('source_id', ''), e, exc_info=True)
            result.errors.append(str(e))

    # Step 2: 全部入库后判定 NEW
    # NEW = 本次首次入库（first_seen == last_seen）且最终状态仍是 active。
    # 历史回填/被更新的同路径包替代的行是 superseded，或 first_seen 被回填成
    # published_at，两种条件都不满足 → 不推送。
    for sid in saved_sids:
        snap = snap_db.get_snapshot(sid)
        if (snap and snap.get('status') == 'active'
                and snap.get('first_seen_at') == snap.get('last_seen_at')):
            result.new_items.append((sid, snap))

            # A shared URL may belong to several chains. Keep the
            # physical snapshot event, but expose one event per chain.
            from src.core.scheduler import _get_chain
            snap_chains = _get_chain(
                snap.get('source_id', 0),
                snap.get('source_url', ''),
                snap.get('path_id'),
            )
            if not isinstance(snap_chains, list):
                snap_chains = [snap_chains] if snap_chains else []
            seen_chains = set()
            for chain in snap_chains:
                if not isinstance(chain, list) or not chain:
                    continue
                chain_key = tuple(chain)
                if chain_key in seen_chains:
                    continue
                seen_chains.add(chain_key)
                result.new_chain_items.append((sid, snap, chain))
        else:
            result.unchanged_count += 1
            result.unchanged_items.append((sid, snap))

    # 2026-07-27: 删除 mark_rollback_pending + _confirm_rollbacks 跨 cycle 累积确认路径
    # (ad203e5 设计意图保留,但因为 seen_ids 错位 + DB 永远没真写过 rollback 状态,
    #  全部提交给 status='withdrawn' 通过 collector 当场识别路径处理)
    # withdrawn_items 唯一保留: collector 现场报告 withdrawn 的行 → 走 ⚠️ 撤回通知
    if withdrawn_items:
        result.rollback_items.extend([(d['id'], d) for d in withdrawn_items])
        logger.info(f'Source {source_id}: {len(withdrawn_items)} withdrawn item(s) '
                    f'reported by collector, queued for rollback push')

    return result


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
    logger.info(f'[链匹配] snap_chain={snap_chain}, rule_chains={rule_chains}')

    if not snap_chain:
            logger.warning(f'[链匹配] snap_chain 为空，无法匹配')
            return False

    if not rule_chains:
        logger.info(f'[链匹配] rule_chains 为空，匹配全部')
        return True  # 无 chains 条件 = 全部匹配

    for entry in rule_chains:
        rc = entry.get('chain', [])
        mode = entry.get('match', 'leaf')
        logger.info(f'[链匹配] 尝试匹配: rc={rc}, mode={mode}')

        if not rc:
            logger.info(f'[链匹配] ⏭️ 跳过空的 rule chain')
            continue

        if mode == 'leaf':
            if snap_chain == rc:
                logger.info(f'[链匹配] ✅ leaf 匹配成功: {snap_chain} == {rc}')
                return True
            else:
                logger.info(f'[链匹配] ❌ leaf 不匹配: {snap_chain} != {rc}')
        elif mode == 'subtree':
            # subtree: snap_chain 的前缀必须等于 rule_chain
            if len(snap_chain) >= len(rc) and snap_chain[:len(rc)] == rc:
                logger.info(f'[链匹配] ✅ subtree 匹配成功: {snap_chain[:len(rc)]} == {rc}')
                return True
            else:
                logger.info(f'[链匹配] ❌ subtree 不匹配: {snap_chain[:len(rc)]} != {rc}')

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
    logger.info(f'[订阅匹配] 规则: {rule.get("name")}, 待匹配包数: {len(new_items)}')
    conditions = rule.get('filter_conditions', {})
    logger.info(f'[订阅匹配] 条件: {conditions}')

    # 校验 valid_until：过期规则不匹配任何新包
    valid_until = rule.get('valid_until', '')
    if valid_until:
        from datetime import datetime, timezone
        try:
            expiry = datetime.fromisoformat(valid_until.replace(' ', 'T'))
            # Compat: front-end may pass naive ISO string (no tzinfo). Treat
            # naive as UTC so legacy rules with '2020-01-01T00:00:00'-style
            # values still expire correctly instead of silently matching all
            # (TypeError on naive vs tz-aware comparison was swallowed by the
            # bare except below).
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expiry:
                return []  # 规则已过期，跳过
        except (ValueError, TypeError):
            pass  # 格式错误视为不限制

    # 3-tuple inputs already carry one chain event from the detector. Legacy
    # 2-tuple inputs still need to resolve all chains from content_sources.
    typed_items = []
    for item in new_items:
        if len(item) == 3:
            sid, snap, chain = item
            typed_items.append((sid, snap, chain))
        elif len(item) == 2:
            sid, snap = item
            typed_items.append((sid, snap, None))
        else:
            logger.warning(f'[订阅匹配] 忽略非法 item 长度: {len(item)}')

    try:
        from src.core.scheduler import _get_chain as _resolve_chain
    except (ImportError, Exception):
        logger.warning('scheduler._get_chain not available, falling back to legacy filter')
        _resolve_chain = None

    def _chains_for(snap, pre_chain=None):
        if isinstance(pre_chain, list) and pre_chain:
            return [pre_chain]
        if not _resolve_chain:
            return []
        chains = _resolve_chain(
            snap.get('source_id', 0),
            snap.get('source_url', ''),
            snap.get('path_id'),
        )
        if not isinstance(chains, list):
            return [chains] if chains else []
        return chains

    chains = conditions.get('chains', []) if isinstance(conditions, dict) else []
    has_chain_conditions = isinstance(conditions, dict) and 'chains' in conditions
    if not conditions:
        # Empty conditions means match all, one event per resolved chain.
        matched = []
        for sid, snap, pre_chain in typed_items:
            resolved = _chains_for(snap, pre_chain)
            if resolved:
                matched.extend((sid, snap, chain) for chain in resolved)
            else:
                # Preserve the old fallback for legacy callers when no chain
                # can be resolved; new events normally always have a chain.
                matched.append((sid, snap, None))
        return matched

    if not has_chain_conditions:
        # Legacy conditions without a `chains` key remain unsupported. This
        # preserves the existing products/versions/package_types behavior.
        logger.info(f'[订阅匹配] 规则 {rule.get("name")} 无 chains 条件,不匹配任何包')
        return []

    if chains:
        urgency = conditions.get('urgency', [])
        keywords = conditions.get('keywords', [])
        matched = []
        for sid, snap, pre_chain in typed_items:
            logger.info(f'[订阅匹配] 检查包: {snap.get("file_name")}')
            snap_chains = _chains_for(snap, pre_chain)
            logger.info(f'[订阅匹配] snap_chains: {snap_chains}')

            # One physical snapshot may produce multiple matching chain
            # events. A chain is emitted at most once per rule.
            emitted = set()
            for sc in snap_chains:
                if not isinstance(sc, list) or not sc:
                    continue
                if tuple(sc) in emitted or not _chain_matches(sc, chains):
                    continue
                emitted.add(tuple(sc))

                # Chain match first, then the shared package-level filters.
                if urgency and snap.get('urgency') not in urgency:
                    logger.info(f'[订阅匹配] ❌ urgency 不匹配: {snap.get("urgency")} 不在 {urgency} 中')
                    continue
                if keywords:
                    desc = snap.get('description_raw', '')
                    if not any(kw.lower() in desc.lower() for kw in keywords):
                        logger.info(f'[订阅匹配] ❌ keywords 不匹配: {keywords} 中')
                        continue

                logger.info(f'[订阅匹配] ✅ 全部条件匹配，加入结果')
                matched.append((sid, snap, sc))

        logger.info(f'[订阅匹配] 规则 {rule.get("name")} 匹配结果: {len(matched)} 个包')
        return matched

    # chains 字段为空/缺失时,旧格式不匹配任何新包；保留该兼容行为。
    logger.info(f'[订阅匹配] 规则 {rule.get("name")} 无 chains 条件,不匹配任何包')
    return []


def _cst_to_utc_str(target: datetime) -> str:
    """业务边界按 UTC+8 计算，写回 DB 一律转 UTC。"""
    return to_utc(target).replace(tzinfo=None).strftime(DB_FMT)


def compute_push_time(delay_days: int) -> str:
    """Compute the push_after timestamp for a delayed push (unit: days)."""
    from src.core.timeutil import utc_now
    if delay_days <= 0:
        return utc_now().strftime(DB_FMT)
    target = utc_now() + timedelta(days=delay_days)
    return target.strftime(DB_FMT)


def is_quiet_time(rule: dict) -> bool:
    """Check if current time falls within the rule's quiet period (UTC+8 wall clock)."""
    quiet_start = rule.get('quiet_start', '')
    quiet_end = rule.get('quiet_end', '')
    if not quiet_start or not quiet_end:
        return False

    now = cst_now().strftime('%H:%M')
    if quiet_start <= quiet_end:
        return quiet_start <= now <= quiet_end
    else:
        # Crosses midnight (e.g., 22:00 - 08:00)
        return now >= quiet_start or now <= quiet_end


def is_window_time(rule: dict) -> bool:
    """Check if current time falls within the rule's push window (UTC+8 wall clock).

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

    now = cst_now()
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
    Window is interpreted as UTC+8; the returned push time is UTC (DB convention).
    """
    wc = rule.get('window_config') or {}
    # Backward compat: delay_strategy='window' without window_config gets default window
    if not wc and rule.get('delay_strategy') == 'window':
        wc = {'days': [1, 2, 3, 4, 5], 'start': '09:00', 'end': '18:00'}
    days = wc.get('days', [])
    start = wc.get('start', '')
    if not start:
        from src.core.timeutil import utc_now
        return utc_now().strftime(DB_FMT)

    now = cst_now()
    current_time = now.strftime('%H:%M')

    if not days:
        # Digest mode (days=[]): only time-of-day restriction, find next opening
        if current_time < start:
            # Window opens today
            target = now.replace(hour=int(start.split(':')[0]), minute=int(start.split(':')[1]), second=0, microsecond=0)
        else:
            # Window already passed today, open tomorrow at start
            target = now.replace(hour=int(start.split(':')[0]), minute=int(start.split(':')[1]), second=0, microsecond=0) + timedelta(days=1)
        return _cst_to_utc_str(target)

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
    return _cst_to_utc_str(target)


def check_min_interval(rule: dict, product_name: str) -> bool:
    """Check if minimum interval has passed since last notification for this product/rule.

    Returns True if OK to notify, False if too soon.
    """
    min_hours = rule.get('min_interval_hours', 0)
    if min_hours <= 0:
        return True

    from src.core.timeutil import utc_now
    from src.models.database import query

    cutoff = (utc_now() - timedelta(hours=min_hours)).strftime(DB_FMT)
    rows = query(
        """SELECT COUNT(*) as cnt FROM delivery_log dl
           JOIN snapshots s ON dl.snapshot_id = s.id
           WHERE s.product_name = ? AND dl.sent_at > ? AND dl.delivery_status = 'sent'""",
        (product_name, cutoff)
    )
    count = rows[0]['cnt'] if rows else 0
    return count == 0
