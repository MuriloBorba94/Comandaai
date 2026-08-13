from __future__ import annotations

from ..extensions import db
from .mixins import TimestampMixin

STATUSES = ("trial", "active", "past_due", "canceled", "suspended")


class Tenant(TimestampMixin, db.Model):
    __tablename__ = "tenant"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(50), unique=True, nullable=False, index=True)
    nome_fantasia = db.Column(db.String(120), nullable=False)
    razao_social = db.Column(db.String(160))
    cnpj = db.Column(db.String(20))
    email_contato = db.Column(db.String(160), nullable=False)
    telefone_contato = db.Column(db.String(20))
    timezone = db.Column(db.String(40), default="America/Recife", nullable=False)

    plano = db.Column(db.String(30), default="trial", nullable=False)
    status = db.Column(db.String(20), default="trial", nullable=False, index=True)
    trial_termina_em = db.Column(db.DateTime)
    assinatura_provider = db.Column(db.String(30))
    assinatura_id_externo = db.Column(db.String(120), index=True)
    proxima_cobranca_em = db.Column(db.DateTime)

    # Quantas mesas o salão tem. 0 = não atende mesa, e o fluxo de comanda fica
    # indisponível para este tenant. No sistema original o limite de mesas era
    # uma constante no código (1..30), o que não serve quando cada restaurante
    # tem um salão diferente.
    qtd_mesas = db.Column(db.Integer, default=0, nullable=False)

    # Janela de tempo estimado informada ao cliente, por tenant.
    tempo_estimado_min = db.Column(db.Integer, default=40, nullable=False)
    tempo_estimado_max = db.Column(db.Integer, default=60, nullable=False)

    # Kill-switch manual do super-admin da plataforma, independente do
    # status de cobrança (ex.: suspender por abuso sem tocar na assinatura).
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    usuarios = db.relationship("Usuario", back_populates="tenant", cascade="all, delete-orphan")
    produtos = db.relationship("Produto", back_populates="tenant", cascade="all, delete-orphan")
    categorias = db.relationship("Categoria", back_populates="tenant", cascade="all, delete-orphan")
    adicionais = db.relationship("Adicional", back_populates="tenant", cascade="all, delete-orphan")
    pedidos = db.relationship("Pedido", back_populates="tenant", cascade="all, delete-orphan")

    @property
    def atende_mesa(self) -> bool:
        return (self.qtd_mesas or 0) > 0
