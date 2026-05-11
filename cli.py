#!/usr/bin/env python3
"""NSFOCUS 升级监控平台 - CLI 管理工具

直连 SQLite 数据库，不依赖 Flask Web 服务。
用于安全配置管理，避免 Web 界面暴露风险。

用法:
  nsfocus-cli status
  nsfocus-cli customer list|add|edit|delete|show
  nsfocus-cli channel list|add|edit|delete|show|test
  nsfocus-cli rule list|add|edit|delete|show|enable|disable
  nsfocus-cli collect [--mode delta|full]
  nsfocus-cli export customers|rules|channels [--output FILE.yaml]
  nsfocus-cli import FILE.yaml
"""

import argparse
import json
import os
import sys
from datetime import datetime

# Ensure project root is on path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

# Load .env
_env_path = os.path.join(PROJECT_ROOT, '.env')
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, val = line.partition('=')
                os.environ.setdefault(key.strip(), val.strip())

from src.models.database import init_db, query, execute


def _init():
    """Initialize database connection."""
    init_db(os.path.join(PROJECT_ROOT, 'data'))


# ══════════════════════════════════════════════════════════════
# STATUS
# ══════════════════════════════════════════════════════════════

def cmd_status(args):
    """Show system overview."""
    _init()
    print("╔══════════════════════════════════════╗")
    print("║  绿盟升级监控平台 - 系统状态         ║")
    print("╚══════════════════════════════════════╝")
    print()

    # Snapshots
    rows = query("SELECT status, COUNT(*) as cnt FROM snapshots GROUP BY status")
    print("📦 快照:")
    for r in rows:
        print(f"   {r['status']}: {r['cnt']}")

    # Products
    rows = query("SELECT product_name, COUNT(*) as cnt FROM snapshots WHERE status='active' GROUP BY product_name ORDER BY cnt DESC")
    print()
    print("🔧 产品分布 (active):")
    for r in rows:
        print(f"   {r['product_name']}: {r['cnt']}")

    # Rules
    rows = query("SELECT id, name, enabled, digest_mode FROM subscription_rules")
    print()
    print("📋 订阅规则:")
    for r in rows:
        status = "✅" if r['enabled'] else "⛔"
        digest = f" [{r['digest_mode']}]" if r['digest_mode'] else ""
        print(f"   #{r['id']} {status} {r['name']}{digest}")

    # Channels
    rows = query("SELECT id, name, type, is_active FROM channels")
    print()
    print("📡 通知渠道:")
    for r in rows:
        status = "✅" if r['is_active'] else "⛔"
        print(f"   #{r['id']} {status} {r['name']} ({r['type']})")

    # Customers
    rows = query("SELECT COUNT(*) as cnt FROM customers")
    print()
    print(f"👤 客户: {rows[0]['cnt']}")

    # Recent deliveries
    rows = query("SELECT delivery_status, COUNT(*) as cnt FROM delivery_log GROUP BY delivery_status")
    print()
    print("📤 推送统计:")
    for r in rows:
        print(f"   {r['delivery_status']}: {r['cnt']}")


# ══════════════════════════════════════════════════════════════
# CUSTOMER
# ══════════════════════════════════════════════════════════════

def cmd_customer_list(args):
    _init()
    from src.models.customer import list_all
    customers = list_all()
    if not customers:
        print("(无客户)")
        return
    print(f"{'ID':<4} {'名称':<20} {'公司':<20} {'持有产品'}")
    print("-" * 70)
    for c in customers:
        products = ', '.join(c.get('owned_products', []) or [])
        print(f"{c['id']:<4} {c['name']:<20} {c.get('company',''):<20} {products}")


def cmd_customer_add(args):
    _init()
    from src.models.customer import create
    name = args.name
    company = args.company or ''
    products = args.products.split(',') if args.products else []
    cid = create(
        created_by=1,
        name=name,
        company=company,
        owned_products=products,
        contact=args.contact or '',
        email=args.email or '',
        phone=args.phone or '',
        notes=args.notes or '',
    )
    print(f"✅ 客户已创建: ID={cid}, 名称={name}")


