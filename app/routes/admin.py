from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from datetime import datetime

from ..decorators import admin_required
from ..extensions import db
from ..models.adicional import Adicional
from ..models.categoria import Categoria
from ..models.cupom import TIPOS_CUPOM, BairroEntrega, Cupom
from ..models.produto import Produto
from ..services.cupons import normalizar_codigo
from ..services.imagens import remover_imagem, salvar_imagem_produto, salvar_logo_tenant
from ..services.recursos import requer_recurso, tenant_libera
from ..utils import para_float, para_int

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


# Conversão de número digitado vive em app/utils.py: era duplicada aqui e na
# área da plataforma, com regras que já divergiram uma vez.
_to_float = para_float
_to_int = para_int


def _to_datetime(valor: str | None) -> datetime | None:
    """Aceita "2026-08-20" e "2026-08-20T18:30" dos inputs de data do navegador."""
    texto = (valor or "").strip()
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        return None


def _to_data(valor: str | None):
    """Converte o valor de um input type=date. Devolve None quando vazio."""
    from datetime import date as _date

    texto = (valor or "").strip()
    if not texto:
        return None
    try:
        return _date.fromisoformat(texto)
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
    from ..services.recursos import limite_do_tenant, mensagem_de_limite, uso_do_tenant

    if request.method == "POST":
        qtd_mesas = max(0, min(_to_int(request.form.get("qtd_mesas")), 200))
        minimo = max(1, _to_int(request.form.get("tempo_estimado_min")) or 40)
        maximo = max(minimo, _to_int(request.form.get("tempo_estimado_max")) or 60)

        # O salão é um número, não uma contagem de linhas: o teto do plano vale
        # sobre o valor pedido, e não "cabe mais uma".
        teto_mesas = limite_do_tenant(g.tenant, "max_mesas")
        if teto_mesas is not None and qtd_mesas > teto_mesas:
            flash(mensagem_de_limite(g.tenant, "max_mesas"), "erro")
            return redirect(url_for("admin.configuracoes"))

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

    from ..layout import COR_PADRAO, cor_valida
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
        limites=uso_do_tenant(g.tenant),
        cor_atual=cor_valida(g.tenant.cor_marca) or COR_PADRAO,
    )


@admin_bp.route("/configuracoes/identidade", methods=["POST"])
@admin_required
@requer_recurso("identidade")
def identidade():
    """Logo e cor de marca do restaurante.

    Rota separada do resto das configurações de propósito: um arquivo recusado
    aqui não pode desfazer nem atrapalhar o salvamento de mesas e tempos, que
    são outro formulário.

    A cor passa por `cor_valida` porque ela vai para dentro de um bloco
    `<style>` no layout: texto arbitrário ali seria injeção de CSS. Valor
    inválido volta a ser o padrão em vez de virar erro, já que a única origem
    prática é um navegador sem `input type=color`.
    """
    from ..layout import cor_valida

    arquivo = request.files.get("logo")
    enviou_arquivo = bool(arquivo and getattr(arquivo, "filename", ""))

    if enviou_arquivo:
        try:
            logo = salvar_logo_tenant(arquivo, tenant_slug=g.tenant.slug)
        except ValueError as exc:
            flash(str(exc), "erro")
            return redirect(url_for("admin.configuracoes"))
        antiga = g.tenant.logo
        g.tenant.logo = logo.caminho_relativo
        if antiga:
            remover_imagem(antiga)
    elif request.form.get("remover_logo") == "on":
        remover_imagem(g.tenant.logo)
        g.tenant.logo = None

    # Só mexe na cor se o campo veio: um POST que traz apenas a logo não deve
    # apagar a cor já escolhida.
    if "cor_marca" in request.form:
        g.tenant.cor_marca = cor_valida(request.form.get("cor_marca"))

    db.session.commit()
    flash("Identidade visual salva.", "sucesso")
    return redirect(url_for("admin.configuracoes"))


