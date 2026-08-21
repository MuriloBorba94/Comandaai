from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..extensions import limiter
from ..models.usuario import Usuario
from ..sessao import marcar_acesso

auth_bp = Blueprint("auth", __name__)


def login_falhou(response) -> bool:
    """Diz ao limiter se esta tentativa deve consumir cota.

    Login bem-sucedido redireciona (302); falha re-renderiza o formulário (200).
    Assim só as falhas contam, e quem acerta a senha nunca é bloqueado.
    """
    return response.status_code != 302


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(
    lambda: current_app.config["LOGIN_RATELIMIT"],
    methods=["POST"],
    deduct_when=login_falhou,
)
def login():
    if g.tenant is None:
        abort(404)

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        usuario = Usuario.query.filter_by(tenant_id=g.tenant.id, username=username, ativo=True).first()
        if usuario and usuario.check_password(password):
            # session.clear() antes de gravar: sem isso, um carrinho da vitrine
            # (ou uma sessão anterior) sobreviveria dentro da sessão logada.
            session.clear()
            session["logged_in"] = True
            session["usuario_id"] = usuario.id
            # `username` alimenta o chip do painel e, mais importante, vira o
            # autor das movimentações de estoque e dos itens lançados na
            # comanda. Faltava aqui, então todo o histórico saía sem dono.
            session["username"] = usuario.username
            session["nome"] = usuario.nome
            session["tenant_id"] = g.tenant.id
            session["role"] = usuario.role
            marcar_acesso()
            current_app.logger.info(
                "Login: tenant=%s username=%r", g.tenant.slug, usuario.username
            )
            from ..services.auditoria import registrar
            from ..models.auditoria import ACAO_LOGIN

            registrar(ACAO_LOGIN, tenant=g.tenant, alvo=usuario.username)
            return redirect(url_for("admin.dashboard"))
        current_app.logger.warning(
            "Login de tenant falhou: tenant=%s username=%r ip=%s",
            g.tenant.slug,
            username,
            request.remote_addr,
        )
        from ..models.auditoria import ACAO_LOGIN_FALHOU
        from ..services.auditoria import registrar

        # O usuário tentado entra como alvo, e não como ator: quem tentou é
        # desconhecido, e registrar o nome digitado como autor daria a
        # entender que aquela pessoa fez algo.
        registrar(
            ACAO_LOGIN_FALHOU,
            tenant=g.tenant,
            alvo=username[:120] or "(sem usuário)",
            ator="anônimo",
        )
        flash("Usuário ou senha inválidos.", "erro")

    return render_template("auth/login.html", tenant=g.tenant)


@auth_bp.route("/suporte/<token>")
def suporte_entrar(token: str):
    """Troca o passe da plataforma por uma sessão neste restaurante.

    Sem `@login_required`: quem chega aqui ainda não tem sessão — o passe É a
    credencial. Ele vale 2 minutos, serve uma vez só e é preso a este
    restaurante (ver app/models/suporte.py).
    """
    from ..services.suporte import PasseInvalido, consumir

    if g.tenant is None:
        abort(404)

    try:
        consumir(g.tenant, token)
    except PasseInvalido as exc:
        current_app.logger.warning(
            "Passe de suporte recusado: tenant=%s motivo=%s ip=%s",
            g.tenant.slug,
            exc,
            request.remote_addr,
        )
        flash(str(exc), "erro")
        return redirect(url_for("auth.login"))

    flash(f"Modo suporte em {g.tenant.nome_fantasia}. Tudo o que você fizer fica registrado.", "sucesso")
    return redirect(url_for("admin.dashboard"))


@auth_bp.route("/suporte/sair", methods=["POST"])
def suporte_sair():
    from ..services.suporte import encerrar

    encerrar()
    flash("Você saiu do modo suporte.", "sucesso")
    return redirect(url_for("auth.login"))


@auth_bp.route("/logout")
def logout():
    from ..services.suporte import encerrar, em_suporte

    # Sair pelo botão comum, estando em suporte, precisa registrar a saída do
    # mesmo jeito: senão o diário mostra uma entrada sem fim correspondente.
    if em_suporte():
        encerrar()
    session.clear()
    return redirect(url_for("auth.login"))
