"""Sources, Channels, Subscriptions, Customers, History — concise API routes."""

from flask import Blueprint, request, jsonify, g
from src.web.auth import require_auth
from src.core.logger import get_logger
logger = get_logger('api')

BASE_URL = 'https://update.nsfocus.com'


def _audit(action: str, details: dict = None):
    """Log audit entry to file (best-effort)."""
    try:
        import logging, os
        from logging.handlers import RotatingFileHandler
        log_path = os.getenv('MONITOR_LOG_DIR', '/root/nsfocus-monitor/logs')
        audit_log_path = os.path.join(log_path, 'audit.log')
        audit_logger = logging.getLogger('audit.file')
        if not audit_logger.handlers:
            audit_logger.setLevel(logging.INFO)
            audit_logger.propagate = False
            handler = RotatingFileHandler(audit_log_path, maxBytes=10_000_000, backupCount=5, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%dT%H:%M:%S'))
            audit_logger.addHandler(handler)
        ip = request.headers.get('X-Forwarded-For', request.remote_addr) or ''
        audit_logger.info(f'[{action}] user_id={getattr(g, "user_id", "?")} ip={ip} details={details or {}}')
    except Exception:
        pass

# ── Content Sources ──────────────────────────────────────

bp_sources = Blueprint('sources', __name__, url_prefix='/api/sources')

@bp_sources.route('', methods=['GET'])
def list_sources():
    from src.models.content_source import list_sources as ls
    rows = ls('nsfocus')
    return jsonify({'code': 0, 'data': [dict(r) for r in rows]})

# ── Channels ─────────────────────────────────────────────

bp_channels = Blueprint('channels', __name__, url_prefix='/api/channels')

@bp_channels.route('', methods=['GET'])
@require_auth
def list_channels():
    from src.models.channel import list_by_user
    rows = list_by_user(g.user_id)
    return jsonify({'code': 0, 'data': [dict(r) for r in rows]})

@bp_channels.route('', methods=['POST'])
@require_auth
def create_channel():
    data = request.get_json() or {}
    name = data.get('name', '')
    ch_type = data.get('type', '')
    config = data.get('config', {})
    if not name or not ch_type:
        return jsonify({'code': 40001, 'message': '缺少参数'}), 400
    from src.models.channel import create
    cid = create(g.user_id, name, ch_type, config,
                 email_hourly_limit=data.get('email_hourly_limit', 0),
                 email_daily_limit=data.get('email_daily_limit', 0))
    _audit('channel_create', {'id': cid, 'name': name, 'type': ch_type})
    return jsonify({'code': 0, 'data': {'id': cid}})

@bp_channels.route('/<int:ch_id>', methods=['PUT'])
@require_auth
def update_channel(ch_id: int):
    data = request.get_json() or {}
    from src.models.channel import update
    update(ch_id, **data)
    _audit('channel_update', {'id': ch_id})
    return jsonify({'code': 0})

@bp_channels.route('/<int:ch_id>', methods=['DELETE'])
@require_auth
def delete_channel(ch_id: int):
    from src.models.channel import get_by_id
    ch = get_by_id(ch_id)
    ch_name = ch['name'] if ch else f'id={ch_id}'

    from src.models.database import query
    msgs = []

    # subscription rules that reference this channel via rule_channels
    ref_rules = query(
        "SELECT sr.id, sr.name FROM subscription_rules sr "
        "INNER JOIN rule_channels rc ON sr.id = rc.rule_id "
        "WHERE rc.channel_id = ?", (ch_id,))
    if ref_rules:
        names = '、'.join(f'「{r["name"]}」(id={r["id"]})' for r in ref_rules)
        msgs.append(f'订阅规则：{names}')

    # delivery_log history records for this channel
    ref_dl = query(
        "SELECT sr.id, sr.name, COUNT(*) as cnt FROM delivery_log dl "
        "INNER JOIN subscription_rules sr ON dl.rule_id = sr.id "
        "WHERE dl.channel_id = ? GROUP BY sr.id, sr.name", (ch_id,))
    if ref_dl:
        detail = '、'.join(f'「{r["name"]}」({r["cnt"]}条历史记录)' for r in ref_dl)
        msgs.append(f'历史推送记录：{detail}')

    if msgs:
        msg = f'渠道「{ch_name}」存在以下关联，无法删除：\n  • ' + '\n  • '.join(msgs) + '\n\n请先在订阅规则中取消该渠道的绑定后再试。'
        return jsonify({'code': 40900, 'message': msg}), 409

    from src.models.channel import delete as ch_delete
    ch_delete(ch_id)
    _audit('channel_delete', {'id': ch_id})
    return jsonify({'code': 0})

@bp_channels.route('/<int:ch_id>/test', methods=['POST'])
@require_auth
def test_channel(ch_id: int):
    from src.models.channel import get_by_id
    from src.notifiers.base import NotificationMessage
    from src.notifiers.router import NOTIFIERS
    from src.notifiers.email import TestLogWriter

    ch = get_by_id(ch_id)
    if not ch:
        return jsonify({'code': 40400}), 404

    test_msg = NotificationMessage(
        title='测试通知',
        product_name='测试产品',
        version_branch='V1.0',
        package_type='test',
        file_name='test_package.bin',
        package_version='1.0.0',
        md5_hash='a' * 32,
        description_summary='这是一条测试消息，用于验证渠道配置。',
        urgency='normal'
    )

    notifier = NOTIFIERS.get(ch['type'])
    if not notifier:
        return jsonify({'code': 40001, 'message': f'Unknown channel type: {ch["type"]}'}), 400

    # Only email currently emits per-step traces; other notifiers get the null writer.
    if ch['type'] == 'email':
        log_writer = TestLogWriter(ch_id, ch.get('name', ''))
        ch_cfg = dict(ch['config'])
        ch_cfg['_test_log_writer'] = log_writer
        result = notifier.send(test_msg, ch_cfg)
    else:
        result = notifier.send(test_msg, ch['config'])

    return jsonify({
        'code': 0,
        'data': {
            'success': result.success,
            'error': result.error_message,
            'log_path': f'/tmp/email_test_{ch_id}.log' if ch['type'] == 'email' else None,
        }
    })


@bp_channels.route('/<int:ch_id>/test-log', methods=['GET'])
@require_auth
def get_channel_test_log(ch_id: int):
    """Read the most recent test log for an email channel.
    Returns 404 if no test has been run yet."""
    import os
    path = f'/tmp/email_test_{ch_id}.log'
    if not os.path.exists(path):
        return jsonify({'code': 40400, 'message': '尚未运行测试'}), 404
    try:
        stat = os.stat(path)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError as e:
        return jsonify({'code': 50000, 'message': f'读取失败: {e}'}), 500
    return jsonify({
        'code': 0,
        'data': {
            'path': path,
            'size': stat.st_size,
            'mtime': stat.st_mtime,
            'content': content,
        }
    })


@bp_channels.route('/log-file', methods=['GET'])
@require_auth
def read_log_file():
    """Generic log file reader restricted to whitelisted email log paths.

    Used by the push result modal to display the SMTP trace produced during
    a manual push (which lives at /tmp/email_push_{channel_id}.log, a path
    the frontend receives as part of the push response).

    Path traversal protection: only allow files under /tmp whose name
    matches the email_test_*.log or email_push_*.log pattern.
    """
    import os
    import re
    path = request.args.get('path', '')
    # Whitelist: must be exactly /tmp/email_{test,push}_<digits>.log
    if not re.fullmatch(r'/tmp/email_(test|push)_\d+\.log', path):
        return jsonify({'code': 40001, 'message': '非法的日志文件路径'}), 400
    if not os.path.exists(path):
        return jsonify({'code': 40400, 'message': '日志文件不存在'}), 404
    try:
        stat = os.stat(path)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError as e:
        return jsonify({'code': 50000, 'message': f'读取失败: {e}'}), 500
    return jsonify({
        'code': 0,
        'data': {
            'path': path,
            'size': stat.st_size,
            'mtime': stat.st_mtime,
            'content': content,
        }
    })

# ── Customers ────────────────────────────────────────────

bp_customers = Blueprint('customers', __name__, url_prefix='/api/customers')

@bp_customers.route('', methods=['GET'])
@require_auth
def list_customers():
    from src.models.customer import list_all
    rows = list_all()
    return jsonify({'code': 0, 'data': rows})

@bp_customers.route('', methods=['POST'])
@require_auth
def create_customer():
    data = request.get_json() or {}
    # Validate required fields
    required = ['name', 'company', 'email']
    for f in required:
        if not data.get(f, '').strip():
            return jsonify({'code': 40001, 'message': f'{f} 为必填项'}), 400
    # Sanitize: strip all string values
    for k in data:
        if isinstance(data[k], str):
            data[k] = data[k].strip()
    # Check duplicate name
    from src.models.database import query
    existing = list(query("SELECT id FROM customers WHERE name = ?", (data['name'],)))
    if existing:
        return jsonify({'code': 40001, 'message': '客户名称已存在'}), 400
    from src.models.customer import create
    cid = create(g.user_id, **data)
    _audit('customer_create', {'id': cid, 'name': data.get('name'), 'company': data.get('company')})
    return jsonify({'code': 0, 'data': {'id': cid}})

@bp_customers.route('/<int:cid>', methods=['PUT'])
@require_auth
def update_customer(cid: int):
    data = request.get_json() or {}
    if 'name' in data and isinstance(data['name'], str):
        data['name'] = data['name'].strip()
        # Check duplicate name (exclude self)
        from src.models.database import query
        existing = list(query("SELECT id FROM customers WHERE name = ? AND id != ?", (data['name'], cid)))
        if existing:
            return jsonify({'code': 40001, 'message': '客户名称已存在'}), 400
    from src.models.customer import update
    update(cid, **data)
    _audit('customer_update', {'id': cid})
    return jsonify({'code': 0})

@bp_customers.route('/<int:cid>/', methods=['DELETE'], strict_slashes=False)
@bp_customers.route('/<int:cid>', methods=['DELETE'])
@require_auth
def delete_customer(cid: int):
    from src.models.customer import get_by_id
    cust = get_by_id(cid)
    cust_name = cust['name'] if cust else f'id={cid}'

    from src.models.customer import delete
    from src.models.database import query
    msgs = []

    # subscription_rules that set this customer
    ref_rules = query("SELECT id, name FROM subscription_rules WHERE customer_id = ?", (cid,))
    if ref_rules:
        names = '、'.join(f'「{r["name"]}」(id={r["id"]})' for r in ref_rules)
        msgs.append(f'订阅规则：{names}')

    # rule_channels that bind this customer
    ref_rc = query(
        "SELECT sr.id, sr.name FROM subscription_rules sr "
        "INNER JOIN rule_channels rc ON sr.id = rc.rule_id "
        "WHERE rc.customer_id = ?", (cid,))
    if ref_rc:
        names = '、'.join(f'「{r["name"]}」(id={r["id"]})' for r in ref_rc)
        msgs.append(f'渠道绑定：{names}')

    # delivery_log history records
    ref_dl = query(
        "SELECT sr.id, sr.name, COUNT(*) as cnt FROM delivery_log dl "
        "INNER JOIN subscription_rules sr ON dl.rule_id = sr.id "
        "WHERE dl.customer_id = ? GROUP BY sr.id, sr.name", (cid,))
    if ref_dl:
        detail = '、'.join(f'「{r["name"]}」({r["cnt"]}条历史记录)' for r in ref_dl)
        msgs.append(f'历史推送记录：{detail}')

    if msgs:
        msg = f'客户「{cust_name}」存在以下关联，无法删除：\n  • ' + '\n  • '.join(msgs) + '\n\n请先删除或解绑相关订阅规则后再试。'
        return jsonify({'code': 40900, 'message': msg}), 409

    delete(cid)
    _audit('customer_delete', {'id': cid})
    return jsonify({'code': 0})

# ── Subscriptions ─────────────────────────────────────────

bp_subscriptions = Blueprint('subscriptions', __name__, url_prefix='/api/subscriptions')

@bp_subscriptions.route('', methods=['GET'])
@require_auth
def list_subscriptions():
    from src.models.subscription import list_rules
    rows = list_rules(g.user_id)
    return jsonify({'code': 0, 'data': rows})

@bp_subscriptions.route('', methods=['POST'])
@require_auth
def create_subscription():
    data = request.get_json() or {}
    channels = data.pop('channels', [])
    customers = data.pop('customers', [])
    from src.models.subscription import create_rule, bind_channel, unbind_channel
    rid = create_rule(g.user_id, **data)
    unbind_channel(rid)
    for ch_id in channels:
        bind_channel(rid, channel_id=ch_id)
    for cust_id in customers:
        bind_channel(rid, customer_id=cust_id)
    _audit('subscription_create', {'id': rid, 'name': data.get('name')})
    return jsonify({'code': 0, 'data': {'id': rid}})

@bp_subscriptions.route('/<int:rid>', methods=['PUT'])
@require_auth
def update_subscription(rid: int):
    data = request.get_json() or {}
    channels = data.pop('channels', None)
    customers = data.pop('customers', None)
    from src.models.subscription import update_rule, bind_channel, unbind_channel
    update_rule(rid, **data)
    if channels is not None:
        unbind_channel(rid)
        for ch_id in channels:
            bind_channel(rid, channel_id=ch_id)
    if customers is not None:
        for cust_id in customers:
            bind_channel(rid, customer_id=cust_id)
    _audit('subscription_update', {'id': rid})
    return jsonify({'code': 0})

@bp_subscriptions.route('/<int:rid>', methods=['DELETE'])
@require_auth
def delete_subscription(rid: int):
    import traceback
    from src.models.subscription import delete_rule
    try:
        delete_rule(rid)
    except Exception as e:
        import sys, logging
        logging.getLogger('api').critical(f'delete_rule({rid}) FAILED: {e}\n{traceback.format_exc()}')
        raise
    _audit('subscription_delete', {'id': rid})
    return jsonify({'code': 0})


@bp_subscriptions.route('/<int:rid>/toggle', methods=['POST'])
@require_auth
def toggle_subscription(rid: int):
    """Toggle enabled/disabled."""
    from src.models.subscription import get_rule, update_rule
    rule = get_rule(rid)
    if not rule:
        return jsonify({'code': 40400}), 404
    update_rule(rid, enabled=0 if rule.get('enabled') else 1)
    new_status = not rule.get('enabled')
    _audit('subscription_toggle', {'id': rid, 'enabled': new_status})
    return jsonify({'code': 0, 'data': {'enabled': new_status}})

# ── History ───────────────────────────────────────────────

bp_history = Blueprint('history', __name__, url_prefix='/api/history')

@bp_history.route('', methods=['GET'])
@require_auth
def get_history():
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 20, type=int)
    days = request.args.get('days', type=int)
    product = request.args.get('product')
    customer_id = request.args.get('customer_id', type=int)
    from src.models.subscription import get_history as gh
    rows, total = gh(page, limit, product, customer_id, days=days)
    return jsonify({'code': 0, 'data': {'items': [dict(r) for r in rows], 'total': total, 'page': page}})

