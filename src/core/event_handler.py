"""System event handler — record events and send notifications.

Called by:
- router.py: emit_push() on delivery success/failure
- scheduler.py: emit_collection_summary() on collection complete
- scheduler.py: emit_session_error() on session heartbeat failure
- log_scanner.py: emit_log_error() on log anomaly
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
    """推送成功/失败时调用"""
    event_type = 'push_success' if success else 'push_failed'

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
    """
    if not summaries:
        return

    event_type = 'push_summary'
    if not is_event_enabled(event_type):
        return

    # Build message
    message_text = _build_push_summary_message(summaries)

    total_all = sum(s['total'] for s in summaries)
    success_all = sum(s['success'] for s in summaries)
    failed_all = sum(s['failed'] for s in summaries)

    # Log to DB
    log_event(
        event_type=event_type,
        severity='WARNING' if failed_all > 0 else 'INFO',
        message={
            'total': total_all, 'success': success_all, 'failed': failed_all,
            'summaries': summaries,
        }
    )

    # Send to notification channel
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
            logger.info(f'Push summary sent: total={total_all}, success={success_all}, failed={failed_all}')
        except Exception as e:
            logger.error(f'Failed to send push summary: {e}')


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
    """Build collection summary notification message (three modes)."""
    total_new = summary.get('total_new', 0)
    errors = summary.get('errors', [])
    products = summary.get('products', {})

    started_at = summary.get('started_at', '')
    finished_at = summary.get('finished_at', '') or datetime.utcnow().isoformat()
    duration = summary.get('duration_s', 0)

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
    product_count = len(products)
    lines = [
        f"{icon} {mode_label} 完成 | {product_count}产品 | 新增 {total_new} 个包",
        f"耗时：{_fmt_duration(duration)}",
    ]

    # --- 有新包：按产品→类型两级展开 ---
    if total_new > 0:
        lines.append('─' * 30)
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
            lines.append(f'📦 {pname}')
            lines.append(f'   └─ {type_str}')

    # --- 无新包但有错误 ---
    elif errors:
        lines.append('─' * 30)
        lines.append('错误摘要：')
        for err in errors[:5]:
            # 截断超长错误
            msg = err[:120] if len(err) > 120 else err
            lines.append(f'• {msg}')

    return '\n'.join(lines)


def emit_collection_summary(summary: dict, mode: str):
    """采集任务入库完成后调用"""
    event_type = 'collection_summary'

    if not is_event_enabled(event_type):
        return

    # 防重：最近 5 分钟内已有相同 event_type 的记录则跳过
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

    channel = get_notify_channel()
    if not channel:
        return

    total_new = summary.get('total_new', 0)
    total_rollback = summary.get('total_rollback', 0)
    errors = summary.get('errors', [])
    products = summary.get('products', {})
    duration = summary.get('duration_s', 0)

    # 构建消息
    message_text = _build_summary_message(summary, mode)

    # 记录到日志表
    log_event(
        event_type=event_type,
        severity='WARNING' if errors else 'INFO',
        message={
            'mode': mode,
            'total_new': total_new,
            'total_rollback': total_rollback,
            'duration_s': duration,
            'errors_count': len(errors),
            'products': products,
        }
    )

    # 发送通知
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
                logger.info(f'Event notification sent: {event_type}, result=success')
            else:
                logger.error(f'Event notification failed: {event_type}, error={result.error_message}')
        except Exception as e:
            logger.error(f'Failed to send event notification: {e}')


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

    # 记录到日志表
    log_event(
        event_type=event_type,
        severity='CRITICAL',
        message={
            'log_file': log_file,
            'error_type': error_type,
            'keyword': keyword,
            'context': context,
            'line_number': line_number,
        }
    )

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