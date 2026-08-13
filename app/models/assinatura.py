from __future__ import annotations

from datetime import date, datetime

from ..extensions import db
from .mixins import TimestampMixin

# Provedores de cobrança. "manual" = você recebe o PIX e registra o pagamento na
# mão; é o modo com que a plataforma começa a operar. "asaas" fica reservado
# para quando existir chave de API (a cobrança é criada no gateway e o webhook
# confirma o pagamento).
PROVEDOR_MANUAL = "manual"
PROVEDOR_ASAAS = "asaas"
PROVEDORES = (PROVEDOR_MANUAL, PROVEDOR_ASAAS)

COBRANCA_PENDENTE = "pendente"
COBRANCA_PAGA = "paga"
COBRANCA_CANCELADA = "cancelada"
STATUS_COBRANCA = (COBRANCA_PENDENTE, COBRANCA_PAGA, COBRANCA_CANCELADA)


class Plano(TimestampMixin, db.Model):
    """Plano de assinatura oferecido pela plataforma, com o preço mensal.

    Vive no nível da plataforma (não é escopado por tenant): é o seu catálogo de
    venda. `Tenant.plano` guarda o slug deste plano — uma referência por texto em
    vez de chave estrangeira, para não precisar migrar os tenants que já existem
    e porque o plano do tenant precisa sobreviver à exclusão de um plano do
    catálogo.
    """

    __tablename__ = "plano"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(30), unique=True, nullable=False, index=True)
    nome = db.Column(db.String(60), nullable=False)
    preco_mensal = db.Column(db.Float, default=0.0, nullable=False)
    descricao = db.Column(db.String(200))
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    ordem = db.Column(db.Integer, default=0, nullable=False)

    @property
    def gratuito(self) -> bool:
        return (self.preco_mensal or 0) <= 0

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Plano {self.slug!r} R$ {self.preco_mensal:.2f}>"


class Cobranca(TimestampMixin, db.Model):
    """Uma mensalidade de um tenant.

    `competencia` é o mês de referência, sempre no dia 1. O par
    (tenant_id, competencia) é único: é o que impede o ciclo de faturamento de
    gerar duas cobranças do mesmo mês se rodar duas vezes no mesmo dia.
    """

    __tablename__ = "cobranca"

    __table_args__ = (
        db.UniqueConstraint("tenant_id", "competencia", name="uq_cobranca_tenant_competencia"),
    )

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)

    competencia = db.Column(db.Date, nullable=False, index=True)
    vencimento = db.Column(db.Date, nullable=False, index=True)
    # Plano e preço congelados: mudar o preço do catálogo depois não reescreve
    # cobrança já emitida.
    plano_slug = db.Column(db.String(30), nullable=False)
    valor = db.Column(db.Float, nullable=False)

    status = db.Column(db.String(20), default=COBRANCA_PENDENTE, nullable=False, index=True)
    provedor = db.Column(db.String(20), default=PROVEDOR_MANUAL, nullable=False)
    id_externo = db.Column(db.String(120), index=True)

    pago_em = db.Column(db.DateTime)
    valor_pago = db.Column(db.Float)
    metodo_pagamento = db.Column(db.String(40))
    observacao = db.Column(db.String(300))

    tenant = db.relationship("Tenant", back_populates="cobrancas")

    @property
    def paga(self) -> bool:
        return self.status == COBRANCA_PAGA

    @property
    def pendente(self) -> bool:
        return self.status == COBRANCA_PENDENTE

    def dias_de_atraso(self, hoje: date | None = None) -> int:
        """Dias corridos desde o vencimento. 0 se paga, cancelada ou em dia."""
        if self.status != COBRANCA_PENDENTE:
            return 0
        hoje = hoje or date.today()
        return max(0, (hoje - self.vencimento).days)

    @property
    def rotulo_competencia(self) -> str:
        return self.competencia.strftime("%m/%Y") if self.competencia else "—"

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Cobranca tenant={self.tenant_id} {self.rotulo_competencia} {self.status}>"


def primeiro_dia(momento: date | datetime | None = None) -> date:
    """Normaliza qualquer data para o primeiro dia do seu mês."""
    base = momento or date.today()
    if isinstance(base, datetime):
        base = base.date()
    return base.replace(day=1)


def somar_um_mes(referencia: date) -> date:
    """Primeiro dia do mês seguinte ao de `referencia`."""
    referencia = primeiro_dia(referencia)
    if referencia.month == 12:
        return referencia.replace(year=referencia.year + 1, month=1)
    return referencia.replace(month=referencia.month + 1)
