"""Registro do que foi feito, por quem e quando.

Um log de auditoria que grava tudo é um log que ninguém lê. Este grava pouco de
propósito: só o que alguém iria querer reconstituir meses depois, e que é sempre
uma destas três coisas —

- **dinheiro**: cobrança marcada como paga, pagamento confirmado, pedido
  cancelado depois de já ter valor;
- **acesso**: entrar como outro restaurante, criar ou remover restaurante,
  trocar plano, gerar código do agente de impressão;
- **configuração que muda para onde o dinheiro vai**: a chave PIX e as
  credenciais de WhatsApp.

O que NÃO entra: cadastro de produto, mudança de preço, movimento de estoque.
Não porque não importem, mas porque já têm registro próprio (o estoque tem
razão com autor) e porque encher o log de rotina é o jeito mais eficiente de
tornar inútil o registro do que é raro.

`tenant_id` é opcional: ação da plataforma (criar restaurante, marcar
mensalidade como paga) não pertence a nenhum restaurante.
"""

from __future__ import annotations

from datetime import datetime

from ..extensions import db

# Quem agiu.
ATOR_USUARIO = "usuario"
ATOR_PLATAFORMA = "plataforma"
ATOR_SISTEMA = "sistema"

# Ações registradas. Ficam nomeadas aqui para que a tela consiga traduzir e
# para que digitar errado o nome de uma ação não passe despercebido.
ACAO_LOGIN = "login"
ACAO_LOGIN_FALHOU = "login_falhou"
ACAO_PEDIDO_CANCELADO = "pedido_cancelado"
ACAO_PAGAMENTO_CONFIRMADO = "pagamento_confirmado"
ACAO_PAGAMENTO_CORRIGIDO = "pagamento_corrigido"
ACAO_CAIXA_ABERTO = "caixa_aberto"
ACAO_CAIXA_FECHADO = "caixa_fechado"
ACAO_PIX_ALTERADO = "pix_alterado"
ACAO_WHATSAPP_ALTERADO = "whatsapp_alterado"
ACAO_AGENTE_PAREADO = "agente_pareado"
ACAO_USUARIO_CRIADO = "usuario_criado"
ACAO_USUARIO_ALTERADO = "usuario_alterado"
ACAO_TENANT_CRIADO = "tenant_criado"
ACAO_TENANT_REMOVIDO = "tenant_removido"
ACAO_PLANO_ALTERADO = "plano_alterado"
ACAO_COBRANCA_PAGA = "cobranca_paga"
ACAO_IMPERSONACAO_INICIO = "impersonacao_inicio"
ACAO_IMPERSONACAO_FIM = "impersonacao_fim"

ROTULO_DA_ACAO = {
    ACAO_LOGIN: "Entrou no sistema",
    ACAO_LOGIN_FALHOU: "Tentativa de login recusada",
    ACAO_PEDIDO_CANCELADO: "Cancelou um pedido",
    ACAO_PAGAMENTO_CONFIRMADO: "Confirmou o recebimento de um PIX",
    ACAO_PAGAMENTO_CORRIGIDO: "Corrigiu a forma de pagamento",
    ACAO_CAIXA_ABERTO: "Abriu a loja e o caixa",
    ACAO_CAIXA_FECHADO: "Fechou a loja e conferiu o caixa",
    ACAO_PIX_ALTERADO: "Alterou a chave PIX",
    ACAO_WHATSAPP_ALTERADO: "Alterou o envio de WhatsApp",
    ACAO_AGENTE_PAREADO: "Gerou código do agente de impressão",
    ACAO_USUARIO_CRIADO: "Deu acesso a alguém",
    ACAO_USUARIO_ALTERADO: "Mudou o acesso de alguém",
    ACAO_TENANT_CRIADO: "Criou um restaurante",
    ACAO_TENANT_REMOVIDO: "Removeu um restaurante",
    ACAO_PLANO_ALTERADO: "Alterou o plano de um restaurante",
    ACAO_COBRANCA_PAGA: "Marcou uma mensalidade como paga",
    ACAO_IMPERSONACAO_INICIO: "Entrou como o restaurante",
    ACAO_IMPERSONACAO_FIM: "Saiu do modo suporte",
}


class Auditoria(db.Model):
    __tablename__ = "auditoria"

    id = db.Column(db.Integer, primary_key=True)
    # SET NULL, e não CASCADE: remover um restaurante não pode apagar o registro
    # de que ele foi removido, nem o do que aconteceu antes.
    tenant_id = db.Column(
        db.Integer, db.ForeignKey("tenant.id", ondelete="SET NULL"), index=True
    )
    # Guardado por texto, porque precisa sobreviver à remoção do restaurante.
    tenant_slug = db.Column(db.String(50), index=True)

    ator = db.Column(db.String(80), nullable=False)
    ator_tipo = db.Column(db.String(20), default=ATOR_USUARIO, nullable=False)

    acao = db.Column(db.String(40), nullable=False, index=True)
    # O que foi mexido: "Pedido #12", "Cobrança 08/2026". Texto pronto para ler,
    # e não um id que exige outra consulta para significar alguma coisa.
    alvo = db.Column(db.String(120))
    detalhes = db.Column(db.String(500))

    # De onde veio. Só faz sentido quando há proxy confiável configurado; sem
    # isso o Flask enxerga o IP do próprio proxy (ver TRUSTED_PROXIES).
    ip = db.Column(db.String(45))

    criado_em = db.Column(db.DateTime, default=datetime.now, nullable=False, index=True)

    tenant = db.relationship("Tenant")

    @property
    def rotulo(self) -> str:
        return ROTULO_DA_ACAO.get(self.acao, self.acao)

    def __repr__(self) -> str:  # pragma: no cover - conveniência de debug
        return f"<Auditoria {self.acao} por {self.ator}>"
