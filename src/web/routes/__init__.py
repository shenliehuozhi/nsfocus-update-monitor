"""Register all API routes on the Flask app."""

import hashlib
import os

from flask import Flask, request, send_from_directory


def register_routes(app: Flask):
    """Register all route blueprints."""
    from src.web.routes.auth_routes import bp as auth_bp
    from src.web.routes.session_routes import bp as session_bp
    from src.web.routes.api_routes import (
        bp_sources, bp_channels, bp_customers,
        bp_subscriptions, bp_history, bp_settings,
        bp_options, bp_latest, bp_snap
    )
    from src.web.routes.system_routes import bp as bp_system
    from src.web.routes.diff_routes import bp_diff
    from src.web.routes.metrics_routes import bp as bp_metrics
    from src.web.routes.cve_routes import bp as bp_cve

    app.register_blueprint(auth_bp)
    app.register_blueprint(session_bp)
    app.register_blueprint(bp_sources)
    app.register_blueprint(bp_channels)
    app.register_blueprint(bp_customers)
    app.register_blueprint(bp_subscriptions)
    app.register_blueprint(bp_history)
    app.register_blueprint(bp_settings)
    app.register_blueprint(bp_options)
    app.register_blueprint(bp_latest)
    app.register_blueprint(bp_snap)
    app.register_blueprint(bp_system)
    app.register_blueprint(bp_diff)
    app.register_blueprint(bp_metrics)
    app.register_blueprint(bp_cve)

    # Serve SPA index
    templates_dir = os.path.join(os.path.dirname(__file__), '..', 'templates')
    static_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'web', 'static')

    @app.route('/')
    def index():
        from src.web.asset_versioning import versioned_index
        with open(os.path.join(templates_dir, 'index.html'), encoding='utf-8') as f:
            html = f.read()
        body = versioned_index(html, static_dir)
        resp = app.response_class(body, content_type='text/html; charset=utf-8')
        resp.headers['Cache-Control'] = 'no-cache'
        resp.set_etag(hashlib.sha256(body.encode('utf-8')).hexdigest()[:16])
        return resp.make_conditional(request)

    @app.route('/favicon.ico')
    def serve_favicon():
        return send_from_directory(static_dir, 'favicon.ico')