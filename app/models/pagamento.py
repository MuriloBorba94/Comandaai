"""Pagamento online de um pedido.

Um pedido tem no máximo um pagamento: o cliente escolhe pagar agora, e o
registro guarda o que foi cobrado, por qual meio e quando entrou. Não é a mesma
coisa que `Pedido.pagamento`, que é só o texto da forma escolhida ("Dinheiro",
"Cartão na entrega") e existe mesmo quando ninguém paga pelo site.

O dinheiro vai direto para a conta do restaurante — a chave PIX é dele. A
plataforma não é intermediária de pagamento e nunca toca no valor; o que ela faz
é montar o código de cobrança e registrar a confirmação.
"""

from __future__ import annotations

from datetime import datetime

from ..extensions import db

# Esperando o cliente pagar. É onde todo pagamento nasce.
STATUS_AGUARDANDO = "aguardando"
# Recebimento confirmado — no PIX direto, por uma pessoa que viu o dinheiro cair.
STATUS_PAGO = "pago"
# O pedido foi cancelado antes de pagar, ou o restaurante desistiu da cobrança.
STATUS_CANCELADO = "cancelado"
# Algo não bate e precisa de gente olhando: pagamento que chegou depois do
# cancelamento, valor diferente do cobrado. Nunca vira "pago" sozinho.
STATUS_REVISAO = "revisao"

STATUS_TODOS = (STATUS_AGUARDANDO, STATUS_PAGO, STATUS_CANCELADO, STATUS_REVISAO)

ROTULO_DO_STATUS = {
    STATUS_AGUARDANDO: "Aguardando pagamento",
    STATUS_PAGO: "Pago",
    STATUS_CANCELADO: "Cancelado",
    STATUS_REVISAO: "Conferir",
}


class Pagamento(db.Model):
    __tablename__ = "pagamento"

    __table_args__ = (
        # Um pagamento por pedido. Sem isto, um duplo clique no checkout criaria
        # duas cobranças do mesmo valor e o cliente poderia pagar as duas.
        db.UniqueConstraint("pedido_id", name="uq_pagamento_pedido"),
    )

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pedido_id = db.Column(
        db.Integer, db.ForeignKey("pedido.id", ondelete="CASCADE"), nullable=False, index=True
    )

    provedor = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(20), default=STATUS_AGUARDANDO, nullable=False, index=True)

    # Em centavos, e não em float: é o valor que foi COBRADO, e comparar
    # dinheiro com float é como o sistema deixa de bater por um centavo.
    valor_centavos = db.Column(db.Integer, nullable=False)

    # O "copia e cola" que o cliente leva para o aplicativo do banco, e que
    # também vira o QR na tela. Congelado: se o total do pedido mudasse depois,
    # o código já entregue ao cliente continuaria valendo o que ele viu.
    brcode = db.Column(db.Text)
    # Identificador que aparece no extrato do restaurante, para ele casar o
    # dinheiro com o pedido.
    txid = db.Column(db.String(30), index=True)
    # Identificador do pagamento no provedor externo, quando houver um.
    referencia = db.Column(db.String(120), index=True)

    pago_em = db.Column(db.DateTime)
    # Quem apertou "recebi" no painel. No PIX direto a confirmação é humana, e
    # sem o nome não há a quem perguntar quando o valor não bate no fim do dia.
    confirmado_por = db.Column(db.String(80))
    erro = db.Column(db.String(500))
    resposta_bruta = db.Column(db.Text)

    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)
    atualizado_em = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    pedido = db.relationship("Pedido", back_populates="pagamento_online")
    tenant = db.relationship("Tenant")

    @property
    def valor(self) -> float:
        return (self.valor_centavos or 0) / 100

    @property
    def rotulo(self) -> str:
        return ROTULO_DO_STATUS.get(self.status, self.status)

    @property
    def pago(self) -> bool:
        return self.status == STATUS_PAGO

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Pagamento pedido={self.pedido_id} {self.provedor} {self.status}>"
