"""Sources, Channels, Subscriptions, Customers, History — concise API routes."""

from flask import Blueprint, request, jsonify, g
from src.web.auth import require_auth


def _audit(action: str, details: dict = None):
    """Log audit entry (best-effort)."""
    try:
        from src.models.audit import log
        ip = request.headers.get('X-Forwarded-For', request.remote_addr) or ''
        log(g.user_id, action, details or {}, ip)
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
    cid = create(g.user_id, name, ch_type, config)
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
    from src.models.channel import delete
    delete(ch_id)
    _audit('channel_delete', {'id': ch_id})
    return jsonify({'code': 0})

@bp_channels.route('/<int:ch_id>/test', methods=['POST'])
@require_auth
def test_channel(ch_id: int):
    from src.models.channel import get_by_id
    from src.notifiers.base import NotificationMessage
    from src.notifiers.router import NOTIFIERS

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

    result = notifier.send(test_msg, ch['config'])
    return jsonify({'code': 0, 'data': {'success': result.success, 'error': result.error_message}})

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
    from src.models.customer import create
    cid = create(g.user_id, **data)
    _audit('customer_create', {'id': cid, 'name': data.get('name'), 'company': data.get('company')})
    return jsonify({'code': 0, 'data': {'id': cid}})

@bp_customers.route('/<int:cid>', methods=['PUT'])
@require_auth
def update_customer(cid: int):
    data = request.get_json() or {}
    from src.models.customer import update
    update(cid, **data)
    _audit('customer_update', {'id': cid})
    return jsonify({'code': 0})

@bp_customers.route('/<int:cid>', methods=['DELETE'])
@require_auth
def delete_customer(cid: int):
    from src.models.customer import delete
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
    from src.models.subscription import delete_rule
    delete_rule(rid)
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
        return jsonify({'code': 40400}), 404

    channel = get_by_id(channel_id)
    if not channel:
        return jsonify({'code': 40400, 'message': '渠道不存在'}), 404
    if not channel.get('is_active'):
        return jsonify({'code': 40001, 'message': '渠道已停用'}), 400

    # Look up customer name for the title
    customer_name = ''
    if customer_id:
        try:
            from src.models.customer import get_by_id as get_cust
            cust = get_cust(customer_id)
            if cust:
                customer_name = cust.get('name', '')
        except Exception:
            pass

    message = NotificationMessage.from_snapshot(snap)
    if customer_name:
        message.title = f'[重推至{customer_name}] {message.title}'

    from src.notifiers.router import NOTIFIERS
    notifier = NOTIFIERS.get(channel['type'])
    if not notifier:
        return jsonify({'code': 40001, 'message': f'不支持的渠道类型: {channel["type"]}'}), 400

    result = notifier.send(message, channel['config'])

    from src.models.subscription import log_delivery
    log_delivery(
        snapshot_id=sid,
        channel_id=channel_id,
        channel_type=channel['type'],
        channel_name=channel.get('name', channel['type']),
        customer_id=customer_id,
        status='sent' if result.success else 'failed',
        error=result.error_message,
    )

    return jsonify({
        'code': 0 if result.success else 50001,
        'data': {'success': result.success, 'error': result.error_message},
        'message': '已推送' if result.success else f'推送失败: {result.error_message}',
    })

# ── Push by mode (rule/channel/customer) ─────────────────────

