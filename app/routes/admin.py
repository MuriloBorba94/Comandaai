from __future__ import annotations

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..decorators import admin_required
from ..extensions import db
from ..models.produto import Produto

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/")
@admin_required
def dashboard():
    total_produtos = Produto.query.filter_by(tenant_id=g.tenant.id).count()
    return render_template("admin/dashboard.html", tenant=g.tenant, total_produtos=total_produtos)


@admin_bp.route("/produtos", methods=["GET", "POST"])
@admin_required
def produtos():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        preco_raw = request.form.get("preco", "0").replace(",", ".")
        if nome:
            try:
                preco = float(preco_raw)
            except ValueError:
                preco = 0.0
            produto = Produto(tenant_id=g.tenant.id, nome=nome, preco=preco)
            db.session.add(produto)
            db.session.commit()
            flash("Produto adicionado.", "sucesso")
        return redirect(url_for("admin.produtos"))

    lista = Produto.query.filter_by(tenant_id=g.tenant.id).order_by(Produto.nome).all()
    return render_template("admin/produtos.html", tenant=g.tenant, produtos=lista)


@admin_bp.route("/produtos/<int:produto_id>/excluir", methods=["POST"])
@admin_required
def produto_excluir(produto_id: int):
    produto = Produto.query.filter_by(id=produto_id, tenant_id=g.tenant.id).first()
    if produto:
        db.session.delete(produto)
        db.session.commit()
        flash("Produto removido.", "sucesso")
    return redirect(url_for("admin.produtos"))
