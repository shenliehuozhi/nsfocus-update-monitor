"""Register all API routes on the Flask app."""

from flask import Flask, send_from_directory
import os


def register_routes(app: Flask):
    """Register all route blueprints."""
    from src.web.routes.auth_routes import bp as auth_bp
    from src.web.routes.dashboard import bp as dashboard_bp
    from src.web.routes.session_routes import bp as session_bp
    from src.web.routes.api_routes import (
        bp_sources, bp_channels, bp_customers,
        bp_subscriptions, bp_history, bp_settings,
        bp_options, bp_latest, bp_snap
    )
    from src.web.routes.system_routes import bp as bp_system

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
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

    # Serve SPA index
    templates_dir = os.path.join(os.path.dirname(__file__), '..', 'templates')

    @app.route('/')
    def index():
        return send_from_directory(templates_dir, 'index.html')

    @app.route('/favicon.ico')
    def serve_favicon():
        static_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'web', 'static')
        return send_from_directory(static_dir, 'favicon.ico')