@bp_history.route('', methods=['DELETE'])
@require_auth
def clear_history():
    """Clear push history. Query: ?days=N to keep last N days."""
    days = request.args.get('days', type=int)
    from src.models.subscription import clear_history as ch
    deleted = ch(older_than_days=days)
    _audit('history_clear', {'days': days, 'deleted': deleted})
    msg = f'已清空最近{days}天之前的 {deleted} 条推送记录' if days else f'已清空全部 {deleted} 条推送记录'
    return jsonify({'code': 0, 'message': msg, 'data': {'deleted': deleted}})

@bp_history.route('/<int:sid>/resend', methods=['POST'])
@require_auth
def resend(sid: int):
    from src.models.snapshot import get_snapshot
    snap = get_snapshot(sid)
    if not snap:
        return jsonify({'code': 40400}), 404
    # For resend, we just trigger notification for all rules
    from src.notifiers.router import route_notifications
    from src.models.subscription import get_enabled_rules
    for rule in get_enabled_rules():
        route_notifications(sid, rule['id'])
    return jsonify({'code': 0, 'message': '已重新推送'})


@bp_history.route('/<int:sid>/resend-targeted', methods=['POST'])
@require_auth
def resend_targeted(sid: int):
    """Re-push a snapshot to a specific customer and channel.

    Body: {"channel_id": 1, "customer_id": 3}
    """
    data = request.get_json() or {}
    channel_id = data.get('channel_id', 0)
    customer_id = data.get('customer_id')

    if not channel_id:
        return jsonify({'code': 40001, 'message': '缺少渠道ID'}), 400

    from src.models.snapshot import get_snapshot
    from src.models.channel import get_by_id
    from src.notifiers.base import NotificationMessage

    snap = get_snapshot(sid)
    if not snap:
        return jsonify({'code': 40400, 'message': '快照不存在'}), 404

    channel = get_by_id(channel_id)
    if not channel:
        return jsonify({'code': 40400, 'message': '渠道不存在'}), 404
    if not channel.get('is_active'):
        return jsonify({'code': 40001, 'message': '渠道已停用'}), 400

    # ── 构建 relay_config（复用手动推送的配置）──
    # 先复制渠道配置
    relay_config = dict(channel['config'])

    customer_name = ''
    # 从客户档案获取邮箱和附件大小
    if customer_id:
        try:
            from src.models.customer import get_by_id as get_cust
            cust = get_cust(customer_id)
            if cust:
                customer_name = cust.get('name', '')
                cust_email = cust.get('email', '').strip()
                if cust_email:
                    relay_config['to_list'] = [cust_email]
                cust_attach_mb = cust.get('attachment_max_mb', 0)
                if cust_attach_mb > 0:
                    relay_config['attachment_max_mb'] = str(cust_attach_mb)
        except Exception:
            pass

    # 如果客户没有邮箱，尝试从订阅规则获取
    if not relay_config.get('to_list'):
        try:
            from src.models.subscription import get_rule
            # 获取关联的规则
            from src.models.database import query
            rows = query(
                "SELECT rule_id FROM delivery_log WHERE snapshot_id = ? AND channel_id = ? LIMIT 1",
                (sid, channel_id)
            )
            if rows:
                rule = get_rule(rows[0]['rule_id'])
                if rule and rule.get('customer_emails'):
                    relay_config['rule_emails'] = rule['customer_emails']
        except Exception:
            pass

    # 如果还是没有收件人，使用 SMTP 用户作为 fallback
    if not relay_config.get('to_list') and not relay_config.get('rule_emails'):
        smtp_user = channel['config'].get('smtp_user', '')
        if smtp_user:
            relay_config['to_list'] = [smtp_user]
            import logging
            logging.getLogger('api').warning(f'[resend] 无收件人，使用 smtp_user({smtp_user}) 作为 fallback')

    # 注入渠道和客户 ID（用于限流和日志）
    relay_config['_channel_id'] = channel_id
    relay_config['_customer_id'] = customer_id or 0

    # 获取邮件速率限制配置
    from src.notifiers.router import _get_email_rate_settings
    limits = _get_email_rate_settings()
    relay_config['_cust_hourly_limit'] = limits['cust_hourly']
    relay_config['_cust_daily_limit'] = limits['cust_daily']
    relay_config['_global_hourly_limit'] = limits['global_hourly']
    relay_config['_global_daily_limit'] = limits['global_daily']

    # ── 构造消息 ──
    message = NotificationMessage.from_snapshot(snap)
    if customer_name:
        message.title = f'[重推至{customer_name}] {message.title}'

    # ── 发送 ──
    from src.notifiers.router import NOTIFIERS
    notifier = NOTIFIERS.get(channel['type'])
    if not notifier:
        return jsonify({'code': 40001, 'message': f'不支持的渠道类型: {channel["type"]}'}), 400

    # 使用 relay_config 发送（包含收件人信息）
    result = notifier.send(message, relay_config)

    # 还原 email 渠道实际发送时的收件人列表（to_list + rule_emails 去重合并）
    # 与 src/notifiers/email.py:111-115 保持一致;否则 delivery_log.recipient 字段空,
    # 推送历史视图只能 fallback 到 channel_name,误导用户
    final_to_list = list(relay_config.get('to_list') or [])
    rule_emails_final = (relay_config.get('rule_emails') or '').strip()
    if rule_emails_final:
        extras = [e.strip() for e in rule_emails_final.split(',') if e.strip()]
        final_to_list = list(dict.fromkeys(final_to_list + extras))
    recipient = ','.join(final_to_list)

    # 写入投递日志（customer_id 已经正确传入，不需要改）
    try:
        from src.models.subscription import log_delivery
        log_delivery(
            snapshot_id=sid,
            channel_id=channel_id,
            channel_type=channel['type'],
            channel_name=channel.get('name', channel['type']),
            customer_id=customer_id,
            status='sent' if result.success else 'failed',
            error=result.error_message,
            sender=getattr(result, 'sender', '') or '',
            recipient=recipient,
        )
    except Exception as e:
        import logging
        logging.getLogger('monitor.subscription').warning(f'log_delivery failed (不影响推送): {e}')

    return jsonify({
        'code': 0 if result.success else 50001,
        'data': {'success': result.success, 'error': result.error_message},
        'message': '已推送' if result.success else f'推送失败: {result.error_message}',
    })


