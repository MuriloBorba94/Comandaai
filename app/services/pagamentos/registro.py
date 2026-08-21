"""A fábrica de provedores — o que faltava no sistema original.

Quem quer cobrar pergunta aqui qual provedor atende este restaurante, e nunca
precisa saber o nome dele. Somar um gateway automático no futuro é escrever a
classe, registrar nesta lista e oferecer a opção na tela: nenhum `if` novo no
serviço de pedidos, na vitrine ou no painel.

Por que só existe um provedor hoje: os números do sistema em produção. De 162
pagamentos online, 159 saíram pelo PIX direto e 3 pelo gateway — os três no
mesmo dia de julho, nunca repetidos. Portar o gateway agora seria carregar
código que ninguém usa e que eu não teria como testar de verdade, sem conta e
sem chave. A abstração fica pronta; o provedor entra quando houver um
restaurante que precise dele.
"""

from __future__ import annotations

from .base import Cobranca, ProvedorPix
from .direto import PixDireto

# Ordem importa: o primeiro provedor configurado é o que atende.
PROVEDORES: tuple[ProvedorPix, ...] = (PixDireto(),)

POR_SLUG = {provedor.slug: provedor for provedor in PROVEDORES}


def provedor(slug: str) -> ProvedorPix | None:
    return POR_SLUG.get(slug)


def provedor_do_tenant(tenant) -> ProvedorPix | None:
    """O provedor que este restaurante consegue usar agora, ou None."""
    if tenant is None:
        return None
    for candidato in PROVEDORES:
        if candidato.configurado(tenant):
            return candidato
    return None


def por_que_nao(tenant) -> str:
    """O que falta para este restaurante receber pelo site.

    Existe para a tela de configuração poder explicar em vez de só esconder a
    opção — dono de restaurante que não vê o recurso acha que o sistema não tem.
    """
    if tenant is None:
        return "Restaurante não identificado."
    return PROVEDORES[0].falta_configurar(tenant)


__all__ = ["Cobranca", "PROVEDORES", "ProvedorPix", "por_que_nao", "provedor", "provedor_do_tenant"]
