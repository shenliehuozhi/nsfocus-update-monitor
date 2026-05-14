#!/usr/bin/env python3
"""Run full collection from CLI, bypassing Flask HTTP auth."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import sqlite3, json, time
from src.models.user_session import get_active_sessions
from src.core.scheduler import run_now

print("Step 1: Check active sessions")
sessions = get_active_sessions()
print(f"  Active sessions: {len(sessions)}")
if sessions:
    print(f"  Session 0: user_id={sessions[0]['user_id']}, status={sessions[0]['status']}")
    print(f"  Cookie: {sessions[0]['cookie_value']}")
    cookie = sessions[0]['cookie_value']
else:
    print("  NO SESSIONS!")
    cookie = None

if not cookie:
    print("ERROR: No valid session cookie. Aborting.")
    sys.exit(1)

print("\nStep 2: Run full collection (this will take a while...)")
start = time.time()
result = run_now(mode='full')
elapsed = time.time() - start

print(f"\nCollection done in {elapsed:.0f}s")
print(f"Status: {result.get('status')}")
print(f"Total new: {result.get('total_new')}")
print(f"Total rollback: {result.get('total_rollback')}")
print(f"Errors: {len(result.get('errors', []))}")
if result.get('errors'):
    for e in result['errors'][:5]:
        print(f"  ERROR: {e}")
