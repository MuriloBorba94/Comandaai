"""Telas de operação do restaurante: painel da cozinha e salão (mesas).

Diferente de /admin, que é do dono da loja, estas telas são para quem está
trabalhando no turno — por isso exigem apenas login no tenant, não papel de
admin.
"""

from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from ..decorators import login_required
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
    transicionar,
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
    )


@operacao_bp.route("/cozinha/pedidos/<int:pedido_id>/status", methods=["POST"])
@login_required
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


# --------------------------------------------------------------------------- #
# Salão (mesas)
# --------------------------------------------------------------------------- #


@operacao_bp.route("/mesas")
@login_required
def mesas():
    if not g.tenant.atende_mesa:
        flash(
            "O salão não está configurado. Defina a quantidade de mesas nas configurações.",
            "erro",
        )
        return redirect(url_for("admin.configuracoes"))

    ocupadas = mesas_ativas(g.tenant.id)
    return render_template(
        "operacao/mesas.html",
        tenant=g.tenant,
        numeros=range(1, (g.tenant.qtd_mesas or 0) + 1),
        ocupadas=ocupadas,
    )


@operacao_bp.route("/mesas/<int:numero>")
@login_required
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


@operacao_bp.route("/mesas/<int:numero>/fechar", methods=["POST"])
@login_required
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
