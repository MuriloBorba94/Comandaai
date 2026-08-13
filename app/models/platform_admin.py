from __future__ import annotations

from ..extensions import db
from .mixins import PasswordMixin, TimestampMixin


class PlatformAdmin(PasswordMixin, TimestampMixin, db.Model):
    """Super-admin do revendedor da plataforma. Tabela própria e sessão
    própria (session["platform_admin_id"]) para nunca se confundir com um
    Usuario de tenant — evita que um `if` esquecido vaze superpoderes entre
    tenants."""

    __tablename__ = "platform_admin"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