# ── Push by mode (customer/channel/manual_email) ──────────────

@bp_history.route('/<int:sid>/push', methods=['POST'])
@require_auth
def push_by_mode(sid: int):
    """Push a snapshot to a customer, channel, or manually entered email.

    Body: {"mode": "customer"|"channel"|"manual_email", "target_id": <id>,
           "email": "a@b.com,c@d.com"}  (email only for manual_email mode)

    Rate limit: max 5 pushes per key (email or channel) per 1-minute window.
    Exceeding triggers a 10-minute ban on that key.
    """
    data = request.get_json() or {}
    mode = data.get('mode', '')
    target_id = data.get('target_id', 0)
    manual_emails = data.get('email', '')
    # Issue #3: manual_email mode can pick which SMTP channel to send through.
    # Optional — when omitted, falls back to the first active email channel.
    manual_channel_id = int(data.get('channel_id') or 0)

    from src.notifiers.router import _is_maintenance_mode
    if _is_maintenance_mode():
        return jsonify({'code': 40001, 'message': '维护模式已开启,所有推送已静默'}), 400

    if mode not in ('customer', 'channel', 'manual_email'):
        return jsonify({'code': 40001, 'message': f'不支持的推送模式: {mode}'}), 400

    from src.models.snapshot import get_snapshot
    from src.notifiers.router import NOTIFIERS
    from src.notifiers.base import NotificationMessage
    from src.models.subscription import log_delivery
    from src.models.channel import get_by_id, list_active
    from src.core.rate_limiter import check, record

    snap = get_snapshot(sid)
    if not snap:
        return jsonify({'code': 40400, 'message': '快照不存在'}), 404

    message = NotificationMessage.from_snapshot(snap)
    results = []

    # ── Resolve target emails and rate-limit keys ──────────────
    targets = []  # list of (rate_key, channel_config, to_emails, label)

    if mode == 'channel':
        if not target_id:
            return jsonify({'code': 40001, 'message': '缺少渠道ID'}), 400
        ch = get_by_id(target_id)
        if not ch:
            return jsonify({'code': 40400, 'message': '渠道不存在'}), 404
        if not ch.get('is_active'):
            return jsonify({'code': 40001, 'message': '渠道已停用'}), 400
        notifier = NOTIFIERS.get(ch['type'])
        if not notifier:
            return jsonify({'code': 40001, 'message': f'不支持的渠道类型: {ch["type"]}'}), 400
        # Rate-limit key: channel ID
        rate_key = f'ch:{target_id}'
        targets.append((rate_key, ch, notifier, ch.get('name', ch['type'])))

    elif mode == 'customer':
        logger.info('[DEBUG] 进入 customer 推送分支')
        if not target_id:
            return jsonify({'code': 40001, 'message': '缺少客户ID'}), 400
        from src.models.customer import get_by_id as get_cust
        cust = get_cust(target_id)
        if not cust:
            return jsonify({'code': 40400, 'message': '客户不存在'}), 404
        cust_email = (cust.get('email') or '').strip()
        if not cust_email:
            return jsonify({'code': 40001, 'message': '该客户未设置邮箱，无法推送'}), 400
        # Find an active email channel to relay through
        email_ch = None
        for ch in list_active():
            if ch['type'] == 'email':
                email_ch = ch
                break
        if not email_ch:
            return jsonify({'code': 40001, 'message': '没有可用的邮件渠道，请先在通知渠道中配置邮箱'}), 400

        # Build relay config: use channel's SMTP but override to_list
        relay_config = dict(email_ch['config'])
        relay_config['to_list'] = [cust_email]
        relay_config['name'] = f'{email_ch.get("name", "email")} → {cust.get("name", cust_email)}'
        relay_config['_channel_id'] = email_ch['id']
        relay_config['_customer_id'] = target_id
        relay_config['_email_hourly_limit'] = email_ch.get('email_hourly_limit', 0)
        relay_config['_email_daily_limit'] = email_ch.get('email_daily_limit', 0)
        cust_attach_mb = cust.get('attachment_max_mb', 0)
        if cust_attach_mb > 0:
            relay_config['attachment_max_mb'] = str(cust_attach_mb)
            logger.info(f'[INFO] set attachment_max_mb to {cust_attach_mb} for customer {target_id}')
        # Inject global/customer limits from system_settings
        from src.notifiers.router import _get_email_rate_settings
        limits = _get_email_rate_settings()
        relay_config['_cust_hourly_limit'] = limits['cust_hourly']
        relay_config['_cust_daily_limit'] = limits['cust_daily']
        relay_config['_global_hourly_limit'] = limits['global_hourly']
        relay_config['_global_daily_limit'] = limits['global_daily']
        rate_key = cust_email
        targets.append((rate_key, email_ch, NOTIFIERS['email'], relay_config.get('name', '')))

    elif mode == 'manual_email':
        if not manual_emails.strip():
            return jsonify({'code': 40001, 'message': '请输入邮箱地址'}), 400
        # Issue #3: honor the SMTP channel the user picked (or fall back to first active)
        email_ch = None
        if manual_channel_id:
            email_ch = get_by_id(manual_channel_id)
            if not email_ch or email_ch.get('type') != 'email':
                return jsonify({'code': 40001, 'message': '所选 SMTP 渠道无效'}), 400
            if not email_ch.get('is_active'):
                return jsonify({'code': 40001, 'message': '所选 SMTP 渠道已停用'}), 400
        else:
            for ch in list_active():
                if ch['type'] == 'email':
                    email_ch = ch
                    break
        if not email_ch:
            return jsonify({'code': 40001, 'message': '没有可用的邮件渠道，请先在通知渠道中配置邮箱'}), 400

        emails = [e.strip() for e in manual_emails.split(',') if e.strip()]
        if not emails:
            return jsonify({'code': 40001, 'message': '请输入有效的邮箱地址'}), 400

        # Validate basic email format
        import re
        email_re = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
        invalid = [e for e in emails if not email_re.match(e)]
        if invalid:
            return jsonify({'code': 40001, 'message': f'邮箱格式无效: {", ".join(invalid)}'}), 400

        relay_config = dict(email_ch['config'])
        relay_config['to_list'] = emails
        relay_config['name'] = f'{email_ch.get("name", "email")} → 手动'
        relay_config['_channel_id'] = email_ch['id']
        relay_config['_customer_id'] = 0
        relay_config['_email_hourly_limit'] = email_ch.get('email_hourly_limit', 0)
        relay_config['_email_daily_limit'] = email_ch.get('email_daily_limit', 0)
        # Inject global limits from system_settings
        from src.notifiers.router import _get_email_rate_settings
        limits = _get_email_rate_settings()
        relay_config['_cust_hourly_limit'] = limits['cust_hourly']
        relay_config['_cust_daily_limit'] = limits['cust_daily']
        relay_config['_global_hourly_limit'] = limits['global_hourly']
        relay_config['_global_daily_limit'] = limits['global_daily']
        rate_key = emails[0] if len(emails) == 1 else f'manual:{",".join(sorted(emails))}'
        targets.append((rate_key, email_ch, NOTIFIERS['email'], relay_config.get('name', '')))

    # ── Rate limit check ───────────────────────────────────────
    for rate_key, ch, notifier, label in targets:
        allowed, err_msg, retry_after = check(rate_key)
        if not allowed:
            return jsonify({
                'code': 42900,
                'message': err_msg,
                'data': {'retry_after': retry_after}
            }), 429

    # ── Execute pushes ─────────────────────────────────────────
    for rate_key, ch, notifier, label in targets:
        if mode == 'channel':
            ch_cfg = dict(ch['config'])
            ch_cfg['_channel_id'] = ch['id']
            ch_cfg['_customer_id'] = 0
            ch_cfg['_email_hourly_limit'] = ch.get('email_hourly_limit', 0)
            ch_cfg['_email_daily_limit'] = ch.get('email_daily_limit', 0)
            # Inject global/customer limits from system_settings
            from src.notifiers.router import _get_email_rate_settings
            limits = _get_email_rate_settings()
            ch_cfg['_cust_hourly_limit'] = limits['cust_hourly']
            ch_cfg['_cust_daily_limit'] = limits['cust_daily']
            ch_cfg['_global_hourly_limit'] = limits['global_hourly']
            ch_cfg['_global_daily_limit'] = limits['global_daily']
            # Issue #4: capture SMTP trace for email channels so the user
            # can see exactly what happened during this push.
            log_path = None
            if ch['type'] == 'email':
                from src.notifiers.email import PushLogWriter
                log_path = f'/tmp/email_push_{ch["id"]}.log'
                ch_cfg['_test_log_writer'] = PushLogWriter(ch['id'], label, log_path)
                # "按渠道发送" 邮件渠道:收件人为 SMTP 用户自己(测试链路用)
                # 渠道 config 里现在没有 to_list(已迁移),如不存在则 fallback
                if not ch_cfg.get('to_list'):
                    smtp_user = ch_cfg.get('smtp_user') or ''
                    if smtp_user:
                        ch_cfg['to_list'] = [smtp_user]
            result = notifier.send(message, ch_cfg)
            results.append({
                'channel': label,
                'success': result.success,
                'error': result.error_message,
                'log_path': log_path if (ch['type'] == 'email') else None,
            })
            log_delivery(
                snapshot_id=sid, channel_id=ch['id'],
                channel_type=ch['type'], channel_name=label,
                status='sent' if result.success else 'failed',
                error=result.error_message
            )
        else:
            # customer / manual_email: use relay config
            log_path = None
            if email_ch.get('type') == 'email':
                from src.notifiers.email import PushLogWriter
                log_path = f'/tmp/email_push_{email_ch["id"]}.log'
                relay_config['_test_log_writer'] = PushLogWriter(
                    email_ch['id'],
                    relay_config.get('name', email_ch.get('name', 'email')),
                    log_path,
                )
            result = notifier.send(message, relay_config)
            results.append({
                'channel': label,
                'success': result.success,
                'error': result.error_message,
                'log_path': log_path,
            })
            log_delivery(
                snapshot_id=sid, channel_id=ch['id'],
                channel_type='email', channel_name=label,
                customer_id=target_id if mode == 'customer' else None,
                status='sent' if result.success else 'failed',
                error=result.error_message
            )

        # Record rate limit on success or failure (to prevent retry spam)
        record(rate_key)

    if not results:
        return jsonify({'code': 40001, 'message': '没有找到可用的推送目标'}), 400

    success_count = sum(1 for r in results if r['success'])
    return jsonify({
        'code': 0,
        'data': {'results': results, 'total': len(results), 'success': success_count},
        'message': f'已推送到 {success_count}/{len(results)} 个目标',
    })


