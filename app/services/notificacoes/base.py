"""O contrato que todo jeito de mandar WhatsApp cumpre.

Mesmo desenho do provedor de pagamento: quem quer avisar o cliente pergunta ao
registro qual provedor atende este restaurante, e nunca sabe qual é.

A propriedade que separa os dois mundos é `automatico`. Ela não é detalhe de
implementação — muda o que o sistema faz:

- provedor automático dispara todas as etapas do pedido sozinho;
- provedor manual prepara só o aviso de confirmação, porque cada etapa a mais
  seria um clique a mais para quem está atendendo o salão.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Envio:
    """Resultado de uma tentativa de envio."""

    ok: bool
    id_externo: str | None = None
    erro: str | None = None
    # True quando não houve envio de fato porque o provedor depende de alguém
    # clicar. A notificação fica esperando em vez de contar como falha.
    aguarda_pessoa: bool = False


class ProvedorWhatsApp:
    slug: str = ""
    nome: str = ""
    automatico: bool = False

    def configurado(self, tenant) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def falta_configurar(self, tenant) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def enviar(self, notificacao) -> Envio:  # pragma: no cover - interface
        raise NotImplementedError
