#!/usr/bin/env python3
"""
Session Pollution 检测脚本 v3

遍历 package_type_discovered.paths 里的最终页面 URL，
每访问一个后检查 BVS bvssys 是否变成 vm。
发现污染后跳过该 URL，继续检测其他 URL，收集所有污染源。

用法：
    python3 test_session_pollution3.py <PHPSESSID>
"""

import sys
import urllib.request
import time
import json

BVS_CHECK_URL = "https://update.nsfocus.com/update/listBvsV6/v/bvssys"
CHECK_INTERVAL = 0.3

def get_all_paths(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT id, name, entry_url, package_type_discovered FROM content_sources WHERE is_active = 1"
    )
    rows = cur.fetchall()
    conn.close()
    result = []
    for r in rows:
        name = r['name']
        pt = r['package_type_discovered']
        if pt:
            try:
                data = json.loads(pt)
                for p in data.get('paths', []):
                    url = p.get('url', '')
                    if url:
                        result.append((name, url))
            except:
                pass
    return result

def is_login_page(html, url):
    if '/login' in url or '/portal/' in url:
        return True
    if html[:500].count('登录') > 2 or html[:500].count('login') > 2:
        return True
    return False

def check_bvs_format(session):
    try:
        req = urllib.request.Request(BVS_CHECK_URL)
        req.add_header('Cookie', f'PHPSESSID={session}')
        req.add_header('User-Agent', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36')
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
    except Exception as e:
        return f'error: {e}'

    if is_login_page(html, resp.geturl()):
        return 'login'

    std = html.count('/update/downloads/id/')
    vm = html.count('/update/downloadsVm/id/')
    if std > 0 and vm == 0:
        return 'standard'
    elif vm > 0 and std == 0:
        return 'vm'
    elif vm > 0 and std > 0:
        return f'mixed(std={std}, vm={vm})'
    else:
        return 'none'

def fetch_url(session, url):
    full_url = f'https://update.nsfocus.com{url}' if url.startswith('/') else url
    try:
        req = urllib.request.Request(full_url)
        req.add_header('Cookie', f'PHPSESSID={session}')
        req.add_header('User-Agent', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36')
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read().decode('utf-8', errors='ignore')
    except Exception as e:
        return None

def run(session):
    db_path = '/root/nsfocus-monitor/data/nsfocus_monitor.db'
    paths = get_all_paths(db_path)
    print(f"[初始化] 检查 BVS 初始状态 ...")
    initial_format = check_bvs_format(session)
    print(f"[初始化] BVS 页面格式: {initial_format}")
    if initial_format == 'login':
        print("[错误] session 已失效")
        return
    if initial_format == 'vm':
        print("[警告] 初始就是 vm 格式，从略过已知污染源开始测试 ...")

    # 已知污染源
    skip_urls = {'/update/selectPro/id/2'}
    pollution_sources = []  # [(pollutant_url, bvs_format_after)]

    current = initial_format
    current_product = None
    total = len(paths)
    checked = 0
    skipped = 0

    for idx, (product_name, url) in enumerate(paths):
        if product_name != current_product:
            current_product = product_name
            print(f"\n--- {product_name} ---")

        # 跳过已知的污染源
        if url in skip_urls:
            print(f"  [{idx+1}/{total}] {url[:70]}  [跳过-已知污染源]")
            skipped += 1
            continue

        print(f"  [{idx+1}/{total}] {url[:70]}", end='', flush=True)
        checked += 1

        html = fetch_url(session, url)
        time.sleep(CHECK_INTERVAL)
        new_format = check_bvs_format(session)

        if new_format != current and new_format not in ('error',):
            print(f"  >>> 污染! BVS={new_format} (前={current})")
            pollution_sources.append((url, new_format))
            skip_urls.add(url)  # 把这个也加入跳过
            current = new_format
        else:
            print(f"  BVS={new_format}")

    print(f"\n{'='*60}")
    print(f"总计检测: {checked} 个 URL")
    print(f"已知污染源: {len(pollution_sources)} 个")
    for u, f in pollution_sources:
        print(f"  污染URL: {u}")
        print(f"  污染后BVS格式: {f}")
    if not pollution_sources:
        print(f"结论: 未检测到额外污染源，全程保持: {initial_format}")
    else:
        print(f"结论: 共发现 {len(pollution_sources)} 个污染URL，已全部跳过")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法: python3 test_session_pollution3.py <PHPSESSID>")
        sys.exit(1)
    run(sys.argv[1].strip())
