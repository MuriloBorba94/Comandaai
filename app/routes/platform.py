from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from ..decorators import platform_admin_required
from ..extensions import db, limiter
from ..models.platform_admin import PlatformAdmin
from ..models.tenant import Tenant
from ..models.usuario import Usuario
from .auth import login_falhou

platform_bp = Blueprint("platform", __name__, url_prefix="/plataforma")


@platform_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(
    lambda: current_app.config["LOGIN_RATELIMIT"],
    methods=["POST"],
    deduct_when=login_falhou,
)
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        admin = PlatformAdmin.query.filter_by(username=username, ativo=True).first()
        if admin and admin.check_password(password):
            session.clear()
            session["platform_admin_id"] = admin.id
            return redirect(url_for("platform.tenants_list"))
        current_app.logger.warning(
            "Login de super-admin falhou: username=%r ip=%s", username, request.remote_addr
        )
        flash("Usuário ou senha inválidos.", "erro")
    return render_template("platform/login.html")


@platform_bp.route("/logout")
def logout():
    session.pop("platform_admin_id", None)
    return redirect(url_for("platform.login"))


@platform_bp.route("/tenants")
@platform_admin_required
def tenants_list():
    tenants = Tenant.query.order_by(Tenant.nome_fantasia).all()
    return render_template("platform/tenants_list.html", tenants=tenants)


@platform_bp.route("/tenants/novo", methods=["GET", "POST"])
@platform_admin_required
def tenant_new():
    if request.method == "POST":
        slug = request.form.get("slug", "").strip().lower()
        nome_fantasia = request.form.get("nome_fantasia", "").strip()
        email_contato = request.form.get("email_contato", "").strip()
        plano = request.form.get("plano", "trial").strip()
        admin_username = request.form.get("admin_username", "").strip()
        admin_password = request.form.get("admin_password", "")

        erro = None
        if not slug or not nome_fantasia or not email_contato or not admin_username or not admin_password:
            erro = "Preencha todos os campos obrigatórios."
        elif Tenant.query.filter_by(slug=slug).first():
            erro = "Já existe um tenant com esse slug."

        if erro:
            flash(erro, "erro")
            return render_template("platform/tenant_form.html", form=request.form)

        tenant = Tenant(
            slug=slug,
            nome_fantasia=nome_fantasia,
            email_contato=email_contato,
            plano=plano,
            status="trial",
        )
        db.session.add(tenant)
        db.session.flush()

        admin_user = Usuario(
            tenant_id=tenant.id,
            nome=f"Admin {nome_fantasia}",
            username=admin_username,
            role="admin",
        )
        admin_user.set_password(admin_password)
        db.session.add(admin_user)
        db.session.commit()

        flash(f"Tenant '{nome_fantasia}' criado.", "sucesso")
        return redirect(url_for("platform.tenants_list"))

    return render_template("platform/tenant_form.html", form={})