@bp_history.route('/<int:sid>/push', methods=['POST'])
@require_auth
def push_by_mode(sid: int):
    """Push a snapshot through a specific rule, channel, or to a specific customer.

    Body: {"mode": "rule"|"channel"|"customer", "target_id": <id>}
    """
    data = request.get_json() or {}
    mode = data.get('mode', 'rule')
    target_id = data.get('target_id', 0)

    from src.notifiers.router import _is_maintenance_mode
    if _is_maintenance_mode():
        return jsonify({'code': 40001, 'message': '维护模式已开启，所有推送已静默'}), 400

    if not target_id:
        return jsonify({'code': 40001, 'message': '缺少目标ID'}), 400

    from src.models.snapshot import get_snapshot
    from src.notifiers.router import NOTIFIERS, route_notifications
    from src.notifiers.base import NotificationMessage
    from src.models.subscription import log_delivery

    snap = get_snapshot(sid)
    if not snap:
        return jsonify({'code': 40400, 'message': '快照不存在'}), 404

    message = NotificationMessage.from_snapshot(snap)
    results = []

    if mode == 'rule':
        # Push through subscription rule (rule's bound channels + customers)
        from src.models.subscription import get_rule, get_rule_channels
        rule = get_rule(target_id)
        if not rule:
            return jsonify({'code': 40400, 'message': '规则不存在'}), 404
        bindings = get_rule_channels(target_id)
        if not bindings:
            return jsonify({'code': 40001, 'message': '该规则未绑定任何渠道'}), 400

        from src.models.channel import get_by_id
        for binding in bindings:
            ch = get_by_id(binding.get('channel_id'))
            if not ch or not ch.get('is_active'):
                continue
            notifier = NOTIFIERS.get(ch['type'])
            if not notifier:
                continue
            result = notifier.send(message, ch['config'])
            results.append({'channel': ch.get('name', ch['type']),
                          'success': result.success, 'error': result.error_message})
            log_delivery(snapshot_id=sid, channel_id=ch['id'],
                        channel_type=ch['type'], channel_name=ch.get('name', ''),
                        customer_id=binding.get('customer_id'),
                        status='sent' if result.success else 'failed',
                        error=result.error_message)

    elif mode == 'channel':
        # Push directly to a specific channel
        from src.models.channel import get_by_id
        ch = get_by_id(target_id)
        if not ch:
            return jsonify({'code': 40400, 'message': '渠道不存在'}), 404
        if not ch.get('is_active'):
            return jsonify({'code': 40001, 'message': '渠道已停用'}), 400
        notifier = NOTIFIERS.get(ch['type'])
        if not notifier:
            cht = ch['type']
            return jsonify({'code': 40001, 'message': f'不支持的渠道类型: {cht}'}), 400
        result = notifier.send(message, ch['config'])
        results.append({'channel': ch.get('name', ch['type']),
                      'success': result.success, 'error': result.error_message})
        log_delivery(snapshot_id=sid, channel_id=ch['id'],
                    channel_type=ch['type'], channel_name=ch.get('name', ''),
                    status='sent' if result.success else 'failed',
                    error=result.error_message)

    elif mode == 'customer':
        # Push to all active channels bound to this customer via rules
        from src.models.customer import get_by_id as get_cust
        from src.models.channel import get_by_id
        from src.models.subscription import get_enabled_rules, get_rule_channels
        cust = get_cust(target_id)
        if not cust:
            return jsonify({'code': 40400, 'message': '客户不存在'}), 404

        sent_channels = set()
        for rule in get_enabled_rules():
            for binding in get_rule_channels(rule['id']):
                if binding.get('customer_id') != target_id:
                    continue
                ch_id = binding.get('channel_id')
                if ch_id in sent_channels:
                    continue
                ch = get_by_id(ch_id)
                if not ch or not ch.get('is_active'):
                    continue
                notifier = NOTIFIERS.get(ch['type'])
                if not notifier:
                    continue
                result = notifier.send(message, ch['config'])
                results.append({'channel': ch.get('name', ch['type']),
                              'success': result.success, 'error': result.error_message})
                sent_channels.add(ch_id)
                log_delivery(snapshot_id=sid, channel_id=ch['id'],
                            channel_type=ch['type'], channel_name=ch.get('name', ''),
                            customer_id=target_id,
                            status='sent' if result.success else 'failed',
                            error=result.error_message)

    else:
        return jsonify({'code': 40001, 'message': f'不支持的推送模式: {mode}'}), 400

    if not results:
        return jsonify({'code': 40001, 'message': '没有找到可用的推送目标'}), 400

    success_count = sum(1 for r in results if r['success'])
    return jsonify({
        'code': 0,
        'data': {'results': results, 'total': len(results), 'success': success_count},
        'message': f'已推送到 {success_count}/{len(results)} 个渠道',
    })


# ── Options API (dynamic dropdowns) ──────────────────────────

bp_options = Blueprint('options', __name__, url_prefix='/api/options')


@bp_options.route('/products', methods=['GET'])
@require_auth
def get_products():
    """Get list of monitored products from snapshots."""
    from src.models.database import query
    rows = query("SELECT DISTINCT product_name FROM snapshots WHERE status='active' ORDER BY product_name")
    products = [r['product_name'] for r in rows]
    if not products:
        products = ['WAF', 'IPS', 'IDS', 'RSAS', 'NF', 'UTS']
    return jsonify({'code': 0, 'data': products})


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
    """Get latest snapshots grouped by product."""
    from src.models.database import query
    product = request.args.get('product', '')
    
    if product:
        rows = query(
            """SELECT * FROM snapshots WHERE product_name=? AND status='active'
               ORDER BY last_seen_at DESC LIMIT 50""",
            (product,))
    else:
        rows = query(
            """SELECT * FROM snapshots WHERE status='active'
               ORDER BY product_name, last_seen_at DESC""")
    
    # Group by product
    grouped = {}
    for r in rows:
        p = r['product_name']
        if p not in grouped:
            grouped[p] = []
        if len(grouped[p]) < 20:
            grouped[p].append(dict(r))
    
    return jsonify({'code': 0, 'data': grouped})

bp_settings = Blueprint('settings', __name__, url_prefix='/api/settings')

@bp_settings.route('/scheduler', methods=['GET'])
@require_auth
def get_scheduler():
    from src.core.scheduler import get_status
    return jsonify({'code': 0, 'data': get_status()})


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


@bp_settings.route('/config', methods=['GET'])
@require_auth
def get_config():
    """Get all system settings."""
    from src.models.database import query
    rows = query("SELECT key, value FROM system_settings ORDER BY key")
    config = {r['key']: r['value'] for r in rows}
    return jsonify({'code': 0, 'data': config})


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
