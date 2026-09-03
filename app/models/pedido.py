from __future__ import annotations

import secrets
from datetime import datetime

from ..extensions import db
from .mixins import TimestampMixin

# Fluxo de status.
#
# "Aguardando PIX" é o único que existe antes de o restaurante saber do pedido:
# o cliente escolheu pagar pelo site e o dinheiro ainda não entrou. Ele não
# desce para a cozinha e não baixa estoque — sai daqui só quando alguém confirma
# o recebimento (ver services/pagamentos/__init__.py) ou quando é cancelado.
STATUS_AGUARDANDO_PIX = "Aguardando PIX"
STATUS_NOVO = "Novo"
STATUS_CONFIRMADO = "Confirmado"
STATUS_EM_PREPARO = "Em preparo"
STATUS_PRONTO = "Pronto"
STATUS_SAIU_ENTREGA = "Saiu para entrega"
STATUS_ENTREGUE = "Entregue"
STATUS_CANCELADO = "Cancelado"

STATUS_TODOS = (
    STATUS_AGUARDANDO_PIX,
    STATUS_NOVO,
    STATUS_CONFIRMADO,
    STATUS_EM_PREPARO,
    STATUS_PRONTO,
    STATUS_SAIU_ENTREGA,
    STATUS_ENTREGUE,
    STATUS_CANCELADO,
)

# Status que ainda exigem ação de alguém — o que a cozinha precisa ver.
#
# "Aguardando PIX" entra: quem está no balcão precisa enxergar que existe um
# pedido a caminho do pagamento, e é dali que ele confirma o recebimento. O que
# NÃO pode é a comida começar a ser feita, e disso cuida o fluxo de transições:
# de "Aguardando PIX" só se sai pagando ou cancelando.
STATUS_ATIVOS = (
    STATUS_AGUARDANDO_PIX,
    STATUS_NOVO,
    STATUS_CONFIRMADO,
    STATUS_EM_PREPARO,
    STATUS_PRONTO,
    STATUS_SAIU_ENTREGA,
)

STATUS_FINAIS = (STATUS_ENTREGUE, STATUS_CANCELADO)

# Depois disto sem ninguém pedir nada, a mesa aparece como ociosa no mapa. Não é
# problema: é a mesa que provavelmente já terminou e ninguém foi lá perguntar.
MINUTOS_PARA_OCIOSA = 10

# O nome de cada estado, escrito no cartão ao lado da cor.
#
# Não é redundância: verde, azul e vermelho têm quase a mesma luminosidade, e
# quem tem daltonismo (uns 8% dos homens) não distingue verde de vermelho. Numa
# tela cujo ponto inteiro é "a cor é a informação", isso deixaria alguém da
# equipe sem conseguir usar o salão. A palavra resolve, e cabe.
ROTULO_DO_ESTADO = {
    "consumo": "consumo",
    "conta": "conta",
    "ociosa": "ociosa",
}

TIPO_ENTREGA = "Entrega"
TIPO_RETIRADA = "Retirada"
TIPO_MESA = "Mesa"
TIPOS = (TIPO_ENTREGA, TIPO_RETIRADA, TIPO_MESA)

# Cada status registra quando aconteceu, para medir tempo de cozinha depois.
CAMPO_TIMESTAMP = {
    STATUS_CONFIRMADO: "confirmado_em",
    STATUS_EM_PREPARO: "em_preparo_em",
    STATUS_PRONTO: "pronto_em",
    STATUS_SAIU_ENTREGA: "saiu_entrega_em",
    STATUS_ENTREGUE: "entregue_em",
    STATUS_CANCELADO: "cancelado_em",
}


