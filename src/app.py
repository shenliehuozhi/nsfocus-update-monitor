"""Flask Application Factory"""
import os
import time
import logging
import logging.handlers
from flask import Flask, request, g
from flask_cors import CORS


def create_app(config_path=None):
    app = Flask(__name__,
                template_folder='src/web/templates',
                static_folder='src/web/static')

    # CORS
    CORS(app)

    # Config
    app.config['SECRET_KEY'] = os.getenv('MONITOR_SECRET_KEY', 'dev-secret-change-me')
    app.config['JWT_SECRET'] = os.getenv('MONITOR_JWT_SECRET', 'dev-jwt-secret-change-me')
    app.config['DATA_DIR'] = os.getenv('MONITOR_DATA_DIR',
                                       os.path.join(os.path.dirname(__file__), '..', 'data'))
    app.config['LOG_DIR'] = os.getenv('MONITOR_LOG_DIR',
                                      os.path.join(os.path.dirname(__file__), '..', 'logs'))
    app.config['COLLECT_INTERVAL'] = int(os.getenv('MONITOR_COLLECT_INTERVAL', '4'))
    app.config['ROLLBACK_CONFIRM'] = int(os.getenv('MONITOR_ROLLBACK_CONFIRM', '2'))
    app.config['ATTACHMENT_MAX_SIZE'] = int(os.getenv('MONITOR_ATTACHMENT_MAX_SIZE', '10485760'))

    # Ensure directories exist
    os.makedirs(app.config['DATA_DIR'], exist_ok=True)
    os.makedirs(app.config['LOG_DIR'], exist_ok=True)

    # Hook Flask's own logger into our file handler so uncaught exceptions
    # and werkzeug messages appear in app.log
    from src.core.logger import get_logger
    monitor_logger = get_logger('flask')
    app.logger.handlers = monitor_logger.handlers  # inherit our handlers
    app.logger.setLevel(logging.DEBUG)

    # Also capture werkzeug access logs (suppress duplicate since we have access.log)
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.handlers = monitor_logger.handlers
    werkzeug_logger.setLevel(logging.WARNING)  # Only warnings/errors, not every request

    # Health check
    @app.route('/api/health')
    def health():
        return {'code': 0, 'data': {'status': 'ok'}}

    # ---- Access log middleware ----
    _access_logger = None

    def _get_access_logger():
        nonlocal _access_logger
        if _access_logger is None:
            log_dir = app.config['LOG_DIR']
            os.makedirs(log_dir, exist_ok=True)
            _access_logger = logging.getLogger('access')
            _access_logger.setLevel(logging.INFO)
            _access_logger.propagate = False
            fh = logging.handlers.RotatingFileHandler(
                os.path.join(log_dir, 'access.log'),
            maxBytes=10 * 1024 * 1024,
            backupCount=10
            )
            fh.setFormatter(logging.Formatter('%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
            _access_logger.addHandler(fh)
        return _access_logger

    @app.before_request
    def _access_start():
        g._req_start = time.time()

    @app.after_request
    def _access_log(response):
        duration_ms = int((time.time() - g.get('_req_start', time.time())) * 1000)
        ip = request.headers.get('X-Forwarded-For', request.remote_addr) or '-'
        method = request.method
        path = request.path
        status = response.status_code
        _get_access_logger().info(f'{ip} {method} {path} {status} {duration_ms}ms')
        response.headers['X-Response-Time-ms'] = str(duration_ms)
        return response

    # Initialize database
    from src.models.database import init_db
    init_db(app.config['DATA_DIR'])
    from src.models import init_all_tables
    with app.app_context():
        init_all_tables()

    # Register routes
    from src.web.routes import register_routes
    register_routes(app)

    # Start scheduler
    from src.core.scheduler import start_scheduler
    app.scheduler = start_scheduler(app)

    return app
