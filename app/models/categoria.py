from __future__ import annotations

from ..extensions import db
from .mixins import TimestampMixin


class Categoria(TimestampMixin, db.Model):
    """Seção do cardápio de um tenant (ex.: "Burgers", "Pizzas Doces").

    No sistema single-tenant original a categoria era um texto livre em
    Produto.categoria, e a ordem da vitrine estava fixa no código ("Burgers",
    "Combos", "Acompanhamentos", "Bebidas"). Isso não sobrevive ao
    multi-tenant: cada restaurante tem as suas seções e a sua ordem, então a
    categoria virou entidade própria, escopada por tenant e ordenável.
    """

    __tablename__ = "categoria"

    # Unicidade POR TENANT: dois restaurantes podem ter "Bebidas" sem colidir.
    __table_args__ = (db.UniqueConstraint("tenant_id", "nome", name="uq_categoria_tenant_nome"),)

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    nome = db.Column(db.String(60), nullable=False)
    # Posição na vitrine; empates caem para ordem alfabética.
    ordem = db.Column(db.Integer, default=0, nullable=False)
    ativa = db.Column(db.Boolean, default=True, nullable=False)

    tenant = db.relationship("Tenant", back_populates="categorias")
    produtos = db.relationship("Produto", back_populates="categoria")

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Categoria {self.nome!r} tenant={self.tenant_id}>"
