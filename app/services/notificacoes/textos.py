"""O que o cliente lê no WhatsApp.

Portado do sistema antigo, com as adaptações que o multi-tenant exige: o nome do
restaurante e o endereço de acompanhamento vêm do tenant, e não de constantes
com "Borba's Burguer" cravado.

Duas correções em relação ao original:

1. **`order.cliente.split()[0]` quebrava com nome vazio.** `"".split()` devolve
   lista vazia, e o `[0]` levantava IndexError — no meio do envio, depois de o
   pedido já estar gravado. O cadastro exige nome, mas um espaço em branco
   passava.
2. **O número do pedido era o `id` global.** Aqui é o `numero`, que reinicia em
   cada restaurante: o cliente recebe "pedido #7", que é o que ele vê na tela,
   e não "#4712".
"""

from __future__ import annotations

from flask import current_app

from ...models.notificacao import (
    EVENTO_CANCELADO,
    EVENTO_CONFIRMADO,
    EVENTO_EM_PREPARO,
    EVENTO_ENTREGUE,
    EVENTO_PRONTO,
    EVENTO_SAIU_ENTREGA,
)
from ...models.pedido import TIPO_ENTREGA


def primeiro_nome(cliente: str) -> str:
    """Só o primeiro nome, que é como se fala com alguém no WhatsApp.

    Devolve "" quando não dá para extrair nada, e quem chama trata o vazio. O
    original fazia `cliente.split()[0]` direto e estourava com nome em branco.
    """
    partes = (cliente or "").split()
    return partes[0] if partes else ""


def _saudacao(cliente: str) -> str:
    nome = primeiro_nome(cliente)
    return f", {nome}" if nome else ""


def link_de_acompanhamento(pedido) -> str:
    """Endereço público do pedido, no domínio do próprio restaurante."""
    base = (current_app.config.get("TENANT_BASE_DOMAINS") or ["localhost"])[0]
    porta = str(current_app.config.get("PORT", "5000"))
    esquema = "https" if current_app.config.get("SESSION_COOKIE_SECURE") else "http"
    sufixo = "" if porta in ("80", "443") else f":{porta}"
    return f"{esquema}://{pedido.tenant.slug}.{base}{sufixo}/pedido/{pedido.public_token}"


def _dinheiro(valor) -> str:
    return f"{float(valor or 0):.2f}".replace(".", ",")


def montar(pedido, evento: str) -> str:
    """O texto do aviso, pronto para ir ao WhatsApp."""
    nome = _saudacao(pedido.cliente)
    loja = pedido.tenant.nome_fantasia
    numero = pedido.numero
    link = link_de_acompanhamento(pedido)

    if evento == EVENTO_CONFIRMADO:
        prazo = ""
        if pedido.tempo_estimado_min and pedido.tempo_estimado_max:
            prazo = f" Previsão de {pedido.tempo_estimado_min} a {pedido.tempo_estimado_max} min."
        return (
            f"✅ Pedido #{numero} confirmado{nome}!{prazo} "
            f"Total: R$ {_dinheiro(pedido.total)}.\n\n"
            f"Acompanhe por aqui: {link}\n\n"
            f"Obrigado pela preferência! — {loja}"
        )

    if evento == EVENTO_EM_PREPARO:
        return f"👨‍🍳 {loja}: seu pedido #{numero} já está em preparo{nome}! Só mais um pouquinho."

    if evento == EVENTO_PRONTO:
        if pedido.tipo == TIPO_ENTREGA:
            return f"✅ Pedido #{numero} pronto{nome}! Já vai seguir para entrega. — {loja}"
        return f"✅ Pedido #{numero} prontinho{nome}! Pode vir buscar quando quiser. — {loja}"

    if evento == EVENTO_SAIU_ENTREGA:
        return (
            f"🛵 Seu pedido #{numero} saiu para entrega{nome}! Fique de olho no celular.\n\n"
            f"Acompanhe: {link}\n— {loja}"
        )

    if evento == EVENTO_ENTREGUE:
        return f"❤️ Pedido #{numero} entregue! Muito obrigado por escolher a {loja}{nome}. Bom apetite!"

    if evento == EVENTO_CANCELADO:
        return (
            f"{primeiro_nome(pedido.cliente) or 'Olá'}, seu pedido #{numero} foi cancelado. "
            f"Qualquer dúvida, é só responder esta mensagem. 🙏 — {loja}"
        )

    return f"Atualização do pedido #{numero}: {pedido.status}. — {loja}"
