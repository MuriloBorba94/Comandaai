from __future__ import annotations

from datetime import datetime

from ..extensions import db
from .mixins import TimestampMixin

TIPO_PERCENTUAL = "percentual"
TIPO_FIXO = "fixo"
TIPOS_CUPOM = (TIPO_PERCENTUAL, TIPO_FIXO)

# Estados de um uso de cupom. "reservado" segura a vaga durante o checkout sem
# contar como consumo; só "usado" incrementa o contador do cupom.
USO_RESERVADO = "reservado"
USO_USADO = "usado"
USO_LIBERADO = "liberado"


class Cupom(TimestampMixin, db.Model):
    __tablename__ = "cupom"

    # Unicidade POR TENANT: dois restaurantes podem ter o cupom "BEMVINDO".
    # No sistema original o código era unique global, o que num SaaS faria o
    # primeiro cliente a criar "BEMVINDO" bloquear o código para todos os outros.
    __table_args__ = (db.UniqueConstraint("tenant_id", "codigo", name="uq_cupom_tenant_codigo"),)

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    codigo = db.Column(db.String(40), nullable=False, index=True)
    descricao = db.Column(db.String(160))
    tipo = db.Column(db.String(20), default=TIPO_PERCENTUAL, nullable=False)
    valor = db.Column(db.Float, nullable=False)
    pedido_minimo = db.Column(db.Float, default=0.0, nullable=False)
    limite_usos = db.Column(db.Integer, default=1, nullable=False)
    usos_confirmados = db.Column(db.Integer, default=0, nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    # Por padrão o cupom NÃO vale sobre combo promocional, para não empilhar
    # desconto em cima de item que já está com preço promocional.
    permite_combo_promocional = db.Column(db.Boolean, default=False, nullable=False)
    inicio_em = db.Column(db.DateTime)
    fim_em = db.Column(db.DateTime)

    tenant = db.relationship("Tenant", back_populates="cupons")
    usos = db.relationship("CupomUso", back_populates="cupom", cascade="all, delete-orphan")

    @property
    def reservas_ativas(self) -> int:
        agora = datetime.now()
        return sum(
            1
            for uso in self.usos
            if uso.status == USO_RESERVADO and (not uso.expira_em or uso.expira_em > agora)
        )

    @property
    def disponiveis(self) -> int:
        """Quantos usos ainda podem ser vendidos agora.

        Reserva ativa conta como indisponível: é isso que impede dois checkouts
        simultâneos levarem o último uso do mesmo cupom.
        """
        return max(
            0,
            int(self.limite_usos or 0) - int(self.usos_confirmados or 0) - self.reservas_ativas,
        )

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Cupom {self.codigo!r} tenant={self.tenant_id}>"


class CupomUso(db.Model):
    """Uso de um cupom por um pedido, no padrão reservar → usar / liberar."""

    __tablename__ = "cupom_uso"

    id = db.Column(db.Integer, primary_key=True)
    cupom_id = db.Column(db.Integer, db.ForeignKey("cupom.id", ondelete="CASCADE"), nullable=False, index=True)
    # Um pedido tem no máximo um cupom.
    pedido_id = db.Column(
        db.Integer, db.ForeignKey("pedido.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    status = db.Column(db.String(20), default=USO_RESERVADO, nullable=False, index=True)
    desconto = db.Column(db.Float, default=0.0, nullable=False)
    reservado_em = db.Column(db.DateTime, default=datetime.now, nullable=False)
    # Fica nulo nesta fase: a reserva só expira quando o pedido puder ficar
    # preso aguardando pagamento (Fase 6). Expirar enquanto a cozinha demora
    # liberaria o cupom para outra pessoa com o desconto já concedido.
    expira_em = db.Column(db.DateTime)
    usado_em = db.Column(db.DateTime)
    liberado_em = db.Column(db.DateTime)

    cupom = db.relationship("Cupom", back_populates="usos")
    pedido = db.relationship(
        "Pedido", backref=db.backref("cupom_uso", uselist=False, cascade="all, delete-orphan")
    )

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<CupomUso cupom={self.cupom_id} pedido={self.pedido_id} {self.status}>"


class BairroEntrega(TimestampMixin, db.Model):
    """Bairro atendido por um tenant, com taxa e prazo próprios."""

    __tablename__ = "bairro_entrega"

    # Também por tenant: "Centro" existe em toda cidade. No original era unique
    # global.
    __table_args__ = (db.UniqueConstraint("tenant_id", "nome", name="uq_bairro_tenant_nome"),)

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    nome = db.Column(db.String(100), nullable=False)
    taxa = db.Column(db.Float, default=0.0, nullable=False)
    prazo_adicional_min = db.Column(db.Integer, default=0, nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)
    ordem = db.Column(db.Integer, default=0, nullable=False)

    tenant = db.relationship("Tenant", back_populates="bairros")

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<BairroEntrega {self.nome!r} tenant={self.tenant_id}>"
