#!/usr/bin/env python3
"""
Session Pollution 检测脚本

目的：遍历所有产品页面，每访问一个页面后立即检查 listBvsV6/v/bvssys
的下载链接格式是否从 downloads/id（标品）变成 downloadsVm/id（虚拟化）。

用法：
    python3 test_session_pollution.py <PHPSESSID>
"""

import sys
import urllib.request
import time
import sqlite3
import hashlib

# ------------------------------------------------------------
# 配置
# ------------------------------------------------------------
BVS_CHECK_URL = "https://update.nsfocus.com/update/listBvsV6/v/bvssys"
CHECK_INTERVAL = 0.5  # 每次访问后等这么久再检查 BVS 页面（秒）

# ------------------------------------------------------------
# 从数据库读取所有需要检测的 source_url
# ------------------------------------------------------------
def get_all_source_urls(db_path):
    """从 content_sources 表读取所有 entry_url（去重）"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT DISTINCT entry_url, name FROM content_sources WHERE entry_url IS NOT NULL AND entry_url != ''"
    )
    rows = cur.fetchall()
    conn.close()
    return [(r['name'], r['entry_url']) for r in rows]

# ------------------------------------------------------------
# 模拟采集器导航链：每个 source_url 下探到最后一层版本页
# ------------------------------------------------------------
def get_version_urls(session, entry_url):
    """
    从产品入口页抓取所有版本子页面 URL。
    返回 [(version_name, sub_url), ...]
    """
    try:
        req = urllib.request.Request(entry_url)
        req.add_header('Cookie', f'PHPSESSID={session}')
        req.add_header('User-Agent', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36')
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
    except Exception as e:
        return []

    # 检查是否跳转到登录页
    if is_login_page(html, resp.geturl()):
        return []

    version_urls = []
    # 提取所有产品版本子链接（相对路径）
    import re
    base = resp.geturl().rsplit('/', 1)[0] if '/' in resp.geturl() else ''
    links = re.findall(r'href=["\']([^"\']+)["\']', html)
    for link in links:
        link = link.strip()
        if not link or link.startswith('#') or link.startswith('javascript'):
            continue
        if '/update/' in link and ('detail' in link or 'list' in link or 'Index' in link):
            if link.startswith('/'):
                full_url = 'https://update.nsfocus.com' + link
            elif link.startswith('http'):
                full_url = link
            else:
                full_url = base + '/' + link
            # 去重
            if not any(v[1] == full_url for v in version_urls):
                version_urls.append((link, full_url))

    return version_urls

def is_login_page(html, url):
    if '/login' in url or '/portal/' in url:
        return True
    if html[:500].count('登录') > 2 or html[:500].count('login') > 2:
        return True
    return False

# ------------------------------------------------------------
# 检查 BVS 页面当前是什么格式
# ------------------------------------------------------------
def check_bvs_format(session):
    """
    返回 'standard' (downloads/id), 'vm' (downloadsVm/id), 'none' (无链接), 'login' (登录页)
    """
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

    std_count = html.count('/update/downloads/id/')
    vm_count = html.count('/update/downloadsVm/id/')

    if std_count > 0 and vm_count == 0:
        return 'standard'
    elif vm_count > 0 and std_count == 0:
        return 'vm'
    elif vm_count > 0 and std_count > 0:
        return f'mixed(std={std_count}, vm={vm_count})'
    else:
        return 'none'

# ------------------------------------------------------------
# 核心检测逻辑
# ------------------------------------------------------------
def run(session):
    db_path = '/root/nsfocus-monitor/data/nsfocus_monitor.db'

    print(f"[初始化] 检查 BVS 初始状态 ...")
    initial_format = check_bvs_format(session)
    print(f"[初始化] BVS 页面格式: {initial_format}")

    if initial_format == 'login':
        print("[错误] session 已失效，请重新提供有效的 PHPSESSID")
        return
    if initial_format == 'vm':
        print("[警告] 初始 BVS 页面就是 vm 格式，session 可能已异常")

    sources = get_all_source_urls(db_path)
    print(f"[信息] 共 {len(sources)} 个产品入口待检测\n")

    # 全局状态
    current_bvs_format = initial_format
    polluted = False
    polluted_after_url = None

    for idx, (product_name, entry_url) in enumerate(sources):
        print(f"\r[{idx+1}/{len(sources)}] 遍历产品: {product_name[:40]:<40}  当前BVS={current_bvs_format}", end='', flush=True)

        # 1. 访问入口页
        try:
            version_urls = get_version_urls(session, entry_url)
        except Exception as e:
            version_urls = []

        # 2. 访问每个版本子页面
        for ver_name, ver_url in version_urls:
            try:
                req = urllib.request.Request(ver_url)
                req.add_header('Cookie', f'PHPSESSID={session}')
                req.add_header('User-Agent', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36')
                with urllib.request.urlopen(req, timeout=15) as resp:
                    # 访问后立即检查 BVS
                    time.sleep(CHECK_INTERVAL)
                    new_format = check_bvs_format(session)

                    if new_format != current_bvs_format and new_format != 'error':
                        print(f"\n\n*** [检测异常] ***")
                        print(f"  产品: {product_name}")
                        print(f"  访问URL: {ver_url}")
                        print(f"  变化前 BVS 格式: {current_bvs_format}")
                        print(f"  变化后 BVS 格式: {new_format}")
                        polluted = True
                        polluted_after_url = ver_url
                        current_bvs_format = new_format
                        # 不退出，继续检测看是否还会变化
            except Exception as e:
                pass  # 单个 URL 失败不打断整体流程

        # 每个入口测完了再统一检查一次 BVS 基准状态
        time.sleep(CHECK_INTERVAL)
        final_format = check_bvs_format(session)
        if final_format != current_bvs_format and final_format != 'error':
            if not polluted:
                print(f"\n\n*** [检测异常-入口级] ***")
                print(f"  产品: {product_name}")
                print(f"  入口URL: {entry_url}")
                print(f"  变化前 BVS 格式: {current_bvs_format}")
                print(f"  变化后 BVS 格式: {final_format}")
                polluted = True
                polluted_after_url = entry_url
                current_bvs_format = final_format

    print(f"\n\n{'='*50}")
    if polluted:
        print(f"结论: session 发生污染，疑似在访问 {polluted_after_url} 后变化")
    else:
        print(f"结论: 未检测到 session 污染，BVS 格式全程保持: {initial_format}")
    print(f"总计检测产品入口: {len(sources)}")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法: python3 test_session_pollution.py <PHPSESSID>")
        sys.exit(1)
    session = sys.argv[1].strip()
    run(session)