def cmd_customer_edit(args):
    _init()
    from src.models.customer import update, get_by_id
    c = get_by_id(args.id)
    if not c:
        print(f"❌ 客户 #{args.id} 不存在")
        sys.exit(1)
    kwargs = {}
    if args.name:
        kwargs['name'] = args.name
    if args.company:
        kwargs['company'] = args.company
    if args.products:
        kwargs['owned_products'] = args.products.split(',')
    if args.contact:
        kwargs['contact'] = args.contact
    if args.email:
        kwargs['email'] = args.email
    if args.phone:
        kwargs['phone'] = args.phone
    if args.notes:
        kwargs['notes'] = args.notes
    if kwargs:
        update(args.id, **kwargs)
        print(f"✅ 客户 #{args.id} 已更新: {', '.join(kwargs.keys())}")
    else:
        print("❌ 未提供任何修改参数")


def cmd_customer_delete(args):
    _init()
    from src.models.customer import delete, get_by_id
    c = get_by_id(args.id)
    if not c:
        print(f"❌ 客户 #{args.id} 不存在")
        sys.exit(1)
    delete(args.id)
    print(f"✅ 客户 #{args.id} '{c['name']}' 已删除")


def cmd_customer_show(args):
    _init()
    from src.models.customer import get_by_id
    c = get_by_id(args.id)
    if not c:
        print(f"❌ 客户 #{args.id} 不存在")
        return
    print(f"ID:      {c['id']}")
    print(f"名称:    {c['name']}")
    print(f"公司:    {c.get('company','')}")
    print(f"联系人:  {c.get('contact','')}")
    print(f"邮箱:    {c.get('email','')}")
    print(f"电话:    {c.get('phone','')}")
    products = c.get('owned_products', [])
    print(f"产品:    {', '.join(products) if products else '(无)'}")
    print(f"备注:    {c.get('notes','')}")
    print(f"创建时间: {c.get('created_at','')}")


# ══════════════════════════════════════════════════════════════
# CHANNEL
# ══════════════════════════════════════════════════════════════

def cmd_channel_list(args):
    _init()
    from src.models.channel import list_by_user
    from src.core.crypto import decrypt
    channels = list_by_user(1)  # admin user
    if not channels:
        print("(无渠道)")
        return
    print(f"{'ID':<4} {'名称':<16} {'类型':<10} {'状态':<6} {'配置预览'}")
    print("-" * 70)
    for ch in channels:
        status = "✅" if ch.get('is_active') else "⛔"
        config = ch.get('config', {})
        cfg_preview = ''
        if isinstance(config, dict):
            webhook = config.get('webhook_url', '')
            cfg_preview = webhook[:40] + '...' if len(webhook) > 40 else webhook
        print(f"{ch['id']:<4} {ch['name']:<16} {ch['type']:<10} {status:<6} {cfg_preview}")


def cmd_channel_add(args):
    _init()
    from src.models.channel import create
    if args.type not in ('wecom', 'dingtalk', 'feishu', 'email'):
        print(f"❌ 不支持的渠道类型: {args.type} (支持: wecom, dingtalk, feishu, email)")
        sys.exit(1)
    config = {}
    if args.webhook:
        config['webhook_url'] = args.webhook
    cid = create(user_id=1, name=args.name, channel_type=args.type, config=config)
    print(f"✅ 渠道已创建: ID={cid}, 名称={args.name}, 类型={args.type}")


def cmd_channel_edit(args):
    _init()
    from src.models.channel import update, get_by_id
    ch = get_by_id(args.id)
    if not ch:
        print(f"❌ 渠道 #{args.id} 不存在")
        sys.exit(1)
    kwargs = {}
    if args.name:
        kwargs['name'] = args.name
    if args.webhook:
        from src.core.crypto import decrypt
        config = ch.get('config', {})
        if isinstance(config, str):
            try:
                config = json.loads(decrypt(config))
            except Exception:
                config = {}
        config['webhook_url'] = args.webhook
        kwargs['config'] = config
    if kwargs:
        update(args.id, **kwargs)
        print(f"✅ 渠道 #{args.id} 已更新")
    else:
        print("❌ 未提供修改参数")


def cmd_channel_delete(args):
    _init()
    from src.models.channel import delete, get_by_id
    ch = get_by_id(args.id)
    if not ch:
        print(f"❌ 渠道 #{args.id} 不存在")
        sys.exit(1)
    delete(args.id)
    print(f"✅ 渠道 #{args.id} '{ch['name']}' 已删除")


