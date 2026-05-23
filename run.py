"""NSFOCUS Update Monitor - Application Entry Point"""
import os
import sys

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env file
_env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, val = line.partition('=')
                os.environ.setdefault(key.strip(), val.strip())

from src.app import create_app

app = create_app()

if __name__ == '__main__':
    port = int(os.getenv('MONITOR_PORT', '9999'))
    host = os.getenv('MONITOR_HOST', '0.0.0.0')
    debug = os.getenv('MONITOR_DEBUG', 'false').lower() == 'true'
    app.run(host=host, port=port, debug=debug, threaded=False)
