"""Quais recursos o plano de um tenant libera.

O catálogo de recursos está em `app/models/assinatura.py`. Aqui fica a pergunta
que o resto da aplicação faz: "este tenant pode usar X?".

Regra de compatibilidade, importante: um tenant cujo plano não está no catálogo
— ou cujo plano nunca teve recursos configurados — libera tudo. Aplicar
feature-gating num sistema em uso não pode tirar acesso de ninguém em silêncio;
a restrição começa a valer quando o plano é configurado de propósito.
"""

from __future__ import annotations

from functools import wraps

from flask import abort, flash, g, redirect, url_for

from ..models.assinatura import RECURSOS, RECURSOS_SLUGS, Plano


def recursos_do_tenant(tenant) -> set[str]:
    if tenant is None:
        return set()
    plano = Plano.query.filter_by(slug=tenant.plano).first() if tenant.plano else None
    if plano is None:
        # Plano fora do catálogo: não é motivo para bloquear quem já usa.
        return set(RECURSOS_SLUGS)
    return plano.recursos_liberados


def tenant_libera(tenant, slug: str) -> bool:
    return slug in recursos_do_tenant(tenant)


def rotulo_do_recurso(slug: str) -> str:
    for chave, rotulo, _ in RECURSOS:
        if chave == slug:
            return rotulo
    return slug


def requer_recurso(slug: str):
    """Bloqueia a rota quando o plano do tenant não inclui o recurso.

    Devolve uma mensagem explicando o motivo em vez de um 404 seco: quem está
    operando o restaurante precisa entender que é uma questão de plano, não um
    defeito do sistema.
    """

    def decorador(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            tenant = g.get("tenant")
            if tenant is None:
                abort(404)
            if not tenant_libera(tenant, slug):
                flash(
                    f"O recurso “{rotulo_do_recurso(slug)}” não está incluído no "
                    f"plano deste restaurante. Fale com o suporte para liberar.",
                    "erro",
                )
                return redirect(url_for("admin.dashboard"))
            return view(*args, **kwargs)

        return wrapped

    return decorador