def cmd_channel_show(args):
    _init()
    from src.models.channel import get_by_id
    ch = get_by_id(args.id)
    if not ch:
        print(f"❌ 渠道 #{args.id} 不存在")
        return
    print(f"ID:      {ch['id']}")
    print(f"名称:    {ch['name']}")
    print(f"类型:    {ch['type']}")
    print(f"状态:    {'启用' if ch.get('is_active') else '停用'}")
    config = ch.get('config', {})
    print(f"Webhook: {config.get('webhook_url', '(未配置)')}")
    print(f"创建时间: {ch.get('created_at', '')}")


def cmd_channel_test(args):
    _init()
    from src.models.channel import get_by_id
    from src.notifiers.base import NotificationMessage
    from src.notifiers.router import NOTIFIERS
    ch = get_by_id(args.id)
    if not ch:
        print(f"❌ 渠道 #{args.id} 不存在")
        sys.exit(1)
    notifier = NOTIFIERS.get(ch['type'])
    if not notifier:
        print(f"❌ 不支持的渠道类型: {ch['type']}")
        sys.exit(1)
    test_msg = NotificationMessage(
        title='CLI 测试通知',
        product_name='测试产品',
        package_version='v1.0.0-test',
        description='这是一条来自 nsfocus-cli 的测试消息',
        is_rollback=False,
    )
    print(f"📤 正在发送测试消息到 {ch['name']} ({ch['type']})...")
    result = notifier.send(test_msg, ch['config'])
    if result.success:
        print(f"✅ 发送成功")
    else:
        print(f"❌ 发送失败: {result.error_message}")


# ══════════════════════════════════════════════════════════════
# RULE
# ══════════════════════════════════════════════════════════════

def cmd_rule_list(args):
    _init()
    from src.models.subscription import list_rules
    rules = list_rules(1)
    if not rules:
        print("(无规则)")
        return
    print(f"{'ID':<4} {'状态':<6} {'名称':<24} {'摘要':<10} {'延迟'}")
    print("-" * 70)
    for r in rules:
        status = "✅" if r.get('enabled') else "⛔"
        digest = r.get('digest_mode', '') or '-'
        delay = f"{r.get('delay_hours', 0)}h" if r.get('delay_hours') else '即时'
        print(f"{r['id']:<4} {status:<6} {r['name']:<24} {digest:<10} {delay}")


def cmd_rule_add(args):
    _init()
    from src.models.subscription import create_rule
    rid = create_rule(
        user_id=1,
        name=args.name,
        enabled=1,
        delay_hours=args.delay or 0,
        digest_mode=args.digest or '',
    )
    print(f"✅ 规则已创建: ID={rid}, 名称={args.name}")


def cmd_rule_edit(args):
    _init()
    from src.models.subscription import update_rule, get_rule
    r = get_rule(args.id)
    if not r:
        print(f"❌ 规则 #{args.id} 不存在")
        sys.exit(1)
    kwargs = {}
    if args.name:
        kwargs['name'] = args.name
    if args.delay is not None:
        kwargs['delay_hours'] = args.delay
    if args.digest:
        kwargs['digest_mode'] = args.digest
    if kwargs:
        update_rule(args.id, **kwargs)
        print(f"✅ 规则 #{args.id} 已更新")
    else:
        print("❌ 未提供修改参数")


def cmd_rule_delete(args):
    _init()
    from src.models.subscription import get_rule
    from src.models.database import execute
    r = get_rule(args.id)
    if not r:
        print(f"❌ 规则 #{args.id} 不存在")
        sys.exit(1)
    execute("DELETE FROM subscription_rules WHERE id = ?", (args.id,))
    print(f"✅ 规则 #{args.id} '{r['name']}' 已删除")


def cmd_rule_show(args):
    _init()
    from src.models.subscription import get_rule, get_rule_channels
    r = get_rule(args.id)
    if not r:
        print(f"❌ 规则 #{args.id} 不存在")
        return
    print(f"ID:         {r['id']}")
    print(f"名称:       {r['name']}")
    print(f"状态:       {'启用' if r.get('enabled') else '停用'}")
    print(f"延迟:       {r.get('delay_hours', 0)}h")
    print(f"摘要模式:   {r.get('digest_mode', '无')}")
    print(f"最小间隔:   {r.get('min_interval_hours', 0)}h")
    print(f"静默时段:   {r.get('quiet_start', '')} - {r.get('quiet_end', '')}")
    filter_cond = r.get('filter_conditions', {})
    if isinstance(filter_cond, str):
        try:
            filter_cond = json.loads(filter_cond)
        except Exception:
            filter_cond = {}
    print(f"过滤条件:   {json.dumps(filter_cond, ensure_ascii=False) if filter_cond else '(无)'}")
    # Bound channels
    bindings = get_rule_channels(args.id)
    print(f"绑定渠道:   {len(bindings)} 个")
    for b in bindings:
        from src.models.channel import get_by_id
        ch = get_by_id(b.get('channel_id'))
        ch_name = ch['name'] if ch else '?'
        print(f"              → {ch_name}")


