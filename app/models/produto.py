from __future__ import annotations

from ..extensions import db
from .mixins import TimestampMixin


class Produto(TimestampMixin, db.Model):
    """Entidade de exemplo, tenant-scoped, usada para provar de ponta a ponta
    que dados de um tenant nunca aparecem para outro. Os campos espelham o
    Produto do repo single-tenant original para facilitar o port futuro."""

    __tablename__ = "produto"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    nome = db.Column(db.String(100), nullable=False)
    descricao = db.Column(db.String(300))
    preco = db.Column(db.Float, nullable=False)
    disponivel = db.Column(db.Boolean, default=True, nullable=False)

    tenant = db.relationship("Tenant", back_populates="produtos")
