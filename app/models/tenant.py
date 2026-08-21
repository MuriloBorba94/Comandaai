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

    # Identidade visual do restaurante. A logo é um caminho relativo dentro de
    # static/uploads, como as fotos de produto — isolada na pasta do tenant. A
    # cor entra no CSS, então é validada como hex antes de ser usada (ver
    # app/layout.py); guardar aqui texto inválido não vira injeção.
    logo = db.Column(db.String(200))
    cor_marca = db.Column(db.String(7))

    # Recebimento por PIX. A chave é do RESTAURANTE: o dinheiro cai direto na
    # conta dele e a plataforma não é intermediária de pagamento em momento
    # nenhum. Chave PIX não é segredo (é um e-mail, um telefone, um CNPJ ou uma
    # chave aleatória — feita para ser divulgada), então fica em coluna comum;
    # o que exige cuidado é só não deixar ninguém editar a de outro tenant.
    #
    # Nome e cidade vão impressos no código e aparecem na tela do banco do
    # cliente. Quando vazios, caem para o nome fantasia do restaurante.
    pix_chave = db.Column(db.String(80))
    pix_recebedor = db.Column(db.String(60))
    pix_cidade = db.Column(db.String(40))

    # Meta de margem sobre o PREÇO DE VENDA, como no sistema original: o preço
    # sugerido é custo / (1 - margem/100). Não é markup sobre o custo — 60 aqui
    # significa "quero que 60% do preço seja lucro", não "somar 60% ao custo".
    margem_lucro = db.Column(db.Float, default=60.0, nullable=False)

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
    cupons = db.relationship("Cupom", back_populates="tenant", cascade="all, delete-orphan")
    bairros = db.relationship("BairroEntrega", back_populates="tenant", cascade="all, delete-orphan")
    insumos = db.relationship("Insumo", back_populates="tenant", cascade="all, delete-orphan")
    despesas = db.relationship("Despesa", back_populates="tenant", cascade="all, delete-orphan")
    receitas_avulsas = db.relationship(
        "ReceitaAvulsa", back_populates="tenant", cascade="all, delete-orphan"
    )
    cobrancas = db.relationship(
        "Cobranca",
        back_populates="tenant",
        cascade="all, delete-orphan",
        order_by="desc(Cobranca.competencia)",
    )

    @property
    def atende_mesa(self) -> bool:
        return (self.qtd_mesas or 0) > 0
