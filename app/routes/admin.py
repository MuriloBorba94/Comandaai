from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from datetime import datetime

from ..decorators import admin_required
from ..extensions import db
from ..models.adicional import Adicional
from ..models.categoria import Categoria
from ..models.cupom import TIPOS_CUPOM, BairroEntrega, Cupom
from ..models.produto import Produto
from ..services.cupons import normalizar_codigo
from ..services.imagens import remover_imagem, salvar_imagem_produto
from ..services.recursos import requer_recurso, tenant_libera

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _to_float(valor: str | None) -> float:
    """Interpreta preço digitado no padrão brasileiro.

    Aceita "45,90", "45.90" e também "1.234,56" — neste último o ponto é
    separador de milhar, e trocá-lo por vírgula às cegas produziria "1.234.56",
    que não converte e viraria 0.0 silenciosamente.
    """
    texto = (valor or "").strip()
    if not texto:
        return 0.0
    if "," in texto and "." in texto:
        texto = texto.replace(".", "")  # ponto é milhar; a vírgula é o decimal
    texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return 0.0


def _to_int(valor: str | None) -> int:
    try:
        return int((valor or "0").strip())
    except ValueError:
        return 0


def _to_datetime(valor: str | None) -> datetime | None:
    """Aceita "2026-08-20" e "2026-08-20T18:30" dos inputs de data do navegador."""
    texto = (valor or "").strip()
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        return None


def _imagem_enviada(produto: Produto):
    """Processa a foto enviada, se o plano do tenant incluir fotos.

    Quando não inclui, o arquivo é ignorado com aviso em vez de erro: o resto do
    cadastro do produto continua valendo.
    """
    arquivo = request.files.get("imagem")
    if not arquivo or not getattr(arquivo, "filename", ""):
        return None
    if not tenant_libera(g.tenant, "fotos"):
        flash("O plano deste restaurante não inclui fotos nos produtos.", "erro")
        return None
    return salvar_imagem_produto(arquivo, tenant_slug=g.tenant.slug, produto_id=produto.id)


def _categoria_do_tenant(categoria_id: str | None) -> Categoria | None:
    """Resolve a categoria escolhida no formulário DENTRO do tenant atual.

    Um id de categoria de outro tenant simplesmente não é encontrado, e o
    produto fica sem categoria em vez de apontar para fora do tenant.
    """
    if not categoria_id or not str(categoria_id).strip().isdigit():
        return None
    return Categoria.query.filter_by(id=int(categoria_id), tenant_id=g.tenant.id).first()


def _categorias_do_tenant() -> list[Categoria]:
    return (
        Categoria.query.filter_by(tenant_id=g.tenant.id)
        .order_by(Categoria.ordem, Categoria.nome)
        .all()
    )


def _adicionais_do_tenant() -> list[Adicional]:
    return Adicional.query.filter_by(tenant_id=g.tenant.id).order_by(Adicional.nome).all()


@admin_bp.route("/configuracoes", methods=["GET", "POST"])
@admin_required
def configuracoes():
    """Ajustes da loja feitos pelo próprio dono do restaurante."""
    if request.method == "POST":
        qtd_mesas = max(0, min(_to_int(request.form.get("qtd_mesas")), 200))
        minimo = max(1, _to_int(request.form.get("tempo_estimado_min")) or 40)
        maximo = max(minimo, _to_int(request.form.get("tempo_estimado_max")) or 60)

        # Reduzir o salão não pode deixar comanda aberta fora da faixa: ela
        # sumiria do mapa e ninguém conseguiria fechar (o "fantasma" que o
        # sistema original produzia com mesa inválida).
        from ..services.pedidos import mesas_ativas

        fora_da_faixa = [numero for numero in mesas_ativas(g.tenant.id) if numero > qtd_mesas]
        if fora_da_faixa:
            flash(
                "Feche primeiro as comandas das mesas "
                + ", ".join(str(n) for n in sorted(fora_da_faixa))
                + " antes de reduzir o salão.",
                "erro",
            )
            return redirect(url_for("admin.configuracoes"))

        g.tenant.qtd_mesas = qtd_mesas
        g.tenant.tempo_estimado_min = minimo
        g.tenant.tempo_estimado_max = maximo
        db.session.commit()
        flash("Configurações salvas.", "sucesso")
        return redirect(url_for("admin.configuracoes"))

    from ..models.assinatura import RECURSOS, Plano
    from ..services.recursos import recursos_do_tenant

    liberados = recursos_do_tenant(g.tenant)
    return render_template(
        "admin/configuracoes.html",
        tenant=g.tenant,
        plano=Plano.query.filter_by(slug=g.tenant.plano).first(),
        # Mostra o catálogo inteiro marcando o que está incluído: o dono precisa
        # saber tanto o que tem quanto o que ganharia mudando de plano.
        recursos=[(rotulo, explicacao, slug in liberados) for slug, rotulo, explicacao in RECURSOS],
    )


