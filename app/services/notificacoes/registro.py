"""Qual provedor de WhatsApp atende este restaurante.

Diferente do PIX, aqui a escolha é explícita: o restaurante marca na tela se
quer o link grátis ou a API oficial. Não dá para adivinhar pela configuração —
ter credencial da Meta cadastrada não significa querer usá-la hoje, e cair
sozinho no modo pago seria decidir gasto no lugar do dono.

A única regra automática é a de segurança: quem escolheu "meta" mas ainda não
terminou de configurar volta para o link, para o cliente continuar sendo avisado
enquanto a conta não fica pronta. Um aviso que não sai é pior do que um aviso
que exige um clique.
"""

from __future__ import annotations

from .base import Envio, ProvedorWhatsApp
from .link import LinkWhatsApp
from .meta import MetaCloud

PROVEDORES: tuple[ProvedorWhatsApp, ...] = (LinkWhatsApp(), MetaCloud())

POR_SLUG = {provedor.slug: provedor for provedor in PROVEDORES}

PADRAO = LinkWhatsApp.slug


def provedor(slug: str) -> ProvedorWhatsApp | None:
    return POR_SLUG.get(slug)


def provedor_do_tenant(tenant) -> ProvedorWhatsApp:
    """O provedor em uso agora. Sempre devolve um — o link nunca falha."""
    if tenant is None:
        return POR_SLUG[PADRAO]
    escolhido = POR_SLUG.get((getattr(tenant, "whatsapp_provedor", "") or "").strip())
    if escolhido is None:
        return POR_SLUG[PADRAO]
    if not escolhido.configurado(tenant):
        return POR_SLUG[PADRAO]
    return escolhido


def caiu_para_o_link(tenant) -> bool:
    """O restaurante pediu envio automático mas ele não está de pé?

    A tela precisa dizer isso em voz alta: quem marcou "automático" e continua
    tendo que clicar merece saber por quê.
    """
    pedido = (getattr(tenant, "whatsapp_provedor", "") or "").strip()
    escolhido = POR_SLUG.get(pedido)
    return escolhido is not None and escolhido.slug != PADRAO and not escolhido.configurado(tenant)


__all__ = [
    "Envio",
    "PADRAO",
    "PROVEDORES",
    "ProvedorWhatsApp",
    "caiu_para_o_link",
    "provedor",
    "provedor_do_tenant",
]
