"""System event handler — record events and send notifications.

Called by:
- router.py: emit_push_summary() once per cycle (aggregated push report)
- scheduler.py: emit_collection_summary() on collection complete
- scheduler.py: emit_session_error() on session heartbeat failure
- log_scanner.py: emit_log_error() on log anomaly
- collectors/nsfocus.py: emit_network_error() on network error

Deprecated:
- emit_push() 单条推送成功/失败通知 — 2026-07-22 移除前端选项,后端函数保留为
  no-op 占位,避免外部 import 报错。如果未来需要单条通知,可以从 router.py
  _send_immediate 成功后调 emit_push(...) 重新启用。
"""

import json
from datetime import datetime, timezone

import requests

from src.core.logger import get_logger
from src.models.event_log import (
    log_event, get_config, get_notify_channel, is_event_enabled
)
from src.notifiers.base import _utc_to_cst_display

logger = get_logger('event_handler')

_MODE_LABELS = {'quick': 'Quick Scan', 'full': 'Full Scan', 'delta': 'Delta Scan'}

_PKG_TYPE_CN = {
    'sys': '系统', 'rule': '规则', 'nti': '威胁情报', 'av': '病毒库',
    'apprule': '应用规则', 'url': 'URL库', 'wcs': '恶意站点', 'judge': '研判',
    'geo': '地理库', 'interface': '接口', 'special': '特殊', 'other': '其他',
    'merge': '合并', 'client': '客户端', 'av_stream': '流式病毒库',
}


def _get_notifier(channel: dict):
    """获取渠道对应的notifier实例"""
    from src.notifiers.router import NOTIFIERS
    return NOTIFIERS.get(channel.get('type'))


def _build_push_message(snap: dict, rule: dict, success: bool,
                        error: str = None, is_rollback: bool = False) -> str:
    """构建推送通知消息内容"""
    lines = []
    if is_rollback:
        lines.append("【包回退通知】" if success else "【回退推送失败】")
    elif success:
        lines.append("【产品更新推送】")
    else:
        lines.append("【推送失败】")

    lines.append(f"客户：{rule.get('customer_name', rule.get('name', '未知'))}")
    lines.append(f"规则：{rule.get('name', '未知')}")

    if success:
        # 产品概述
        product_name = snap.get('product_name', '')
        version_branch = snap.get('version_branch', '')
        package_type = snap.get('package_type', '')
        file_name = snap.get('file_name', '')

        if product_name:
            lines.append(f"产品：{product_name}")
        if version_branch:
            lines.append(f"版本：{version_branch}")
        if package_type:
            lines.append(f"类型：{package_type}")
        if file_name:
            lines.append(f"包名：{file_name}")
    else:
        lines.append(f"失败原因：{error or '未知错误'}")

    return '\n'.join(lines)


def emit_push(snap: dict, rule: dict, success: bool, error: str = None,
              is_rollback: bool = False):
    """[DEPRECATED 2026-07-22] 单条推送成功/失败通知 — 前端已合并到 push_summary。

    调用方无(router.py 不再触发),保留函数体以防外部 import 调用,但不执行任何操作。
    推送结果请用 emit_push_summary() (router.py cycle 末调一次)。
    """
    logger.debug(f'emit_push() called but deprecated: snap={snap.get("file_name")} success={success}')
    return

    # 检查是否启用
    if not is_event_enabled(event_type):
        return

    config = get_config()
    channel = get_notify_channel()
    if not channel:
        logger.warning(f'Event {event_type}: no notification channel configured')
        return

    # 记录到日志表
    log_event(
        event_type=event_type,
        severity='INFO' if success else 'WARNING',
        product_name=snap.get('product_name'),
        source_url=snap.get('source_url'),
        rule_id=rule.get('id'),
        channel_id=channel.get('id') if channel else None,
        channel_type=channel.get('type') if channel else None,
        customer_id=rule.get('customer_id'),
        is_rollback=is_rollback,
        message={
            'success': success,
            'error': error,
            'is_rollback': is_rollback,
            'file_name': snap.get('file_name'),
            'product_name': snap.get('product_name'),
            'version_branch': snap.get('version_branch'),
            'package_type': snap.get('package_type'),
        }
    )

    # 发送通知
    message_text = _build_push_message(snap, rule, success, error, is_rollback)
    notifier = _get_notifier(channel)
    if notifier:
        from src.notifiers.base import NotificationMessage
        msg = NotificationMessage(
            title='绿盟监控 - 推送通知',
            product_name=snap.get('product_name', ''),
            version_branch=snap.get('version_branch', ''),
            package_type=snap.get('package_type', ''),
            file_name=snap.get('file_name', ''),
            package_version=snap.get('package_version', ''),
            md5_hash=snap.get('md5_hash', ''),
            description_full=message_text,
            is_rollback=is_rollback,
        )
        try:
            result = notifier.send(msg, channel.get('config', {}))
            logger.info(f'Event notification sent: {event_type}, result={result.success}')
        except Exception as e:
            logger.error(f'Failed to send event notification: {e}')