@admin_bp.route("/relatorios")
@admin_required
@requer_recurso("relatorios")
def relatorios():
    """Quanto o restaurante vendeu. Sem modelo novo: tudo vem dos pedidos."""
    from ..services.relatorios import PERIODOS, painel

    try:
        dias = int(request.args.get("dias", PERIODOS[0]))
    except ValueError:
        dias = PERIODOS[0]

    return render_template(
        "admin/relatorios.html", tenant=g.tenant, dados=painel(g.tenant.id, dias=dias)
    )


@admin_bp.route("/")
@admin_required
def dashboard():
    from ..services.pedidos import pedidos_ativos

    return render_template(
        "admin/dashboard.html",
        tenant=g.tenant,
        total_produtos=Produto.query.filter_by(tenant_id=g.tenant.id).count(),
        total_categorias=Categoria.query.filter_by(tenant_id=g.tenant.id).count(),
        total_adicionais=Adicional.query.filter_by(tenant_id=g.tenant.id).count(),
        total_ativos=len(pedidos_ativos(g.tenant.id)),
    )


# --------------------------------------------------------------------------- #
# Categorias
# --------------------------------------------------------------------------- #


@admin_bp.route("/categorias", methods=["GET", "POST"])
@admin_required
def categorias():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("Informe o nome da categoria.", "erro")
        elif Categoria.query.filter_by(tenant_id=g.tenant.id, nome=nome).first():
            flash(f"Você já tem uma categoria chamada '{nome}'.", "erro")
        else:
            db.session.add(
                Categoria(tenant_id=g.tenant.id, nome=nome, ordem=_to_int(request.form.get("ordem")))
            )
            db.session.commit()
            flash("Categoria criada.", "sucesso")
        return redirect(url_for("admin.categorias"))

    return render_template("admin/categorias.html", tenant=g.tenant, categorias=_categorias_do_tenant())


@admin_bp.route("/categorias/<int:categoria_id>/salvar", methods=["POST"])
@admin_required
def categoria_salvar(categoria_id: int):
    categoria = Categoria.query.filter_by(id=categoria_id, tenant_id=g.tenant.id).first()
    if categoria is None:
        flash("Categoria não encontrada.", "erro")
        return redirect(url_for("admin.categorias"))

    nome = request.form.get("nome", "").strip()
    duplicada = (
        Categoria.query.filter(
            Categoria.tenant_id == g.tenant.id,
            Categoria.nome == nome,
            Categoria.id != categoria.id,
        ).first()
        if nome
        else None
    )
    if not nome:
        flash("Informe o nome da categoria.", "erro")
    elif duplicada:
        flash(f"Você já tem uma categoria chamada '{nome}'.", "erro")
    else:
        categoria.nome = nome
        categoria.ordem = _to_int(request.form.get("ordem"))
        categoria.ativa = request.form.get("ativa") == "on"
        db.session.commit()
        flash("Categoria atualizada.", "sucesso")
    return redirect(url_for("admin.categorias"))


@admin_bp.route("/categorias/<int:categoria_id>/excluir", methods=["POST"])
@admin_required
def categoria_excluir(categoria_id: int):
    categoria = Categoria.query.filter_by(id=categoria_id, tenant_id=g.tenant.id).first()
    if categoria is not None:
        # Os produtos NÃO são apagados junto: ficam sem categoria e continuam
        # no cardápio, agrupados em "Sem categoria".
        for produto in categoria.produtos:
            produto.categoria_id = None
        db.session.delete(categoria)
        db.session.commit()
        flash("Categoria removida. Os produtos dela ficaram sem categoria.", "sucesso")
    return redirect(url_for("admin.categorias"))


# --------------------------------------------------------------------------- #
# Adicionais
# --------------------------------------------------------------------------- #