class Pedido(TimestampMixin, db.Model):
    __tablename__ = "pedido"

    __table_args__ = (
        # Numeração reinicia em cada tenant: o cliente do restaurante vê
        # "Pedido #1", não "#4712" por causa dos pedidos de outras lojas.
        db.UniqueConstraint("tenant_id", "numero", name="uq_pedido_tenant_numero"),
        # Idempotência por tenant: reenvio do mesmo formulário (duplo clique,
        # conexão instável) não cria um segundo pedido.
        db.UniqueConstraint("tenant_id", "client_request_id", name="uq_pedido_tenant_request"),
    )

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True)
    numero = db.Column(db.Integer, nullable=False)

    # Token do link público de acompanhamento. Aleatório para que um cliente não
    # consiga ver o pedido de outro trocando o número na URL.
    public_token = db.Column(
        db.String(64), unique=True, nullable=False, index=True, default=lambda: secrets.token_urlsafe(24)
    )
    client_request_id = db.Column(db.String(64))

    cliente = db.Column(db.String(100), nullable=False)
    telefone = db.Column(db.String(20), index=True)
    tipo = db.Column(db.String(20), nullable=False)

    # Mesa em coluna própria. No sistema original o número da mesa era escrito
    # dentro de `endereco` como "Mesa 01" e lido de volta com parsing de string,
    # o que fazia comandas "fantasma" quando o texto não batia.
    mesa = db.Column(db.Integer, index=True)
    comanda_aberta = db.Column(db.Boolean, default=False, nullable=False, index=True)
    endereco = db.Column(db.String(350))

    # Bairro da entrega. Guarda o id (para relatório) e também nome e taxa
    # congelados: se o dono renomear o bairro ou mudar a taxa depois, o pedido
    # histórico continua mostrando o que o cliente contratou.
    bairro_id = db.Column(db.Integer, db.ForeignKey("bairro_entrega.id", ondelete="SET NULL"), index=True)
    bairro_nome = db.Column(db.String(100))

    # Onde o cliente disse que está, quando ele escolheu dizer.
    #
    # É COMPLEMENTO do endereço escrito, nunca substituto: o texto continua
    # obrigatório na entrega. Endereço em vila, condomínio ou rua sem placa é
    # justamente onde o entregador se perde, e é lá que um ponto no mapa
    # resolve — mas alguém tem de poder pedir sem GPS, do computador, com a
    # localização negada no navegador.
    #
    # A precisão vem junto porque nem toda leitura vale o mesmo. Um ponto de
    # 2 km de raio, tirado do IP em vez do GPS, mandaria o entregador para o
    # bairro errado com ar de certeza — pior do que não ter ponto nenhum. Com o
    # número guardado, a tela do entregador mostra a dúvida em vez de escondê-la.
    cliente_lat = db.Column(db.Float)
    cliente_lng = db.Column(db.Float)
    cliente_local_precisao = db.Column(db.Float)

    # Cupom aplicado, também com o código congelado.
    cupom_id = db.Column(db.Integer, db.ForeignKey("cupom.id", ondelete="SET NULL"), index=True)
    cupom_codigo = db.Column(db.String(40), index=True)

    pagamento = db.Column(db.String(80), nullable=False)
    observacao = db.Column(db.String(500))

    subtotal = db.Column(db.Float, default=0.0, nullable=False)
    taxa_entrega = db.Column(db.Float, default=0.0, nullable=False)
    desconto = db.Column(db.Float, default=0.0, nullable=False)
    total = db.Column(db.Float, default=0.0, nullable=False)

    # Custo dos insumos e lucro bruto, gravados na baixa de estoque. Ficam no
    # pedido (e não recalculados depois) porque o preço de compra do insumo muda
    # com o tempo, e o lucro daquele dia foi o daquele dia.
    custo_produtos = db.Column(db.Float, default=0.0, nullable=False)
    lucro_bruto = db.Column(db.Float, default=0.0, nullable=False)
    estoque_baixado = db.Column(db.Boolean, default=False, nullable=False, index=True)

    status = db.Column(db.String(30), default=STATUS_NOVO, nullable=False, index=True)
    origem = db.Column(db.String(20), default="site", nullable=False)
    tempo_estimado_min = db.Column(db.Integer)
    tempo_estimado_max = db.Column(db.Integer)

    # Quem está levando, e onde essa pessoa estava da última vez que o celular
    # dela contou. A posição fica no PEDIDO, e não numa tabela de rastro: o
    # cliente precisa saber onde o pedido dele está agora, não o caminho
    # percorrido — e guardar o trajeto inteiro de um entregador é vigiar
    # funcionário, coisa que o sistema não tem por que fazer.
    # A chave estrangeira leva NOME de propósito. Sem ele, o autogenerate do
    # Alembic produz `create_foreign_key(None, ...)`, e no SQLite — onde alterar
    # tabela significa recriá-la — a migration morre com "Constraint must have a
    # name". O erro não aparece ao gerar; aparece ao aplicar, que no servidor é
    # no meio da publicação.
    entregador_id = db.Column(
        db.Integer,
        db.ForeignKey("usuario.id", ondelete="SET NULL", name="fk_pedido_entregador"),
        index=True,
    )
    entrega_lat = db.Column(db.Float)
    entrega_lng = db.Column(db.Float)
    entrega_atualizado_em = db.Column(db.DateTime)

    # Quando a mesa consumiu pela última vez, e quando pediu a conta. São os
    # dois relógios que o mapa do salão precisa: sem eles, uma mesa que já comeu
    # e está esperando a conta fica com a mesma cara de uma que acabou de sentar.
    #
    # `ultimo_consumo_em` existe em vez de reaproveitar `updated_at` porque este
    # último muda por qualquer coisa — troca de status, reimpressão de comanda —
    # e "faz 10 minutos que ninguém pede nada" precisa significar exatamente
    # isso, senão a cor mente.
    ultimo_consumo_em = db.Column(db.DateTime)
    conta_pedida_em = db.Column(db.DateTime)

    confirmado_em = db.Column(db.DateTime)
    em_preparo_em = db.Column(db.DateTime)
    pronto_em = db.Column(db.DateTime)
    saiu_entrega_em = db.Column(db.DateTime)
    entregue_em = db.Column(db.DateTime)
    cancelado_em = db.Column(db.DateTime)

    tenant = db.relationship("Tenant", back_populates="pedidos")
    entregador = db.relationship("Usuario", foreign_keys=[entregador_id])
    pagamento_online = db.relationship(
        "Pagamento", back_populates="pedido", uselist=False, cascade="all, delete-orphan"
    )
    notificacoes = db.relationship(
        "Notificacao", back_populates="pedido", cascade="all, delete-orphan",
        order_by="Notificacao.id",
    )
    itens = db.relationship(
        "PedidoItem", back_populates="pedido", cascade="all, delete-orphan", order_by="PedidoItem.id"
    )

    @property
    def ativo(self) -> bool:
        return self.status in STATUS_ATIVOS

    @property
    def descricao_local(self) -> str:
        """Onde o pedido vai: usado pela cozinha e pela página do cliente."""
        if self.tipo == TIPO_MESA:
            return f"Mesa {self.mesa:02d}" if self.mesa else "Mesa"
        if self.tipo == TIPO_RETIRADA:
            return "Retirada no local"
        partes = [self.endereco or "Entrega"]
        if self.bairro_nome:
            partes.append(self.bairro_nome)
        return " — ".join(partes)

    @property
    def minutos_sem_consumo(self) -> int:
        """Há quanto tempo ninguém pede nada nesta comanda."""
        referencia = self.ultimo_consumo_em or self.created_at
        if referencia is None:
            return 0
        return int((datetime.now() - referencia).total_seconds() // 60)

    @property
    def estado_no_salao(self) -> str:
        """Como esta comanda aparece no mapa de mesas.

        A ordem das perguntas é a ordem de urgência de quem está atendendo:
        quem pediu a conta espera alguém AGORA, e isso vale mais do que o
        tempo parado.
        """
        if self.conta_pedida_em is not None:
            return "conta"
        if self.minutos_sem_consumo >= MINUTOS_PARA_OCIOSA:
            return "ociosa"
        return "consumo"

    @property
    def rastreavel(self) -> bool:
        """Vale mostrar o mapa? Só a caminho, e só com posição recente.

        Posição velha é pior do que nenhuma: o cliente olha um ponto parado e
        conclui que o entregador empacou, quando na verdade foi o celular que
        parou de contar.
        """
        from datetime import timedelta

        if self.status != STATUS_SAIU_ENTREGA:
            return False
        if self.entrega_lat is None or self.entrega_atualizado_em is None:
            return False
        return datetime.now() - self.entrega_atualizado_em < timedelta(minutes=5)

    # Acima disto o ponto não é localização, é palpite: leitura de IP ou de
    # torre de celular cai nessa faixa, e mandar alguém a um raio de meio
    # quilômetro é mandá-lo à rua errada. O número é generoso de propósito —
    # GPS de celular na rua fica entre 5 e 50 m, e num prédio pode passar de
    # 100 sem estar errado.
    PRECISAO_MAXIMA_M = 500.0

    @property
    def tem_local_do_cliente(self) -> bool:
        """Se há um ponto do cliente em que se possa confiar para traçar rota."""
        if self.cliente_lat is None or self.cliente_lng is None:
            return False
        precisao = self.cliente_local_precisao
        return precisao is None or precisao <= self.PRECISAO_MAXIMA_M

    def recalcular_total(self) -> None:
        """Soma os itens e aplica taxa e desconto.

        O total nunca vem do navegador — é sempre derivado dos itens gravados.
        """
        self.subtotal = round(sum(item.total for item in self.itens), 2)
        self.total = round(
            (self.subtotal or 0.0) + (self.taxa_entrega or 0.0) - (self.desconto or 0.0), 2
        )

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Pedido #{self.numero} tenant={self.tenant_id} {self.status}>"


class PedidoItem(db.Model):
    """Item de um pedido, com preço e nome congelados no momento da venda.

    O produto pode ser renomeado, ter preço alterado ou ser excluído depois; o
    pedido histórico precisa continuar mostrando o que o cliente comprou e pagou.
    """

    __tablename__ = "pedido_item"

    id = db.Column(db.Integer, primary_key=True)
    pedido_id = db.Column(db.Integer, db.ForeignKey("pedido.id", ondelete="CASCADE"), nullable=False, index=True)
    # SET NULL: excluir um produto do cardápio não pode apagar histórico de venda.
    produto_id = db.Column(db.Integer, db.ForeignKey("produto.id", ondelete="SET NULL"), index=True)

    nome = db.Column(db.String(100), nullable=False)
    preco_base = db.Column(db.Float, nullable=False)
    # Preço unitário já somando os adicionais escolhidos.
    preco_unitario = db.Column(db.Float, nullable=False)
    quantidade = db.Column(db.Integer, default=1, nullable=False)
    total = db.Column(db.Float, nullable=False)
    observacao = db.Column(db.String(180))

    pedido = db.relationship("Pedido", back_populates="itens")
    adicionais = db.relationship(
        "PedidoItemAdicional", back_populates="item", cascade="all, delete-orphan", order_by="PedidoItemAdicional.nome"
    )

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<PedidoItem {self.quantidade}x {self.nome!r}>"


class PedidoItemAdicional(db.Model):
    """Adicional escolhido num item, também com nome e preço congelados."""

    __tablename__ = "pedido_item_adicional"

    id = db.Column(db.Integer, primary_key=True)
    pedido_item_id = db.Column(
        db.Integer, db.ForeignKey("pedido_item.id", ondelete="CASCADE"), nullable=False, index=True
    )
    adicional_id = db.Column(db.Integer, db.ForeignKey("adicional.id", ondelete="SET NULL"), index=True)
    nome = db.Column(db.String(60), nullable=False)
    preco = db.Column(db.Float, default=0.0, nullable=False)

    item = db.relationship("PedidoItem", back_populates="adicionais")


def agora() -> datetime:
    return datetime.now()