def _build_push_summary_message(summaries: list) -> str:
    """Build a consolidated push summary message from all channel summaries."""
    total_all = sum(s['total'] for s in summaries)
    success_all = sum(s['success'] for s in summaries)
    failed_all = sum(s['failed'] for s in summaries)

    lines = [
        f"【推送汇总】共 {total_all} 个包 | 成功 {success_all} | 失败 {failed_all}",
    ]

    for s in summaries:
        rule_name = s.get('rule_name', '')
        customer_name = s.get('customer_name', '')
        channel_type = s.get('channel_type', '')
        channel_name = s.get('channel_name', '')
        pushed_at = s.get('pushed_at', '')
        # Format pushed_at to CST
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(pushed_at.replace('Z', '+00:00'))
            pushed_cst = dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            pushed_cst = pushed_at

        success = s.get('success', 0)
        failed = s.get('failed', 0)

        # Channel label
        channel_label = {
            'wecom': '企微', 'dingtalk': '钉钉', 'feishu': '飞书',
            'email': '邮件', 'apprise': 'Apprise'
        }.get(channel_type, channel_type)
        channel_display = f"{channel_label} {channel_name}" if channel_name else channel_label

        lines.append('─' * 30)
        lines.append(f"规则：{rule_name} | 客户：{customer_name} | 渠道：{channel_display}")
        lines.append(f"时间：{pushed_cst} | 成功：{success} | 失败：{failed}")

        items = s.get('items', [])
        if items:
            for item in items:
                fn = item.get('file_name', '')
                ok = '✓' if item.get('success') else '✗'
                err = item.get('error', '')
                lines.append(f"  {ok} {fn}" + (f" | {err}" if err else ""))

    return '\n'.join(lines)


def emit_push_summary(summaries: list):
    """Send an aggregated push summary to the system event notification channel.
    Called once per collection cycle via _emit_push_summary in router.py.

    2026-07-22: 0 推送也发(路径验证 + 用户反馈)。
    summaries=[] 时发一条"本次无推送"短消息,有 summaries 时跟以前一样。
    """
    event_type = 'push_summary'
    if not is_event_enabled(event_type):
        return

    # 0 推送:发一条简短通知(让用户知道路径在跑)
    if not summaries:
        message_text = '【推送汇总】本次无推送(0 个 NEW 包)'
    else:
        message_text = _build_push_summary_message(summaries)

    # 统计数(0 推送时都是 0)
    if summaries:
        total_all = sum(s['total'] for s in summaries)
        success_all = sum(s['success'] for s in summaries)
        failed_all = sum(s['failed'] for s in summaries)
    else:
        total_all = success_all = failed_all = 0

    # Send to notification channel（不依赖 DB 写入）
    channel = get_notify_channel()
    if not channel:
        return

    notifier = _get_notifier(channel)
    if notifier:
        from src.notifiers.base import NotificationMessage
        msg = NotificationMessage(
            title='绿盟监控 - 推送汇总',
            description_full=message_text,
        )
        try:
            result = notifier.send(msg, channel.get('config', {}))
            if result.success:
                logger.info(f'Push summary sent: total={total_all}, success={success_all}, failed={failed_all}')
            else:
                logger.error(f'Push summary failed: {result.error_message}')
        except Exception as e:
            logger.error(f'Failed to send push summary: {e}')


