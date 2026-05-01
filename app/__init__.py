from flask import Flask, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flasgger import Swagger
import logging

db = SQLAlchemy()
migrate = Migrate()
limiter = Limiter(key_func=get_remote_address)


def create_app(config=None):
    app = Flask(__name__)

    # Load config
    if config is None:
        from config import get_config
        app.config.from_object(get_config())
    else:
        app.config.from_object(config)

    # Init extensions
    db.init_app(app)
    migrate.init_app(app, db)
    limiter.init_app(app)

    # Swagger docs
    swagger_config = {
        "headers": [],
        "specs": [
            {
                "endpoint": "apispec",
                "route": "/apispec.json",
                "rule_filter": lambda rule: True,
                "model_filter": lambda tag: True,
            }
        ],
        "static_url_path": "/flasgger_static",
        "swagger_ui": True,
        "specs_route": "/docs/",
    }
    Swagger(app, config=swagger_config)

    # Register blueprints
    from app.routes.auth import auth_bp
    from app.routes.stores_public import stores_public_bp
    from app.routes.stores_admin import stores_admin_bp
    from app.routes.users_admin import users_admin_bp
    from app.routes.health import health_bp

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(stores_public_bp, url_prefix="/api/stores")
    app.register_blueprint(stores_admin_bp, url_prefix="/api/admin/stores")
    app.register_blueprint(users_admin_bp, url_prefix="/api/admin/users")
    app.register_blueprint(health_bp)

    # Global error handlers
    register_error_handlers(app)

    # Logging
    logging.basicConfig(level=logging.INFO)

    return app


def register_error_handlers(app):
    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"error": "Bad Request", "message": str(e)}), 400

    @app.errorhandler(401)
    def unauthorized(e):
        return jsonify({"error": "Unauthorized", "message": "Authentication required"}), 401

    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({"error": "Forbidden", "message": "Insufficient permissions"}), 403

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not Found", "message": str(e)}), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"error": "Method Not Allowed", "message": str(e)}), 405

    @app.errorhandler(422)
    def unprocessable(e):
        return jsonify({"error": "Unprocessable Entity", "message": str(e)}), 422

    @app.errorhandler(429)
    def rate_limit_exceeded(e):
        return jsonify({
            "error": "Too Many Requests",
            "message": "Rate limit exceeded. Please slow down.",
            "retry_after": str(e.retry_after) if hasattr(e, "retry_after") else None,
        }), 429

    @app.errorhandler(500)
    def internal_error(e):
        app.logger.error(f"Internal error: {e}")
        return jsonify({"error": "Internal Server Error", "message": "An unexpected error occurred"}), 500

    @app.errorhandler(Exception)
    def handle_unexpected(e):
        app.logger.exception(f"Unhandled exception: {e}")
        return jsonify({"error": "Internal Server Error", "message": "An unexpected error occurred"}), 500
