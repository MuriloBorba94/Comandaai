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
    # Id deste restaurante dentro do Asaas. Criado uma vez e reaproveitado: um
    # cliente novo a cada mês encheria a conta de duplicatas do mesmo
    # restaurante e quebraria os relatórios do próprio gateway.
    asaas_customer_id = db.Column(db.String(60), index=True)
    proxima_cobranca_em = db.Column(db.DateTime)

    # Identidade visual do restaurante: a logo, e só. É um caminho relativo
    # dentro de static/uploads, como as fotos de produto — isolada na pasta do
    # tenant.
    #
    # Havia aqui um `cor_marca` ao lado. Ele saiu com o tema Industry, que fixa
    # a cor do sistema: a coluna guardava uma escolha que nenhuma tela mostrava.
    logo = db.Column(db.String(200))

    # Recebimento por PIX. A chave é do RESTAURANTE: o dinheiro cai direto na
    # conta dele e a plataforma não é intermediária de pagamento em momento
    # nenhum. Chave PIX não é segredo (é um e-mail, um telefone, um CNPJ ou uma
    # chave aleatória — feita para ser divulgada), então fica em coluna comum;
    # o que exige cuidado é só não deixar ninguém editar a de outro tenant.
    #
    # Nome e cidade vão impressos no código e aparecem na tela do banco do
    # cliente. Quando vazios, caem para o nome fantasia do restaurante.
    #
    # `pix_cidade` guarda a cidade DO RESTAURANTE, não só a do recebedor: ela
    # também completa o endereço quando o entregador abre a rota no mapa. O nome
    # do campo é herança de quando o PIX era o único uso — o rótulo na tela já
    # diz "cidade do restaurante". Renomear a coluna custaria uma migration para
    # ganhar nada que o comentário não resolva.
    pix_chave = db.Column(db.String(80))
    pix_recebedor = db.Column(db.String(60))
    pix_cidade = db.Column(db.String(40))

    # Como este restaurante avisa o cliente pelo WhatsApp.
    #
    # "link" (padrão) prepara a mensagem e alguém clica: não custa nada e
    # funciona no primeiro dia. "meta" envia sozinho pela API oficial, com a
    # conta e a fatura DO RESTAURANTE — a plataforma não intermedeia o envio.
    #
    # O token é um segredo de verdade e fica em texto no banco, como a Meta o
    # entrega. Quem tiver o arquivo do banco tem o token, então o backup precisa
    # ser tratado como material sensível — o que já valia, porque ali também
    # estão telefone e endereço dos clientes.
    whatsapp_provedor = db.Column(db.String(20), default="link", nullable=False)
    whatsapp_phone_id = db.Column(db.String(40))
    whatsapp_token = db.Column(db.Text)
    # Modelos aprovados pela Meta, como `evento=nome`, separados por vírgula ou
    # quebra de linha. Mesmo formato dos limites do plano.
    whatsapp_modelos = db.Column(db.Text)

    # Meta de margem sobre o PREÇO DE VENDA, como no sistema original: o preço
    # sugerido é custo / (1 - margem/100). Não é markup sobre o custo — 60 aqui
    # significa "quero que 60% do preço seja lucro", não "somar 60% ao custo".
    margem_lucro = db.Column(db.Float, default=60.0, nullable=False)

    # Quantas mesas o salão tem. 0 = não atende mesa, e o fluxo de comanda fica
    # indisponível para este tenant. No sistema original o limite de mesas era
    # uma constante no código (1..30), o que não serve quando cada restaurante
    # tem um salão diferente.
    qtd_mesas = db.Column(db.Integer, default=0, nullable=False)

    # A loja está atendendo agora?
    #
    # Padrão True para não fechar a porta de quem já vende: ligar um
    # interruptor novo não pode tirar do ar quem nunca soube que ele existia.
    # Quem passar a usar o caixa controla isto pelo painel.
    loja_aberta = db.Column(db.Boolean, default=True, nullable=False)

    # Janela de tempo informada ao cliente. A de ENTREGA é a base; a de retirada
    # é separada porque o cliente que busca no balcão não espera o deslocamento
    # — e, quando não preenchida, continua sendo derivada da de entrega, como
    # era antes de existirem os dois campos.
    tempo_estimado_min = db.Column(db.Integer, default=40, nullable=False)
    tempo_estimado_max = db.Column(db.Integer, default=60, nullable=False)
    tempo_retirada_min = db.Column(db.Integer)
    tempo_retirada_max = db.Column(db.Integer)

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
