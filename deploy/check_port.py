#!/usr/bin/env python3
"""Port availability check before service start (INC-001 lesson)."""
import os
import socket
import sys

host = os.getenv('MONITOR_HOST', '0.0.0.0')
port = int(os.getenv('MONITOR_PORT', '9999'))

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind((host, port))
    s.close()
    print(f'{host}:{port} available')
    sys.exit(0)
except OSError:
    print(f'FATAL: {host}:{port} already in use', file=sys.stderr)
    sys.exit(1)
