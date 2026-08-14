from __future__ import annotations

import re
from datetime import date, datetime, timedelta

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

from sqlalchemy import func

from ..decorators import platform_admin_required
from ..extensions import db, limiter
from ..models.assinatura import (
    COBRANCA_PAGA,
    COBRANCA_PENDENTE,
    RECURSOS,
    Cobranca,
    Plano,
)
from ..models.pedido import Pedido
from ..models.platform_admin import PlatformAdmin
from ..models.tenant import STATUSES, Tenant
from ..models.usuario import Usuario
from ..utils import para_float, para_int
from ..services.faturamento_saas import (
    cancelar_cobranca,
    executar_ciclo,
    gerar_cobranca,
    registrar_pagamento,
    resumo_do_tenant,
)
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


# Mesma conversão usada no admin do tenant, em app/utils.py.
_para_float = para_float
_para_int = para_int


def _planos_validos() -> list[str]:
    """Slugs de plano aceitos: o catálogo, se existir; senão os embutidos.

    Sem isso, criar um plano novo no catálogo faria a validação recusá-lo, porque
    a lista de planos estaria fixa no código.
    """
    do_catalogo = [p.slug for p in Plano.query.order_by(Plano.ordem, Plano.slug).all()]
    return do_catalogo or list(PLANOS)


def _para_data(valor: str | None) -> date | None:
    texto = (valor or "").strip()
    if not texto:
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        return None


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
            return redirect(url_for("platform.inicio"))
        current_app.logger.warning(
            "Login de super-admin falhou: username=%r ip=%s", username, request.remote_addr
        )
        flash("Usuário ou senha inválidos.", "erro")
    return render_template("platform/login.html")


@platform_bp.route("/logout")
def logout():
    session.pop("platform_admin_id", None)
    return redirect(url_for("platform.login"))


