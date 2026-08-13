from __future__ import annotations

import secrets
from datetime import datetime

from ..extensions import db
from .mixins import TimestampMixin

# Fluxo de status. "Aguardando PIX" do sistema original fica de fora: pagamento
# é a Fase 6, e um status que nada movimenta só confundiria a cozinha.
STATUS_NOVO = "Novo"
STATUS_CONFIRMADO = "Confirmado"
STATUS_EM_PREPARO = "Em preparo"
STATUS_PRONTO = "Pronto"
STATUS_SAIU_ENTREGA = "Saiu para entrega"
STATUS_ENTREGUE = "Entregue"
STATUS_CANCELADO = "Cancelado"

STATUS_TODOS = (
    STATUS_NOVO,
    STATUS_CONFIRMADO,
    STATUS_EM_PREPARO,
    STATUS_PRONTO,
    STATUS_SAIU_ENTREGA,
    STATUS_ENTREGUE,
    STATUS_CANCELADO,
)

# Status que ainda exigem ação de alguém — o que a cozinha precisa ver.
STATUS_ATIVOS = (
    STATUS_NOVO,
    STATUS_CONFIRMADO,
    STATUS_EM_PREPARO,
    STATUS_PRONTO,
    STATUS_SAIU_ENTREGA,
)

STATUS_FINAIS = (STATUS_ENTREGUE, STATUS_CANCELADO)

TIPO_ENTREGA = "Entrega"
TIPO_RETIRADA = "Retirada"
TIPO_MESA = "Mesa"
TIPOS = (TIPO_ENTREGA, TIPO_RETIRADA, TIPO_MESA)

# Cada status registra quando aconteceu, para medir tempo de cozinha depois.
CAMPO_TIMESTAMP = {
    STATUS_CONFIRMADO: "confirmado_em",
    STATUS_EM_PREPARO: "em_preparo_em",
    STATUS_PRONTO: "pronto_em",
    STATUS_SAIU_ENTREGA: "saiu_entrega_em",
    STATUS_ENTREGUE: "entregue_em",
    STATUS_CANCELADO: "cancelado_em",
}