@bp_history.route('/<int:sid>/email-push', methods=['POST'])
@require_auth
def push_email(sid: int):
    """Unified email push: pick SMTP channel + recipients.

    Body: {"channel_id": <int>, "recipients": ["a@x.com", "b@y.com"]}

    Recipients can be customer emails resolved client-side or any manually
    entered email. Server doesn't care about the source — it just sends one
    email to all listed recipients via the chosen SMTP channel.

    Returns structured results with smtp_channel_name so the frontend can
    show which SMTP server actually handled the push.
    """
    data = request.get_json() or {}
    channel_id = int(data.get('channel_id') or 0)
    recipients = data.get('recipients') or []
    customer_id = data.get('customer_id')
    from src.notifiers.router import _is_maintenance_mode
    if _is_maintenance_mode():
        return jsonify({'code': 40001, 'message': '维护模式已开启,所有推送已静默'}), 400

    if not channel_id:
        return jsonify({'code': 40001, 'message': '请选择 SMTP 渠道'}), 400
    if not recipients or not isinstance(recipients, list) or not all(isinstance(e, str) for e in recipients):
        return jsonify({'code': 40001, 'message': '请提供收件人列表'}), 400

    recipients = [e.strip() for e in recipients if e and e.strip()]
    if not recipients:
        return jsonify({'code': 40001, 'message': '请提供有效的收件人'}), 400

    import re
    email_re = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    invalid = [e for e in recipients if not email_re.match(e)]
    if invalid:
        return jsonify({'code': 40001, 'message': f'邮箱格式无效: {", ".join(invalid)}'}), 400

    from src.models.snapshot import get_snapshot
    from src.notifiers.router import NOTIFIERS, _get_email_rate_settings
    from src.notifiers.email import PushLogWriter
    from src.models.subscription import log_delivery
    from src.models.channel import get_by_id

    snap = get_snapshot(sid)
    if not snap:
        return jsonify({'code': 40400, 'message': '快照不存在'}), 404

    ch = get_by_id(channel_id)
    if not ch or ch.get('type') != 'email':
        return jsonify({'code': 40001, 'message': '所选 SMTP 渠道无效'}), 400
    if not ch.get('is_active'):
        return jsonify({'code': 40001, 'message': '所选 SMTP 渠道已停用'}), 400

    notifier = NOTIFIERS.get('email')
    if not notifier:
        return jsonify({'code': 40001, 'message': 'email notifier unavailable'}), 400

    from src.notifiers.base import NotificationMessage
    message = NotificationMessage.from_snapshot(snap)

    # Rate-limit key — single key for the whole send (one email goes to N recipients)
    rate_key = recipients[0] if len(recipients) == 1 else f'manual:{",".join(sorted(recipients))}'

    from src.core.rate_limiter import check, record
    allowed, err_msg, retry_after = check(rate_key)
    if not allowed:
        return jsonify({
            'code': 42900,
            'message': err_msg,
            'data': {'retry_after': retry_after}
        }), 429

    # Build relay config
    relay_config = dict(ch['config'])
    relay_config['to_list'] = recipients
    relay_config['name'] = f"{ch.get('name', 'email')} → {','.join(recipients)}"
    relay_config['_channel_id'] = ch['id']
    relay_config['_customer_id'] = customer_id or 0
    relay_config['_email_hourly_limit'] = ch.get('email_hourly_limit', 0)
    relay_config['_email_daily_limit'] = ch.get('email_daily_limit', 0)

    if customer_id:
        from src.models.customer import get_by_id as get_cust
        cust = get_cust(customer_id)
        if cust:
            cust_attach_mb = cust.get('attachment_max_mb', 0)
            if cust_attach_mb > 0:
                relay_config['attachment_max_mb'] = str(cust_attach_mb)
                logger.info(f'[DEBUG] push_email: set attachment_max_mb to {cust_attach_mb} for customer {customer_id}')
    limits = _get_email_rate_settings()
    relay_config['_cust_hourly_limit'] = limits['cust_hourly']
    relay_config['_cust_daily_limit'] = limits['cust_daily']
    relay_config['_global_hourly_limit'] = limits['global_hourly']
    relay_config['_global_daily_limit'] = limits['global_daily']

    # Inject SMTP trace writer
    log_path = f'/tmp/email_push_{ch["id"]}.log'
    relay_config['_test_log_writer'] = PushLogWriter(
        ch['id'], relay_config.get('name', ch.get('name', 'email')), log_path,
    )

    result = notifier.send(message, relay_config)

    # Log delivery (one row per push, regardless of recipient count)
    # channel_name 只存渠道名(不带邮箱),收件人单独存 recipient 列 — 避免前端两列重复
    # sender 存 SMTP 发件邮箱(从 result.sender 读,email.py 已填)
    log_delivery(
        snapshot_id=sid,
        channel_id=ch['id'],
        channel_type='email',
        channel_name=ch.get('name', 'email'),
        customer_id=customer_id or 0,
        status='sent' if result.success else 'failed',
        error=result.error_message,
        recipient=','.join(recipients),
        sender=getattr(result, 'sender', '') or '',
    )
    record(rate_key)

    results = [{
        'channel': ch.get('name', 'email'),
        'smtp_channel_name': ch.get('name', 'email'),
        'smtp_host': (ch.get('config') or {}).get('smtp_host', ''),
        'recipients': recipients,
        'success': result.success,
        'error': result.error_message,
        'log_path': log_path,
    }]

    return jsonify({
        'code': 0 if result.success else 50001,
        'data': {
            'results': results,
            'total': 1,
            'success': 1 if result.success else 0,
            'smtp_channel_name': ch.get('name', 'email'),
        },
        'message': '已发送' if result.success else f'发送失败: {result.error_message}',
    })


