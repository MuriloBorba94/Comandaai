from __future__ import annotations

from datetime import datetime

from ..extensions import db
from .mixins import TimestampMixin

# Tipos de movimentação. "estorno" devolve ao estoque o que a baixa de um pedido
# cancelado tirou; "perda" registra quebra ou vencimento, que some do estoque mas
# não é venda.
MOV_ENTRADA = "entrada"
MOV_SAIDA = "saida"
MOV_PERDA = "perda"
MOV_AJUSTE_ENTRADA = "ajuste_entrada"
MOV_AJUSTE_SAIDA = "ajuste_saida"
MOV_ESTORNO = "estorno"

TIPOS_MOVIMENTACAO = (
    MOV_ENTRADA,
    MOV_SAIDA,
    MOV_PERDA,
    MOV_AJUSTE_ENTRADA,
    MOV_AJUSTE_SAIDA,
    MOV_ESTORNO,
)

# Tipos que aumentam o saldo; os demais diminuem.
TIPOS_QUE_SOMAM = {MOV_ENTRADA, MOV_AJUSTE_ENTRADA, MOV_ESTORNO}

ROTULO_MOVIMENTACAO = {
    MOV_ENTRADA: "Entrada (compra)",
    MOV_SAIDA: "Saída (venda)",
    MOV_PERDA: "Perda",
    MOV_AJUSTE_ENTRADA: "Ajuste para mais",
    MOV_AJUSTE_SAIDA: "Ajuste para menos",
    MOV_ESTORNO: "Estorno de cancelamento",
}

UNIDADES = ("g", "kg", "ml", "l", "un", "fatia", "porção")


class Insumo(TimestampMixin, db.Model):
    """Matéria-prima do restaurante: carne, pão, queijo, embalagem.

    O custo não é digitado por unidade — vem do pacote de compra, que é como o
    dono compra de verdade: "5 kg por R$ 120" em vez de "R$ 0,024 por grama".
    """

    __tablename__ = "insumo"

    __table_args__ = (db.UniqueConstraint("tenant_id", "nome", name="uq_insumo_tenant_nome"),)

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    nome = db.Column(db.String(100), nullable=False)
    unidade = db.Column(db.String(10), default="g", nullable=False)

    preco_compra = db.Column(db.Float, default=0.0, nullable=False)
    quantidade_compra = db.Column(db.Float, default=1.0, nullable=False)

    estoque_atual = db.Column(db.Float, default=0.0, nullable=False)
    estoque_minimo = db.Column(db.Float, default=0.0, nullable=False)
    # Insumo sem controle entra no custo do prato mas não movimenta saldo — serve
    # para tempero e afins, cujo consumo não vale a pena rastrear.
    controle_estoque = db.Column(db.Boolean, default=True, nullable=False)

    tenant = db.relationship("Tenant", back_populates="insumos")
    fichas = db.relationship("FichaTecnica", back_populates="insumo", cascade="all, delete-orphan")

    @property
    def custo_unitario(self) -> float:
        """Custo de uma unidade, derivado do pacote de compra."""
        quantidade = float(self.quantidade_compra or 0)
        if quantidade <= 0:
            return 0.0
        return float(self.preco_compra or 0) / quantidade

    @property
    def abaixo_do_minimo(self) -> bool:
        if not self.controle_estoque:
            return False
        return float(self.estoque_atual or 0) <= float(self.estoque_minimo or 0)

    @property
    def negativo(self) -> bool:
        """Saldo negativo significa venda sem entrada registrada."""
        return self.controle_estoque and float(self.estoque_atual or 0) < 0

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Insumo {self.nome!r} tenant={self.tenant_id}>"


class FichaTecnica(db.Model):
    """Quanto de um insumo cada unidade de um produto consome.

    ATENÇÃO: a tabela liga produto e insumo sem carregar tenant_id, então o banco
    por si só não impede ligar o produto de um tenant ao insumo de outro. Quem
    garante isso é o serviço de estoque, e há teste cobrindo a tentativa.
    """

    __tablename__ = "ficha_tecnica"

    __table_args__ = (
        db.UniqueConstraint("produto_id", "insumo_id", name="uq_ficha_produto_insumo"),
    )

    id = db.Column(db.Integer, primary_key=True)
    produto_id = db.Column(db.Integer, db.ForeignKey("produto.id", ondelete="CASCADE"), nullable=False, index=True)
    insumo_id = db.Column(db.Integer, db.ForeignKey("insumo.id", ondelete="CASCADE"), nullable=False, index=True)
    quantidade_usada = db.Column(db.Float, default=0.0, nullable=False)

    produto = db.relationship("Produto", back_populates="ficha")
    insumo = db.relationship("Insumo", back_populates="fichas")

    @property
    def custo(self) -> float:
        return round(self.insumo.custo_unitario * float(self.quantidade_usada or 0), 4)


class MovimentacaoEstoque(db.Model):
    """Razão do estoque: toda mudança de saldo deixa uma linha aqui.

    Guarda saldo anterior e posterior para a conferência não depender de
    recalcular a soma inteira — se um dia o saldo divergir, a linha mostra onde.
    """

    __tablename__ = "movimentacao_estoque"

    __table_args__ = (
        # No máximo uma saída (e um estorno) por pedido e insumo. É essa trava que
        # torna a baixa idempotente: reprocessar o mesmo pedido não consome duas
        # vezes. Movimentação manual tem pedido_id nulo, e NULL não colide em
        # unique — então ajustes podem repetir livremente.
        db.UniqueConstraint(
            "pedido_id", "insumo_id", "tipo", name="uq_movimento_pedido_insumo_tipo"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    # RESTRICT: insumo com histórico de movimentação não pode ser apagado, senão
    # o razão fica com furo.
    insumo_id = db.Column(db.Integer, db.ForeignKey("insumo.id", ondelete="RESTRICT"), nullable=False, index=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey("pedido.id", ondelete="SET NULL"), index=True)

    tipo = db.Column(db.String(20), nullable=False, index=True)
    quantidade = db.Column(db.Float, nullable=False)
    saldo_anterior = db.Column(db.Float, nullable=False)
    saldo_posterior = db.Column(db.Float, nullable=False)
    custo_unitario = db.Column(db.Float, default=0.0, nullable=False)
    observacao = db.Column(db.String(250))
    usuario = db.Column(db.String(80))
    created_at = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)

    insumo = db.relationship("Insumo")
    pedido = db.relationship("Pedido")

    @property
    def rotulo(self) -> str:
        return ROTULO_MOVIMENTACAO.get(self.tipo, self.tipo)

    @property
    def soma(self) -> bool:
        return self.tipo in TIPOS_QUE_SOMAM
