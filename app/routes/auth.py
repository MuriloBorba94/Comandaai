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
            session.clear()
            session["logged_in"] = True
            session["user_id"] = usuario.id
            session["tenant_id"] = g.tenant.id
            session["role"] = usuario.role
            return redirect(url_for("admin.dashboard"))
        current_app.logger.warning(
            "Login de tenant falhou: tenant=%s username=%r ip=%s",
            g.tenant.slug,
            username,
            request.remote_addr,
        )
        flash("Usuário ou senha inválidos.", "erro")

    return render_template("auth/login.html", tenant=g.tenant)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
