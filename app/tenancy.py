from __future__ import annotations

from flask import Flask, abort, current_app, g, render_template, request, session

from .models.tenant import Tenant

# Rotas que exigem um tenant identificado; sem ele, 404 em vez de ambiguidade.
TENANT_REQUIRED_BLUEPRINTS = {"admin", "auth", "api", "operacao", "entregas"}

BLOCKED_STATUSES = {"canceled", "suspended"}


def _strip_port(host: str) -> str:
    return host.split(":")[0].strip().lower()


def _extract_slug(host: str, base_domains: list[str]) -> str | None:
    for base_domain in base_domains:
        if host == base_domain:
            return None  # domínio "apex", sem tenant (página institucional)
        suffix = "." + base_domain
        if host.endswith(suffix):
            remainder = host[: -len(suffix)]
            if remainder:
                return remainder.split(".")[0]
    return None


def resolve_tenant():
    g.tenant = None
    g.is_platform_area = False

    host = _strip_port(request.host)

    if host == current_app.config["PLATFORM_HOSTNAME"]:
        g.is_platform_area = True
        return

    slug = _extract_slug(host, current_app.config["TENANT_BASE_DOMAINS"])

    if slug is None:
        # Escape hatch para ferramentas que não resolvem subdomínios de fato
        # (curl, pytest, Postman): header explícito ou query param.
        slug = request.headers.get("X-Tenant-Slug") or request.args.get("tenant")

    if slug is None:
        if request.blueprint in TENANT_REQUIRED_BLUEPRINTS:
            abort(404)
        return

    tenant = Tenant.query.filter_by(slug=slug.lower()).first()
    if tenant is None:
        abort(404)

    if tenant.status in BLOCKED_STATUSES or not tenant.ativo:
        # 402 não existe como HTTPException no Werkzeug, então a resposta é
        # retornada direto aqui (um valor não-None de before_request encerra
        # a requisição sem chamar a view).
        #
        # O aviso de cobrança acompanha a resposta para a tela poder dizer o
        # MOTIVO. Bloqueio sem explicação faz o dono da loja achar que o sistema
        # quebrou. E quando não há cobrança em aberto (bloqueio manual), a tela
        # não pode acusar inadimplência que não existe.
        from .services.faturamento_saas import aviso_de_assinatura

        return (
            render_template(
                "tenant_suspended.html",
                tenant=tenant,
                aviso=aviso_de_assinatura(tenant),
                contato=current_app.config.get("PLATFORM_CONTATO") or "",
            ),
            402,
        )

    g.tenant = tenant

    # Defesa em profundidade: uma sessão aberta em outro tenant nunca deve
    # valer aqui, mesmo se um cookie for reaproveitado por engano.
    if session.get("logged_in") and session.get("tenant_id") != tenant.id:
        session.clear()
    return None


def register_tenancy(app: Flask) -> None:
    app.before_request(resolve_tenant)
