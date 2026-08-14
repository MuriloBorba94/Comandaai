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


def _aplicar_proxy_fix(app: Flask) -> None:
    """Faz o Flask ler o IP e o esquema reais quando há proxy na frente.

    Só é aplicado quando TRUSTED_PROXIES > 0. Sem proxy, X-Forwarded-For
    continua ignorado de propósito: confiar nele nesse caso permitiria a
    qualquer cliente forjar o header e escapar do rate limit de login trocando
    o valor a cada tentativa.
    """
    quantidade = int(app.config.get("TRUSTED_PROXIES") or 0)
    if quantidade <= 0:
        return

    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=quantidade,
        x_proto=quantidade,
        x_host=quantidade,
        x_port=quantidade,
    )
    app.logger.info("ProxyFix ativo para %s proxy(s) confiável(is).", quantidade)


def create_app(config_object=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)
    _validate_secret_key(app)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["LOG_FOLDER"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    limiter.init_app(app)

    _configure_logging(app)
    _aplicar_proxy_fix(app)

    from . import models  # noqa: F401
    from .cli import register_cli
    from .routes.admin import admin_bp
    from .routes.auth import auth_bp
    from .routes.operacao import operacao_bp
    from .routes.platform import platform_bp
    from .routes.public import public_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(operacao_bp)
    app.register_blueprint(platform_bp)

    register_tenancy(app)
    register_cli(app)

    from .layout import registrar as registrar_layout

    registrar_layout(app)

    @app.template_global()
    def aviso_assinatura():
        """Aviso de mensalidade para quem administra o restaurante.

        Devolve None para o cliente final. A cobrança que o restaurante paga à
        plataforma não é da conta de quem está pedindo um lanche, e mostrá-la na
        vitrine seria vazamento de informação comercial.
        """
        from flask import g, session

        from .services.faturamento_saas import aviso_de_assinatura

        tenant = g.get("tenant")
        if tenant is None or not session.get("logged_in"):
            return None
        if session.get("tenant_id") != tenant.id:
            return None
        return aviso_de_assinatura(tenant)

    @app.template_global()
    def insumos_para_repor() -> int:
        """Quantos insumos do tenant estão no mínimo, para o contador da sidebar.

        Devolve 0 quando o plano não inclui estoque: sem isso a consulta rodaria
        em toda página de quem nem tem o recurso.
        """
        from flask import g

        from .services.recursos import tenant_libera

        tenant = g.get("tenant")
        if tenant is None or not tenant_libera(tenant, "estoque"):
            return 0
        from .services.estoque import insumos_em_alerta

        return len(insumos_em_alerta(tenant.id))

    @app.template_global()
    def resumo_do_dia() -> dict:
        """Os quatro números da faixa no alto do painel.

        Espelha a `v17-overview-strip` da Gestão original. Estoque e contas só
        são consultados quando o plano inclui o recurso: sem essa guarda, a
        consulta rodaria em toda página de quem nem tem acesso à tela.
        """
        from datetime import date, datetime, time, timedelta

        from flask import g
        from sqlalchemy import func

        from .models.pedido import STATUS_CANCELADO, Pedido
        from .services.recursos import tenant_libera

        vazio = {"pedidos_hoje": 0, "vendas_hoje": 0.0, "estoque_baixo": 0, "contas_abertas": 0}
        tenant = g.get("tenant")
        if tenant is None:
            return vazio

        hoje = date.today()
        desde = datetime.combine(hoje, time.min)
        ate = datetime.combine(hoje + timedelta(days=1), time.min)
        pedidos, vendas = (
            db.session.query(func.count(Pedido.id), func.coalesce(func.sum(Pedido.total), 0.0))
            .filter(
                Pedido.tenant_id == tenant.id,
                Pedido.status != STATUS_CANCELADO,
                Pedido.created_at >= desde,
                Pedido.created_at < ate,
            )
            .one()
        )

        estoque_baixo = 0
        if tenant_libera(tenant, "estoque"):
            from .services.estoque import insumos_em_alerta

            estoque_baixo = len(insumos_em_alerta(tenant.id))

        contas_abertas = 0
        if tenant_libera(tenant, "financeiro"):
            from .models.financeiro import Despesa

            contas_abertas = Despesa.query.filter_by(tenant_id=tenant.id, paga=False).count()

        return {
            "pedidos_hoje": int(pedidos or 0),
            "vendas_hoje": float(vendas or 0.0),
            "estoque_baixo": estoque_baixo,
            "contas_abertas": contas_abertas,
        }

    @app.template_global()
    def libera(slug: str) -> bool:
        """Diz se o plano do tenant atual inclui um recurso.

        Usado nos templates para não mostrar link de recurso que a pessoa não
        pode abrir — a rota também barra, mas oferecer e negar seria pior.
        """
        from flask import g

        from .services.recursos import tenant_libera

        return tenant_libera(g.get("tenant"), slug)

    @app.template_global()
    def url_do_tenant(slug: str, caminho: str = "/") -> str:
        """Monta o endereço público de um tenant a partir da configuração.

        Evita `localhost:5000` cravado em template, que quebraria em produção.
        """
        base = (app.config.get("TENANT_BASE_DOMAINS") or ["localhost"])[0]
        porta = str(app.config.get("PORT", "5000"))
        esquema = "https" if app.config.get("SESSION_COOKIE_SECURE") else "http"
        sufixo = "" if porta in ("80", "443") else f":{porta}"
        return f"{esquema}://{slug}.{base}{sufixo}{caminho}"

    @app.template_filter("pct")
    def pct(valor, casas: int = 1) -> str:
        """Porcentagem no padrão brasileiro: 23.2 -> "23,2".

        Sem isto o percentual saía com ponto ao lado de valores em real com
        vírgula, na mesma linha.
        """
        try:
            numero = float(valor or 0)
        except (TypeError, ValueError):
            numero = 0.0
        return f"{numero:.{casas}f}".replace(".", ",")

    @app.template_filter("brl")
    def brl(valor) -> str:
        """Formata número no padrão brasileiro: 1234.5 -> "1.234,50"."""
        try:
            numero = float(valor or 0)
        except (TypeError, ValueError):
            numero = 0.0
        # Formata no padrão en_US e troca os separadores, evitando depender de
        # locale instalado no sistema (que varia entre Windows e Linux).
        return f"{numero:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")

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

    @app.errorhandler(413)
    def payload_too_large(error):
        limite_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        message = f"Arquivo grande demais. O limite por imagem é {limite_mb} MB."
        if request.path.startswith("/api/"):
            return jsonify(status="erro", mensagem=message), 413
        return render_template("error.html", code=413, message=message), 413

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
