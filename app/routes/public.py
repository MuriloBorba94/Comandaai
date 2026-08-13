from __future__ import annotations

from flask import Blueprint, g, render_template

from ..models.produto import Produto

public_bp = Blueprint("public", __name__)


@public_bp.route("/")
def index():
    if g.tenant is None:
        return render_template("public/landing.html")

    produtos = Produto.query.filter_by(tenant_id=g.tenant.id, disponivel=True).order_by(Produto.nome).all()
    return render_template("public/index.html", tenant=g.tenant, produtos=produtos)
