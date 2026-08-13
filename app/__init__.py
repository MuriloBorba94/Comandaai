from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from flask_wtf.csrf import CSRFError

from .config import INSECURE_SECRET_KEYS, ConfiguracaoInvalida, Config
from .extensions import csrf, db, limiter, migrate
from .tenancy import register_tenancy

SECRET_KEY_ERROR = (
    "O sistema não foi iniciado: falta uma SECRET_KEY própria no .env. "
    "Gere uma com: python -c \"import secrets; print(secrets.token_hex(32))\""
)


def _validate_secret_key(app: Flask) -> None:
    if app.config.get("TESTING"):
        return
    key = (app.config.get("SECRET_KEY") or "").strip()
    if key.lower() in INSECURE_SECRET_KEYS or len(key) < 24:
        raise ConfiguracaoInvalida(SECRET_KEY_ERROR)


def create_app(config_object=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)
    _validate_secret_key(app)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["LOG_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)

    _configure_logging(app)

    from . import models  # noqa: F401
    from .cli import register_cli
    from .routes.admin import admin_bp
    from .routes.auth import auth_bp
    from .routes.platform import platform_bp
    from .routes.public import public_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(platform_bp)

    register_tenancy(app)
    register_cli(app)

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        message = "Sua página ficou desatualizada. Atualize a tela e tente novamente."
        if request.path.startswith("/api/"):
            return jsonify(status="erro", mensagem=message), 400
        return render_template("error.html", code=400, message=message), 400

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("error.html", code=403, message="Você não tem permissão para esta ação."), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", code=404, message="Página não encontrada."), 404

    @app.errorhandler(429)
    def too_many_requests(error):
        message = (
            "Muitas tentativas de login a partir deste endereço. "
            "Aguarde alguns minutos e tente novamente."
        )
        if request.path.startswith("/api/"):
            return jsonify(status="erro", mensagem=message), 429
        return render_template("error.html", code=429, message=message), 429

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        app.logger.exception("Erro interno: %s", error)
        return render_template("error.html", code=500, message="Ocorreu um erro interno."), 500

    return app


def _configure_logging(app: Flask) -> None:
    log_path = os.path.join(app.config["LOG_FOLDER"], "saas.log")
    handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