# ── Options API (dynamic dropdowns) ──────────────────────────

bp_options = Blueprint('options', __name__, url_prefix='/api/options')


@bp_options.route('/products', methods=['GET'])
@require_auth
def get_products():
    """Get list of monitored products from content_sources."""
    from src.models.database import query
    rows = query("SELECT name FROM content_sources WHERE is_active=1 ORDER BY name")
    products = [r['name'] for r in rows]
    if not products:
        products = ['WEB应用防护系统(WAF)', '网络入侵防护系统(IPS)', '网络入侵检测系统(IDS)', '远程安全评估系统(RSAS)', '下一代防火墙(NF/SG)', '绿盟综合威胁探针(UTS)']
    return jsonify({'code': 0, 'data': products})


@bp_options.route('/products/all', methods=['GET'])
@require_auth
def get_all_products():
    """Get all products with is_active status for management page."""
    from src.models.database import query
    rows = query("""
        SELECT id, name, display_name, source_type, category,
               is_active, health_status, last_collected_at, created_at
        FROM content_sources
        ORDER BY is_active DESC, name ASC
    """)
    return jsonify({'code': 0, 'data': [dict(r) for r in rows]})


@bp_options.route('/products/<int:product_id>', methods=['PATCH'])
@require_auth
def update_product(product_id):
    """Update a single product (enable/disable)."""
    from src.models.database import query, execute
    product = query("SELECT id FROM content_sources WHERE id=?", (product_id,))
    if not product:
        return jsonify({'code': 40400, 'message': '产品不存在'}), 404
    body = request.get_json() or {}
    if 'is_active' in body:
        execute("UPDATE content_sources SET is_active=? WHERE id=?", (int(bool(body['is_active'])), product_id))
    return jsonify({'code': 0, 'message': '更新成功'})