def emit_cleanup_summary(summary: dict):
    """清理任务执行完成后调用

    summary 字段（来自 _run_db_cleanup）:
      - started_at, finished_at: UTC ISO
      - ok: bool，是否全部步骤成功
      - steps: [{name, ok, detail, deleted/before/after/per_group/...}, ...]
      - errors: [str, ...]
    """
    event_type = 'cleanup_summary'

    if not is_event_enabled(event_type):
        return

    # 防重：最近 1 分钟内已有相同事件跳过（cron 触发，可能与其他 cron 撞时间）
    try:
        from src.models.database import query
        rows = query(
            "SELECT id FROM system_event_log WHERE event_type=? "
            "AND created_at>=datetime('now','-1 minute') LIMIT 1",
            (event_type,)
        )
        if rows:
            logger.debug(f'Skip duplicate {event_type} (recent event already sent)')
            return
    except Exception:
        pass

    steps = summary.get('steps', [])
    errors = summary.get('errors', [])
    ok = summary.get('ok', len(errors) == 0)

    # 1) 写 system_event_log（按系统事件配置：失败时 WARNING，否则 INFO）
    severity = 'INFO' if ok else 'WARNING'
    try:
        log_event(
            event_type=event_type,
            severity=severity,
            product_name='',
            message={
                'started_at': summary.get('started_at', ''),
                'finished_at': summary.get('finished_at', ''),
                'ok': ok,
                'steps': steps,
                'errors': errors,
            },
        )
    except Exception as e:
        logger.warning(f'Failed to log cleanup_summary event: {e}')

    # 2) 按配置发通知
    channel = get_notify_channel()
    if not channel:
        return

    from src.notifiers.base import _utc_to_cst_display
    cst_now = _utc_to_cst_display(
        summary.get('finished_at', datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S'))
    )

    # 渲染消息体
    icon = '🧹' if ok else '⚠️'
    title = f'{icon} 清理任务{"完成" if ok else "部分失败"}'
    lines = [title, f"执行时间：{cst_now}", ""]

    # 步骤详情
    name_cn = {
        'heartbeat_log': 'heartbeat_log',
        'audit_log': 'audit_log (30天)',
        'delivery_log': 'delivery_log (90天)',
        'snapshots': 'snapshots (按 path_id 分组)',
    }
    for step in steps:
        nm = name_cn.get(step['name'], step['name'])
        mark = '✓' if step['ok'] else '✗'
        lines.append(f"{mark} {nm}: {step['detail']}")

    if errors:
        lines.append('')
        lines.append('错误:')
        for err in errors:
            lines.append(f"  - {err}")

    message_text = '\n'.join(lines)

    notifier = _get_notifier(channel)
    if notifier:
        from src.notifiers.base import NotificationMessage
        msg = NotificationMessage(
            title='绿盟监控 - 数据清理汇总',
            product_name='',
            version_branch='',
            package_type='',
            file_name='',
            package_version='',
            md5_hash='',
            description_full=message_text,
        )
        try:
            result = notifier.send(msg, channel.get('config', {}))
            if result.success:
                logger.info(f'Cleanup summary notification sent: ok={ok}')
            else:
                logger.error(f'Cleanup summary notification failed: {result.error_message}')
        except Exception as e:
            logger.error(f'Failed to send cleanup summary: {e}')


def _fmt_duration(seconds: int) -> str:
    """Format duration in human-readable form."""
    if seconds < 60:
        return f'{seconds}秒'
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f'{m}分{s}秒'
    else:
        total_m = seconds // 60
        h = total_m // 60
        m = total_m % 60
        return f'{h}小时{m}分'


def _build_summary_message(summary: dict, mode: str) -> str:
    """Build collection summary notification message.

    结构:
      [头部] icon + mode + 总览 + 采集开始/结束 + 总耗时
      [采集情况段]  (有内容才出现)
         - 有新包:产品 → 类型 列表
         - 无新包但有错误:错误摘要
         - 0 items:统计 (0 空 / N 正常 / 共 M 产品)
      [推送段]  (有推送才出现)
         - 推送开始/结束 + 总耗时
         - 推送结果汇总 (成功/失败/给客户)
    """
    total_new = summary.get('total_new', 0)
    errors = summary.get('errors', [])
    products = summary.get('products', {})
    duration = summary.get('duration_s', 0)
    total_products = summary.get('products_total', len(products))

    # Determine icon and label
    if total_new > 0:
        icon = '🔔'
        severity = 'normal'
    elif errors:
        icon = '⚠️'
        severity = 'high'
    else:
        icon = 'ℹ️'
        severity = 'normal'

    mode_label = _MODE_LABELS.get(mode, mode)

    # ── 时间窗口(采集开始/结束 + 总耗时) ──
    started_at = summary.get('started_at', '')
    finished_at = summary.get('finished_at', '')
    from datetime import datetime, timezone, timedelta
    tz_cst = timezone(timedelta(hours=8))

    def _to_cst_str(iso_str):
        if not iso_str:
            return ''
        try:
            ts = iso_str.replace('Z', '+00:00')
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(tz_cst).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return iso_str

    start_cst = _to_cst_str(started_at)
    end_cst = _to_cst_str(finished_at)

    # ── 头部 ──
    lines = [f"{icon} {mode_label} 完成 | 新增 {total_new} 个包"]
    if start_cst:
        lines.append(f"采集开始：{start_cst}（CST）")
    if end_cst:
        lines.append(f"采集结束：{end_cst}（CST）")
    lines.append(f"耗时：{_fmt_duration(duration)}")

    # ── 采集情况段(独立小节) ──
    has_collection_section = total_new > 0 or errors or summary.get('zero_items')
    if has_collection_section:
        # 段标题 + 段间空一行
        lines.append('')
        lines.append('─' * 30)
        lines.append('📊 采集情况')

        if total_new > 0:
            lines.append('')
            lines.append(f'新增包详情（{total_new} 个）')
            for pname, pdata in sorted(products.items()):
                new_count = pdata.get('new', 0)
                if new_count == 0:
                    continue
                by_type = pdata.get('by_type', {})
                type_lines = []
                for pkg_type, count in sorted(by_type.items(), key=lambda x: -x[1]):
                    label = _PKG_TYPE_CN.get(pkg_type, pkg_type)
                    type_lines.append(f'{label}：{count}个包')
                type_str = '，'.join(type_lines)
                lines.append(f'  📦 {pname}')
                lines.append(f'     └─ {type_str}')

        elif errors:
            lines.append('')
            lines.append('错误摘要：')
            for err in errors[:5]:
                msg = err[:120] if len(err) > 120 else err
                lines.append(f'  • {msg}')

        # 0 items 诊断(quick 模式)
        zero_items = summary.get('zero_items', []) or []
        if zero_items:
            changed_diag = [d for d in zero_items if d.get('changed_urls')]
            stable_diag = [d for d in zero_items if not d.get('changed_urls')]
            lines.append('')
            if changed_diag:
                lines.append(
                    f'⚠️ {len(zero_items)} 个产品本次 0 items,'
                    f'其中 {len(changed_diag)} 个页面 hash 发生变化:'
                )
                for d in changed_diag:
                    lines.append(f'  • {d["name"]}')
                    for c in d['changed_urls'][:5]:
                        short = c['url'] if len(c['url']) <= 50 else '...' + c['url'][-47:]
                        if c.get('first_seen'):
                            lines.append(f'    - {short}')
                            lines.append(f'      hash: (首次记录,本次为基准)')
                        else:
                            lines.append(f'    - {short}')
                            lines.append(f'      hash: {c["prev"][:8]}... → {c["hash"][:8]}...')
                    if len(d['changed_urls']) > 5:
                        lines.append(f'    ... 共 {len(d["changed_urls"])} 个 URL 变化')
            else:
                # 改为:总产品 80 / 0 items 12 / 正常 68 的拆解
                normal_count = total_products - len(zero_items)
                lines.append(
                    f'ℹ️ {len(zero_items)} 个产品本次 0 items'
                    f' / {normal_count} 正常 (共 {total_products} 产品)'
                )

    # ── 推送段(独立小节) ──
    push_summaries = summary.get('push_summaries') or []
    if push_summaries:
        # 段标题 + 段间空一行
        lines.append('')
        lines.append('─' * 30)
        lines.append('📨 推送情况')

        # 推送时间窗口
        all_pushed = []
        for s in push_summaries:
            for item in s.get('items', []):
                pa = item.get('pushed_at', '')
                if pa:
                    all_pushed.append(pa)
        if all_pushed:
            push_start_utc = min(all_pushed)
            push_end_utc = max(all_pushed)
            try:
                start_dt = datetime.fromisoformat(push_start_utc.replace('Z', '+00:00'))
                end_dt = datetime.fromisoformat(push_end_utc.replace('Z', '+00:00'))
                start_cst_push = start_dt.astimezone(tz_cst).strftime('%Y-%m-%d %H:%M:%S')
                end_cst_push = end_dt.astimezone(tz_cst).strftime('%Y-%m-%d %H:%M:%S')
                push_seconds = int((end_dt - start_dt).total_seconds())
                lines.append('')
                lines.append(f'推送开始：{start_cst_push}（CST）')
                lines.append(f'推送结束：{end_cst_push}（CST）')
                lines.append(f'耗时：{_fmt_duration(push_seconds)}')
            except Exception:
                pass

        # 推送结果汇总
        total_push = sum(s.get('total', 0) for s in push_summaries)
        total_success = sum(s.get('success', 0) for s in push_summaries)
        total_failed = sum(s.get('failed', 0) for s in push_summaries)

        lines.append('')
        lines.append(f'推送结果汇总：成功 {total_success} / 失败 {total_failed}（共 {total_push} 条）')

        # 按 (客户 × 产品) 聚合
        cust_prod = {}
        for s in push_summaries:
            cust = s.get('customer_name', '') or '(未指定客户)'
            for item in s.get('items', []):
                prod = item.get('product_name', '') or '(未知产品)'
                cust_prod.setdefault(cust, {}).setdefault(prod, 0)
                cust_prod[cust][prod] += 1

        lines.append('')
        lines.append('推送明细：')
        for cust in sorted(cust_prod.keys()):
            prod_lines = []
            for prod in sorted(cust_prod[cust].keys()):
                prod_lines.append(f'{prod} ×{cust_prod[cust][prod]}')
            lines.append(f'  📤 {cust}：' + '，'.join(prod_lines))

    return '\n'.join(lines)


def emit_collection_summary(summary: dict, mode: str):
    """采集任务入库完成后调用"""
    event_type = 'collection_summary'

    if not is_event_enabled(event_type):
        return

    # 防重：最近 5 分钟内已有相同 event_type 的记录则跳过
    # 注意：不再写 DB，只依赖通知渠道的去重能力
    try:
        from src.models.database import query
        rows = query(
            "SELECT id FROM system_event_log WHERE event_type=? AND created_at>=datetime('now','-5 minutes') LIMIT 1",
            (event_type,)
        )
        if rows:
            logger.debug(f'Skip duplicate collection_summary (recent event already sent)')
            return
    except Exception:
        pass

    total_new = summary.get('total_new', 0)
    total_rollback = summary.get('total_rollback', 0)
    errors = summary.get('errors', [])
    products = summary.get('products', {})
    duration = summary.get('duration_s', 0)

    # 构建消息
    message_text = _build_summary_message(summary, mode)

    channel = get_notify_channel()
    if not channel:
        return

    # 发送通知（不依赖 DB 写入）
    notifier = _get_notifier(channel)
    if notifier:
        from src.notifiers.base import NotificationMessage
        msg = NotificationMessage(
            title=f'绿盟监控 - 采集任务完成',
            product_name='',
            version_branch='',
            package_type='',
            file_name='',
            package_version='',
            md5_hash='',
            description_full=message_text,
        )
        try:
            result = notifier.send(msg, channel.get('config', {}))
            if result.success:
                logger.info(f'Collection summary notification sent: {mode}, new={total_new}')
            else:
                logger.error(f'Collection summary notification failed: {result.error_message}')
        except Exception as e:
            logger.error(f'Failed to send collection summary: {e}')


def _write_push_summary_from_delivery_log(finished_at: str = ''):
    """采集结束时，将本次推送结果合并写入 system_event_log，不发通知。

    只记录到日志表，供前端事件列表查看。是否发送企微通知由系统事件配置决定。

    注意：此函数已废弃。推送汇总已由 emit_push_summary(summaries: list) 统一处理
    （包含写日志 + 发企微通知），不再需要此版本。
    """
    from src.models.event_log import is_event_enabled, get_notify_channel, log_event
    from src.models.database import query

    event_type = 'push_summary'

    if not is_event_enabled(event_type):
        return

    # 查 delivery_log 中本次采集窗口内的推送记录
    # finished_at 是采集结束时间（UTC ISO 格式），取前后 5 分钟窗口
    cutoff = finished_at[:19] if finished_at else datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    rows = query(
        """SELECT dl.snapshot_id, dl.rule_id, dl.channel_id, dl.channel_type,
                  dl.channel_name, dl.customer_id, dl.delivery_status,
                  dl.error_message, dl.sent_at,
                  s.file_name, s.product_name, s.version_branch, s.package_type,
                  s.package_version,
                  cu.name as customer_name, cu.email as customer_email,
                  sr.name as rule_name
           FROM delivery_log dl
           JOIN snapshots s ON dl.snapshot_id = s.id
           LEFT JOIN customers cu ON dl.customer_id = cu.id
           LEFT JOIN subscription_rules sr ON dl.rule_id = sr.id
           WHERE dl.sent_at >= datetime(?)
             AND dl.delivery_status IN ('sent', 'failed')
           ORDER BY dl.sent_at DESC""",
        (cutoff,)
    )

    if not rows:
        return

    total = len(rows)
    success_count = sum(1 for r in rows if r['delivery_status'] == 'sent')
    failed_count = total - success_count

    log_event(
        event_type=event_type,
        severity='WARNING' if failed_count > 0 else 'INFO',
        message={
            'total': total,
            'success': success_count,
            'failed': failed_count,
            'deliveries': [
                {
                    'file_name': r['file_name'],
                    'product_name': r.get('product_name', ''),
                    'customer_name': r.get('customer_name', ''),
                    'channel_name': r.get('channel_name', ''),
                    'channel_type': r.get('channel_type', ''),
                    'customer_email': r.get('customer_email', ''),
                    'sent_at': r.get('sent_at', ''),
                    'status': r['delivery_status'],
                    'error': r.get('error_message', ''),
                }
                for r in rows
            ],
        }
    )
    logger.info(f'Push summary logged: total={total}, success={success_count}, failed={failed_count}')


def emit_session_error(username: str, product_name: str, reason: str, source: str = 'session'):
    """Session心跳异常或系统健康检查告警时调用。
    source: 'session' - 具体用户Session异常
            'health_check' - 调度器健康检查失败
    """
    event_type = 'session_error'

    if not is_event_enabled(event_type):
        return

    channel = get_notify_channel()
    if not channel:
        return

    from src.notifiers.base import _utc_to_cst_display
    cst_now = _utc_to_cst_display(datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S'))

    if source == 'health_check':
        title = '【系统健康检查告警】'
        suggestion = '建议：请检查调度器是否正常运行'
    else:
        title = '【Session 异常】'
        suggestion = '建议：请更新该用户的 Session'

    message_text = '\n'.join([
        title,
        f"用户名：{username}",
        f"异常原因：{reason}",
        f"检测时间：{cst_now}",
        "",
        suggestion,
    ])

    # 记录到日志表
    log_event(
        event_type=event_type,
        severity='CRITICAL',
        product_name=product_name,
        message={
            'username': username,
            'reason': reason,
        }
    )

    # 发送通知
    notifier = _get_notifier(channel)
    if notifier:
        from src.notifiers.base import NotificationMessage, _format_markdown_bodies
        msg = NotificationMessage(
            title='绿盟监控 - Session 异常',
            product_name='',
            version_branch='',
            package_type='',
            file_name='',
            package_version='',
            md5_hash='',
            description_full=message_text,
        )
        try:
            bodies = _format_markdown_bodies(msg, skip_empty_meta=True)
            name = channel.get('name', '')
            for i, body in enumerate(bodies):
                payload = {'msgtype': 'markdown', 'markdown': {'content': body}}
                resp = requests.post(channel.get('config', {}).get('webhook_url'), json=payload, timeout=10)
                result = resp.json()
                if result.get('errcode') != 0:
                    logger.error(f'Failed to send session error notification: {result.get("errmsg", "unknown")}')
                    return
            logger.info(f'Event notification sent: {event_type}')
        except Exception as e:
            logger.error(f'Failed to send session error notification: {e}')


def emit_session_expired(session_id: int, purpose: str, collect_mode: str, reason: str, source: str = 'pre-flight'):
    """Session预检发现失效时直接发送通知（不写DB）。

    source: 'pre-flight' - 采集前预检发现的失效
            'heartbeat'  - 周期心跳发现的失效
    """
    event_type = 'session_expired'
    if not is_event_enabled(event_type):
        return

    channel = get_notify_channel()
    if not channel:
        return

    from src.notifiers.base import _utc_to_cst_display
    cst_now = _utc_to_cst_display(datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S'))

    purpose_cn = '采集' if purpose == 'collect' else '探测'
    collect_tag = f'/[{collect_mode}]' if purpose == 'collect' and collect_mode else ''

    message_text = '\n'.join([
        '【Session 失效】',
        f"Session ID：{session_id}",
        f"用途：{purpose_cn}{collect_tag}",
        f"失效原因：{reason}",
        f"检测时间：{cst_now}",
        "",
        "建议：请重新登录获取新的 Session cookie 并更新",
    ])

    notifier = _get_notifier(channel)
    if notifier:
        from src.notifiers.base import NotificationMessage, _format_markdown_bodies
        msg = NotificationMessage(
            title='绿盟监控 - Session 失效',
            product_name='',
            version_branch='',
            package_type='',
            file_name='',
            package_version='',
            md5_hash='',
            description_full=message_text,
        )
        try:
            bodies = _format_markdown_bodies(msg, skip_empty_meta=True)
            for i, body in enumerate(bodies):
                payload = {'msgtype': 'markdown', 'markdown': {'content': body}}
                resp = requests.post(channel.get('config', {}).get('webhook_url'), json=payload, timeout=10)
                result = resp.json()
                if result.get('errcode') != 0:
                    logger.error(f'Failed to send session_expired notification: {result.get("errmsg", "unknown")}')
                    return
            logger.info(f'Session expired notification sent: sid={session_id} ({purpose}{collect_tag})')
        except Exception as e:
            logger.error(f'Failed to send session_expired notification: {e}')


def emit_log_error(log_file: str, error_type: str, keyword: str,
                   context: str, line_number: int = None):
    """日志扫描发现异常时调用"""
    event_type = 'log_error'

    if not is_event_enabled(event_type):
        return

    channel = get_notify_channel()
    if not channel:
        return

    from src.notifiers.base import _utc_to_cst_display
    cst_now = _utc_to_cst_display(datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S'))

    message_text = '\n'.join([
        "【日志异常检测】",
        f"扫描时间：{cst_now}",
        f"异常类型：{error_type}",
        f"关键词：{keyword}",
        f"日志文件：{log_file}",
        "",
        "上下文：",
        context[:500] if context else '—',
    ])

    # 发送通知
    notifier = _get_notifier(channel)
    if notifier:
        from src.notifiers.base import NotificationMessage, _format_markdown_bodies
        msg = NotificationMessage(
            title='绿盟监控 - 日志异常',
            product_name='',
            version_branch='',
            package_type='',
            file_name='',
            package_version='',
            md5_hash='',
            description_full=message_text,
        )
        try:
            bodies = _format_markdown_bodies(msg, skip_empty_meta=True)
            for i, body in enumerate(bodies):
                payload = {'msgtype': 'markdown', 'markdown': {'content': body}}
                resp = requests.post(channel.get('config', {}).get('webhook_url'), json=payload, timeout=10)
                result = resp.json()
                if result.get('errcode') != 0:
                    logger.error(f'Failed to send log error notification: {result.get("errmsg", "unknown")}')
                    return
            logger.info(f'Event notification sent: {event_type}')
        except Exception as e:
            logger.error(f'Failed to send log error notification: {e}')


def emit_network_error(errors: list, source: str = 'collector'):
    """采集过程遇到网络错误时汇总通知（不写数据库）。

    errors: list of dict, each with keys: product_name, url, error_msg
    source: 'collector' - nsfocus.py 单个URL采集失败
            'scheduler'   - scheduler.py 整产品采集异常
    """
    if not errors:
        return

    event_type = 'log_error'

    if not is_event_enabled(event_type):
        return

    channel = get_notify_channel()
    if not channel:
        return

    from src.notifiers.base import _utc_to_cst_display
    cst_now = _utc_to_cst_display(datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S'))

    lines = ["【采集网络错误】", f"检测时间：{cst_now}", ""]
    for i, e in enumerate(errors[:10], 1):
        # error_time 是 log 行的实际时间戳(CST),不是告警生成时间
        # 解决"告警延迟 8h 后看不出错误什么时候真发生"的问题
        err_time = e.get('error_time', '').replace('T', ' ')
        time_str = f"  时间: {err_time}" if err_time else ""
        lines.append(f"{i}. 产品:{e['product_name']}{time_str}")
        lines.append(f"   URL:{e['url']}")
        lines.append(f"   错误:{e['error_msg'][:100]}")
    if len(errors) > 10:
        lines.append(f"... 还有 {len(errors) - 10} 条错误")

    lines.extend(["", "建议：请检查网络连通性或调整 collect_timeout 配置"])

    # 发送通知（不写 DB）
    notifier = _get_notifier(channel)
    if notifier:
        from src.notifiers.base import NotificationMessage, _format_markdown_bodies
        msg = NotificationMessage(
            title='绿盟监控 - 采集网络错误',
            product_name='',
            version_branch='',
            package_type='',
            file_name='',
            package_version='',
            md5_hash='',
            description_full='\n'.join(lines),
        )
        try:
            bodies = _format_markdown_bodies(msg, skip_empty_meta=True)
            for i, body in enumerate(bodies):
                payload = {'msgtype': 'markdown', 'markdown': {'content': body}}
                resp = requests.post(channel.get('config', {}).get('webhook_url'), json=payload, timeout=10)
                result = resp.json()
                if result.get('errcode') != 0:
                    logger.error(f'Failed to send network error notification: {result.get("errmsg", "unknown")}')
                    return
            logger.info(f'Event notification sent: {event_type}, count={len(errors)}')
        except Exception as e:
            logger.error(f'Failed to send network error notification: {e}')


def emit_login_bruteforce(ip: str, count: int, recent_failures: list):
    """检测到同一 IP 连续登录失败 ≥3 次时调用（由 log_scanner 触发）"""
    event_type = 'login_bruteforce'

    if not is_event_enabled(event_type):
        return

    channel = get_notify_channel()
    if not channel:
        return

    from src.notifiers.base import _utc_to_cst_display
    cst_now = _utc_to_cst_display(datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S'))

    failure_details = '\n'.join([
        f"- {ts} | {details.get('reason', 'unknown')}" + (f" | 用户: {details.get('username', '')}" if details.get('username') else '')
        for ts, details in recent_failures
    ]) or '无详细记录'

    message_text = '\n'.join([
        "【登录暴力破解告警】",
        f"攻击 IP：{ip}",
        f"失败次数：{count} 次",
        f"检测时间：{cst_now}",
        "",
        "最近失败记录：",
        failure_details,
        "",
        "建议：请检查是否有人在尝试暴力破解该账户",
    ])

    # 记录到日志表
    log_event(
        event_type=event_type,
        severity='CRITICAL',
        message={
            'ip': ip,
            'count': count,
            'recent_failures': recent_failures,
        }
    )

    # 发送通知
    notifier = _get_notifier(channel)
    if notifier:
        from src.notifiers.base import NotificationMessage, _format_markdown_bodies
        msg = NotificationMessage(
            title='绿盟监控 - 登录暴力破解告警',
            product_name='',
            version_branch='',
            package_type='',
            file_name='',
            package_version='',
            md5_hash='',
            description_full=message_text,
        )
        try:
            bodies = _format_markdown_bodies(msg, skip_empty_meta=True)
            for i, body in enumerate(bodies):
                payload = {'msgtype': 'markdown', 'markdown': {'content': body}}
                resp = requests.post(channel.get('config', {}).get('webhook_url'), json=payload, timeout=10)
                result = resp.json()
                if result.get('errcode') != 0:
                    logger.error(f'Failed to send login_bruteforce notification: {result.get("errmsg", "unknown")}')
                    return
            logger.info(f'Event notification sent: {event_type}')
        except Exception as e:
            logger.error(f'Failed to send login_bruteforce notification: {e}')