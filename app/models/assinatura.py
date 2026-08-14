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

# Recursos que um plano pode liberar.
#
# O catálogo vive no código, não no banco, porque cada slug corresponde a um
# caminho real da aplicação: um recurso cadastrável que nenhum código consulta
# seria só um texto bonito na tela.
#
# O cardápio, o carrinho, o pedido e o acompanhamento pelo cliente NÃO entram
# aqui: são a base do produto e nunca são bloqueados. Vender um plano que não
# tira pedido não faria sentido.
RECURSOS = (
    ("cozinha", "Painel da cozinha", "Fila de pedidos por status, com atualização automática."),
    ("mesas", "Salão e comanda de mesa", "Mapa de mesas, comanda aberta e fechamento."),
    ("cupons", "Cupons de desconto", "Cupom com limite de usos e pedido mínimo."),
    ("bairros", "Taxa de entrega por bairro", "Taxa e prazo próprios por região."),
    ("fotos", "Fotos nos produtos", "Imagem no cardápio, otimizada automaticamente."),
    ("relatorios", "Relatórios de venda", "Faturamento, ticket médio e mais vendidos."),
    ("estoque", "Estoque e ficha técnica", "Insumos, receita por produto, custo e lucro por venda."),
)

RECURSOS_SLUGS = tuple(slug for slug, _, _ in RECURSOS)


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

    # Slugs de RECURSOS liberados, separados por vírgula.
    #
    # NULL significa "não configurado", e aí o plano libera TUDO. É deliberado:
    # sem isso, aplicar feature-gating num sistema em uso tiraria na hora todos
    # os recursos de todos os clientes. A restrição só começa a valer quando
    # alguém marca as caixas na tela de planos.
    recursos = db.Column(db.Text)

    @property
    def gratuito(self) -> bool:
        return (self.preco_mensal or 0) <= 0

    @property
    def recursos_configurados(self) -> bool:
        return self.recursos is not None

    @property
    def recursos_liberados(self) -> set[str]:
        """Recursos que este plano libera. Plano não configurado libera todos."""
        if self.recursos is None:
            return set(RECURSOS_SLUGS)
        return {
            trecho.strip()
            for trecho in self.recursos.split(",")
            if trecho.strip() in RECURSOS_SLUGS
        }

    def definir_recursos(self, slugs) -> None:
        """Grava os recursos liberados, ignorando slugs que não existem."""
        validos = [slug for slug in RECURSOS_SLUGS if slug in set(slugs or [])]
        # String vazia (e não NULL) registra "configurado, mas nada liberado".
        self.recursos = ",".join(validos)

    def libera(self, slug: str) -> bool:
        return slug in self.recursos_liberados

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Plano {self.slug!r} R$ {self.preco_mensal:.2f}>"


class Cobranca(TimestampMixin, db.Model):
    """Uma mensalidade de um tenant.

    `competencia` é o mês de referência, sempre no dia 1. O par
    (tenant_id, competencia) é único: é o que impede o ciclo de faturamento de
    gerar duas cobranças do mesmo mês se rodar duas vezes no mesmo dia.
    """

    __tablename__ = "cobranca"

    # Índice único PARCIAL: no máximo uma cobrança viva por competência, mas
    # canceladas ficam fora da conta.
    #
    # Uma unique constraint comum impediria reemitir o mês depois de cancelar
    # por engano — e apagar a cancelada para liberar a vaga perderia o registro
    # de que houve um cancelamento.
    __table_args__ = (
        db.Index(
            "uq_cobranca_competencia_viva",
            "tenant_id",
            "competencia",
            unique=True,
            sqlite_where=db.text("status != 'cancelada'"),
            postgresql_where=db.text("status != 'cancelada'"),
        ),
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