@admin_bp.route("/adicionais", methods=["GET", "POST"])
@admin_required
def adicionais():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("Informe o nome do adicional.", "erro")
        elif Adicional.query.filter_by(tenant_id=g.tenant.id, nome=nome).first():
            flash(f"Você já tem um adicional chamado '{nome}'.", "erro")
        else:
            db.session.add(
                Adicional(tenant_id=g.tenant.id, nome=nome, preco=_to_float(request.form.get("preco")))
            )
            db.session.commit()
            flash("Adicional criado.", "sucesso")
        return redirect(url_for("admin.adicionais"))

    return render_template("admin/adicionais.html", tenant=g.tenant, adicionais=_adicionais_do_tenant())


@admin_bp.route("/adicionais/<int:adicional_id>/salvar", methods=["POST"])
@admin_required
def adicional_salvar(adicional_id: int):
    adicional = Adicional.query.filter_by(id=adicional_id, tenant_id=g.tenant.id).first()
    if adicional is None:
        flash("Adicional não encontrado.", "erro")
        return redirect(url_for("admin.adicionais"))

    nome = request.form.get("nome", "").strip()
    if not nome:
        flash("Informe o nome do adicional.", "erro")
    else:
        adicional.nome = nome
        adicional.preco = _to_float(request.form.get("preco"))
        adicional.disponivel = request.form.get("disponivel") == "on"
        db.session.commit()
        flash("Adicional atualizado.", "sucesso")
    return redirect(url_for("admin.adicionais"))


@admin_bp.route("/adicionais/<int:adicional_id>/excluir", methods=["POST"])
@admin_required
def adicional_excluir(adicional_id: int):
    adicional = Adicional.query.filter_by(id=adicional_id, tenant_id=g.tenant.id).first()
    if adicional is not None:
        db.session.delete(adicional)
        db.session.commit()
        flash("Adicional removido.", "sucesso")
    return redirect(url_for("admin.adicionais"))


# --------------------------------------------------------------------------- #
# Bairros de entrega
# --------------------------------------------------------------------------- #


def _bairros_do_tenant() -> list[BairroEntrega]:
    return (
        BairroEntrega.query.filter_by(tenant_id=g.tenant.id)
        .order_by(BairroEntrega.ordem, BairroEntrega.nome)
        .all()
    )


@admin_bp.route("/bairros", methods=["GET", "POST"])
@admin_required
@requer_recurso("bairros")
def bairros():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("Informe o nome do bairro.", "erro")
        elif BairroEntrega.query.filter_by(tenant_id=g.tenant.id, nome=nome).first():
            flash(f"Você já atende o bairro '{nome}'.", "erro")
        else:
            db.session.add(
                BairroEntrega(
                    tenant_id=g.tenant.id,
                    nome=nome,
                    taxa=_to_float(request.form.get("taxa")),
                    prazo_adicional_min=max(0, _to_int(request.form.get("prazo_adicional_min"))),
                    ordem=_to_int(request.form.get("ordem")),
                )
            )
            db.session.commit()
            flash("Bairro adicionado.", "sucesso")
        return redirect(url_for("admin.bairros"))

    return render_template("admin/bairros.html", tenant=g.tenant, bairros=_bairros_do_tenant())


@admin_bp.route("/bairros/<int:bairro_id>/salvar", methods=["POST"])
@admin_required
@requer_recurso("bairros")
def bairro_salvar(bairro_id: int):
    bairro = BairroEntrega.query.filter_by(id=bairro_id, tenant_id=g.tenant.id).first()
    if bairro is None:
        flash("Bairro não encontrado.", "erro")
        return redirect(url_for("admin.bairros"))

    nome = request.form.get("nome", "").strip()
    duplicado = (
        BairroEntrega.query.filter(
            BairroEntrega.tenant_id == g.tenant.id,
            BairroEntrega.nome == nome,
            BairroEntrega.id != bairro.id,
        ).first()
        if nome
        else None
    )
    if not nome:
        flash("Informe o nome do bairro.", "erro")
    elif duplicado:
        flash(f"Você já atende o bairro '{nome}'.", "erro")
    else:
        bairro.nome = nome
        bairro.taxa = _to_float(request.form.get("taxa"))
        bairro.prazo_adicional_min = max(0, _to_int(request.form.get("prazo_adicional_min")))
        bairro.ordem = _to_int(request.form.get("ordem"))
        bairro.ativo = request.form.get("ativo") == "on"
        db.session.commit()
        flash("Bairro atualizado.", "sucesso")
    return redirect(url_for("admin.bairros"))


