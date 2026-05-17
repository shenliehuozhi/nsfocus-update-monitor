#!/usr/bin/env python3
"""批量刷新57个产品的package_type_discovered.paths[].url"""
import sys, json, time
sys.path.insert(0, 'src')

from src.models.database import query
from src.models.user_session import get_active_sessions
from src.core.scheduler import refresh_pkg_type_single

def main():
    sessions = get_active_sessions()
    if not sessions:
        print("No sessions available")
        sys.exit(1)
    cookie = sessions[0]['cookie_value']

    # Get products needing URL refresh (paths lack url)
    conn_query = query("""
        SELECT id, name, package_type_discovered
        FROM content_sources
        WHERE is_active = 1 AND package_type_discovered IS NOT NULL
    """)

    need_refresh = []
    for row in conn_query:
        if row['package_type_discovered']:
            data = json.loads(row['package_type_discovered'])
            paths = data.get('paths', [])
            has_url = any(p.get('url') for p in paths)
            if not has_url and paths:
                need_refresh.append((row['id'], row['name']))

    print(f"Need refresh: {len(need_refresh)} products")
    for i, (sid, name) in enumerate(need_refresh):
        print(f"[{i+1}/{len(need_refresh)}] Refreshing {name} (id={sid})...", flush=True)
        try:
            refresh_pkg_type_single(sid, cookie)
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
        time.sleep(0.5)

    print("Done!")

if __name__ == '__main__':
    main()
