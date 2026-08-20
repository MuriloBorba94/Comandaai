"""Telas de operação do restaurante: painel da cozinha e salão (mesas).

Diferente de /admin, que é do dono da loja, estas telas são para quem está
trabalhando no turno — por isso exigem apenas login no tenant, não papel de
admin.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..decorators import login_required
from ..services.recursos import requer_recurso
from ..models.categoria import Categoria
from ..models.pedido import (
    STATUS_ATIVOS,
    Pedido,
    TIPO_MESA,
)
from ..models.produto import Produto
from ..services.pedidos import (
    FORMAS_PAGAMENTO,
    adicionar_itens_comanda,
    criar_pedido,
    fechar_comanda,
    mesas_ativas,
    pedidos_ativos,
    proximos_status,
    total_aguardando,
    transicionar,
    versao_da_fila,
)

operacao_bp = Blueprint("operacao", __name__)


def _produtos_para_lancamento():
    """Cardápio disponível, agrupado como na vitrine, para lançar na comanda."""
    categorias = (
        Categoria.query.filter_by(tenant_id=g.tenant.id, ativa=True)
        .order_by(Categoria.ordem, Categoria.nome)
        .all()
    )
    produtos = (
        Produto.query.filter_by(tenant_id=g.tenant.id, disponivel=True).order_by(Produto.nome).all()
    )
    grupos = []
    for categoria in categorias:
        itens = [p for p in produtos if p.categoria_id == categoria.id]
        if itens:
            grupos.append((categoria.nome, itens))
    soltos = [p for p in produtos if p.categoria_id is None]
    if soltos:
        grupos.append(("Outros", soltos))
    return grupos


def _linha_do_formulario() -> dict:
    try:
        quantidade = max(1, min(int(request.form.get("quantidade", "1")), 30))
    except ValueError:
        quantidade = 1
    return {
        "produto_id": request.form.get("produto_id"),
        "quantidade": quantidade,
        "adicionais": [v for v in request.form.getlist("adicionais") if v.strip().isdigit()],
        "observacao": (request.form.get("observacao") or "").strip()[:180],
    }


# --------------------------------------------------------------------------- #
# Cozinha
# --------------------------------------------------------------------------- #


@operacao_bp.route("/cozinha")
@login_required
@requer_recurso("cozinha")
def cozinha():
    ativos = pedidos_ativos(g.tenant.id)
    # Uma coluna por status, na ordem do fluxo.
    colunas = [(status, [p for p in ativos if p.status == status]) for status in STATUS_ATIVOS]
    return render_template(
        "operacao/cozinha.html",
        tenant=g.tenant,
        colunas=colunas,
        total_ativos=len(ativos),
        proximos_status=proximos_status,
        versao=versao_da_fila(g.tenant.id),
        aguardando=total_aguardando(g.tenant.id),
    )


@operacao_bp.route("/cozinha/eventos")
@login_required
@requer_recurso("cozinha")
def cozinha_eventos():
    """Resposta minúscula que diz se a fila mudou desde a última consulta.

    O painel consulta esta rota de poucos em poucos segundos e só recarrega a
    tela quando a versão muda — assim a cozinha vê pedido novo sozinha, sem o
    custo de reenviar o painel inteiro a cada consulta.
    """
    return jsonify(
        versao=versao_da_fila(g.tenant.id),
        aguardando=total_aguardando(g.tenant.id),
    )


@operacao_bp.route("/cozinha/pedidos/<int:pedido_id>/status", methods=["POST"])
@login_required
@requer_recurso("cozinha")
def pedido_status(pedido_id: int):
    pedido = Pedido.query.filter_by(id=pedido_id, tenant_id=g.tenant.id).first()
    if pedido is None:
        flash("Pedido não encontrado.", "erro")
        return redirect(url_for("operacao.cozinha"))

    try:
        transicionar(pedido, request.form.get("status", ""), actor=session.get("username"))
        flash(f"Pedido #{pedido.numero}: {pedido.status}.", "sucesso")
    except ValueError as exc:
        flash(str(exc), "erro")
    return redirect(url_for("operacao.cozinha"))


@operacao_bp.route("/cozinha/pedidos/<int:pedido_id>/imprimir", methods=["POST"])
@login_required
@requer_recurso("impressao")
def pedido_imprimir(pedido_id: int):
    """Manda a comanda para a impressora de novo.

    Existe porque papel amassa, acaba e some. Aqui o pedido de impressão partiu
    de uma pessoa, então ele entra na fila mesmo sem agente pareado — e a tela
    avisa que ninguém vai buscá-lo, em vez de fingir que deu certo.
    """
    from ..services.impressao import agente_do_tenant, enfileirar

    pedido = Pedido.query.filter_by(id=pedido_id, tenant_id=g.tenant.id).first()
    if pedido is None:
        flash("Pedido não encontrado.", "erro")
    elif enfileirar(pedido, forcar=True) is None:
        flash("Não foi possível colocar a comanda na fila.", "erro")
    elif agente_do_tenant(g.tenant.id) is None:
        flash(
            f"Comanda do pedido #{pedido.numero} na fila, mas nenhum computador está "
            "pareado para imprimir. Configure no menu Impressão.",
            "erro",
        )
    else:
        flash(f"Comanda do pedido #{pedido.numero} enviada para a impressora.", "sucesso")

    return redirect(request.referrer or url_for("operacao.cozinha"))


# --------------------------------------------------------------------------- #
# Salão (mesas)
# --------------------------------------------------------------------------- #


def _catalogo_da_comanda() -> tuple[list[dict], list[dict]]:
    """Cardápio e adicionais como listas simples, para o PDV de mesa montar.

    Vai para o HTML como JSON. Cada produto carrega os ids dos adicionais que
    ELE aceita: no sistema original o botão de personalizar só aparecia para a
    categoria "Burgers", cravada no código — aqui quem manda é o vínculo
    produto↔adicional, que é o que o servidor de fato aceita no carrinho.
    """
    produtos = []
    for categoria_nome, itens in _produtos_para_lancamento():
        for produto in itens:
            produtos.append(
                {
                    "id": produto.id,
                    "nome": produto.nome,
                    "preco": float(produto.preco or 0),
                    "categoria": categoria_nome,
                    "adicionais": [a.id for a in produto.adicionais if a.disponivel],
                }
            )

    adicionais = {}
    for produto in Produto.query.filter_by(tenant_id=g.tenant.id, disponivel=True).all():
        for extra in produto.adicionais:
            if extra.disponivel:
                adicionais[extra.id] = {
                    "id": extra.id,
                    "nome": extra.nome,
                    "preco": float(extra.preco or 0),
                }
    return produtos, sorted(adicionais.values(), key=lambda a: a["nome"])


def _resumo_dos_itens(pedido) -> str:
    """Texto do pedido para o modal de ações da mesa, uma linha por item."""
    linhas = []
    for item in pedido.itens:
        linha = f"{item.quantidade}x {item.nome}"
        extras = ", ".join(extra.nome for extra in item.adicionais)
        if extras:
            linha += f"\n   + {extras}"
        if item.observacao:
            linha += f"\n   obs.: {item.observacao}"
        linhas.append(linha)
    return "\n".join(linhas) or "Comanda sem itens."


@operacao_bp.route("/mesas")
@login_required
@requer_recurso("mesas")
def mesas():
    if not g.tenant.atende_mesa:
        flash(
            "O salão não está configurado. Defina a quantidade de mesas nas configurações.",
            "erro",
        )
        return redirect(url_for("admin.configuracoes"))

    ocupadas = mesas_ativas(g.tenant.id)
    produtos, adicionais = _catalogo_da_comanda()
    return render_template(
        "operacao/mesas.html",
        tenant=g.tenant,
        numeros=range(1, (g.tenant.qtd_mesas or 0) + 1),
        ocupadas=ocupadas,
        resumos={numero: _resumo_dos_itens(pedido) for numero, pedido in ocupadas.items()},
        catalogo=produtos,
        adicionais=adicionais,
        formas_pagamento=FORMAS_PAGAMENTO,
    )


@operacao_bp.route("/mesas/<int:numero>/comanda", methods=["POST"])
@login_required
@requer_recurso("mesas")
def mesa_comanda(numero: int):
    """Recebe o carrinho montado no PDV de mesa e abre ou complementa a comanda.

    O corpo tem o MESMO formato do carrinho da vitrine ({produto_id, quantidade,
    adicionais, observacao}) porque as duas telas caem no mesmo serviço de
    pedidos — nada de total digitado pelo cliente da API: o preço é recalculado
    aqui a partir dos ids.
    """
    if not g.tenant.atende_mesa or numero < 1 or numero > (g.tenant.qtd_mesas or 0):
        return jsonify(status="erro", mensagem=f"A mesa {numero} não existe."), 400

    dados = request.get_json(silent=True) or {}
    carrinho = dados.get("carrinho") or []
    if not isinstance(carrinho, list) or not carrinho:
        return jsonify(status="erro", mensagem="Monte o pedido antes de enviar."), 400

    observacao = (dados.get("observacao") or "").strip()[:180]
    linhas = []
    for bruta in carrinho[:60]:
        if not isinstance(bruta, dict):
            continue
        try:
            quantidade = max(1, min(int(bruta.get("quantidade", 1)), 30))
        except (TypeError, ValueError):
            quantidade = 1
        extras = [str(v) for v in (bruta.get("adicionais") or []) if str(v).strip().isdigit()]
        linhas.append(
            {
                "produto_id": bruta.get("produto_id"),
                "quantidade": quantidade,
                "adicionais": extras,
                "observacao": (bruta.get("remocoes") or "").strip()[:180],
            }
        )

    if not linhas:
        return jsonify(status="erro", mensagem="Monte o pedido antes de enviar."), 400

    pedido = mesas_ativas(g.tenant.id).get(numero)
    try:
        if pedido is None:
            criar_pedido(
                g.tenant,
                {
                    "cliente": f"Mesa {numero:02d}",
                    "tipo": TIPO_MESA,
                    "mesa": numero,
                    "carrinho": linhas,
                    "observacao": observacao,
                    "origem": "mesa",
                },
            )
            mensagem = f"Mesa {numero:02d} aberta e pedido enviado para a cozinha."
        else:
            adicionar_itens_comanda(pedido, linhas, actor=session.get("username"))
            mensagem = "Itens adicionados à comanda."
    except ValueError as exc:
        return jsonify(status="erro", mensagem=str(exc)), 400

    return jsonify(status="ok", mensagem=mensagem)


@operacao_bp.route("/mesas/<int:numero>")
@login_required
@requer_recurso("mesas")
def mesa_detalhe(numero: int):
    if not g.tenant.atende_mesa:
        return redirect(url_for("admin.configuracoes"))
    if numero < 1 or numero > (g.tenant.qtd_mesas or 0):
        flash(f"A mesa {numero} não existe.", "erro")
        return redirect(url_for("operacao.mesas"))

    return render_template(
        "operacao/mesa.html",
        tenant=g.tenant,
        numero=numero,
        pedido=mesas_ativas(g.tenant.id).get(numero),
        grupos=_produtos_para_lancamento(),
        formas_pagamento=FORMAS_PAGAMENTO,
    )


@operacao_bp.route("/mesas/<int:numero>/itens", methods=["POST"])
@login_required
@requer_recurso("mesas")
def mesa_lancar_item(numero: int):
    linha = _linha_do_formulario()
    pedido = mesas_ativas(g.tenant.id).get(numero)

    try:
        if pedido is None:
            # Primeiro item abre a comanda da mesa.
            criar_pedido(
                g.tenant,
                {
                    "cliente": (request.form.get("cliente") or "").strip() or f"Mesa {numero}",
                    "tipo": TIPO_MESA,
                    "mesa": numero,
                    "carrinho": [linha],
                    "origem": "mesa",
                },
            )
            flash(f"Comanda da mesa {numero} aberta.", "sucesso")
        else:
            adicionar_itens_comanda(pedido, [linha], actor=session.get("username"))
            flash("Item lançado na comanda.", "sucesso")
    except ValueError as exc:
        flash(str(exc), "erro")

    return redirect(url_for("operacao.mesa_detalhe", numero=numero))


@operacao_bp.route("/mesas/<int:numero>/conta", methods=["POST"])
@login_required
@requer_recurso("impressao")
def mesa_conta(numero: int):
    """Imprime a conferência de consumo que vai para a mesa antes de fechar.

    Sai separada da comanda de produção de propósito: uma vai para a cozinha
    com o que fazer, a outra vai para o cliente com o que ele deve. Misturar as
    duas é como o pedido acaba saindo duas vezes na chapa.
    """
    from ..models.impressao import TIPO_FECHAMENTO
    from ..services.impressao import agente_do_tenant, enfileirar

    pedido = mesas_ativas(g.tenant.id).get(numero)
    if pedido is None:
        flash(f"A mesa {numero} não tem comanda aberta.", "erro")
        return redirect(url_for("operacao.mesas"))

    if enfileirar(pedido, TIPO_FECHAMENTO, forcar=True) is None:
        flash("Não foi possível colocar a conta na fila.", "erro")
    elif agente_do_tenant(g.tenant.id) is None:
        flash(
            "Conta na fila, mas nenhum computador está pareado para imprimir. "
            "Configure no menu Impressão.",
            "erro",
        )
    else:
        flash(f"Conta da mesa {numero:02d} enviada para a impressora.", "sucesso")

    return redirect(url_for("operacao.mesa_detalhe", numero=numero))


@operacao_bp.route("/mesas/<int:numero>/fechar", methods=["POST"])
@login_required
@requer_recurso("mesas")
def mesa_fechar(numero: int):
    pedido = mesas_ativas(g.tenant.id).get(numero)
    if pedido is None:
        flash(f"A mesa {numero} não tem comanda aberta.", "erro")
        return redirect(url_for("operacao.mesas"))

    try:
        fechar_comanda(pedido, request.form.get("pagamento", ""))
        flash(f"Comanda da mesa {numero} fechada: R$ {pedido.total:.2f}".replace(".", ","), "sucesso")
    except ValueError as exc:
        flash(str(exc), "erro")
        return redirect(url_for("operacao.mesa_detalhe", numero=numero))

    return redirect(url_for("operacao.mesas"))
