from __future__ import annotations

from ..extensions import db
from .mixins import PasswordMixin, TimestampMixin


class Usuario(PasswordMixin, TimestampMixin, db.Model):
    __tablename__ = "usuario"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    nome = db.Column(db.String(100), nullable=False)
    # Não é mais globalmente único (repo single-tenant original exigia isso);
    # dois tenants podem ter usuários com o mesmo username sem conflito.
    username = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(20), default="admin", nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    tenant = db.relationship("Tenant", back_populates="usuarios")

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "username", name="uq_usuario_tenant_username"),
    )