@admin_bp.route("/bairros/<int:bairro_id>/excluir", methods=["POST"])
@admin_required
@requer_recurso("bairros")
def bairro_excluir(bairro_id: int):
    bairro = BairroEntrega.query.filter_by(id=bairro_id, tenant_id=g.tenant.id).first()
    if bairro is not None:
        # Pedidos antigos guardam bairro_nome e taxa congelados, então o
        # histórico não se perde ao excluir o bairro.
        db.session.delete(bairro)
        db.session.commit()
        flash("Bairro removido.", "sucesso")
    return redirect(url_for("admin.bairros"))


# --------------------------------------------------------------------------- #
# Cupons
# --------------------------------------------------------------------------- #


@admin_bp.route("/cupons", methods=["GET", "POST"])
@admin_required
@requer_recurso("cupons")
def cupons():
    if request.method == "POST":
        codigo = normalizar_codigo(request.form.get("codigo"))
        tipo = request.form.get("tipo", "percentual")
        if not codigo:
            flash("Informe o código do cupom (letras, números, - e _).", "erro")
        elif tipo not in TIPOS_CUPOM:
            flash("Tipo de cupom inválido.", "erro")
        elif Cupom.query.filter_by(tenant_id=g.tenant.id, codigo=codigo).first():
            flash(f"Você já tem um cupom '{codigo}'.", "erro")
        else:
            db.session.add(
                Cupom(
                    tenant_id=g.tenant.id,
                    codigo=codigo,
                    descricao=request.form.get("descricao", "").strip() or None,
                    tipo=tipo,
                    valor=_to_float(request.form.get("valor")),
                    pedido_minimo=_to_float(request.form.get("pedido_minimo")),
                    limite_usos=max(1, _to_int(request.form.get("limite_usos")) or 1),
                    permite_combo_promocional=request.form.get("permite_combo_promocional") == "on",
                    inicio_em=_to_datetime(request.form.get("inicio_em")),
                    fim_em=_to_datetime(request.form.get("fim_em")),
                )
            )
            db.session.commit()
            flash(f"Cupom {codigo} criado.", "sucesso")
        return redirect(url_for("admin.cupons"))

    lista = Cupom.query.filter_by(tenant_id=g.tenant.id).order_by(Cupom.codigo).all()
    return render_template("admin/cupons.html", tenant=g.tenant, cupons=lista, tipos=TIPOS_CUPOM)


@admin_bp.route("/cupons/<int:cupom_id>/salvar", methods=["POST"])
@admin_required
@requer_recurso("cupons")
def cupom_salvar(cupom_id: int):
    cupom = Cupom.query.filter_by(id=cupom_id, tenant_id=g.tenant.id).first()
    if cupom is None:
        flash("Cupom não encontrado.", "erro")
        return redirect(url_for("admin.cupons"))

    limite = max(1, _to_int(request.form.get("limite_usos")) or 1)
    if limite < (cupom.usos_confirmados or 0):
        flash(
            f"Este cupom já foi usado {cupom.usos_confirmados} vez(es); "
            "o limite não pode ficar abaixo disso.",
            "erro",
        )
        return redirect(url_for("admin.cupons"))

    cupom.descricao = request.form.get("descricao", "").strip() or None
    cupom.valor = _to_float(request.form.get("valor"))
    cupom.pedido_minimo = _to_float(request.form.get("pedido_minimo"))
    cupom.limite_usos = limite
    cupom.ativo = request.form.get("ativo") == "on"
    cupom.permite_combo_promocional = request.form.get("permite_combo_promocional") == "on"
    cupom.inicio_em = _to_datetime(request.form.get("inicio_em"))
    cupom.fim_em = _to_datetime(request.form.get("fim_em"))
    db.session.commit()
    flash("Cupom atualizado.", "sucesso")
    return redirect(url_for("admin.cupons"))


@admin_bp.route("/cupons/<int:cupom_id>/excluir", methods=["POST"])
@admin_required
@requer_recurso("cupons")
def cupom_excluir(cupom_id: int):
    cupom = Cupom.query.filter_by(id=cupom_id, tenant_id=g.tenant.id).first()
    if cupom is not None:
        # O código fica gravado em cupom_codigo de cada pedido, então excluir
        # não apaga o histórico de quem usou.
        db.session.delete(cupom)
        db.session.commit()
        flash("Cupom removido.", "sucesso")
    return redirect(url_for("admin.cupons"))


