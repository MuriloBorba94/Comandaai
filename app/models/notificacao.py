"""Avisos de WhatsApp que o restaurante manda ao cliente sobre o pedido.

Uma notificação é o TEXTO CONGELADO de um aviso e o registro do que aconteceu
com ele. O texto é montado quando o aviso nasce, e não na hora de enviar, pelo
mesmo motivo da comanda de impressão: se o pedido mudar entre uma coisa e outra,
o cliente precisa receber o que valia quando o aviso foi disparado.

Dois modos de entrega convivem, e a diferença entre eles não é técnica, é de
quem aperta o botão:

- **link** — o sistema prepara a mensagem e alguém do restaurante clica para
  abrir o WhatsApp com o texto pronto. Não custa nada, funciona no primeiro dia
  e não depende de conta em lugar nenhum.
- **meta** — a API oficial envia sozinha. Custa por mensagem e exige conta de
  negócios verificada.

O modo não-oficial do sistema antigo (Baileys, automação do WhatsApp Web) ficou
de fora de propósito: ele funciona conectando-se ao WhatsApp pessoal do número,
o que viola os termos e faz o número ser banido. Num sistema de um restaurante
só, o dono assume esse risco por conta própria; numa plataforma com vários
restaurantes, um banimento derruba o atendimento de quem não escolheu nada.
"""

from __future__ import annotations

from datetime import datetime

from ..extensions import db

STATUS_PENDENTE = "pendente"
# Preparada e esperando uma pessoa clicar (modo link).
STATUS_AGUARDANDO_ENVIO = "aguardando_envio"
STATUS_ENVIADA = "enviada"
STATUS_ERRO = "erro"
# O pedido mudou de estado antes de o aviso sair, e ele não faz mais sentido.
STATUS_CANCELADA = "cancelada"

STATUS_TODOS = (
    STATUS_PENDENTE,
    STATUS_AGUARDANDO_ENVIO,
    STATUS_ENVIADA,
    STATUS_ERRO,
    STATUS_CANCELADA,
)

ROTULO_DO_STATUS = {
    STATUS_PENDENTE: "Na fila",
    STATUS_AGUARDANDO_ENVIO: "Esperando você enviar",
    STATUS_ENVIADA: "Enviada",
    STATUS_ERRO: "Falhou",
    STATUS_CANCELADA: "Cancelada",
}

# Depois de tantas falhas seguidas o aviso para de ser tentado. Sem teto, um
# número inválido faria o disparador girar para sempre no mesmo cliente.
MAX_TENTATIVAS = 5

# Eventos que geram aviso. O slug é o que liga a mudança de status ao texto.
EVENTO_CONFIRMADO = "confirmado"
EVENTO_EM_PREPARO = "em_preparo"
EVENTO_PRONTO = "pronto"
EVENTO_SAIU_ENTREGA = "saiu_entrega"
EVENTO_ENTREGUE = "entregue"
EVENTO_CANCELADO = "cancelado"

EVENTOS = (
    EVENTO_CONFIRMADO,
    EVENTO_EM_PREPARO,
    EVENTO_PRONTO,
    EVENTO_SAIU_ENTREGA,
    EVENTO_ENTREGUE,
    EVENTO_CANCELADO,
)

ROTULO_DO_EVENTO = {
    EVENTO_CONFIRMADO: "Pedido confirmado",
    EVENTO_EM_PREPARO: "Em preparo",
    EVENTO_PRONTO: "Pronto",
    EVENTO_SAIU_ENTREGA: "Saiu para entrega",
    EVENTO_ENTREGUE: "Entregue",
    EVENTO_CANCELADO: "Cancelado",
}

# Estes dois valem o clique em qualquer modo:
#
# - confirmado, porque tira o cliente da dúvida de "será que viram meu pedido?";
# - cancelado, porque cancelar o jantar de alguém sem avisar é pior do que
#   qualquer trabalho a mais no balcão — e cancelamento é raro, então não vira
#   rotina de cliques.
#
# As etapas do meio (em preparo, pronto, saiu para entrega) só são disparadas
# com envio automático: no modo link seriam três cliques por pedido, e o cliente
# já acompanha tudo pela página do pedido.
EVENTOS_SEMPRE = (EVENTO_CONFIRMADO, EVENTO_CANCELADO)


class Notificacao(db.Model):
    __tablename__ = "notificacao"

    __table_args__ = (
        # Um aviso por evento por pedido. É o que impede o cliente de receber
        # "seu pedido saiu para entrega" duas vezes quando alguém clica de novo.
        db.UniqueConstraint("pedido_id", "evento", name="uq_notificacao_pedido_evento"),
    )

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pedido_id = db.Column(
        db.Integer, db.ForeignKey("pedido.id", ondelete="CASCADE"), nullable=False, index=True
    )

    evento = db.Column(db.String(30), nullable=False)
    telefone = db.Column(db.String(20), nullable=False)
    mensagem = db.Column(db.Text, nullable=False)

    status = db.Column(db.String(20), default=STATUS_PENDENTE, nullable=False, index=True)
    provedor = db.Column(db.String(20))
    tentativas = db.Column(db.Integer, default=0, nullable=False)
    erro = db.Column(db.String(700))

    # Identificador da mensagem no provedor, quando ele devolve um.
    id_externo = db.Column(db.String(120))
    enviada_em = db.Column(db.DateTime)
    # Quem clicou, no modo link. Sem isso não há a quem perguntar quando o
    # cliente diz que nunca recebeu.
    enviada_por = db.Column(db.String(80))

    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)

    pedido = db.relationship("Pedido", back_populates="notificacoes")
    tenant = db.relationship("Tenant")

    @property
    def rotulo(self) -> str:
        return ROTULO_DO_STATUS.get(self.status, self.status)

    @property
    def rotulo_do_evento(self) -> str:
        return ROTULO_DO_EVENTO.get(self.evento, self.evento)

    @property
    def pendente(self) -> bool:
        return self.status in (STATUS_PENDENTE, STATUS_AGUARDANDO_ENVIO, STATUS_ERRO)

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Notificacao pedido={self.pedido_id} {self.evento} {self.status}>"
