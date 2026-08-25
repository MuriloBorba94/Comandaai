"""Receber e listar os contatos deixados na página do produto.

A validação aqui é deliberadamente frouxa em tudo menos no essencial. Um
formulário de vendas que recusa o contato porque o e-mail tem um formato
estranho perde a venda para provar um ponto — e o retorno vai acontecer pelo
WhatsApp de qualquer jeito. Por isso só nome e telefone são obrigatórios, e o
e-mail entra como veio.

O que NÃO é frouxo: tamanho de campo (banco não é caixa de texto infinita) e
repetição (o mesmo telefone mandando dez vezes em cinco minutos é dedo nervoso
ou robô, e nos dois casos uma linha basta).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..extensions import db
from ..models.interesse import SITUACAO_NOVO, SITUACOES, Interesse

# Janela em que o mesmo telefone não gera contato novo. Curta o bastante para
# não engolir quem voltou no dia seguinte com outra dúvida.
JANELA_REPETICAO = timedelta(minutes=30)

MAX_NOME = 120
MAX_TELEFONE = 30
MAX_EMAIL = 160
MAX_MENSAGEM = 1000


def _texto(valor, limite: int) -> str:
    return (str(valor or "").strip())[:limite]


def _so_digitos(telefone: str) -> str:
    return "".join(c for c in telefone if c.isdigit())


def registrar(dados: dict, *, ip: str | None = None) -> Interesse:
    """Grava um contato da página do produto.

    Devolve o registro — o existente, quando é repetição recente, para a tela
    poder agradecer do mesmo jeito. Dizer "você já mandou" a quem clicou duas
    vezes só ensina que o site está quebrado.
    """
    nome = _texto(dados.get("nome"), MAX_NOME)
    telefone = _texto(dados.get("telefone"), MAX_TELEFONE)

    if not nome:
        raise ValueError("Informe o seu nome.")
    if len(_so_digitos(telefone)) < 10:
        raise ValueError("Informe um telefone com DDD.")

    recente = (
        Interesse.query.filter(
            Interesse.telefone == telefone,
            Interesse.criado_em >= datetime.now() - JANELA_REPETICAO,
        )
        .order_by(Interesse.criado_em.desc())
        .first()
    )
    if recente is not None:
        return recente

    contato = Interesse(
        nome=nome,
        telefone=telefone,
        email=_texto(dados.get("email"), MAX_EMAIL) or None,
        mensagem=_texto(dados.get("mensagem"), MAX_MENSAGEM) or None,
        plano=_texto(dados.get("plano"), 80) or None,
        ip=(ip or "")[:45] or None,
        situacao=SITUACAO_NOVO,
    )
    db.session.add(contato)
    db.session.commit()
    return contato


def listar(situacao: str | None = None, limite: int = 200) -> list[Interesse]:
    consulta = Interesse.query
    if situacao in SITUACOES:
        consulta = consulta.filter_by(situacao=situacao)
    return consulta.order_by(Interesse.criado_em.desc()).limit(limite).all()


def quantos_novos() -> int:
    """Para o contador do menu da plataforma — a razão de a tela existir é não
    deixar contato parado sem ninguém ver."""
    return Interesse.query.filter_by(situacao=SITUACAO_NOVO).count()


def atualizar(contato: Interesse, *, situacao: str | None = None, anotacao=None) -> Interesse:
    if situacao is not None:
        if situacao not in SITUACOES:
            raise ValueError("Situação inválida.")
        contato.situacao = situacao
    if anotacao is not None:
        contato.anotacao = _texto(anotacao, MAX_MENSAGEM) or None
    db.session.commit()
    return contato
