"""System event handler — record events and send notifications.

Called by:
- router.py: emit_push() on delivery success/failure
- scheduler.py: emit_collection_summary() on collection complete
- scheduler.py: emit_session_error() on session heartbeat failure
- log_scanner.py: emit_log_error() on log anomaly
"""

import json
from datetime import datetime

from src.core.logger import get_logger
from src.models.event_log import (
    log_event, get_config, get_notify_channel, is_event_enabled
)

logger = get_logger('event_handler')


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


def emit_collection_summary(summary: dict, mode: str):
    """采集任务入库完成后调用"""
    event_type = 'collection_summary'

    if not is_event_enabled(event_type):
        return

    channel = get_notify_channel()
    if not channel:
        return

    # 构建消息
    started_at = summary.get('started_at', '')
    finished_at = datetime.utcnow().isoformat() if not summary.get('finished_at') else summary.get('finished_at')
    duration = summary.get('duration_s', 0)

    lines = [
        "【采集任务完成】" + {'quick': 'Quick Scan', 'full': 'Full Scan', 'delta': 'Delta Scan'}.get(mode, mode),
        f"开始：{started_at[:19] if started_at else '—'}",
        f"结束：{finished_at[:19] if finished_at else '—'}",
        f"耗时：{duration}秒",
        "",
        "采集概况",
    ]

    total_new = summary.get('total_new', 0)
    total_rollback = summary.get('total_rollback', 0)
    errors = summary.get('errors', [])

    # 简化产品更新统计
    products = summary.get('products', {})
    if products:
        for name, pdata in products.items():
            new_count = pdata.get('new', 0)
            if new_count > 0:
                lines.append(f"├── {name}：新增 {new_count} 个包")
    else:
        lines.append("└── 无新增包")

    if errors:
        lines.append("")
        lines.append("错误列表")
        for err in errors[:5]:  # 最多显示5个
            lines.append(f"└── {err[:100]}")

    message_text = '\n'.join(lines)

    # 记录到日志表
    log_event(
        event_type=event_type,
        severity='INFO',
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
            title='绿盟监控 - 采集任务完成',
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


def emit_session_error(username: str, product_name: str, reason: str):
    """Session心跳异常时调用"""
    event_type = 'session_error'

    if not is_event_enabled(event_type):
        return

    channel = get_notify_channel()
    if not channel:
        return

    message_text = '\n'.join([
        "【Session 异常】",
        f"用户名：{username}",
        f"产品：{product_name}",
        f"异常原因：{reason}",
        f"检测时间：{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "建议：请更新该用户的 Session",
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
        from src.notifiers.base import NotificationMessage
        msg = NotificationMessage(
            title='绿盟监控 - Session 异常',
            product_name=product_name,
            version_branch='',
            package_type='',
            file_name='',
            package_version='',
            md5_hash='',
            description_full=message_text,
        )
        try:
            notifier.send(msg, channel.get('config', {}))
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

    message_text = '\n'.join([
        "【日志异常检测】",
        f"扫描时间：{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}",
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
        from src.notifiers.base import NotificationMessage
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
            notifier.send(msg, channel.get('config', {}))
        except Exception as e:
            logger.error(f'Failed to send log error notification: {e}')