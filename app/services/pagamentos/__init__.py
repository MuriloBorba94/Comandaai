"""Pagamento online do pedido: cobrança, confirmação e o que isso move.

O arquivo `registro.py` decide COMO cobrar. Este aqui decide o que acontece no
pedido quando o dinheiro entra — e é onde mora a diferença mais importante em
relação ao sistema original.

Lá, confirmar o recebimento e mover o pedido eram dois caminhos separados: o
atendente podia avançar um pedido de "Aguardando PIX" para "Confirmado" sem
nunca marcar o pagamento como recebido. O pedido ia para a cozinha e o
financeiro ficava dizendo que ninguém pagou. Aqui existe UM caminho para sair de
"Aguardando PIX": confirmar o recebimento. Ele marca o pagamento e move o pedido
na mesma operação, então os dois não têm como divergir.
"""

from __future__ import annotations

from datetime import datetime

from ...extensions import db
from ...models.pagamento import (
    STATUS_AGUARDANDO,
    STATUS_CANCELADO,
    STATUS_PAGO,
    STATUS_REVISAO,
    Pagamento,
)
from ..recursos import tenant_libera
from .base import Cobranca, ProvedorPix
from .brcode import montar
from .registro import PROVEDORES, por_que_nao, provedor, provedor_do_tenant

__all__ = [
    "Cobranca",
    "PROVEDORES",
    "ProvedorPix",
    "cancelar_cobranca",
    "cobranca_disponivel",
    "confirmar_recebimento",
    "criar_cobranca",
    "montar",
    "pagamento_do_pedido",
    "por_que_nao",
    "provedor",
    "provedor_do_tenant",
]


def cobranca_disponivel(tenant) -> bool:
    """Este restaurante pode oferecer "pagar agora" no cardápio?

    Precisa das duas coisas: o recurso no plano e um provedor configurado. Sem a
    segunda, a opção apareceria no checkout e falharia na hora de gerar o
    código — depois de o cliente já ter escolhido.
    """
    return tenant_libera(tenant, "pix") and provedor_do_tenant(tenant) is not None


def pagamento_do_pedido(pedido) -> Pagamento | None:
    return Pagamento.query.filter_by(pedido_id=pedido.id).first()


def criar_cobranca(pedido) -> Pagamento:
    """Gera a cobrança do pedido. Levanta ValueError com o motivo se não der.

    Roda DENTRO da transação que cria o pedido, de propósito: um pedido em
    "Aguardando PIX" sem código para pagar é um beco sem saída para o cliente —
    ele vê uma tela pedindo pagamento e nada para copiar.
    """
    tenant = pedido.tenant
    if not tenant_libera(tenant, "pix"):
        raise ValueError("Este restaurante não recebe pagamento pelo site.")

    escolhido = provedor_do_tenant(tenant)
    if escolhido is None:
        raise ValueError(por_que_nao(tenant))

    existente = pagamento_do_pedido(pedido)
    if existente is not None:
        return existente

    cobranca = escolhido.criar(pedido)
    if not cobranca.ok:
        raise ValueError(cobranca.erro or "Não foi possível gerar a cobrança.")

    pagamento = Pagamento(
        tenant_id=pedido.tenant_id,
        pedido_id=pedido.id,
        provedor=escolhido.slug,
        status=STATUS_AGUARDANDO,
        valor_centavos=int(round((pedido.total or 0) * 100)),
        brcode=cobranca.brcode,
        txid=cobranca.txid,
        referencia=cobranca.referencia,
    )
    db.session.add(pagamento)
    return pagamento


def confirmar_recebimento(pagamento: Pagamento, *, actor: str | None = None) -> bool:
    """Registra que o dinheiro entrou e libera o pedido para a cozinha.

    Devolve False quando não havia nada a fazer (já estava pago) em vez de
    reclamar: dois cliques no mesmo botão é acidente comum, e o segundo não pode
    dar erro na cara de quem está atendendo.
    """
    from ...models.pedido import STATUS_AGUARDANDO_PIX, STATUS_CANCELADO as PEDIDO_CANCELADO
    from ..pedidos import STATUS_CONFIRMADO, transicionar

    pedido = pagamento.pedido
    if pagamento.status == STATUS_PAGO:
        return False

    if pedido.status == PEDIDO_CANCELADO:
        # Dinheiro que entra depois do cancelamento não vira pedido sozinho: ou
        # se devolve, ou se refaz o pedido. As duas decisões são de gente.
        pagamento.status = STATUS_REVISAO
        pagamento.erro = "Recebimento informado depois de o pedido ter sido cancelado."
        db.session.commit()
        raise ValueError(
            "Este pedido está cancelado. O recebimento foi marcado para conferência "
            "em vez de liberar o pedido."
        )

    agora = datetime.now()
    pagamento.status = STATUS_PAGO
    pagamento.pago_em = agora
    pagamento.confirmado_por = (actor or "").strip()[:80] or None
    pagamento.erro = None

    # O texto da forma de pagamento passa a dizer que já foi pago. Quem olha o
    # pedido depois precisa ver isso sem abrir outra tela.
    pedido.pagamento = "PIX online — pago"

    if pedido.status == STATUS_AGUARDANDO_PIX:
        # transicionar() é quem baixa estoque, consome o cupom e manda a comanda
        # para a impressora. Passar por ele é o que garante que pagar um pedido
        # faz exatamente o mesmo que confirmar um pedido comum.
        transicionar(pedido, STATUS_CONFIRMADO, actor=actor)
    else:
        db.session.commit()
    return True


def cancelar_cobranca(pagamento: Pagamento) -> bool:
    """Encerra a cobrança de um pedido que não vai mais acontecer."""
    if pagamento.status in (STATUS_PAGO, STATUS_REVISAO):
        # Cobrança paga não se cancela sozinha, e a que está em conferência
        # precisa continuar visível até alguém decidir o que fazer.
        return False
    if pagamento.status == STATUS_CANCELADO:
        return False
    pagamento.status = STATUS_CANCELADO
    db.session.commit()
    return True
