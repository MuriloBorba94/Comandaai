"""Impressão na cozinha: o agente pareado e a fila de comandas.

Duas diferenças em relação ao sistema original, ambas obrigatórias aqui:

1. **Não existe modo local.** No Borba's Burguer o Flask rodava na mesma máquina
   da impressora, então dava para mandar o papel direto (`win32print`). Aqui o
   servidor está num datacenter e a impressora está no balcão do restaurante —
   a única forma é o agente do estabelecimento buscar o trabalho. Por isso não
   há campo "modo": impressão está ligada quando existe um agente pareado.

2. **A comanda é uma fila própria, não um campo do pedido.** Lá o estado da
   impressão morava no `Pedido` (`print_status`, `print_revision`), o que só
   permite uma impressão por pedido — reimprimir era incrementar a revisão e
   mandar o pedido inteiro de novo. Numa comanda de mesa isso é errado: quando
   a mesa pede mais uma porção, a cozinha precisa receber SÓ o que entrou, não
   as três páginas do que já foi feito. Com uma fila, cada trabalho congela o
   texto que deve sair no papel no momento em que foi criado.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..extensions import db
from .mixins import TimestampMixin

# Quanto tempo sem dar sinal até o agente ser considerado offline. O agente
# consulta o servidor a cada ~3 segundos, então 45s significa "errou umas dez
# tentativas seguidas" — folga suficiente para não piscar "offline" por causa
# de um Wi-Fi instável.
SEGUNDOS_PARA_OFFLINE = 45

# Um trabalho reservado que não recebe confirmação volta para a fila depois
# disto. É o que resolve o caso "o computador do balcão foi desligado no meio
# da impressão": sem isso a comanda ficaria presa em "imprimindo" para sempre.
SEGUNDOS_PARA_LIBERAR_RESERVA = 120

# Depois de tantas falhas seguidas o trabalho para de ser oferecido. Sem teto,
# uma impressora sem papel faria o agente girar para sempre no mesmo pedido e
# nenhum pedido novo sairia.
MAX_TENTATIVAS = 5

STATUS_PENDENTE = "pendente"
STATUS_IMPRIMINDO = "imprimindo"
STATUS_IMPRESSO = "impresso"
STATUS_ERRO = "erro"
STATUS_CANCELADO = "cancelado"

TIPO_COMANDA = "comanda"
TIPO_ADICAO = "adicao"
TIPO_FECHAMENTO = "fechamento"
TIPO_TESTE = "teste"

ROTULO_DO_TIPO = {
    TIPO_COMANDA: "Comanda",
    TIPO_ADICAO: "Itens adicionais",
    TIPO_FECHAMENTO: "Conferência de consumo",
    TIPO_TESTE: "Teste de impressão",
}


class AgenteImpressao(TimestampMixin, db.Model):
    """O programinha que roda no computador do restaurante.

    Um por tenant. O pareamento é um token gerado no painel: o servidor guarda
    só o hash (como senha), e o texto do token aparece uma única vez na tela.
    """

    __tablename__ = "agente_impressao"
    __table_args__ = (db.UniqueConstraint("tenant_id", name="uq_agente_tenant"),)

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )

    token_hash = db.Column(db.String(64), nullable=False, index=True)

    # Preenchidos pelo próprio agente a cada consulta: servem para o dono do
    # restaurante conferir na tela que é a máquina e a impressora certas.
    nome = db.Column(db.String(100), default="", nullable=False)
    impressora = db.Column(db.String(255), default="", nullable=False)
    versao = db.Column(db.String(20), default="", nullable=False)
    ultimo_contato = db.Column(db.DateTime)

    tenant = db.relationship("Tenant")

    @property
    def segundos_desde_contato(self) -> int | None:
        if not self.ultimo_contato:
            return None
        return max(0, int((datetime.now() - self.ultimo_contato).total_seconds()))

    @property
    def online(self) -> bool:
        segundos = self.segundos_desde_contato
        return segundos is not None and segundos <= SEGUNDOS_PARA_OFFLINE


class ImpressaoJob(db.Model):
    """Um papel a ser impresso.

    `conteudo` é o texto final, congelado na hora em que o trabalho entrou na
    fila. Renderizar só na hora da entrega pareceria mais simples, mas faria a
    comanda de "itens adicionais" sair com o pedido inteiro caso o agente
    estivesse desligado quando a mesa pediu mais.
    """

    __tablename__ = "impressao_job"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SET NULL, não CASCADE: um trabalho já impresso é registro do que saiu no
    # papel e não deve sumir se o pedido for removido.
    pedido_id = db.Column(db.Integer, db.ForeignKey("pedido.id", ondelete="SET NULL"), index=True)

    tipo = db.Column(db.String(20), default=TIPO_COMANDA, nullable=False)
    titulo = db.Column(db.String(80), default="", nullable=False)
    conteudo = db.Column(db.Text, nullable=False)

    status = db.Column(db.String(20), default=STATUS_PENDENTE, nullable=False, index=True)
    tentativas = db.Column(db.Integer, default=0, nullable=False)
    erro = db.Column(db.String(500))

    claim_token = db.Column(db.String(64))
    reservado_em = db.Column(db.DateTime)
    impresso_em = db.Column(db.DateTime)

    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)

    pedido = db.relationship("Pedido")

    @property
    def rotulo(self) -> str:
        return ROTULO_DO_TIPO.get(self.tipo, self.tipo)

    @property
    def reserva_expirada(self) -> bool:
        if self.status != STATUS_IMPRIMINDO or not self.reservado_em:
            return False
        limite = datetime.now() - timedelta(seconds=SEGUNDOS_PARA_LIBERAR_RESERVA)
        return self.reservado_em < limite

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<ImpressaoJob #{self.id} {self.tipo} {self.status}>"
