"""O contrato de um provedor de cobrança da mensalidade.

Terceira vez que este desenho aparece no projeto (PIX do cliente, WhatsApp,
agora a mensalidade), e de propósito: quem cobra não sabe por qual caminho, e
somar um gateway novo é escrever uma classe.

A diferença em relação aos outros dois é o que está sendo cobrado. Aqui não é o
cliente pagando o restaurante — é o RESTAURANTE pagando a plataforma. O dinheiro
vem para a conta da plataforma, e por isso a credencial é uma só para todo o
sistema, e não por tenant.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ResultadoCobranca:
    """O que o provedor devolve ao registrar uma mensalidade."""

    ok: bool
    id_externo: str | None = None
    # Endereço onde o restaurante paga (fatura do gateway, com PIX e boleto).
    url_pagamento: str | None = None
    erro: str | None = None
    resposta: dict = field(default_factory=dict)


class ProvedorCobranca:
    slug: str = ""
    nome: str = ""
    # True quando o provedor avisa sozinho que foi pago (webhook). False
    # significa que alguém precisa marcar o pagamento na mão.
    automatico: bool = False

    def configurado(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def falta_configurar(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def criar(self, cobranca) -> ResultadoCobranca:  # pragma: no cover - interface
        raise NotImplementedError