class Pedido(TimestampMixin, db.Model):
    __tablename__ = "pedido"

    __table_args__ = (
        # Numeração reinicia em cada tenant: o cliente do restaurante vê
        # "Pedido #1", não "#4712" por causa dos pedidos de outras lojas.
        db.UniqueConstraint("tenant_id", "numero", name="uq_pedido_tenant_numero"),
        # Idempotência por tenant: reenvio do mesmo formulário (duplo clique,
        # conexão instável) não cria um segundo pedido.
        db.UniqueConstraint("tenant_id", "client_request_id", name="uq_pedido_tenant_request"),
    )

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    numero = db.Column(db.Integer, nullable=False)

    # Token do link público de acompanhamento. Aleatório para que um cliente não
    # consiga ver o pedido de outro trocando o número na URL.
    public_token = db.Column(
        db.String(64), unique=True, nullable=False, index=True, default=lambda: secrets.token_urlsafe(24)
    )
    client_request_id = db.Column(db.String(64))

    cliente = db.Column(db.String(100), nullable=False)
    telefone = db.Column(db.String(20), index=True)
    tipo = db.Column(db.String(20), nullable=False)

    # Mesa em coluna própria. No sistema original o número da mesa era escrito
    # dentro de `endereco` como "Mesa 01" e lido de volta com parsing de string,
    # o que fazia comandas "fantasma" quando o texto não batia.
    mesa = db.Column(db.Integer, index=True)
    comanda_aberta = db.Column(db.Boolean, default=False, nullable=False, index=True)
    endereco = db.Column(db.String(350))

    # Bairro da entrega. Guarda o id (para relatório) e também nome e taxa
    # congelados: se o dono renomear o bairro ou mudar a taxa depois, o pedido
    # histórico continua mostrando o que o cliente contratou.
    bairro_id = db.Column(db.Integer, db.ForeignKey("bairro_entrega.id", ondelete="SET NULL"), index=True)
    bairro_nome = db.Column(db.String(100))

    # Cupom aplicado, também com o código congelado.
    cupom_id = db.Column(db.Integer, db.ForeignKey("cupom.id", ondelete="SET NULL"), index=True)
    cupom_codigo = db.Column(db.String(40), index=True)

    pagamento = db.Column(db.String(80), nullable=False)
    observacao = db.Column(db.String(500))

    subtotal = db.Column(db.Float, default=0.0, nullable=False)
    taxa_entrega = db.Column(db.Float, default=0.0, nullable=False)
    desconto = db.Column(db.Float, default=0.0, nullable=False)
    total = db.Column(db.Float, default=0.0, nullable=False)

    status = db.Column(db.String(30), default=STATUS_NOVO, nullable=False, index=True)
    origem = db.Column(db.String(20), default="site", nullable=False)
    tempo_estimado_min = db.Column(db.Integer)
    tempo_estimado_max = db.Column(db.Integer)

    confirmado_em = db.Column(db.DateTime)
    em_preparo_em = db.Column(db.DateTime)
    pronto_em = db.Column(db.DateTime)
    saiu_entrega_em = db.Column(db.DateTime)
    entregue_em = db.Column(db.DateTime)
    cancelado_em = db.Column(db.DateTime)

    tenant = db.relationship("Tenant", back_populates="pedidos")
    itens = db.relationship(
        "PedidoItem", back_populates="pedido", cascade="all, delete-orphan", order_by="PedidoItem.id"
    )

    @property
    def ativo(self) -> bool:
        return self.status in STATUS_ATIVOS

    @property
    def descricao_local(self) -> str:
        """Onde o pedido vai: usado pela cozinha e pela página do cliente."""
        if self.tipo == TIPO_MESA:
            return f"Mesa {self.mesa:02d}" if self.mesa else "Mesa"
        if self.tipo == TIPO_RETIRADA:
            return "Retirada no local"
        partes = [self.endereco or "Entrega"]
        if self.bairro_nome:
            partes.append(self.bairro_nome)
        return " — ".join(partes)

    def recalcular_total(self) -> None:
        """Soma os itens e aplica taxa e desconto.

        O total nunca vem do navegador — é sempre derivado dos itens gravados.
        """
        self.subtotal = round(sum(item.total for item in self.itens), 2)
        self.total = round(
            (self.subtotal or 0.0) + (self.taxa_entrega or 0.0) - (self.desconto or 0.0), 2
        )

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Pedido #{self.numero} tenant={self.tenant_id} {self.status}>"


class PedidoItem(db.Model):
    """Item de um pedido, com preço e nome congelados no momento da venda.

    O produto pode ser renomeado, ter preço alterado ou ser excluído depois; o
    pedido histórico precisa continuar mostrando o que o cliente comprou e pagou.
    """

    __tablename__ = "pedido_item"

    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey("pedido.id", ondelete="CASCADE"), nullable=False, index=True)
    # SET NULL: excluir um produto do cardápio não pode apagar histórico de venda.
    produto_id = db.Column(db.Integer, db.ForeignKey("produto.id", ondelete="SET NULL"), index=True)

    nome = db.Column(db.String(100), nullable=False)
    preco_base = db.Column(db.Float, nullable=False)
    # Preço unitário já somando os adicionais escolhidos.
    preco_unitario = db.Column(db.Float, nullable=False)
    quantidade = db.Column(db.Integer, default=1, nullable=False)
    total = db.Column(db.Float, nullable=False)
    observacao = db.Column(db.String(180))

    pedido = db.relationship("Pedido", back_populates="itens")
    adicionais = db.relationship(
        "PedidoItemAdicional", back_populates="item", cascade="all, delete-orphan", order_by="PedidoItemAdicional.nome"
    )

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<PedidoItem {self.quantidade}x {self.nome!r}>"


class PedidoItemAdicional(db.Model):
    """Adicional escolhido num item, também com nome e preço congelados."""

    __tablename__ = "pedido_item_adicional"

    id = db.Column(db.Integer, primary_key=True)
    pedido_item_id = db.Column(
        db.Integer, db.ForeignKey("pedido_item.id", ondelete="CASCADE"), nullable=False, index=True
    )
    adicional_id = db.Column(db.Integer, db.ForeignKey("adicional.id", ondelete="SET NULL"), index=True)
    nome = db.Column(db.String(60), nullable=False)
    preco = db.Column(db.Float, default=0.0, nullable=False)

    item = db.relationship("PedidoItem", back_populates="adicionais")


def agora() -> datetime:
    return datetime.now()
