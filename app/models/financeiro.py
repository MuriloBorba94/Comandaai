from __future__ import annotations

from datetime import date

from ..extensions import db
from .mixins import TimestampMixin

# Categorias de despesa.
#
# NÃO existe categoria de "compra de insumos" de propósito. O custo dos insumos
# já entra no resultado pelo CMV (o que foi consumido nas vendas), e lançar a
# compra também como despesa contaria o mesmo dinheiro duas vezes, derrubando o
# lucro artificialmente. Compra de insumo se registra como ENTRADA na tela de
# estoque, que é onde ela atualiza saldo e custo.
CATEGORIAS_DESPESA = (
    "Aluguel",
    "Salários",
    "Energia",
    "Água",
    "Gás",
    "Internet e telefone",
    "Embalagens",
    "Marketing",
    "Impostos e taxas",
    "Manutenção",
    "Aplicativos e sistemas",
    "Outros",
)

CATEGORIAS_RECEITA = (
    "Venda avulsa",
    "Evento",
    "Aluguel de espaço",
    "Outras receitas",
)


class Despesa(TimestampMixin, db.Model):
    """Conta a pagar do restaurante."""

    __tablename__ = "despesa"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)

    descricao = db.Column(db.String(120), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    categoria = db.Column(db.String(50), default="Outros", nullable=False, index=True)
    data_vencimento = db.Column(db.Date, nullable=False, index=True)
    paga = db.Column(db.Boolean, default=False, nullable=False, index=True)
    data_pagamento = db.Column(db.Date)
    observacao = db.Column(db.String(250))

    tenant = db.relationship("Tenant", back_populates="despesas")

    def dias_de_atraso(self, hoje: date | None = None) -> int:
        if self.paga:
            return 0
        hoje = hoje or date.today()
        return max(0, (hoje - self.data_vencimento).days)

    @property
    def vencida(self) -> bool:
        return self.dias_de_atraso() > 0

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Despesa {self.descricao!r} R$ {self.valor:.2f}>"


class ReceitaAvulsa(TimestampMixin, db.Model):
    """Entrada de dinheiro que não passou por um pedido.

    No sistema original chamava-se `Faturamento`, nome que confundia com o
    faturamento das vendas. Aqui é explicitamente o que entra FORA dos pedidos:
    aluguel de espaço, evento fechado, venda no balcão sem registro.
    """

    __tablename__ = "receita_avulsa"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)

    descricao = db.Column(db.String(120))
    valor = db.Column(db.Float, nullable=False)
    categoria = db.Column(db.String(50), default="Outras receitas", nullable=False, index=True)
    data_registro = db.Column(db.Date, nullable=False, index=True)
    observacao = db.Column(db.String(250))

    tenant = db.relationship("Tenant", back_populates="receitas_avulsas")

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<ReceitaAvulsa R$ {self.valor:.2f} {self.data_registro}>"
