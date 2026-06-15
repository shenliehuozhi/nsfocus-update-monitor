#!/usr/bin/env python3
"""Port availability check before service start (INC-001 lesson).

Uses SO_REUSEADDR so TIME_WAIT sockets from a SIGTERM'd previous process
don't block the bind. Falls back to short retries if the port is genuinely
held by another listener.
"""
import os
import socket
import sys
import time

host = os.getenv('MONITOR_HOST', '127.0.0.1')
port = int(os.getenv('MONITOR_PORT', '9999'))


def _try_bind() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # SO_REUSEADDR allows binding to a port in TIME_WAIT state.
    # This is exactly the case here: SIGTERM'd process leaves TIME_WAIT
    # for ~60s, but the new instance should be able to start immediately.
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
        s.close()
        return True
    except OSError:
        return False


if _try_bind():
    print(f'{host}:{port} available')
    sys.exit(0)

# First attempt failed — try a few quick retries before giving up.
# Could be transient: child process exiting, TIME_WAIT not yet bound to us.
for i in range(3):
    time.sleep(0.5)
    if _try_bind():
        print(f'{host}:{port} available (after {i+1} retry)')
        sys.exit(0)

print(f'FATAL: {host}:{port} in use after 3 retries (genuine collision)', file=sys.stderr)
sys.exit(1)
