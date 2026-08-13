from __future__ import annotations

from flask import Blueprint, g, render_template

from ..models.categoria import Categoria
from ..models.produto import Produto

public_bp = Blueprint("public", __name__)

# Rótulo do grupo dos produtos sem categoria. "Sem categoria" é linguagem de
# admin; na vitrine, o cliente final vê "Outros".
GRUPO_SEM_CATEGORIA = "Outros"


@public_bp.route("/")
def index():
    if g.tenant is None:
        return render_template("public/landing.html")

    categorias = (
        Categoria.query.filter_by(tenant_id=g.tenant.id, ativa=True)
        .order_by(Categoria.ordem, Categoria.nome)
        .all()
    )
    produtos = (
        Produto.query.filter_by(tenant_id=g.tenant.id, disponivel=True).order_by(Produto.nome).all()
    )

    # Agrupa na ordem definida pelo tenant. Produto de categoria desativada não
    # aparece — é isso que "desativar categoria" precisa significar para o
    # dono da loja.
    grupos = []
    for categoria in categorias:
        itens = [produto for produto in produtos if produto.categoria_id == categoria.id]
        if itens:
            grupos.append((categoria.nome, itens))

    sem_categoria = [produto for produto in produtos if produto.categoria_id is None]
    if sem_categoria:
        grupos.append((GRUPO_SEM_CATEGORIA, sem_categoria))

    return render_template("public/index.html", tenant=g.tenant, grupos=grupos)