# --------------------------------------------------------------------------- #
# Produtos
# --------------------------------------------------------------------------- #


@admin_bp.route("/produtos", methods=["GET", "POST"])
@admin_required
def produtos():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("Informe o nome do produto.", "erro")
            return redirect(url_for("admin.produtos"))

        categoria = _categoria_do_tenant(request.form.get("categoria_id"))
        produto = Produto(
            tenant_id=g.tenant.id,
            nome=nome,
            descricao=request.form.get("descricao", "").strip() or None,
            preco=_to_float(request.form.get("preco")),
            categoria_id=categoria.id if categoria else None,
            disponivel=request.form.get("disponivel") == "on",
            combo_promocional=request.form.get("combo_promocional") == "on",
        )
        db.session.add(produto)
        db.session.flush()  # precisa do id para nomear o arquivo da imagem

        produto.definir_adicionais(request.form.getlist("adicionais"))

        try:
            imagem = _imagem_enviada(produto)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "erro")
            return redirect(url_for("admin.produtos"))

        if imagem:
            produto.imagem = imagem.caminho_relativo
        db.session.commit()
        flash("Produto adicionado.", "sucesso")
        return redirect(url_for("admin.produtos"))

    lista = (
        Produto.query.filter_by(tenant_id=g.tenant.id)
        .outerjoin(Categoria)
        .order_by(Categoria.ordem.nullslast(), Categoria.nome.nullslast(), Produto.nome)
        .all()
    )
    return render_template(
        "admin/produtos.html",
        tenant=g.tenant,
        produtos=lista,
        categorias=_categorias_do_tenant(),
        adicionais=_adicionais_do_tenant(),
    )


@admin_bp.route("/produtos/<int:produto_id>/editar", methods=["GET", "POST"])
@admin_required
def produto_editar(produto_id: int):
    produto = Produto.query.filter_by(id=produto_id, tenant_id=g.tenant.id).first()
    if produto is None:
        flash("Produto não encontrado.", "erro")
        return redirect(url_for("admin.produtos"))

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        if not nome:
            flash("Informe o nome do produto.", "erro")
            return redirect(url_for("admin.produto_editar", produto_id=produto.id))

        categoria = _categoria_do_tenant(request.form.get("categoria_id"))
        produto.nome = nome
        produto.descricao = request.form.get("descricao", "").strip() or None
        produto.preco = _to_float(request.form.get("preco"))
        produto.categoria_id = categoria.id if categoria else None
        produto.disponivel = request.form.get("disponivel") == "on"
        produto.combo_promocional = request.form.get("combo_promocional") == "on"
        produto.definir_adicionais(request.form.getlist("adicionais"))

        try:
            imagem = _imagem_enviada(produto)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "erro")
            return redirect(url_for("admin.produto_editar", produto_id=produto.id))

        if request.form.get("remover_imagem") == "on" and not imagem:
            remover_imagem(produto.imagem)
            produto.imagem = None
        elif imagem:
            # Troca de imagem: apaga a antiga para não deixar arquivo órfão.
            antiga = produto.imagem
            produto.imagem = imagem.caminho_relativo
            if antiga:
                remover_imagem(antiga)

        db.session.commit()
        flash("Produto atualizado.", "sucesso")
        return redirect(url_for("admin.produtos"))

    return render_template(
        "admin/produto_form.html",
        tenant=g.tenant,
        produto=produto,
        categorias=_categorias_do_tenant(),
        adicionais=_adicionais_do_tenant(),
        ids_vinculados={adicional.id for adicional in produto.adicionais},
    )


@admin_bp.route("/produtos/<int:produto_id>/excluir", methods=["POST"])
@admin_required
def produto_excluir(produto_id: int):
    produto = Produto.query.filter_by(id=produto_id, tenant_id=g.tenant.id).first()
    if produto:
        caminho = produto.imagem
        db.session.delete(produto)
        db.session.commit()
        # Só depois do commit: se a transação falhasse, o arquivo já teria ido.
        if caminho:
            remover_imagem(caminho)
        current_app.logger.info("Produto %s removido do tenant %s", produto_id, g.tenant.slug)
        flash("Produto removido.", "sucesso")
    return redirect(url_for("admin.produtos"))
