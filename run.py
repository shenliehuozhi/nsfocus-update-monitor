"""NSFOCUS Update Monitor - Application Entry Point"""
import os
import sys

# ── Determine base directory (supports both script and PyInstaller onefile bundle) ──
if getattr(sys, 'frozen', False):
    # Running as PyInstaller onefile exe: sys._MEIPASS is the temp extraction dir
    _MEIPASS = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    BASE_DIR = os.path.dirname(sys.executable)

    # _MEIPASS temp dir is often read-only on Windows (SmartScreen, antivirus, etc.)
    # Probe: if BASE_DIR/data is not writable, fall back to per-user app data dir
    _probe_dir = os.path.join(BASE_DIR, 'data')
    try:
        os.makedirs(_probe_dir, exist_ok=True)
        with open(os.path.join(_probe_dir, '.probe'), 'w') as f:
            f.write('')
        os.remove(os.path.join(_probe_dir, '.probe'))
        DATA_DIR = _probe_dir
    except Exception:
        # Fall back to ~/AppData/Local/nsfocus-monitor-data (Windows) or ~/.local (Linux/macOS)
        if sys.platform == 'win32':
            DATA_DIR = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~/AppData/Local')), 'nsfocus-monitor-data')
        else:
            DATA_DIR = os.path.join(os.path.expanduser('~/.local'), 'share', 'nsfocus-monitor-data')
        os.makedirs(DATA_DIR, exist_ok=True)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(BASE_DIR, 'data')

LOG_DIR = os.path.join(BASE_DIR, 'logs')

# Ensure directories exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# Set env BEFORE importing app — app.py reads MONITOR_DATA_DIR at module level
os.environ['MONITOR_DATA_DIR'] = DATA_DIR
os.environ['MONITOR_LOG_DIR'] = LOG_DIR

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

from src.app import create_app
from src.models.database import init_db
from src.models.user import list_users, create_user
import secrets, bcrypt

app = create_app()

# ── First-run setup: create admin if no users exist ─────────────────────────
def _first_run_setup():
    try:
        init_db()
    except Exception as e:
        import sys as _sys
        _sys.stderr.write(f'[ERROR] init_db failed: {e}\n')
        return
    if list_users():
        return  # already initialized
    raw_password = secrets.token_urlsafe(12)
    password_hash = bcrypt.hashpw(raw_password.encode(), bcrypt.gensalt()).decode()
    create_user('admin', password_hash, is_admin=True)
    # Write to file
    pwd_file = os.path.join(DATA_DIR, 'initial_password.txt')
    try:
        with open(pwd_file, 'w') as f:
            f.write(raw_password)
    except Exception:
        pass
    # Print to stderr so it shows in console / exe popup window
    sys.stderr.write(f'\n{"="*60}\n')
    sys.stderr.write(f'  绿盟升级监控 — 初始化完成\n')
    sys.stderr.write(f'{"="*60}\n')
    sys.stderr.write(f'  用户名: admin\n')
    sys.stderr.write(f'  初始密码: {raw_password}\n')
    sys.stderr.write(f'  密码文件: {pwd_file}\n')
    sys.stderr.write(f'{"="*60}\n\n')
    sys.stderr.flush()

_first_run_setup()

if __name__ == '__main__':
    port  = int(os.getenv('MONITOR_PORT',  '9999'))
    host  = os.getenv('MONITOR_HOST',     '127.0.0.1')
    debug = os.getenv('MONITOR_DEBUG',    'false').lower() == 'true'
    app.run(host=host, port=port, debug=debug, threaded=True)