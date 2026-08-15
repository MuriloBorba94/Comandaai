"""Tempo de vida da sessão de quem opera o sistema.

São duas garantias diferentes, e é importante não confundi-las:

1. **Fechou o navegador, acabou a sessão.** Isso depende do cookie NÃO ter data
   de validade — um "cookie de sessão", que o navegador descarta ao fechar. É o
   que `session.permanent = False` faz, e é o padrão do Flask; aqui a escolha
   fica explícita para ninguém ligar `permanent` sem perceber o efeito.

   Limite honesto: navegador configurado para "continuar de onde parou" (Chrome,
   Edge) restaura cookies de sessão ao reabrir. Nenhuma configuração no servidor
   impede isso — é decisão do navegador do usuário.

2. **Parou de mexer, acabou a sessão.** Essa o servidor garante sozinho, e é a
   que cobre o caso acima: a sessão guarda o instante do último acesso e, passado
   o limite, é descartada aqui, antes de qualquer rota rodar. Também resolve o
   risco real do balcão — o painel esquecido aberto num computador compartilhado.

O carimbo só é regravado de minuto em minuto. Sem isso, o painel da cozinha
(que consulta o servidor a cada 8 segundos) reescreveria e reassinaria o cookie
umas 450 vezes por hora, à toa.
"""

from __future__ import annotations

import time

from flask import session

# Chaves que indicam sessão autenticada. Visitante da vitrine tem sessão também
# (o carrinho mora nela) e não pode ser expulso por ficar lendo o cardápio.
CHAVES_DE_LOGIN = ("logged_in", "platform_admin_id")

CARIMBO = "visto_em"
INTERVALO_DE_REGRAVACAO = 60  # segundos


def _esta_logado() -> bool:
    return any(session.get(chave) for chave in CHAVES_DE_LOGIN)


def registrar(app) -> None:
    limite = max(60, int(app.config.get("SESSION_IDLE_MINUTES", 240)) * 60)

    @app.before_request
    def controlar_validade():
        # Cookie sem validade: morre quando o navegador fecha. Só escreve
        # quando alguém tiver ligado `permanent` — atribuir sempre marcaria a
        # sessão como modificada e faria o servidor mandar Set-Cookie até para
        # quem só abriu o cardápio.
        if session.permanent:
            session.permanent = False

        if not _esta_logado():
            return

        agora = time.time()
        visto = session.get(CARIMBO)

        if visto is not None and agora - float(visto) > limite:
            # Descarta a sessão inteira, e não só as chaves de login: sobrar
            # meia sessão é como um erro de autorização costuma nascer.
            session.clear()
            return

        if visto is None or agora - float(visto) > INTERVALO_DE_REGRAVACAO:
            session[CARIMBO] = agora


def marcar_acesso() -> None:
    """Carimba a sessão no momento do login, para o relógio começar a contar."""
    session[CARIMBO] = time.time()
