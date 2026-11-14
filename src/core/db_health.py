"""数据库健康探针。

定期在进程写队列下尝试一次写事务（BEGIN IMMEDIATE + ROLLBACK），
确认当前进程能拿到写锁。恢复后自动把 log_scanner 记录的
DB 错误事件标记为已解决（resolved），避免「已读」长期掩盖真实故障。
"""

from src.core.logger import get_logger

logger = get_logger('db_health')

PROBE_TIMEOUT = 3  # 秒；队列等待与 SQLite 等待各用此预算，避免排队被当成锁争用


def check(timeout: float = PROBE_TIMEOUT) -> dict:
    """执行一次写锁探测，返回 {'ok': bool, 'skipped': bool, 'error': str}。

    进程内写队列在整个超时内繁忙时返回 skipped=True：此时无法判断 SQLite
    锁状态，既不记失败告警，也不自动关闭 DB 错误事件。
    """
    from src.models import database as db_mod

    result = {'ok': False, 'skipped': False, 'error': ''}
    if not db_mod.DB_PATH:
        result['error'] = 'DB_PATH not initialized'
        return result

    probe = db_mod.probe_write_lock(timeout)
    if not probe['attempted']:
        result['skipped'] = True
        result['error'] = 'local write queue busy'
        logger.debug('db health check skipped: local write queue busy')
        return result

    result['ok'] = probe['ok']
    result['error'] = probe['error']
    if result['ok']:
        # DB 已恢复：关闭历史 DB 锁告警（log_scanner 写入的 log_error）
        try:
            from src.models.event_log import resolve_events
            resolved = resolve_events(
                event_type='log_error', message_like='DB错误', resolved_by='auto')
            if resolved:
                logger.info(f'auto-resolved {resolved} DB error event(s)')
        except Exception as e:
            logger.warning(f'auto-resolve DB events failed: {e}')
    else:
        logger.warning(f'db health check failed: {result["error"]}')
    return result