@bp_options.route('/products/batch', methods=['POST'])
@require_auth
def batch_update_products():
    """Batch enable/disable products. Body: {ids: [1,2,3], action: 'enable'|'disable'}"""
    from src.models.database import query, execute
    body = request.get_json() or {}
    ids = body.get('ids', [])
    action = body.get('action', '')
    if not ids:
        return jsonify({'code': 40000, 'message': '未指定产品'}), 400
    if action not in ('enable', 'disable'):
        return jsonify({'code': 40000, 'message': 'action 必须是 enable 或 disable'}), 400
    is_active = 1 if action == 'enable' else 0
    placeholders = ','.join('?' * len(ids))
    execute(f"UPDATE content_sources SET is_active={is_active} WHERE id IN ({placeholders})", tuple(ids))
    return jsonify({'code': 0, 'message': f'已{action} {len(ids)} 个产品'})


@bp_options.route('/versions', methods=['GET'])
@require_auth
def get_versions():
    """Get versions for a product from snapshots."""
    product = request.args.get('product', '')
    from src.models.database import query
    if product:
        rows = query(
            "SELECT DISTINCT version_branch FROM snapshots WHERE product_name=? AND status='active' ORDER BY version_branch",
            (product,))
    else:
        rows = query("SELECT DISTINCT product_name, version_branch FROM snapshots WHERE status='active' ORDER BY product_name, version_branch")
    versions = [r['version_branch'] for r in rows]
    return jsonify({'code': 0, 'data': versions})


