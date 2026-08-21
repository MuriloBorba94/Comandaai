"""Gravar e consultar o log de auditoria.

O ponto de atenção deste módulo é um só, e é o mesmo da impressão e das
notificações: **registrar nunca pode derrubar a ação registrada.** Se gravar a
linha de auditoria falhar, quem cancelou o pedido cancelou o pedido — não se
desfaz uma operação de negócio porque o diário não coube.

Por isso `registrar()` engole o próprio erro e apenas o manda para o log da
aplicação. É uma escolha consciente: prefere-se perder uma linha do diário a
perder a operação.
"""

from __future__ import annotations

from flask import current_app, g, has_request_context, request, session

from ..extensions import db
from ..models.auditoria import (
    ATOR_PLATAFORMA,
    ATOR_SISTEMA,
    ATOR_USUARIO,
    Auditoria,
)


def _quem() -> tuple[str, str]:
    """Descobre quem está agindo, a partir da sessão. Devolve (nome, tipo)."""
    if not has_request_context():
        return "sistema", ATOR_SISTEMA

    if session.get("plataforma_logada"):
        return str(session.get("plataforma_username") or "super-admin")[:80], ATOR_PLATAFORMA

    # Sessão de suporte: quem age é a pessoa da plataforma, mesmo estando
    # dentro do restaurante. Registrar o usuário do restaurante aqui seria
    # atribuir a ele algo que não foi ele quem fez.
    suporte = session.get("impersonado_por")
    if suporte:
        return f"{suporte} (suporte)"[:80], ATOR_PLATAFORMA

    if session.get("logged_in"):
        return str(session.get("username") or "usuário")[:80], ATOR_USUARIO

    return "anônimo", ATOR_SISTEMA


def _endereco() -> str | None:
    if not has_request_context():
        return None
    # Só vale quando há proxy confiável configurado; sem isso o Flask enxerga o
    # IP do próprio proxy, e gravar isso seria registrar sempre o mesmo número.
    return (request.remote_addr or "")[:45] or None


def registrar(
    acao: str,
    *,
    tenant=None,
    alvo: str | None = None,
    detalhes: str | None = None,
    ator: str | None = None,
    ator_tipo: str | None = None,
) -> Auditoria | None:
    """Grava uma linha no diário. Devolve None se não deu (e segue a vida)."""
    try:
        if tenant is None and has_request_context():
            tenant = g.get("tenant")

        nome, tipo = _quem()
        linha = Auditoria(
            tenant_id=getattr(tenant, "id", None),
            tenant_slug=getattr(tenant, "slug", None),
            ator=(ator or nome)[:80],
            ator_tipo=ator_tipo or tipo,
            acao=acao[:40],
            alvo=(alvo or "")[:120] or None,
            detalhes=(detalhes or "")[:500] or None,
            ip=_endereco(),
        )
        db.session.add(linha)
        db.session.commit()
        return linha
    except Exception:  # noqa: BLE001 - o diário nunca derruba a operação
        db.session.rollback()
        current_app.logger.exception("Falha ao registrar auditoria de %s", acao)
        return None


def do_tenant(tenant_id: int, limite: int = 100) -> list[Auditoria]:
    return (
        Auditoria.query.filter_by(tenant_id=tenant_id)
        .order_by(Auditoria.id.desc())
        .limit(limite)
        .all()
    )


def tudo(limite: int = 200, acao: str | None = None) -> list[Auditoria]:
    """Diário inteiro, para a área da plataforma."""
    consulta = Auditoria.query
    if acao:
        consulta = consulta.filter(Auditoria.acao == acao)
    return consulta.order_by(Auditoria.id.desc()).limit(limite).all()