def cmd_rule_enable(args):
    _init()
    from src.models.subscription import update_rule
    update_rule(args.id, enabled=1)
    print(f"✅ 规则 #{args.id} 已启用")


def cmd_rule_disable(args):
    _init()
    from src.models.subscription import update_rule
    update_rule(args.id, enabled=0)
    print(f"✅ 规则 #{args.id} 已禁用")


# ══════════════════════════════════════════════════════════════
# COLLECT
# ══════════════════════════════════════════════════════════════

def cmd_collect(args):
    _init()
    mode = args.mode or 'delta'
    print(f"🔍 开始采集 (模式: {mode})...")
    from src.core.scheduler import run_now
    result = run_now(mode=mode)
    status = result.get('status', 'unknown')
    if status == 'ok':
        print(f"✅ 采集完成: {result.get('total_new', 0)} 新增, {result.get('total_rollback', 0)} 回退")
    elif status == 'skipped':
        print(f"⏭️ 跳过: {result.get('reason', '')}")
    else:
        print(f"❌ 采集失败: {result.get('errors', [])}")


# ══════════════════════════════════════════════════════════════
# EXPORT
# ══════════════════════════════════════════════════════════════

def cmd_export(args):
    _init()
    try:
        import yaml
    except ImportError:
        print("❌ 需要 pyyaml: pip install pyyaml")
        sys.exit(1)

    output = args.output or f"/tmp/nsfocus-export-{datetime.now().strftime('%Y%m%d_%H%M%S')}.yaml"

    if args.what == 'customers':
        from src.models.customer import list_all
        data = list_all()
    elif args.what == 'rules':
        from src.models.subscription import list_rules
        data = list_rules(1)
    elif args.what == 'channels':
        from src.models.channel import list_by_user
        from src.core.crypto import decrypt
        data = list_by_user(1)
        # Don't export encrypted config — decrypt for readability
        for ch in data:
            config = ch.get('config', {})
            if isinstance(config, str):
                try:
                    ch['config'] = json.loads(decrypt(config))
                except Exception:
                    ch['config'] = {}
    else:
        print(f"❌ 未知导出类型: {args.what}")
        sys.exit(1)

    with open(output, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    print(f"✅ 已导出 {len(data)} 条 {args.what} → {output}")


# ══════════════════════════════════════════════════════════════
# IMPORT
# ══════════════════════════════════════════════════════════════

def cmd_import(args):
    _init()
    try:
        import yaml
    except ImportError:
        print("❌ 需要 pyyaml: pip install pyyaml")
        sys.exit(1)

    filepath = args.file
    if not os.path.exists(filepath):
        print(f"❌ 文件不存在: {filepath}")
        sys.exit(1)

    with open(filepath, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    if not isinstance(data, list):
        print("❌ YAML 格式错误: 应为列表")
        sys.exit(1)

    # Detect type from content
    if not data:
        print("⚠️ 空列表，跳过")
        return

    first = data[0]
    if 'type' in first and 'webhook_url' not in str(first):
        # Looks like a channel
        _import_channels(data)
    elif 'owned_products' in first:
        _import_customers(data)
    elif 'filter_conditions' in first or 'delay_hours' in first:
        _import_rules(data)
    else:
        print("❌ 无法识别数据类型 (需要 customers/rules/channels)")
        sys.exit(1)


def _import_customers(items):
    from src.models.customer import create, list_all
    existing = {c['name']: c for c in list_all()}
    created = 0
    skipped = 0
    for item in items:
        name = item.get('name', '')
        if name in existing:
            skipped += 1
            continue
        create(
            created_by=1,
            name=name,
            company=item.get('company', ''),
            owned_products=item.get('owned_products', []),
            contact=item.get('contact', ''),
            email=item.get('email', ''),
            phone=item.get('phone', ''),
            notes=item.get('notes', ''),
        )
        created += 1
    print(f"✅ 导入客户: {created} 新建, {skipped} 跳过")


def _import_channels(items):
    from src.models.channel import create, list_by_user
    existing = {c['name']: c for c in list_by_user(1)}
    created = 0
    skipped = 0
    for item in items:
        name = item.get('name', '')
        if name in existing:
            skipped += 1
            continue
        config = item.get('config', {})
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except Exception:
                config = {}
        create(
            user_id=1,
            name=name,
            channel_type=item.get('type', 'wecom'),
            config=config,
        )
        created += 1
    print(f"✅ 导入渠道: {created} 新建, {skipped} 跳过")


def _import_rules(items):
    from src.models.subscription import create_rule, list_rules
    existing = {r['name']: r for r in list_rules(1)}
    created = 0
    skipped = 0
    for item in items:
        name = item.get('name', '')
        if name in existing:
            skipped += 1
            continue
        create_rule(
            user_id=1,
            name=name,
            enabled=item.get('enabled', 1),
            delay_hours=item.get('delay_hours', 0),
            digest_mode=item.get('digest_mode', ''),
        )
        created += 1
    print(f"✅ 导入规则: {created} 新建, {skipped} 跳过")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description='NSFOCUS 升级监控平台 - CLI 管理工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  nsfocus-cli status
  nsfocus-cli customer list
  nsfocus-cli customer add --name "XX公司" --products "WAF,IPS"
  nsfocus-cli channel add --name "企微通知" --type wecom --webhook "https://..."
  nsfocus-cli rule enable 1
  nsfocus-cli collect --mode delta
  nsfocus-cli export customers --output /tmp/customers.yaml
  nsfocus-cli import /tmp/customers.yaml
        """
    )
    sub = parser.add_subparsers(dest='command', help='子命令')

    # status
    p_status = sub.add_parser('status', help='系统状态概览')
    p_status.set_defaults(func=cmd_status)

    # customer
    p_cust = sub.add_parser('customer', help='客户管理')
    p_cust_sub = p_cust.add_subparsers(dest='action')
    p_cust_list = p_cust_sub.add_parser('list', help='列出所有客户')
    p_cust_list.set_defaults(func=cmd_customer_list)
    p_cust_add = p_cust_sub.add_parser('add', help='添加客户')
    p_cust_add.add_argument('--name', required=True, help='客户名称')
    p_cust_add.add_argument('--company', help='公司名称')
    p_cust_add.add_argument('--products', help='持有产品, 逗号分隔 (如 WAF,IPS)')
    p_cust_add.add_argument('--contact', help='联系人')
    p_cust_add.add_argument('--email', help='邮箱')
    p_cust_add.add_argument('--phone', help='电话')
    p_cust_add.add_argument('--notes', help='备注')
    p_cust_add.set_defaults(func=cmd_customer_add)
    p_cust_edit = p_cust_sub.add_parser('edit', help='编辑客户')
    p_cust_edit.add_argument('id', type=int, help='客户 ID')
    p_cust_edit.add_argument('--name', help='新名称')
    p_cust_edit.add_argument('--company', help='新公司')
    p_cust_edit.add_argument('--products', help='新产品列表')
    p_cust_edit.add_argument('--contact', help='新联系人')
    p_cust_edit.add_argument('--email', help='新邮箱')
    p_cust_edit.add_argument('--phone', help='新电话')
    p_cust_edit.add_argument('--notes', help='新备注')
    p_cust_edit.set_defaults(func=cmd_customer_edit)
    p_cust_del = p_cust_sub.add_parser('delete', help='删除客户')
    p_cust_del.add_argument('id', type=int, help='客户 ID')
    p_cust_del.set_defaults(func=cmd_customer_delete)
    p_cust_show = p_cust_sub.add_parser('show', help='查看客户详情')
    p_cust_show.add_argument('id', type=int, help='客户 ID')
    p_cust_show.set_defaults(func=cmd_customer_show)

    # channel
    p_ch = sub.add_parser('channel', help='通知渠道管理')
    p_ch_sub = p_ch.add_subparsers(dest='action')
    p_ch_list = p_ch_sub.add_parser('list', help='列出所有渠道')
    p_ch_list.set_defaults(func=cmd_channel_list)
    p_ch_add = p_ch_sub.add_parser('add', help='添加渠道')
    p_ch_add.add_argument('--name', required=True, help='渠道名称')
    p_ch_add.add_argument('--type', required=True, choices=['wecom', 'dingtalk', 'feishu', 'email'], help='渠道类型')
    p_ch_add.add_argument('--webhook', help='Webhook URL')
    p_ch_add.set_defaults(func=cmd_channel_add)
    p_ch_edit = p_ch_sub.add_parser('edit', help='编辑渠道')
    p_ch_edit.add_argument('id', type=int, help='渠道 ID')
    p_ch_edit.add_argument('--name', help='新名称')
    p_ch_edit.add_argument('--webhook', help='新 Webhook URL')
    p_ch_edit.set_defaults(func=cmd_channel_edit)
    p_ch_del = p_ch_sub.add_parser('delete', help='删除渠道')
    p_ch_del.add_argument('id', type=int, help='渠道 ID')
    p_ch_del.set_defaults(func=cmd_channel_delete)
    p_ch_show = p_ch_sub.add_parser('show', help='查看渠道详情')
    p_ch_show.add_argument('id', type=int, help='渠道 ID')
    p_ch_show.set_defaults(func=cmd_channel_show)
    p_ch_test = p_ch_sub.add_parser('test', help='测试渠道连通性')
    p_ch_test.add_argument('id', type=int, help='渠道 ID')
    p_ch_test.set_defaults(func=cmd_channel_test)

    # rule
    p_rule = sub.add_parser('rule', help='订阅规则管理')
    p_rule_sub = p_rule.add_subparsers(dest='action')
    p_rule_list = p_rule_sub.add_parser('list', help='列出所有规则')
    p_rule_list.set_defaults(func=cmd_rule_list)
    p_rule_add = p_rule_sub.add_parser('add', help='添加规则')
    p_rule_add.add_argument('--name', required=True, help='规则名称')
    p_rule_add.add_argument('--delay', type=int, help='延迟小时数')
    p_rule_add.add_argument('--digest', choices=['weekly', 'monthly', 'quarterly'], help='摘要模式')
    p_rule_add.set_defaults(func=cmd_rule_add)
    p_rule_edit = p_rule_sub.add_parser('edit', help='编辑规则')
    p_rule_edit.add_argument('id', type=int, help='规则 ID')
    p_rule_edit.add_argument('--name', help='新名称')
    p_rule_edit.add_argument('--delay', type=int, help='新延迟小时数')
    p_rule_edit.add_argument('--digest', choices=['weekly', 'monthly', 'quarterly'], help='新摘要模式')
    p_rule_edit.set_defaults(func=cmd_rule_edit)
    p_rule_del = p_rule_sub.add_parser('delete', help='删除规则')
    p_rule_del.add_argument('id', type=int, help='规则 ID')
    p_rule_del.set_defaults(func=cmd_rule_delete)
    p_rule_show = p_rule_sub.add_parser('show', help='查看规则详情')
    p_rule_show.add_argument('id', type=int, help='规则 ID')
    p_rule_show.set_defaults(func=cmd_rule_show)
    p_rule_on = p_rule_sub.add_parser('enable', help='启用规则')
    p_rule_on.add_argument('id', type=int, help='规则 ID')
    p_rule_on.set_defaults(func=cmd_rule_enable)
    p_rule_off = p_rule_sub.add_parser('disable', help='禁用规则')
    p_rule_off.add_argument('id', type=int, help='规则 ID')
    p_rule_off.set_defaults(func=cmd_rule_disable)

    # collect
    p_coll = sub.add_parser('collect', help='手动触发采集')
    p_coll.add_argument('--mode', choices=['delta', 'full'], default='delta', help='采集模式')
    p_coll.set_defaults(func=cmd_collect)

    # export
    p_exp = sub.add_parser('export', help='导出数据')
    p_exp.add_argument('what', choices=['customers', 'rules', 'channels'], help='导出类型')
    p_exp.add_argument('--output', '-o', help='输出文件路径')
    p_exp.set_defaults(func=cmd_export)

    # import
    p_imp = sub.add_parser('import', help='导入数据')
    p_imp.add_argument('file', help='YAML 文件路径')
    p_imp.set_defaults(func=cmd_import)

    args = parser.parse_args()
    if not hasattr(args, 'func'):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == '__main__':
    main()
