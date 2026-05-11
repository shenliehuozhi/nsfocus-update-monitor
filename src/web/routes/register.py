"""Register all API routes on the Flask app."""

from flask import Flask


def register_routes(app: Flask):
    """Register all route blueprints."""
    from src.web.routes import (
        dashboard, session_routes, sources, subscriptions,
        channels, customers, history, settings
    )

    # API routes
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(session_routes.bp)
    app.register_blueprint(sources.bp)
    app.register_blueprint(subscriptions.bp)
    app.register_blueprint(channels.bp)
    app.register_blueprint(customers.bp)
    app.register_blueprint(history.bp)
    app.register_blueprint(settings.bp)

    # Serve main HTML page
    from flask import send_from_directory
    import os

    templates_dir = os.path.join(os.path.dirname(__file__), '..', 'templates')

    @app.route('/')
    def index():
        return send_from_directory(templates_dir, 'index.html')

    @app.route('/<path:path>')
    def static_files(path):
        return send_from_directory(templates_dir, path)