@bp_options.route('/package-types', methods=['GET'])
@require_auth
def get_package_types():
    """Get package types from snapshots."""
    product = request.args.get('product', '')
    version = request.args.get('version', '')
    from src.models.database import query
    conditions = ["status='active'"]
    params = []
    if product:
        conditions.append("product_name=?")
        params.append(product)
    if version:
        conditions.append("version_branch=?")
        params.append(version)
    sql = f"SELECT DISTINCT package_type FROM snapshots WHERE {' AND '.join(conditions)} ORDER BY package_type"
    rows = query(sql, tuple(params))
    types = [r['package_type'] for r in rows]
    if not types:
        types = ['sys', 'rule', 'nti', 'av', 'url', 'special']
    return jsonify({'code': 0, 'data': types})


# ── Latest Snapshots (dashboard display) ──────────────────────

bp_latest = Blueprint('latest', __name__, url_prefix='/api/latest')


@bp_latest.route('/snapshots', methods=['GET'])
@require_auth
def get_latest_snapshots():
    """Get latest snapshots grouped by source (product).

    Returns dict keyed by source_id, each containing:
      - name, is_active, package_type (paths for tree building)
      - snapshots list (only is_active=true sources, all snapshots kept in DB)
    """
    import json as _json
    from src.models.database import query
    from src.models.snapshot import list_sources

    # 一次返回所有状态(active/superseded/withdrawn),前端按 status 给最新包/撤回/替代打 badge
    status_filter = "status IN ('active', 'superseded', 'withdrawn')"

    # Get all active sources with their package_type paths
    sources = list_sources('nsfocus')
    # Build source metadata map
    source_map = {}
    for s in sources:
        if not s.get('is_active'):
            continue
        pt = s.get('package_type', '')
        if isinstance(pt, str) and pt:
            try:
                pt = _json.loads(pt)
            except Exception:
                pt = None
        else:
            pt = None
        final_pt = pt or {'types': [], 'paths': [], 'modes': {}}
        # Attach path_id to each path so the frontend can disambiguate multi-chain
        # shared URLs (NSFocus real structure). path_id = MD5(BASE_URL + url +
        # JSON(chain))[:12], matches the value stored in snapshots.path_id and
        # the UNIQUE index on (source_id, path_id, file_name, md5_hash).
        import hashlib as _hashlib
        for _p in final_pt.get('paths', []) or []:
            _u = _p.get('url') or ''
            _c = _p.get('chain') or []
            if not _u:
                continue
            _full = _u if _u.startswith('http') else (BASE_URL + _u if _u.startswith('/') else BASE_URL + '/' + _u)
            _p['path_id'] = _hashlib.md5(
                (_full + _json.dumps(_c, ensure_ascii=False)).encode()
            ).hexdigest()[:12]
        source_map[s['id']] = {
            'id': s['id'],
            'name': s['name'],
            'entry_url': (lambda u: u[len(BASE_URL):] if u.startswith(BASE_URL) else u)(s.get('entry_url') or ''),
            'display_name': s.get('display_name') or s['name'],
            'is_active': bool(s.get('is_active')),
            'package_type': final_pt,
            'last_collected_at': s.get('last_collected_at'),
            'snapshots': [],
        }

    if not source_map:
        return jsonify({'code': 0, 'data': {}})

    # Get all snapshots for active sources, with last delivery time
    source_ids = list(source_map.keys())
    placeholders = ','.join(['?'] * len(source_ids))
    rows = query(
        f"""SELECT s.*, dl.last_sent
           FROM snapshots s
           LEFT JOIN (
               SELECT snapshot_id, MAX(sent_at) AS last_sent
               FROM delivery_log
               WHERE delivery_status = 'sent'
               GROUP BY snapshot_id
           ) dl ON dl.snapshot_id = s.id
           WHERE s.source_id IN ({placeholders}) AND {status_filter}
           ORDER BY s.source_id, s.last_seen_at DESC""",
        tuple(source_ids)
    )

    for r in rows:
        sid = r['source_id']
        if sid in source_map:
            rec = dict(r)
            rec['last_delivered_at'] = r.get('last_sent') or None
            # Strip heavy detail-only fields from list response
            rec.pop('description_raw', None)
            rec.pop('md5_hash', None)
            rec.pop('page_hash', None)
            rec.pop('description_parsed', None)
            rec.pop('min_sys_version', None)
            rec.pop('restart_required', None)
            rec.pop('prev_page_hash', None)
            rec.pop('rollback_confirmed_at', None)
            rec.pop('rollback_cycles', None)
            rec.pop('product_name', None)
            # Keep path_id in response — needed by frontend dataBuildFeed to group
            # rows by (path_id = MD5(url + chain)) so multi-chain shared URLs
            # show as separate rows instead of collapsing into N "same files".
            # rec.pop('path_id', None)
            rec.pop('file_size', None)
            del rec['last_sent']
            # Normalize source_url to path only (strip BASE_URL prefix) for tree matching
            src_url = rec.get('source_url') or ''
            if src_url.startswith(BASE_URL):
                rec['source_url'] = src_url[len(BASE_URL):]
            source_map[sid]['snapshots'].append(rec)

    return jsonify({'code': 0, 'data': source_map})


# ── Snapshot Detail ──────────────────────────────────────────────
bp_snap = Blueprint('snap', __name__, url_prefix='/api/snapshot')

