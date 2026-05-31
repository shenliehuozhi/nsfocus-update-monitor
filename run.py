"""NSFOCUS Update Monitor - Application Entry Point"""
import os
import sys

# ── Determine base directory (supports both script and PyInstaller onefile bundle) ──
if getattr(sys, 'frozen', False):
    # Running as PyInstaller onefile exe: sys._MEIPASS is the temp extraction dir
    BASE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Ensure project root is on path
sys.path.insert(0, BASE_DIR)

# ── Load .env from base directory ──
_env_path = os.path.join(BASE_DIR, '.env')
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, val = line.partition('=')
                os.environ.setdefault(key.strip(), val.strip())

# ── Ensure data/logs directories exist next to the exe (or script dir) ──
DATA_DIR = os.path.join(BASE_DIR, 'data')
LOG_DIR  = os.path.join(BASE_DIR, 'logs')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR,  exist_ok=True)

# Persist via env so models/database.py picks them up
os.environ.setdefault('MONITOR_DATA_DIR', DATA_DIR)
os.environ.setdefault('MONITOR_LOG_DIR',  LOG_DIR)

from src.app import create_app

app = create_app()

if __name__ == '__main__':
    port  = int(os.getenv('MONITOR_PORT',  '9999'))
    host  = os.getenv('MONITOR_HOST',     '127.0.0.1')
    debug = os.getenv('MONITOR_DEBUG',    'false').lower() == 'true'
    app.run(host=host, port=port, debug=debug, threaded=True)