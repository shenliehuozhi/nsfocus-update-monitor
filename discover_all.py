#!/usr/bin/env python3
"""Discover package types for all products via deep traversal.
Much faster than full collection - visits only version index pages (not detail pages).
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import sqlite3, json, time
from src.models.snapshot import list_sources, update_source
from src.models.user_session import get_active_sessions
from src.collectors.nsfocus import NsfocusCollector

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

log("Step 1: Get active session")
sessions = get_active_sessions()
if not sessions:
    log("ERROR: No active sessions")
    sys.exit(1)
cookie = sessions[0]['cookie_value']
log(f"  Cookie: {cookie}")

log("Step 2: Load all products")
sources = list_sources('nsfocus')
log(f"  Total nsfocus sources: {len(sources)}")

collector = NsfocusCollector()
collector._set_cookie(cookie)

log("Step 3: Discover package types for each product")
results = {}
for i, src in enumerate(sources):
    sid = src['id']
    name = src['name']
    log(f"[{i+1}/{len(sources)}] {name} (id={sid})...")
    try:
        result = collector.discover_package_types(sid, cookie)
        results[sid] = result
        log(f"  -> types={result.get('types',[])}, branches={len(result.get('branches',{}))}")
    except Exception as e:
        log(f"  -> ERROR: {e}")
        results[sid] = {'types': [], 'branches': {}, 'modes': {}}

log("Step 4: Save to DB")
for sid, result in results.items():
    pkg_json = json.dumps(result)
    update_source(sid, package_type=pkg_json,
                  package_type_discovered=pkg_json,
                  package_type_changed=0)

log("Done!")
# Summary
type_counts = {k: len(v.get('types', [])) for k, v in results.items()}
with_types = sum(1 for v in type_counts.values() if v > 0)
log(f"Summary: {with_types}/{len(results)} products have package types")
