from __future__ import annotations

from ..extensions import db
from .mixins import PasswordMixin, TimestampMixin

# Papéis de quem trabalha no restaurante.
#
# `admin` mexe em tudo, inclusive dinheiro e configuração. `atendente` opera o
# turno: cozinha, mesas, pedidos — não vê financeiro nem troca a chave PIX.
# `entregador` só enxerga as entregas dele.
#
# Não é hierarquia com níveis: são três funções diferentes, e alguém que faz
# duas coisas ganha o papel de maior alcance. Um sistema de permissão fina para
# um restaurante de cinco pessoas custa mais para operar do que resolve.
ROLE_ADMIN = "admin"
ROLE_ATENDENTE = "atendente"
ROLE_ENTREGADOR = "entregador"

ROLES = (
    (ROLE_ADMIN, "Administrador", "Mexe em tudo: cardápio, financeiro, configuração e cobrança."),
    (ROLE_ATENDENTE, "Atendente", "Opera o turno: cozinha, mesas e pedidos. Não vê financeiro."),
    (ROLE_ENTREGADOR, "Entregador", "Vê só as entregas: endereço, rota e baixa na entrega."),
)

ROLES_VALIDOS = tuple(slug for slug, _, _ in ROLES)

ROTULO_DO_ROLE = {slug: rotulo for slug, rotulo, _ in ROLES}


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
