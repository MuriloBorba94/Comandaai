from __future__ import annotations

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from ..models.usuario import Usuario

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
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
        flash("Usuário ou senha inválidos.", "erro")

    return render_template("auth/login.html", tenant=g.tenant)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
