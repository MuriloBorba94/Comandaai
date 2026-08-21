"""O contrato que todo meio de pagamento cumpre.

Esta é a correção que o roteiro pedia sobre o sistema original: lá existia uma
variável `PIX_PROVIDER` no `.env` que **nenhuma fábrica lia** — o código chamava
a InfinitePay direto, com `if provider == "infinitepay"` espalhado por três
arquivos. Trocar de provedor exigiria caçar esses ifs.

Aqui um provedor é uma classe que responde a quatro perguntas:

- `configurado(tenant)` — este restaurante tem o que é preciso para usar?
- `criar(pedido)` — devolve o que o cliente precisa para pagar.
- `confirmacao_manual` — quem confirma o recebimento: uma pessoa ou o provedor?
- `consultar(pagamento)` — só para provedores automáticos.

Quem chama nunca sabe qual provedor está atendendo. Ver `registro.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Cobranca:
    """O que o provedor devolve quando cria uma cobrança.

    `ok=False` traz o motivo em `erro` — uma mensagem para o cliente ler na
    tela, não um traceback.
    """

    ok: bool
    brcode: str | None = None
    txid: str | None = None
    referencia: str | None = None
    erro: str | None = None
    resposta: dict = field(default_factory=dict)


class ProvedorPix:
    """Interface. Um provedor concreto herda daqui e preenche o que precisa."""

    slug: str = ""
    nome: str = ""
    # True quando não existe ninguém do lado de fora avisando que o dinheiro
    # caiu, e portanto alguém do restaurante precisa confirmar na mão.
    confirmacao_manual: bool = True

    def configurado(self, tenant) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def falta_configurar(self, tenant) -> str:  # pragma: no cover - interface
        """Explica o que falta, para a tela poder dizer em vez de só recusar."""
        raise NotImplementedError

    def criar(self, pedido) -> Cobranca:  # pragma: no cover - interface
        raise NotImplementedError

    def consultar(self, pagamento) -> bool:
        """Pergunta ao provedor se já foi pago. Devolve True se mudou algo.

        O padrão é "não sei": um provedor de confirmação manual não tem a quem
        perguntar, e responder outra coisa faria a tela do cliente prometer uma
        atualização que nunca vem.
        """
        return False