@bp_snap.route('/<int:snapshot_id>', methods=['GET'])
@require_auth
def get_snapshot_detail(snapshot_id: int):
    """Get full snapshot detail including description_raw, md5_hash, page_hash."""
    from src.models.snapshot import get_snapshot
    snap = get_snapshot(snapshot_id)
    if not snap:
        return jsonify({'code': 40400, 'message': '快照不存在'}), 404
    # Normalize source_url to path
    src_url = snap.get('source_url') or ''
    if src_url.startswith(BASE_URL):
        snap['source_url'] = src_url[len(BASE_URL):]
    return jsonify({'code': 0, 'data': snap})


bp_settings = Blueprint('settings', __name__, url_prefix='/api/settings')

@bp_settings.route('/scheduler', methods=['GET'])
@require_auth
def get_scheduler():
    from src.core.scheduler import get_status
    from src.models.database import query
    status = get_status()
    # last_run = MAX(last_collected_at) from content_sources（不依赖内存，重启后仍准确）
    last_row = query("SELECT MAX(last_collected_at) as m FROM content_sources WHERE last_collected_at IS NOT NULL")
    status['last_run'] = last_row[0]['m'] if last_row and last_row[0]['m'] else None
    return jsonify({'code': 0, 'data': status})


@bp_settings.route('/scheduler/trigger', methods=['POST'])
@require_auth
def trigger_collection():
    from src.core.scheduler import run_now
    import threading
    # Run in background to avoid blocking the request
    result = {'status': 'started'}
    def _run():
        run_now()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({'code': 0, 'data': result, 'message': '采集任务已触发，请查看日志'})


@bp_settings.route('/cleanup', methods=['POST'])
@require_auth
def trigger_cleanup():
    """手动触发数据库清理（清空 heartbeat_log，清理 30 天前的 audit_log 和 90 天前的 delivery_log）。"""
    from src.core.scheduler import _run_db_cleanup
    import threading
    result = {'status': 'started'}
    def _run():
        _run_db_cleanup()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({'code': 0, 'data': result, 'message': '数据库清理已在后台执行，请查看日志'})


@bp_settings.route('/config', methods=['GET'])
@require_auth
def get_config():
    """Get all system settings."""
    from src.models.database import query
    rows = query("SELECT key, value FROM system_settings ORDER BY key")
    config = {r['key']: r['value'] for r in rows}
    return jsonify({'code': 0, 'data': config})


@bp_settings.route('/force-stop', methods=['POST'])
@require_auth
def force_stop_and_save():
    """强制停止采集并保存系统配置。"""
    data = request.get_json() or {}
    from src.models.database import execute

    # Step 1: 强制停止采集（清 collection_running + 临时关闭调度器）
    try:
        import json
        running_val = json.dumps({'status': '0', 'started_at': '', 'mode': ''})
        execute("INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('collection_running', ?, datetime('now'))",
               (running_val,))
        # 临时将 scheduler_enabled 设为 0，防止保存配置时触发采集
        execute("INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('scheduler_enabled', '0', datetime('now'))")
    except Exception:
        pass  # 忽略错误，继续保存配置

    # Step 2: 保存所有配置项
    for key, value in data.items():
        execute("INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                (key, str(value)))

    _audit('force_stop_settings', {'keys': list(data.keys())})

    # Step 3: 重新调度（如果用户开了调度器）
    if data.get('scheduler_enabled') == '1':
        from src.core.scheduler import refresh_scheduler_jobs
        refresh_scheduler_jobs()

    if data.get('collect_interval'):
        from src.core.scheduler import reschedule_collect
        reschedule_collect()
    if data.get('heartbeat_enabled') == '1' and data.get('heartbeat_interval'):
        from src.core.scheduler import reschedule_heartbeat
        reschedule_heartbeat()

    return jsonify({'code': 0, 'message': '采集已停止，配置已保存'})


@bp_settings.route('/config', methods=['PUT'])
@require_auth
def update_config():
    """Update system settings."""
    data = request.get_json() or {}
    from src.models.database import execute
    for key, value in data.items():
        execute("INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
                (key, str(value)))
    _audit('settings_update', {'keys': list(data.keys())})
    # Reschedule collector if interval changed
    if 'collect_interval' in data:
        from src.core.scheduler import reschedule_collect
        reschedule_collect()
    if 'heartbeat_enabled' in data and 'heartbeat_interval' in data:
        if data.get('heartbeat_enabled') == '1':
            from src.core.scheduler import reschedule_heartbeat
            reschedule_heartbeat()
        else:
            from src.core.scheduler import refresh_scheduler_jobs
            refresh_scheduler_jobs()
    if 'scheduler_enabled' in data:
        from src.core.scheduler import refresh_scheduler_jobs
        refresh_scheduler_jobs()
    return jsonify({'code': 0, 'message': '配置已保存'})


@bp_settings.route('/classification', methods=['GET'])
@require_auth
def get_classification():
    """Get package classification config."""
    from src.notifiers.router import _load_classification_config
    config = _load_classification_config()

    # Also return known product+type combinations from snapshots
    from src.models.database import query
    combos = query(
        "SELECT DISTINCT product_name, package_type FROM snapshots WHERE status='active' ORDER BY product_name, package_type"
    )
    known_types = [{'product': r['product_name'], 'type': r['package_type']} for r in combos]

    return jsonify({
        'code': 0,
        'data': {
            'config': config,
            'known_types': known_types,
        }
    })


@bp_settings.route('/classification', methods=['PUT'])
@require_auth
def update_classification():
    """Update package classification config."""
    data = request.get_json() or {}
    import json
    from src.models.database import execute
    config_json = json.dumps(data, ensure_ascii=False)
    execute(
        "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES ('package_classification', ?, datetime('now'))",
        (config_json,)
    )
    _audit('classification_update', {'keys': list(data.keys())})
    return jsonify({'code': 0, 'message': '分类配置已保存'})


# ── System Event Config ───────────────────────────────────

@bp_settings.route('/event-config', methods=['GET'])
@require_auth
def get_event_config():
    """获取系统事件通知配置"""
    from src.models.event_log import get_config
    return jsonify({'code': 0, 'data': get_config()})


@bp_settings.route('/event-config', methods=['PUT'])
@require_auth
def update_event_config():
    """更新系统事件通知配置"""
    data = request.get_json() or {}
    from src.models.event_log import update_config

    enabled = data.get('enabled')
    channel_id = data.get('channel_id')
    event_types = data.get('event_types')

    # Handle enabled: can be None (no update), True, or False
    if enabled is not None:
        enabled = bool(enabled)

    result = update_config(
        enabled=enabled,
        channel_id=channel_id,
        event_types=event_types
    )
    _audit('event_config_update', {
        'enabled': enabled,
        'channel_id': channel_id,
        'event_types': event_types,
    })
    return jsonify({'code': 0, 'data': result, 'message': '配置已保存'})
