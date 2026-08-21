"""Entrar como um restaurante para dar suporte, e sair.

Este é o código mais delicado do sistema: é um jeito de alguém da plataforma
operar dentro da conta de um cliente. Ele não fica mais seguro por ser
restrito — fica mais seguro por ser **curto, visível e registrado**.

- **curto**: o passe vale 2 minutos e a sessão termina sozinha em 30;
- **visível**: uma faixa aparece em toda página enquanto durar, para ninguém
  esquecer onde está — nem quem entrou, nem quem estiver olhando a tela;
- **registrado**: entrar e sair vão para o diário, e tudo o que for feito
  dentro aparece com o nome de quem da plataforma entrou, não com o do dono do
  restaurante (ver `services/auditoria.py::_quem`).

Não há restrição de ação de propósito. Suporte que só enxerga e não conserta
não resolve o problema de ninguém, e uma lista de "o que o suporte não pode"
daria uma sensação de proteção que a primeira exceção derrubaria.
"""

from __future__ import annotations

import time
from datetime import datetime

from flask import session

from ..extensions import db
from ..models.auditoria import ACAO_IMPERSONACAO_FIM, ACAO_IMPERSONACAO_INICIO
from ..models.suporte import DURACAO_SESSAO_SEGUNDOS, PasseSuporte
from .auditoria import registrar

CHAVE_ADMIN = "impersonado_por"
CHAVE_ATE = "suporte_ate"


class PasseInvalido(RuntimeError):
    """O passe não serve: expirou, já foi usado, ou é de outro restaurante."""


def emitir(tenant, admin: str) -> str:
    """Gera o passe e registra que ele foi pedido.

    O registro acontece na EMISSÃO, e não só no uso: um passe pedido e não
    usado também é informação — alguém quis entrar na conta daquele cliente.
    """
    _, token = PasseSuporte.emitir(tenant, admin)
    registrar(
        ACAO_IMPERSONACAO_INICIO,
        tenant=tenant,
        alvo=tenant.nome_fantasia,
        detalhes=f"passe emitido por {admin}",
        ator=admin,
        ator_tipo="plataforma",
    )
    return token


def consumir(tenant, token: str) -> PasseSuporte:
    """Troca o passe por uma sessão de suporte neste restaurante."""
    passe = PasseSuporte.query.filter_by(token_hash=PasseSuporte.hash_de(token)).first()
    if passe is None:
        raise PasseInvalido("Este link de suporte não existe.")
    if passe.usado_em is not None:
        raise PasseInvalido("Este link de suporte já foi usado.")
    if datetime.now() > passe.expira_em:
        raise PasseInvalido("Este link de suporte expirou. Gere outro na plataforma.")
    if passe.tenant_id != tenant.id:
        # Passe de um restaurante apresentado no endereço de outro. Não é um
        # engano provável — é o que alguém tentaria de propósito.
        raise PasseInvalido("Este link de suporte não é deste restaurante.")

    passe.usado_em = datetime.now()
    db.session.commit()

    # session.clear() antes: sobrar meia sessão anterior é como um erro de
    # autorização costuma nascer.
    session.clear()
    session["logged_in"] = True
    session["tenant_id"] = tenant.id
    session["username"] = passe.admin
    session["nome"] = f"{passe.admin} (suporte)"
    # Papel de admin porque suporte precisa chegar nas telas onde o problema
    # está. O que protege não é limitar o alcance, é o rastro.
    session["role"] = "admin"
    session[CHAVE_ADMIN] = passe.admin
    session[CHAVE_ATE] = time.time() + DURACAO_SESSAO_SEGUNDOS

    from ..sessao import marcar_acesso

    marcar_acesso()
    return passe


def encerrar() -> str | None:
    """Sai do modo suporte. Devolve o nome de quem estava, se estava."""
    admin = session.get(CHAVE_ADMIN)
    if admin:
        from flask import g

        registrar(
            ACAO_IMPERSONACAO_FIM,
            tenant=g.get("tenant"),
            detalhes=f"sessão de suporte encerrada por {admin}",
            ator=admin,
            ator_tipo="plataforma",
        )
    session.clear()
    return admin


def em_suporte() -> bool:
    return bool(session.get(CHAVE_ADMIN))


def minutos_restantes() -> int:
    ate = session.get(CHAVE_ATE)
    if not ate:
        return 0
    return max(0, int((float(ate) - time.time()) / 60))


def registrar_expiracao(app) -> None:
    """Encerra a sessão de suporte quando o prazo dela acaba.

    Diferente da expiração comum, que conta inatividade: aqui o relógio não
    para enquanto a pessoa mexe. Alguém da plataforma dentro da conta de um
    cliente é uma situação que deve terminar sozinha, e não durar até que
    alguém lembre de sair.
    """

    @app.before_request
    def encerrar_suporte_vencido():
        if not session.get(CHAVE_ADMIN):
            return
        ate = session.get(CHAVE_ATE)
        if ate and time.time() > float(ate):
            session.clear()
