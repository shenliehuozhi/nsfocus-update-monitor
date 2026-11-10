#!/usr/bin/env python3
"""数据目录体检报告 —— 只读, 一个删除动作都不做。

为什么只读: 备份文件和迁移残渣表都属于"删了就没有退路"的东西, 该由人逐条拍板。
本脚本负责把散在 data/ 里的负担变成一份带命令的清单, 决定权留在你手上。

用法:
  python3 scripts/data_hygiene_report.py             # 看报告
  python3 scripts/data_hygiene_report.py --json      # 机器可读

数据来源: MONITOR_DATA_DIR(默认仓库 data/), 全程 sqlite mode=ro 连接。
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# 疑似一次性迁移/手工备份的表名特征(生产库里这类表比活数据还大)
JUNK_TABLE_HINTS = re.compile(
    r'^_|backup|deleted|pre_|before_|_202\d{6}|rollback_test|_fix_', re.I)
# 活数据表 —— 无论名字多可疑都不进清单
ALWAYS_KEEP = {
    'snapshots', 'delivery_log', 'channels', 'customers', 'subscription_rules',
    'content_sources', 'event_log', 'system_settings', 'audit_log', 'users',
    'user_sessions', 'heartbeat_log', 'delayed_queue', 'digest_queue',
    'sqlite_sequence',
}
# 备份轮转时默认保留份数(与 delete_snapshot.py 的 MONITOR_DB_BAK_KEEP 对齐)
KEEP = int(os.getenv('MONITOR_DB_BAK_KEEP', '3'))


def data_dir() -> Path:
    d = os.getenv('MONITOR_DATA_DIR')
    return Path(d) if d else Path(__file__).resolve().parents[1] / 'data'


def human(n: float) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(n) < 1024 or unit == 'GB':
            return f'{n:.1f}{unit}' if unit != 'B' else f'{int(n)}B'
        n /= 1024
    return f'{n:.1f}GB'


def backups(dd: Path) -> dict:
    files = sorted(dd.glob('*.bak*'), key=lambda p: p.stat().st_mtime, reverse=True)
    rows = [{'name': p.name, 'bytes': p.stat().st_size,
             'age_days': round((time.time() - p.stat().st_mtime) / 86400, 1),
             'kind': 'pre_delete' if '_pre_delete_' in p.name else 'manual/other'}
            for p in files]
    stale = [r for r in rows if r['kind'] != 'pre_delete'][KEEP:]
    return {
        'count': len(rows),
        'total_bytes': sum(r['bytes'] for r in rows),
        'items': rows,
        # pre_delete 那批由 delete_snapshot.py 的轮转自动收敛, 不必手工管
        'candidates': stale,
        'candidate_bytes': sum(r['bytes'] for r in stale),
    }


def stray_dbs(dd: Path) -> dict:
    """data/ 与仓库根目录下的一次性操作库(`_pre_*.db`)和空壳 DB。

    `.bak` 之外的整库副本用的是 `_pre_<动作>_<时间戳>.db` 命名, 轮转同样管不到;
    0 字节的那些是历史误提交的空壳, 内容恒为空。
    """
    root = dd.parent
    items = []
    for base in (dd, root):
        for p in sorted(base.glob('*.db')):
            if p.name == 'nsfocus_monitor.db' and base == dd:
                continue
            st = p.stat()
            kind = 'empty' if st.st_size == 0 else 'pre-op copy'
            items.append({'path': str(p), 'bytes': st.st_size, 'mtime': st.st_mtime,
                          'age_days': round((time.time() - st.st_mtime) / 86400, 1),
                          'kind': kind})
    copies = sorted([i for i in items if i['kind'] == 'pre-op copy'],
                    key=lambda i: i['mtime'], reverse=True)
    cand = [i for i in items if i['kind'] == 'empty'] + copies[KEEP:]
    return {'items': items, 'candidates': cand,
            'candidate_bytes': sum(i['bytes'] for i in cand)}


def junk_tables(db: Path) -> dict:
    if not db.exists():
        return {'error': f'{db} 不存在', 'items': [], 'total_rows': 0}
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            if r[0] not in ALWAYS_KEEP and JUNK_TABLE_HINTS.search(r[0])]
        items = []
        for n in names:
            try:
                cnt = con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
            except sqlite3.Error as e:
                cnt = -1
                items.append({'table': n, 'rows': cnt, 'error': str(e)})
                continue
            items.append({'table': n, 'rows': cnt})
        # 表级精确大小要 dbstat 虚表, 多数发行版没编进去 → 退化成整库大小 + 行数
        page_bytes = con.execute('PRAGMA page_size').fetchone()[0]
        pages = con.execute('PRAGMA page_count').fetchone()[0]
        has_dbstat = _has_dbstat(con)
        if has_dbstat:
            for i in items:
                try:
                    i['bytes'] = con.execute(
                        'SELECT COALESCE(SUM(pgsize),0) FROM dbstat WHERE name=?',
                        (i['table'],)).fetchone()[0]
                except sqlite3.Error:
                    i['bytes'] = 0
        return {'items': items, 'total_rows': sum(i['rows'] for i in items if i['rows'] > 0),
                'total_bytes': sum(i.get('bytes', 0) for i in items),
                'db_bytes': page_bytes * pages,
                'dbstat_available': has_dbstat}
    finally:
        con.close()


def _has_dbstat(con) -> bool:
    try:
        con.execute('SELECT 1 FROM dbstat LIMIT 1').fetchone()
        return True
    except sqlite3.Error:
        return False


def tracked_data_files(root: Path) -> list:
    """仍被 git 跟踪的 data/ 文件 —— 这些会随仓库推到远端。"""
    try:
        r = subprocess.run(['git', '-C', str(root), 'ls-files', '--', 'data', '*.db', '*.bak*'],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln for ln in r.stdout.splitlines() if ln]


def main() -> int:
    ap = argparse.ArgumentParser(description='数据目录体检(只读)')
    ap.add_argument('--json', action='store_true', help='输出 JSON')
    args = ap.parse_args()

    dd = data_dir()
    report = {
        'data_dir': str(dd),
        'db_bytes': dd.joinpath('nsfocus_monitor.db').stat().st_size
        if dd.joinpath('nsfocus_monitor.db').exists() else 0,
        'backups': backups(dd),
        'stray_dbs': stray_dbs(dd),
        'junk_tables': junk_tables(dd / 'nsfocus_monitor.db'),
        'tracked_in_git': tracked_data_files(dd.parent),
        'keep_policy': KEEP,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    b = report['backups']
    print(f'数据目录: {dd}   主库: {human(report["db_bytes"])}\n')

    print(f'== 备份文件: {b["count"]} 份, 合计 {human(b["total_bytes"])} ==')
    for r in b['items'][:12]:
        print(f'  {human(r["bytes"]):>9}  {r["age_days"]:>6} 天前  [{r["kind"]:>12}]  {r["name"]}')
    if len(b['items']) > 12:
        print(f'  ... 另有 {len(b["items"]) - 12} 份')
    print(f'  其中非 pre_delete 命名(轮转管不到)的清理候选: {len(b["candidates"])} 份 '
          f'{human(b["candidate_bytes"])}')
    if b['candidates']:
        print('  建议命令(自己核对后再贴, 本脚本不执行):')
        print(f'    cd {dd} && ls -la *.bak*   # 先看清楚')
        print(f'    cd {dd} && rm -f ' + ' '.join(f'"{r["name"]}"' for r in b['candidates'][:5]) + ' ...')

    jt = report['junk_tables']
    print(f'\n== 疑似迁移残渣表: {len(jt.get("items", []))} 张, 合计 {jt.get("total_rows", 0)} 行 '
          f'/ {human(jt.get("total_bytes", 0))}  (dbstat 可用: {jt.get("dbstat_available")}) ==')
    for i in sorted(jt.get('items', []), key=lambda x: x.get('bytes', 0), reverse=True):
        err = f"  ({i['error']})" if 'error' in i else ''
        size = human(i['bytes']) if 'bytes' in i else '?'
        print(f'  {size:>9}  {i["rows"]:>7} 行  {i["table"]}{err}')
    if jt.get('items'):
        print('  DROP 前先确认没有代码引用: '
              f'grep -rn "<表名>" src/ scripts/')
        print('  DROP 后需要 VACUUM 才真正回收磁盘(注意: VACUUM 期间磁盘峰值约 2 倍库大小)')

    sd = report['stray_dbs']
    if sd['items']:
        print(f'\n== 一次性操作库 / 空壳 DB: {len(sd["items"])} 个, '
              f'清理候选 {len(sd["candidates"])} 个 {human(sd["candidate_bytes"])} ==')
        cand_paths = {i['path'] for i in sd['candidates']}
        for s in sd['items']:
            print(f'  {human(s["bytes"]):>9}  {s["age_days"]:>6} 天前  '
                  f'[{s["kind"]:>12}]  {s["path"]}' + ('   ← 候选' if s['path'] in cand_paths else ''))
        if sd['candidates']:
            paths = [f'"{i["path"]}"' for i in sd['candidates']]
            print('  建议命令(自己核对后再贴, 本脚本不执行):')
            print('    rm -f ' + ' '.join(paths[:6]) + (' ...' if len(paths) > 6 else ''))

    reclaim = b['candidate_bytes'] + sd['candidate_bytes'] + jt.get('total_bytes', 0)
    print(f'\n== 合计可回收 ≈ {human(reclaim)} '
          f'(备份 {human(b["candidate_bytes"])} + 操作库/空壳 {human(sd["candidate_bytes"])} '
          f'+ 残渣表 {human(jt.get("total_bytes", 0))}) ==')

    if report['tracked_in_git']:
        print(f'\n== 仍被 git 跟踪的 data 相关文件({len(report["tracked_in_git"])} 个, 会随推送上远端) ==')
        for t in report['tracked_in_git']:
            print(f'  {t}')
        print('  取消跟踪(保留本地文件): git rm --cached <path>  —— 注意这会改变仓库内容, 属人工决策')
    print('\n本脚本不删除任何文件。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
