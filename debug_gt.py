#!/usr/bin/env python3
"""Debug: trace where '>' is introduced in WAF discover"""
import sys
sys.path.insert(0, '/root/nsfocus-monitor')

from src.collectors.nsfocus import NsfocusCollector
import re

collector = NsfocusCollector()

# Only WAF (source_id=1)
source_ids = [1]

print("=== Fetching entry page ===")
collector.fetch_entry_pages(source_ids)
print(f"entry_pages keys: {list(collector.entry_pages.keys())}")

entry_html = collector.entry_pages.get(1, '')
print(f"WAF entry HTML length: {len(entry_html)}")

print("\n=== Running discover_package_types for WAF ===")
# Call the actual discover function
result = collector.discover_package_types(source_ids)

print(f"\nResult keys: {result.keys() if isinstance(result, dict) else 'not a dict'}")
if isinstance(result, dict):
    for sid, data in result.items():
        paths = data.get('paths', [])
        print(f"\nsource_id={sid}, {len(paths)} paths total")
        for p in paths[:5]:
            print(f"  chain={p.get('chain')!r}")
            print(f"  paths={p.get('paths')[:3] if p.get('paths') else 'none'}")
        if len(paths) > 5:
            print(f"  ... ({len(paths)-5} more)")
elif isinstance(result, list):
    print(f"Result is list with {len(result)} items")
    for p in result[:5]:
        print(f"  chain={p.get('chain')!r}")