@admin_bp.route("/relatorios")
@admin_required
@requer_recurso("relatorios")
def relatorios():
    """Quanto o restaurante vendeu. Sem modelo novo: tudo vem dos pedidos.

    A tela tem duas partes, e elas respondem a perguntas diferentes: o histórico
    ("qual foi aquele pedido?", "quanto entrou entre tal e tal dia?") e o resumo
    ("como está indo comparado à semana passada?").
    """
    from ..services.relatorios import PERIODOS, historico, painel, totais_do_historico

    try:
        dias = int(request.args.get("dias", PERIODOS[0]))
    except ValueError:
        dias = PERIODOS[0]

    inicio = _to_data(request.args.get("data_inicio"))
    fim = _to_data(request.args.get("data_fim"))
    if inicio and fim and fim < inicio:
        inicio, fim = fim, inicio

    pedidos = historico(g.tenant.id, inicio, fim)
    return render_template(
        "admin/relatorios.html",
        tenant=g.tenant,
        dados=painel(g.tenant.id, dias=dias),
        pedidos=pedidos,
        totais=totais_do_historico(pedidos),
        data_inicio=inicio.isoformat() if inicio else "",
        data_fim=fim.isoformat() if fim else "",
        filtrado=bool(inicio or fim),
    )


@admin_bp.route("/relatorios/exportar")
@admin_required
@requer_recurso("relatorios")
def relatorios_exportar():
    """Baixa o período como CSV, para abrir no Excel ou mandar à contabilidade.

    Separador ponto e vírgula e BOM no começo: é o que faz o Excel em português
    abrir o arquivo com as colunas separadas e os acentos certos. Vírgula como
    separador decimal, pelo mesmo motivo.
    """
    import csv
    import io
    from datetime import datetime as _datetime

    from flask import Response

    from ..services.relatorios import historico

    inicio = _to_data(request.args.get("data_inicio"))
    fim = _to_data(request.args.get("data_fim"))
    if inicio and fim and fim < inicio:
        inicio, fim = fim, inicio

    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";")
    escritor.writerow(
        ["Número", "Data", "Cliente", "Telefone", "Tipo", "Local", "Pagamento",
         "Subtotal", "Entrega", "Desconto", "Total", "Status", "Itens"]
    )
    for pedido in historico(g.tenant.id, inicio, fim, limite=20000):
        itens = " | ".join(
            f"{item.quantidade}x {item.nome}"
            + (" + " + ", ".join(extra.nome for extra in item.adicionais) if item.adicionais else "")
            for item in pedido.itens
        )
        escritor.writerow(
            [
                pedido.numero,
                pedido.created_at.strftime("%d/%m/%Y %H:%M"),
                pedido.cliente,
                pedido.telefone or "",
                pedido.tipo,
                pedido.descricao_local,
                pedido.pagamento,
                _virgula(pedido.subtotal),
                _virgula(pedido.taxa_entrega),
                _virgula(pedido.desconto),
                _virgula(pedido.total),
                pedido.status,
                itens,
            ]
        )

    nome = f"vendas_{g.tenant.slug}_{_datetime.now():%Y%m%d}.csv"
    return Response(
        # BOM no início: é o que faz o Excel em português reconhecer UTF-8 e não
        # transformar "Número" em "NÃºmero".
        "﻿" + buffer.getvalue(),
        # content_type, e não mimetype: com mimetype o Flask acrescenta o charset
        # de novo e o cabeçalho sai com "charset=utf-8" duplicado.
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


def _virgula(valor) -> str:
    """Número no formato que a planilha brasileira entende."""
    return f"{float(valor or 0):.2f}".replace(".", ",")


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

        # O teto do plano vale na criação, não na edição: quem já passou do
        # limite (porque o plano mudou depois) continua editando o que tem.
        from ..services.recursos import dentro_do_limite, mensagem_de_limite

        usados = Produto.query.filter_by(tenant_id=g.tenant.id).count()
        if not dentro_do_limite(g.tenant, "max_produtos", usados):
            flash(mensagem_de_limite(g.tenant, "max_produtos"), "erro")
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


# --------------------------------------------------------------------------- #
# Estoque: insumos, ficha técnica e movimentações
# --------------------------------------------------------------------------- #


def _insumos_do_tenant():
    from ..models.estoque import Insumo

    return Insumo.query.filter_by(tenant_id=g.tenant.id).order_by(Insumo.nome).all()


@admin_bp.route("/insumos", methods=["GET", "POST"])
@admin_required
@requer_recurso("estoque")
def insumos():
    from ..models.estoque import UNIDADES, Insumo
    from ..services.estoque import historico, insumos_em_alerta

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        unidade = (request.form.get("unidade") or "g").strip()
        quantidade_compra = _to_float(request.form.get("quantidade_compra"))

        if not nome:
            flash("Informe o nome do insumo.", "erro")
        elif unidade not in UNIDADES:
            flash("Unidade inválida.", "erro")
        elif quantidade_compra <= 0:
            # Sem isso o custo por unidade seria divisão por zero, e o custo de
            # todo prato que usa o insumo sairia zerado sem aviso.
            flash("A quantidade comprada precisa ser maior que zero.", "erro")
        elif Insumo.query.filter_by(tenant_id=g.tenant.id, nome=nome).first():
            flash(f"Você já tem um insumo chamado '{nome}'.", "erro")
        else:
            db.session.add(
                Insumo(
                    tenant_id=g.tenant.id,
                    nome=nome,
                    unidade=unidade,
                    preco_compra=_to_float(request.form.get("preco_compra")),
                    quantidade_compra=quantidade_compra,
                    estoque_minimo=_to_float(request.form.get("estoque_minimo")),
                    controle_estoque=request.form.get("controle_estoque") == "on",
                )
            )
            db.session.commit()
            flash("Insumo cadastrado.", "sucesso")
        # O cadastro de insumo aparece na tela de Custos (como no original) e
        # também no Estoque; volta para a que enviou.
        destino = "admin.custos" if request.form.get("voltar") == "custos" else "admin.insumos"
        return redirect(url_for(destino))

    return render_template(
        "admin/insumos.html",
        tenant=g.tenant,
        insumos=_insumos_do_tenant(),
        unidades=UNIDADES,
        alertas=insumos_em_alerta(g.tenant.id),
        movimentacoes=historico(g.tenant.id, limite=30),
    )


@admin_bp.route("/insumos/<int:insumo_id>/salvar", methods=["POST"])
@admin_required
@requer_recurso("estoque")
def insumo_salvar(insumo_id: int):
    from ..models.estoque import UNIDADES, Insumo

    insumo = Insumo.query.filter_by(id=insumo_id, tenant_id=g.tenant.id).first()
    if insumo is None:
        flash("Insumo não encontrado.", "erro")
        return redirect(url_for("admin.insumos"))

    nome = request.form.get("nome", "").strip()
    quantidade_compra = _to_float(request.form.get("quantidade_compra"))
    duplicado = (
        Insumo.query.filter(
            Insumo.tenant_id == g.tenant.id, Insumo.nome == nome, Insumo.id != insumo.id
        ).first()
        if nome
        else None
    )

    if not nome:
        flash("Informe o nome do insumo.", "erro")
    elif duplicado:
        flash(f"Você já tem um insumo chamado '{nome}'.", "erro")
    elif quantidade_compra <= 0:
        flash("A quantidade comprada precisa ser maior que zero.", "erro")
    else:
        insumo.nome = nome
        if (request.form.get("unidade") or "") in UNIDADES:
            insumo.unidade = request.form.get("unidade")
        insumo.preco_compra = _to_float(request.form.get("preco_compra"))
        insumo.quantidade_compra = quantidade_compra
        insumo.estoque_minimo = _to_float(request.form.get("estoque_minimo"))
        insumo.controle_estoque = request.form.get("controle_estoque") == "on"
        db.session.commit()
        flash("Insumo atualizado. O novo custo vale para as próximas vendas.", "sucesso")
    return redirect(url_for("admin.insumos"))


@admin_bp.route("/insumos/<int:insumo_id>/excluir", methods=["POST"])
@admin_required
@requer_recurso("estoque")
def insumo_excluir(insumo_id: int):
    from ..models.estoque import Insumo, MovimentacaoEstoque

    insumo = Insumo.query.filter_by(id=insumo_id, tenant_id=g.tenant.id).first()
    if insumo is None:
        flash("Insumo não encontrado.", "erro")
        return redirect(url_for("admin.insumos"))

    movimentos = MovimentacaoEstoque.query.filter_by(insumo_id=insumo.id).count()
    if movimentos:
        # Apagar deixaria furo no razão do estoque. Quem não quer mais controlar
        # o insumo pode desmarcar "controlar estoque".
        flash(
            f"'{insumo.nome}' tem {movimentos} movimentação(ões) no histórico e não pode "
            "ser excluído. Desmarque 'controlar estoque' se não quer mais acompanhá-lo.",
            "erro",
        )
    else:
        db.session.delete(insumo)
        db.session.commit()
        flash("Insumo removido.", "sucesso")
    return redirect(url_for("admin.insumos"))


@admin_bp.route("/insumos/<int:insumo_id>/movimentar", methods=["POST"])
@admin_required
@requer_recurso("estoque")
def insumo_movimentar(insumo_id: int):
    from ..models.estoque import Insumo
    from ..services.estoque import movimentar

    insumo = Insumo.query.filter_by(id=insumo_id, tenant_id=g.tenant.id).first()
    if insumo is None:
        flash("Insumo não encontrado.", "erro")
        return redirect(url_for("admin.insumos"))

    tipo = request.form.get("tipo", "")
    # Só lançamento manual aqui: saída e estorno pertencem ao pedido, e lançá-los
    # na mão faria o razão divergir do que foi realmente vendido.
    if tipo not in ("entrada", "perda", "ajuste_entrada", "ajuste_saida"):
        flash("Tipo de movimentação inválido para lançamento manual.", "erro")
        return redirect(url_for("admin.insumos"))

    try:
        movimentar(
            insumo,
            _to_float(request.form.get("quantidade")),
            tipo,
            usuario=session.get("username"),
            observacao=request.form.get("observacao"),
        )
        db.session.commit()
        flash(f"{insumo.nome}: saldo agora é {insumo.estoque_atual:g} {insumo.unidade}.", "sucesso")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    return redirect(url_for("admin.insumos"))


@admin_bp.route("/custos")
@admin_required
@requer_recurso("custos")
def custos():
    """Custos e fichas técnicas — a aba "Custos" da Gestão original.

    O preço sugerido usa a mesma fórmula de lá: `custo / (1 - margem/100)`, ou
    seja, margem sobre o PREÇO, não sobre o custo. É o que permite ler a meta
    como "quero que 60% do que entra seja lucro".
    """
    from ..models.estoque import UNIDADES

    margem = float(g.tenant.margem_lucro or 0)
    fator = max(0.01, 1 - margem / 100.0)

    linhas = []
    for produto in Produto.query.filter_by(tenant_id=g.tenant.id).order_by(Produto.nome).all():
        custo = produto.custo_por_ficha
        linhas.append(
            {
                "produto": produto,
                "custo": custo,
                "sugerido": custo / fator if custo else 0.0,
                "ingredientes": len(produto.ficha),
            }
        )

    return render_template(
        "admin/custos.html",
        tenant=g.tenant,
        insumos=_insumos_do_tenant(),
        unidades=UNIDADES,
        produtos_custo=linhas,
        margem=margem,
    )


@admin_bp.route("/custos/margem", methods=["POST"])
@admin_required
@requer_recurso("custos")
def custos_margem():
    """Meta de margem do restaurante, usada para o preço sugerido."""
    margem = _to_float(request.form.get("margem_lucro"))
    if not 0 <= margem < 100:
        # 100% ou mais faria `1 - margem/100` chegar a zero e o preço sugerido
        # explodir para infinito.
        flash("A meta de margem precisa ficar entre 0 e 99,9%.", "erro")
    else:
        g.tenant.margem_lucro = margem
        db.session.commit()
        flash("Meta de margem atualizada.", "sucesso")
    return redirect(url_for("admin.custos"))


@admin_bp.route("/produtos/<int:produto_id>/ficha", methods=["GET", "POST"])
@admin_required
@requer_recurso("custos")
def produto_ficha(produto_id: int):
    """Ficha técnica: quanto de cada insumo uma unidade do produto consome."""
    from ..services.estoque import definir_ficha

    produto = Produto.query.filter_by(id=produto_id, tenant_id=g.tenant.id).first()
    if produto is None:
        flash("Produto não encontrado.", "erro")
        return redirect(url_for("admin.produtos"))

    if request.method == "POST":
        linhas = [
            (insumo_id, request.form.get(f"quantidade_{insumo_id}"))
            for insumo_id in request.form.getlist("insumo_id")
        ]
        definir_ficha(produto, linhas)
        db.session.commit()
        flash("Ficha técnica salva.", "sucesso")
        return redirect(url_for("admin.produto_ficha", produto_id=produto.id))

    return render_template(
        "admin/produto_ficha.html",
        tenant=g.tenant,
        produto=produto,
        insumos=_insumos_do_tenant(),
        quantidades={linha.insumo_id: linha.quantidade_usada for linha in produto.ficha},
    )


# --------------------------------------------------------------------------- #
# Financeiro: despesas, receitas avulsas e resultado
# --------------------------------------------------------------------------- #


@admin_bp.route("/financeiro")
@admin_required
@requer_recurso("financeiro")
def financeiro():
    from datetime import date

    from ..models.financeiro import CATEGORIAS_DESPESA, CATEGORIAS_RECEITA
    from ..services.financeiro import painel, periodo_escolhido

    hoje = date.today()
    inicio, fim, chave, rotulo = periodo_escolhido(
        request.args.get("finance_period"),
        request.args.get("finance_start"),
        request.args.get("finance_end"),
        hoje,
    )

    return render_template(
        "admin/financeiro.html",
        tenant=g.tenant,
        dados=painel(g.tenant.id, inicio, fim, hoje=hoje),
        periodo={"chave": chave, "rotulo": rotulo, "inicio": inicio, "fim": fim},
        categorias_despesa=CATEGORIAS_DESPESA,
        categorias_receita=CATEGORIAS_RECEITA,
    )


@admin_bp.route("/financeiro/despesas", methods=["POST"])
@admin_required
@requer_recurso("financeiro")
def despesa_criar():
    from ..models.financeiro import CATEGORIAS_DESPESA, Despesa

    descricao = request.form.get("descricao", "").strip()
    valor = _to_float(request.form.get("valor"))
    vencimento = _to_data(request.form.get("data_vencimento"))
    categoria = request.form.get("categoria", "Outros")

    if not descricao:
        flash("Informe a descrição da despesa.", "erro")
    elif valor <= 0:
        flash("O valor da despesa precisa ser maior que zero.", "erro")
    elif vencimento is None:
        flash("Informe a data de vencimento.", "erro")
    else:
        paga = request.form.get("paga") == "on"
        db.session.add(
            Despesa(
                tenant_id=g.tenant.id,
                descricao=descricao,
                valor=valor,
                categoria=categoria if categoria in CATEGORIAS_DESPESA else "Outros",
                data_vencimento=vencimento,
                paga=paga,
                data_pagamento=vencimento if paga else None,
                observacao=request.form.get("observacao", "").strip() or None,
            )
        )
        db.session.commit()
        flash("Despesa lançada.", "sucesso")
    return redirect(url_for("admin.financeiro"))


@admin_bp.route("/financeiro/despesas/<int:despesa_id>/pagar", methods=["POST"])
@admin_required
@requer_recurso("financeiro")
def despesa_pagar(despesa_id: int):
    from datetime import date

    from ..models.financeiro import Despesa

    despesa = Despesa.query.filter_by(id=despesa_id, tenant_id=g.tenant.id).first()
    if despesa is None:
        flash("Despesa não encontrada.", "erro")
    elif despesa.paga:
        flash("Esta despesa já está paga.", "erro")
    else:
        despesa.paga = True
        despesa.data_pagamento = _to_data(request.form.get("data_pagamento")) or date.today()
        db.session.commit()
        flash(f"'{despesa.descricao}' marcada como paga.", "sucesso")
    return redirect(request.form.get("voltar_para") or url_for("admin.financeiro"))


@admin_bp.route("/financeiro/despesas/<int:despesa_id>/excluir", methods=["POST"])
@admin_required
@requer_recurso("financeiro")
def despesa_excluir(despesa_id: int):
    from ..models.financeiro import Despesa

    despesa = Despesa.query.filter_by(id=despesa_id, tenant_id=g.tenant.id).first()
    if despesa is not None:
        db.session.delete(despesa)
        db.session.commit()
        flash("Despesa removida.", "sucesso")
    return redirect(url_for("admin.financeiro"))


@admin_bp.route("/financeiro/receitas", methods=["POST"])
@admin_required
@requer_recurso("financeiro")
def receita_criar():
    from datetime import date

    from ..models.financeiro import CATEGORIAS_RECEITA, ReceitaAvulsa

    valor = _to_float(request.form.get("valor"))
    categoria = request.form.get("categoria", "Outras receitas")

    if valor <= 0:
        flash("O valor da receita precisa ser maior que zero.", "erro")
    else:
        db.session.add(
            ReceitaAvulsa(
                tenant_id=g.tenant.id,
                descricao=request.form.get("descricao", "").strip() or None,
                valor=valor,
                categoria=categoria if categoria in CATEGORIAS_RECEITA else "Outras receitas",
                data_registro=_to_data(request.form.get("data_registro")) or date.today(),
                observacao=request.form.get("observacao", "").strip() or None,
            )
        )
        db.session.commit()
        flash("Receita lançada.", "sucesso")
    return redirect(url_for("admin.financeiro"))


# --------------------------------------------------------------------------- #
# Impressão na cozinha
# --------------------------------------------------------------------------- #


@admin_bp.route("/impressao")
@admin_required
@requer_recurso("impressao")
def impressao():
    """Pareamento do computador do balcão e as últimas comandas da fila."""
    from ..services import impressao as servico

    return render_template(
        "admin/impressao.html",
        tenant=g.tenant,
        situacao=servico.situacao(g.tenant),
        fila=servico.fila(g.tenant.id),
        # O código só existe em texto no instante em que é gerado. Fica na
        # sessão para sobreviver ao redirect e some na primeira exibição: uma
        # senha que continua na tela é uma senha que alguém fotografa.
        token_novo=session.pop("impressao_token", None),
    )


@admin_bp.route("/impressao/agente.zip")
@admin_required
@requer_recurso("impressao")
def impressao_agente_zip():
    """Entrega o agente pronto para levar ao computador do balcão.

    Sem isto a instrução "baixe a pasta agente/" não teria como ser cumprida
    por quem não tem o código do sistema na mão — que é todo mundo menos eu.

    O que fica de fora do pacote é tão importante quanto o que entra: nada de
    `agente_config.json` (contém o código de ativação de alguém),
    `impressoes_confirmadas.json`, log ou `.venv`. Por isso a lista de arquivos
    é explícita, e não um "compacte a pasta inteira" que um dia levaria junto o
    que apareceu ali no meio.
    """
    import io
    import zipfile
    from pathlib import Path

    from flask import send_file

    ARQUIVOS = (
        "agente_impressao.py",
        "configurar_agente.py",
        "instalar_agente.bat",
        "configurar_agente.bat",
        "iniciar_agente.bat",
        "ativar_inicio_automatico.bat",
        "requirements.txt",
        "LEIA-ME.md",
    )

    origem = Path(current_app.root_path).parent / "agente"
    pacote = io.BytesIO()
    incluidos = 0
    with zipfile.ZipFile(pacote, "w", zipfile.ZIP_DEFLATED) as zip_saida:
        for nome in ARQUIVOS:
            caminho = origem / nome
            if caminho.is_file():
                zip_saida.write(caminho, f"agente/{nome}")
                incluidos += 1

    if incluidos < len(ARQUIVOS):
        # Um zip pela metade instalaria um agente quebrado no restaurante, e o
        # erro só apareceria lá. Melhor não entregar nada e dizer o motivo.
        current_app.logger.error("Pacote do agente incompleto em %s", origem)
        flash("O pacote do agente não está disponível neste servidor. Avise o suporte.", "erro")
        return redirect(url_for("admin.impressao"))

    pacote.seek(0)

    return send_file(
        pacote,
        mimetype="application/zip",
        as_attachment=True,
        download_name="comanda-ai-agente-de-impressao.zip",
    )


@admin_bp.route("/impressao/parear", methods=["POST"])
@admin_required
@requer_recurso("impressao")
def impressao_parear():
    from ..services import impressao as servico

    session["impressao_token"] = servico.parear(g.tenant)
    flash("Código de ativação gerado. Copie agora: ele não será mostrado de novo.", "sucesso")
    return redirect(url_for("admin.impressao"))


@admin_bp.route("/impressao/desligar", methods=["POST"])
@admin_required
@requer_recurso("impressao")
def impressao_desligar():
    from ..services import impressao as servico

    if servico.desparear(g.tenant):
        flash("Impressão remota desligada. O agente instalado parou de receber comandas.", "sucesso")
    else:
        flash("Não havia computador pareado.", "erro")
    return redirect(url_for("admin.impressao"))


@admin_bp.route("/impressao/teste", methods=["POST"])
@admin_required
@requer_recurso("impressao")
def impressao_teste():
    from ..services import impressao as servico

    if servico.agente_do_tenant(g.tenant.id) is None:
        flash("Pareie um computador antes de mandar o teste.", "erro")
    else:
        servico.enfileirar_teste(g.tenant)
        flash("Teste enviado. O papel deve sair em alguns segundos.", "sucesso")
    return redirect(url_for("admin.impressao"))


@admin_bp.route("/impressao/<int:job_id>/cancelar", methods=["POST"])
@admin_required
@requer_recurso("impressao")
def impressao_cancelar(job_id: int):
    from ..services import impressao as servico

    if servico.cancelar(g.tenant, job_id):
        flash("Trabalho tirado da fila.", "sucesso")
    else:
        flash("Este trabalho já foi impresso ou não existe mais.", "erro")
    return redirect(url_for("admin.impressao"))
