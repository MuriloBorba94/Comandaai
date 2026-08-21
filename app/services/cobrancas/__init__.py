"""Por qual caminho a mensalidade de um tenant é cobrada.

`manual` não fala com ninguém: a cobrança existe no sistema, você recebe o PIX e
marca como paga. É o modo com que a plataforma começou a operar e continua
sendo o que vale quando não há chave de API.

`asaas` emite a fatura no gateway e recebe a confirmação por webhook.

A escolha é por tenant (`Tenant.assinatura_provider`), porque a migração de um
para o outro é restaurante a restaurante: não faz sentido virar a chave de todo
mundo de uma vez e descobrir no dia seguinte que faltava o CNPJ de metade deles.
"""

from __future__ import annotations

from ...models.assinatura import PROVEDOR_ASAAS, PROVEDOR_MANUAL
from .asaas import Asaas
from .base import ProvedorCobranca, ResultadoCobranca
from .manual import Manual

PROVEDORES: tuple[ProvedorCobranca, ...] = (Manual(), Asaas())

POR_SLUG = {provedor.slug: provedor for provedor in PROVEDORES}

PADRAO = PROVEDOR_MANUAL

__all__ = [
    "PADRAO",
    "PROVEDORES",
    "PROVEDOR_ASAAS",
    "PROVEDOR_MANUAL",
    "ProvedorCobranca",
    "ResultadoCobranca",
    "provedor",
    "provedor_do_tenant",
]


def provedor(slug: str) -> ProvedorCobranca | None:
    return POR_SLUG.get((slug or "").strip())


def provedor_do_tenant(tenant) -> ProvedorCobranca:
    """O provedor deste restaurante. Cai no manual quando não dá para usar o outro.

    Cair para o manual em vez de falhar é deliberado: a mensalidade precisa
    existir de qualquer jeito. Uma chave de API que faltou não pode fazer o mês
    passar sem cobrança nenhuma — isso não bloqueia ninguém, mas some com a
    receita da plataforma em silêncio.
    """
    escolhido = POR_SLUG.get((getattr(tenant, "assinatura_provider", "") or "").strip())
    if escolhido is None or not escolhido.configurado():
        return POR_SLUG[PADRAO]
    return escolhido
