from __future__ import annotations

import re

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..decorators import platform_admin_required
from ..extensions import db, limiter
from ..models.platform_admin import PlatformAdmin
from ..models.tenant import STATUSES, Tenant
from ..models.usuario import Usuario
from .auth import login_falhou

platform_bp = Blueprint("platform", __name__, url_prefix="/plataforma")

PLANOS = ("trial", "starter", "pro")

# Slugs que não podem ser usados por tenant porque colidiriam com endereços da
# própria plataforma ou com convenções de host.
SLUGS_RESERVADOS = {"www", "api", "admin", "app", "static", "mail", "ftp"}

# Subdomínio válido: minúsculas, dígitos e hífen, começando e terminando em
# alfanumérico. Um slug com ponto quebraria a identificação do tenant (o host
# é fatiado no primeiro ponto), e um com maiúsculas nunca casaria com o host.
PADRAO_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?$")

SENHA_MINIMA = 6


def _validar_slug(slug: str, tenant_id: int | None = None) -> str | None:
    """Devolve a mensagem de erro do slug, ou None se estiver válido."""
    if not slug:
        return "Informe o slug (o subdomínio do tenant)."
    if not PADRAO_SLUG.match(slug):
        return (
            "Slug inválido. Use apenas letras minúsculas, números e hífen, "
            "começando e terminando com letra ou número (ex.: pizzaria-joao)."
        )

    # O primeiro rótulo do hostname da plataforma nunca pode virar tenant: o
    # host da plataforma tem precedência e o tenant ficaria inacessível.
    rotulo_plataforma = (current_app.config.get("PLATFORM_HOSTNAME") or "").split(".")[0]
    if slug in SLUGS_RESERVADOS or slug == rotulo_plataforma:
        return f"O slug '{slug}' é reservado. Escolha outro."

    existente = Tenant.query.filter_by(slug=slug).first()
    if existente is not None and existente.id != tenant_id:
        return f"Já existe um tenant com o slug '{slug}'."
    return None


def _senha_fraca(senha: str) -> str | None:
    """Mensagem de alerta para senha fraca, ou None. Alerta, não impede."""
    avisos = []
    if len(senha) < 8:
        avisos.append("tem menos de 8 caracteres")
    if senha.isdigit():
        avisos.append("é só de números")
    return " e ".join(avisos) if avisos else None


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

        erro = _validar_slug(slug)
        if erro is None:
            if not nome_fantasia or not email_contato or not admin_username or not admin_password:
                erro = "Preencha todos os campos obrigatórios."
            elif len(admin_password) < SENHA_MINIMA:
                erro = f"A senha do admin precisa ter ao menos {SENHA_MINIMA} caracteres."
            elif plano not in PLANOS:
                erro = "Plano inválido."

        if erro:
            flash(erro, "erro")
            return render_template("platform/tenant_form.html", form=request.form, planos=PLANOS)

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

        aviso = _senha_fraca(admin_password)
        if aviso:
            flash(f"Atenção: a senha definida {aviso}.", "erro")
        flash(f"Tenant '{nome_fantasia}' criado.", "sucesso")
        return redirect(url_for("platform.tenants_list"))

    return render_template("platform/tenant_form.html", form={}, planos=PLANOS)


@platform_bp.route("/tenants/<int:tenant_id>/editar", methods=["GET", "POST"])
@platform_admin_required
def tenant_editar(tenant_id: int):
    tenant = db.session.get(Tenant, tenant_id)
    if tenant is None:
        flash("Tenant não encontrado.", "erro")
        return redirect(url_for("platform.tenants_list"))

    if request.method == "POST":
        slug = request.form.get("slug", "").strip().lower()
        nome_fantasia = request.form.get("nome_fantasia", "").strip()
        email_contato = request.form.get("email_contato", "").strip()
        plano = request.form.get("plano", "").strip()
        status = request.form.get("status", "").strip()

        erro = _validar_slug(slug, tenant_id=tenant.id)
        if erro is None:
            if not nome_fantasia or not email_contato:
                erro = "Nome fantasia e e-mail de contato são obrigatórios."
            elif plano not in PLANOS:
                erro = "Plano inválido."
            elif status not in STATUSES:
                erro = "Status inválido."

        if erro:
            flash(erro, "erro")
            return redirect(url_for("platform.tenant_editar", tenant_id=tenant.id))

        slug_antigo = tenant.slug
        tenant.slug = slug
        tenant.nome_fantasia = nome_fantasia
        tenant.razao_social = request.form.get("razao_social", "").strip() or None
        tenant.cnpj = request.form.get("cnpj", "").strip() or None
        tenant.email_contato = email_contato
        tenant.telefone_contato = request.form.get("telefone_contato", "").strip() or None
        tenant.plano = plano
        tenant.status = status
        tenant.ativo = request.form.get("ativo") == "on"
        db.session.commit()

        if slug_antigo != slug:
            current_app.logger.info("Slug de tenant alterado: %s -> %s", slug_antigo, slug)
            flash(
                f"O endereço mudou de '{slug_antigo}' para '{slug}'. "
                "Avise o cliente: o link antigo deixa de funcionar.",
                "erro",
            )
        flash("Tenant atualizado.", "sucesso")
        return redirect(url_for("platform.tenant_editar", tenant_id=tenant.id))

    return render_template(
        "platform/tenant_editar.html",
        tenant=tenant,
        usuarios=sorted(tenant.usuarios, key=lambda u: u.username),
        planos=PLANOS,
        statuses=STATUSES,
    )


@platform_bp.route("/tenants/<int:tenant_id>/usuarios/<int:usuario_id>/senha", methods=["POST"])
@platform_admin_required
def tenant_usuario_senha(tenant_id: int, usuario_id: int):
    """Define nova senha de um usuário de tenant, sem pedir a senha antiga.

    O super-admin da plataforma é quem atende o cliente que perdeu o acesso, e a
    senha antiga é irrecuperável (fica só como hash). Exigi-la aqui tornaria a
    tela inútil justamente no caso em que ela é necessária.

    O usuário é buscado com tenant_id no filtro: dois tenants podem ter um
    usuário 'admin', e resetar o do restaurante errado seria um estrago
    silencioso.
    """
    tenant = db.session.get(Tenant, tenant_id)
    if tenant is None:
        flash("Tenant não encontrado.", "erro")
        return redirect(url_for("platform.tenants_list"))

    usuario = Usuario.query.filter_by(id=usuario_id, tenant_id=tenant.id).first()
    if usuario is None:
        flash("Usuário não encontrado neste tenant.", "erro")
        return redirect(url_for("platform.tenant_editar", tenant_id=tenant.id))

    nova = request.form.get("nova_senha", "")
    repetida = request.form.get("repetir_senha", "")

    if len(nova) < SENHA_MINIMA:
        flash(f"A senha precisa ter ao menos {SENHA_MINIMA} caracteres.", "erro")
    elif nova != repetida:
        # Sem isso, um erro de digitação trancaria o cliente fora do sistema.
        flash("As duas senhas não são iguais. Nada foi alterado.", "erro")
    else:
        usuario.set_password(nova)
        db.session.commit()
        current_app.logger.info(
            "Senha redefinida pela plataforma: tenant=%s usuario=%s por admin_id=%s",
            tenant.slug,
            usuario.username,
            session.get("platform_admin_id"),
        )
        aviso = _senha_fraca(nova)
        if aviso:
            flash(f"Atenção: a senha definida {aviso}.", "erro")
        flash(f"Senha de '{usuario.username}' redefinida.", "sucesso")

    return redirect(url_for("platform.tenant_editar", tenant_id=tenant.id))