@platform_bp.route("/")
@platform_admin_required
def inicio():
    """Página inicial da plataforma: o estado do negócio numa tela.

    Responde as três perguntas de quem abre isso todo dia: quanto entra por mês,
    quem está devendo, e quem parou de usar o sistema.
    """
    hoje = date.today()
    inicio_do_mes = hoje.replace(day=1)
    tenants = Tenant.query.order_by(Tenant.nome_fantasia).all()
    precos = {plano.slug: plano.preco_mensal or 0.0 for plano in Plano.query.all()}

    # Receita recorrente: só quem já saiu do teste e tem plano com preço. Trial
    # não é receita, e contar como se fosse daria uma expectativa falsa.
    receita_recorrente = sum(
        precos.get(tenant.plano, 0.0)
        for tenant in tenants
        if tenant.ativo and tenant.status in ("active", "past_due")
    )

    por_status: dict[str, int] = {}
    for tenant in tenants:
        chave = tenant.status if tenant.ativo else "desativado"
        por_status[chave] = por_status.get(chave, 0) + 1

    abertas = (
        Cobranca.query.filter_by(status=COBRANCA_PENDENTE)
        .order_by(Cobranca.vencimento)
        .all()
    )
    vencidas = [c for c in abertas if c.dias_de_atraso(hoje) > 0]
    pagas_no_mes = [
        c
        for c in Cobranca.query.filter_by(status=COBRANCA_PAGA).all()
        if c.pago_em and c.pago_em.date() >= inicio_do_mes
    ]

    # Trials terminando: é a hora de cobrar ou de perder o cliente em silêncio.
    limite_trial = hoje + timedelta(days=7)
    trials_terminando = sorted(
        (
            tenant
            for tenant in tenants
            if tenant.trial_termina_em
            and tenant.status == "trial"
            and hoje <= tenant.trial_termina_em.date() <= limite_trial
        ),
        key=lambda t: t.trial_termina_em,
    )
    sem_prazo_de_trial = [
        tenant for tenant in tenants if tenant.trial_termina_em is None and tenant.ativo
    ]

    # Uso recente por tenant: quem não recebe pedido há uma semana é candidato a
    # cancelar, e é melhor descobrir antes de ele avisar.
    desde = datetime.now() - timedelta(days=7)
    pedidos_por_tenant = dict(
        db.session.query(Pedido.tenant_id, func.count(Pedido.id))
        .filter(Pedido.created_at >= desde)
        .group_by(Pedido.tenant_id)
        .all()
    )
    atividade = sorted(
        ((tenant, pedidos_por_tenant.get(tenant.id, 0)) for tenant in tenants),
        key=lambda par: (par[1], par[0].nome_fantasia.lower()),
    )

    return render_template(
        "platform/inicio.html",
        hoje=hoje,
        total_tenants=len(tenants),
        por_status=por_status,
        receita_recorrente=receita_recorrente,
        recebido_no_mes=sum((c.valor_pago or c.valor) for c in pagas_no_mes),
        qtd_pagas_no_mes=len(pagas_no_mes),
        total_em_aberto=sum(c.valor for c in abertas),
        qtd_em_aberto=len(abertas),
        vencidas=vencidas[:8],
        total_vencido=sum(c.valor for c in vencidas),
        trials_terminando=trials_terminando,
        sem_prazo_de_trial=sem_prazo_de_trial,
        atividade=atividade,
        pedidos_na_semana=sum(pedidos_por_tenant.values()),
        sem_planos=not precos,
    )


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
            elif plano not in _planos_validos():
                erro = "Plano inválido."

        if erro:
            flash(erro, "erro")
            return render_template(
                "platform/tenant_form.html", form=request.form, planos=_planos_validos()
            )

        # O relógio do teste grátis começa aqui. É o que faz a primeira cobrança
        # sair sozinha quando o período termina.
        dias_trial = int(current_app.config.get("TRIAL_DIAS", 14))
        tenant = Tenant(
            slug=slug,
            nome_fantasia=nome_fantasia,
            email_contato=email_contato,
            plano=plano,
            status="trial",
            trial_termina_em=datetime.combine(
                date.today() + timedelta(days=dias_trial), datetime.min.time()
            ),
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

    return render_template("platform/tenant_form.html", form={}, planos=_planos_validos())


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
            elif plano not in _planos_validos():
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

        # Fim do teste grátis: é o gatilho da primeira cobrança. Vazio significa
        # teste sem prazo, e o tenant nunca é cobrado nem suspenso pelo ciclo.
        fim_trial = _para_data(request.form.get("trial_termina_em"))
        tenant.trial_termina_em = (
            datetime.combine(fim_trial, datetime.min.time()) if fim_trial else None
        )
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
        planos=_planos_validos(),
        statuses=STATUSES,
        assinatura=resumo_do_tenant(tenant),
        hoje=date.today(),
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


# --------------------------------------------------------------------------- #
# Planos (catálogo de venda da plataforma)
# --------------------------------------------------------------------------- #


@platform_bp.route("/planos", methods=["GET", "POST"])
@platform_admin_required
def planos():
    if request.method == "POST":
        slug = (request.form.get("slug") or "").strip().lower()
        nome = (request.form.get("nome") or "").strip()
        if not PADRAO_SLUG.match(slug or ""):
            flash("Slug do plano inválido. Use minúsculas, números e hífen.", "erro")
        elif not nome:
            flash("Informe o nome do plano.", "erro")
        elif Plano.query.filter_by(slug=slug).first():
            flash(f"Já existe um plano '{slug}'.", "erro")
        else:
            plano = Plano(
                slug=slug,
                nome=nome,
                preco_mensal=_para_float(request.form.get("preco_mensal")),
                descricao=(request.form.get("descricao") or "").strip() or None,
                ordem=_para_int(request.form.get("ordem")),
            )
            plano.definir_recursos(request.form.getlist("recursos"))
            db.session.add(plano)
            db.session.commit()
            flash(f"Plano '{nome}' criado.", "sucesso")
        return redirect(url_for("platform.planos"))

    lista = Plano.query.order_by(Plano.ordem, Plano.slug).all()
    # Quantos tenants usam cada plano: evita mexer no preço sem saber o impacto.
    uso = {
        plano.slug: Tenant.query.filter_by(plano=plano.slug).count() for plano in lista
    }
    return render_template("platform/planos.html", planos=lista, uso=uso, recursos=RECURSOS)


@platform_bp.route("/planos/<int:plano_id>/salvar", methods=["POST"])
@platform_admin_required
def plano_salvar(plano_id: int):
    plano = db.session.get(Plano, plano_id)
    if plano is None:
        flash("Plano não encontrado.", "erro")
        return redirect(url_for("platform.planos"))

    nome = (request.form.get("nome") or "").strip()
    if not nome:
        flash("Informe o nome do plano.", "erro")
    else:
        plano.nome = nome
        plano.preco_mensal = _para_float(request.form.get("preco_mensal"))
        plano.descricao = (request.form.get("descricao") or "").strip() or None
        plano.ordem = _para_int(request.form.get("ordem"))
        plano.ativo = request.form.get("ativo") == "on"
        plano.definir_recursos(request.form.getlist("recursos"))
        db.session.commit()
        # O preço novo vale para cobranças futuras: as já emitidas guardam o
        # valor congelado.
        flash("Plano atualizado. Cobranças já emitidas mantêm o valor antigo.", "sucesso")
    return redirect(url_for("platform.planos"))


@platform_bp.route("/planos/<int:plano_id>/excluir", methods=["POST"])
@platform_admin_required
def plano_excluir(plano_id: int):
    plano = db.session.get(Plano, plano_id)
    if plano is None:
        flash("Plano não encontrado.", "erro")
        return redirect(url_for("platform.planos"))

    em_uso = Tenant.query.filter_by(plano=plano.slug).count()
    if em_uso:
        flash(
            f"{em_uso} tenant(s) estão neste plano. Mude o plano deles antes de excluir.",
            "erro",
        )
    else:
        db.session.delete(plano)
        db.session.commit()
        flash("Plano removido.", "sucesso")
    return redirect(url_for("platform.planos"))


# --------------------------------------------------------------------------- #
# Cobranças
# --------------------------------------------------------------------------- #


@platform_bp.route("/cobrancas")
@platform_admin_required
def cobrancas():
    filtro = (request.args.get("status") or COBRANCA_PENDENTE).strip()
    consulta = Cobranca.query
    if filtro != "todas":
        consulta = consulta.filter_by(status=filtro)
    lista = consulta.order_by(Cobranca.vencimento.desc(), Cobranca.id.desc()).limit(200).all()

    hoje = date.today()
    abertas = Cobranca.query.filter_by(status=COBRANCA_PENDENTE).all()
    return render_template(
        "platform/cobrancas.html",
        cobrancas=lista,
        filtro=filtro,
        hoje=hoje,
        total_em_aberto=sum(c.valor for c in abertas),
        qtd_vencidas=sum(1 for c in abertas if c.dias_de_atraso(hoje) > 0),
        recebido_no_mes=sum(
            c.valor_pago or c.valor
            for c in Cobranca.query.filter_by(status=COBRANCA_PAGA).all()
            if c.pago_em and c.pago_em.date() >= hoje.replace(day=1)
        ),
    )


@platform_bp.route("/cobrancas/<int:cobranca_id>/pagar", methods=["POST"])
@platform_admin_required
def cobranca_pagar(cobranca_id: int):
    cobranca = db.session.get(Cobranca, cobranca_id)
    if cobranca is None:
        flash("Cobrança não encontrada.", "erro")
        return redirect(url_for("platform.cobrancas"))

    valor_informado = request.form.get("valor_pago")
    try:
        registrar_pagamento(
            cobranca,
            valor=_para_float(valor_informado) if valor_informado else None,
            metodo=request.form.get("metodo") or "PIX",
            observacao=request.form.get("observacao"),
        )
        flash(
            f"Pagamento de {cobranca.tenant.nome_fantasia} "
            f"({cobranca.rotulo_competencia}) registrado. "
            f"Status do tenant: {cobranca.tenant.status}.",
            "sucesso",
        )
    except ValueError as exc:
        flash(str(exc), "erro")

    return redirect(request.form.get("voltar_para") or url_for("platform.cobrancas"))


@platform_bp.route("/cobrancas/<int:cobranca_id>/cancelar", methods=["POST"])
@platform_admin_required
def cobranca_cancelar(cobranca_id: int):
    cobranca = db.session.get(Cobranca, cobranca_id)
    if cobranca is None:
        flash("Cobrança não encontrada.", "erro")
        return redirect(url_for("platform.cobrancas"))

    try:
        cancelar_cobranca(cobranca, observacao=request.form.get("observacao"))
        flash("Cobrança cancelada.", "sucesso")
    except ValueError as exc:
        flash(str(exc), "erro")
    return redirect(request.form.get("voltar_para") or url_for("platform.cobrancas"))


@platform_bp.route("/tenants/<int:tenant_id>/cobrancas/gerar", methods=["POST"])
@platform_admin_required
def tenant_cobranca_gerar(tenant_id: int):
    tenant = db.session.get(Tenant, tenant_id)
    if tenant is None:
        flash("Tenant não encontrado.", "erro")
        return redirect(url_for("platform.tenants_list"))

    cobranca = gerar_cobranca(tenant)
    if cobranca is None:
        flash(
            "Nada a cobrar: verifique se o plano tem preço, se o teste grátis já "
            "terminou e se o tenant está ativo.",
            "erro",
        )
    else:
        flash(
            f"Cobrança de {cobranca.rotulo_competencia} no valor de "
            f"R$ {cobranca.valor:.2f} com vencimento em "
            f"{cobranca.vencimento.strftime('%d/%m/%Y')}.".replace(".", ",", 1),
            "sucesso",
        )
    return redirect(url_for("platform.tenant_editar", tenant_id=tenant.id))


@platform_bp.route("/cobrancas/ciclo", methods=["POST"])
@platform_admin_required
def cobrancas_ciclo():
    """Roda o ciclo do dia na mão, para quem não tem agendador configurado."""
    resumo = executar_ciclo()
    flash(
        f"Ciclo executado: {resumo['emitidas']} cobrança(s) emitida(s), "
        f"{resumo['avaliados']} tenant(s) avaliado(s), "
        f"{resumo['suspensos']} suspenso(s), {resumo['atrasados']} em atraso.",
        "sucesso",
    )
    return redirect(url_for("platform.cobrancas"))
