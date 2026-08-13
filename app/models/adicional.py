from __future__ import annotations

from ..extensions import db
from .mixins import TimestampMixin

# Liga produtos aos adicionais que eles aceitam. No sistema original os
# adicionais eram uma lista global aplicada a todo produto — o que fazia
# "Bacon extra" aparecer numa Coca-Cola. Aqui cada produto declara os seus.
#
# ATENÇÃO: esta tabela não tem tenant_id, então o banco por si só não impede
# ligar um Produto do tenant A a um Adicional do tenant B. Quem garante isso é
# Produto.definir_adicionais(), e há teste cobrindo a tentativa de ligação
# cruzada.
produto_adicional = db.Table(
    "produto_adicional",
    db.Column("produto_id", db.Integer, db.ForeignKey("produto.id", ondelete="CASCADE"), primary_key=True),
    db.Column("adicional_id", db.Integer, db.ForeignKey("adicional.id", ondelete="CASCADE"), primary_key=True),
)


class Adicional(TimestampMixin, db.Model):
    """Item opcional que pode ser somado a um produto (bacon, borda, etc.)."""

    __tablename__ = "adicional"

    # Unicidade por tenant, como em Categoria.
    __table_args__ = (db.UniqueConstraint("tenant_id", "nome", name="uq_adicional_tenant_nome"),)

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    nome = db.Column(db.String(60), nullable=False)
    preco = db.Column(db.Float, default=0.0, nullable=False)
    disponivel = db.Column(db.Boolean, default=True, nullable=False)

    tenant = db.relationship("Tenant", back_populates="adicionais")
    produtos = db.relationship("Produto", secondary=produto_adicional, back_populates="adicionais")

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Adicional {self.nome!r} tenant={self.tenant_id}>"
