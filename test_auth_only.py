import sys, time, os
sys.path.insert(0, 'src')
os.environ['MONITOR_DATA_DIR'] = '/root/nsfocus-monitor/data'

print("A", flush=True)
# Import minimal pieces
from flask import Flask, jsonify
print("B", flush=True)
from src.web.auth import create_token, decode_token
print("C", flush=True)

# Test create/decode token directly
token = create_token(1, "admin")
print("D token created: %s..." % token[:20], flush=True)
payload = decode_token(token)
print("E decode: %s" % payload, flush=True)
print("DONE", flush=True)