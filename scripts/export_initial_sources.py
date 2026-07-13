#!/usr/bin/env python3
"""Export current DB content_sources (nsfocus) → data/initial_sources.json.

用途: 每次重新打包 exe 前运行一次,把当前 DB 的产品/策略/包类型导出成
       打包种子,让下一版 exe 的出厂种子反映最新运营状态。

is_active 重写规则 (重要):
    1. 若 data/default_active_sources.txt 存在,白名单内的 name 强制 is_active=1,
       其余强制 is_active=0。这样新部署用户看到的默认状态是收敛的,
       而不是出厂 79 条全启用。
    2. 若该文件不存在,沿用 DB 当前 is_active 值(原始 1:1 导出)。

Usage:
    python3 scripts/export_initial_sources.py            # 默认覆盖 data/initial_sources.json
    python3 scripts/export_initial_sources.py --dry-run  # 仅打印,不写文件
    python3 scripts/export_initial_sources.py --out /path/to/other.json
    python3 scripts/export_initial_sources.py --no-rewrite-active  # 跳过白名单重写,DB 原值导出
"""
import sys, os, json, argparse, sqlite3

# allow `python3 scripts/export_initial_sources.py` from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT  = os.path.join(PROJECT_ROOT, 'data', 'initial_sources.json')
WHITELIST    = os.path.join(PROJECT_ROOT, 'data', 'default_active_sources.txt')
# 真实运行时 DB 路径 (根目录那 3 个 0 字节 .db 是历史残留,不是真实数据源)
DEFAULT_DB   = os.path.join(PROJECT_ROOT, 'data', 'nsfocus_monitor.db')


def _json_or_none(raw):
    """DB 里 package_type / package_type_discovered 存的是 JSON 字符串。
    空串/None 时返回 None,有内容时 json.loads 还原成对象/数组。"""
    if raw is None or raw == '':
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def fetch_rows(db_path):
    """直接走 sqlite3,不依赖 src.models.database 初始化(避免误触发其他副作用)。
    按 id 升序(跟出厂 JSON 的写入顺序一致)。"""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT name, entry_url, strategy, category, display_name,
                   is_active, is_manual, package_type, package_type_discovered
              FROM content_sources
             WHERE source_type = 'nsfocus'
             ORDER BY id
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def load_whitelist(path):
    """读取白名单文件,返回 set[str]。空文件/不存在返回 None (调用方决定是否跳过重写)。"""
    if not os.path.exists(path):
        return None
    names = set()
    with open(path, encoding='utf-8') as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            names.add(s)
    return names


def row_to_json(row, *, whitelist=None):
    """镜像 _init_content_sources 读 JSON 时使用的字段集合
    (src/models/__init__.py 行 96-107),保证导出后再导入能完整还原。

    whitelist 不为 None 时,按白名单重写 is_active。
    """
    out = {
        'name':         row['name'],
        'entry_url':    row['entry_url'] or '',
        'strategy':     row['strategy'] or 'auto',
        'category':     row['category'] or 'security',
        'display_name': row['display_name'] or row['name'],
        'is_active':    int(row['is_active']) if row['is_active'] is not None else 1,
        'is_manual':    int(row['is_manual']) if row['is_manual'] is not None else 0,
    }
    if whitelist is not None:
        out['is_active'] = 1 if out['name'] in whitelist else 0

    pt  = _json_or_none(row['package_type'])
    ptd = _json_or_none(row['package_type_discovered'])
    if pt is not None:
        out['package_type'] = pt
    if ptd is not None:
        out['package_type_discovered'] = ptd
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dry-run', action='store_true', help='只打印预览,不写文件')
    ap.add_argument('--out', default=DEFAULT_OUT, help=f'输出路径 (默认: {DEFAULT_OUT})')
    ap.add_argument('--db',  default=DEFAULT_DB,  help=f'SQLite 路径 (默认: {DEFAULT_DB})')
    ap.add_argument('--no-rewrite-active', action='store_true',
                    help='跳过白名单重写,DB 当前 is_active 原值导出 (覆盖 data/default_active_sources.txt)')
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f'[export] FATAL: DB 不存在: {args.db}', file=sys.stderr)
        return 2

    rows = fetch_rows(args.db)
    whitelist = None
    if not args.no_rewrite_active:
        whitelist = load_whitelist(WHITELIST)
    jsons = [row_to_json(r, whitelist=whitelist) for r in rows]

    # 统计
    active_count = sum(1 for j in jsons if j['is_active'] == 1)
    print(f'[export] DB 来源: {args.db}')
    print(f'[export] nsfocus 产品总数: {len(jsons)}')
    if whitelist is not None:
        print(f'[export] 白名单 ({WHITELIST}): {len(whitelist)} 个 → 导出后 active={active_count}, inactive={len(jsons)-active_count}')
    else:
        print(f'[export] 未应用白名单 (DB 原值导出), active={active_count}')
    print(f'[export] 输出路径: {args.out}')
    print('[export] 前 3 条预览:')
    for i, item in enumerate(jsons[:3], 1):
        preview = json.dumps(item, ensure_ascii=False, indent=2)
        print(f'  --- #{i} (is_active={item["is_active"]}, strategy={item["strategy"]}) ---')
        print(f'  {preview[:500]}')

    if args.dry_run:
        print('[export] --dry-run: 跳过写文件')
        return 0

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(jsons, f, ensure_ascii=False, indent=2)
        f.write('\n')
    size_kb = os.path.getsize(args.out) / 1024
    print(f'[export] 写入完成: {args.out} ({size_kb:.1f} KB, {active_count} active / {len(jsons)-active_count} inactive)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
