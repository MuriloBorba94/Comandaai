from __future__ import annotations

import secrets

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..models.categoria import Categoria
from ..models.pedido import Pedido, TIPO_ENTREGA, TIPO_RETIRADA
from ..models.produto import Produto
from ..services.cupons import validar_cupom
from ..services.recursos import tenant_libera
from ..services.pedidos import (
    FORMAS_PAGAMENTO,
    bairros_ativos,
    calcular_carrinho,
    criar_pedido,
)

public_bp = Blueprint("public", __name__)

# Rótulo do grupo dos produtos sem categoria. "Sem categoria" é linguagem de
# admin; na vitrine, o cliente final vê "Outros".
GRUPO_SEM_CATEGORIA = "Outros"

CHAVE_CARRINHO = "carrinho"
CHAVE_CARRINHO_TENANT = "carrinho_tenant"
# Na sessão fica só o CÓDIGO do cupom. O desconto é recalculado a cada tela e
# no fechamento — guardar o valor na sessão seria confiar num dado do cliente.
CHAVE_CUPOM = "cupom"
MAX_LINHAS_CARRINHO = 50


def _carrinho_da_sessao() -> list[dict]:
    """Carrinho do cliente, preso ao tenant que o criou.

    O cookie de sessão já é host-only (não há SESSION_COOKIE_DOMAIN), então o
    carrinho de um tenant não é enviado para o subdomínio de outro. Este cheque
    é defesa em profundidade: se por qualquer motivo o cookie for reaproveitado,
    o carrinho é descartado em vez de misturar cardápios.
    """
    if session.get(CHAVE_CARRINHO_TENANT) != g.tenant.id:
        session.pop(CHAVE_CARRINHO, None)
        session[CHAVE_CARRINHO_TENANT] = g.tenant.id
        return []
    return session.get(CHAVE_CARRINHO, [])


def _salvar_carrinho(carrinho: list[dict]) -> None:
    session[CHAVE_CARRINHO] = carrinho
    session[CHAVE_CARRINHO_TENANT] = g.tenant.id


def _limpar_carrinho() -> None:
    session.pop(CHAVE_CARRINHO, None)
    session.pop(CHAVE_CARRINHO_TENANT, None)
    session.pop(CHAVE_CUPOM, None)


def _itens_calculados(carrinho: list[dict]):
    """Recalcula o carrinho no servidor para exibição.

    Usar a mesma função do fechamento garante que o cliente veja exatamente o
    preço que será cobrado, e que um produto que saiu do ar apareça como erro
    aqui em vez de estourar no checkout.
    """
    if not carrinho:
        return [], 0.0, None
    try:
        itens, subtotal = calcular_carrinho(g.tenant.id, carrinho)
        return itens, float(subtotal), None
    except ValueError as exc:
        return [], 0.0, str(exc)


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

    return render_template(
        "public/index.html",
        tenant=g.tenant,
        grupos=grupos,
        qtd_carrinho=sum(int(linha.get("quantidade", 1)) for linha in _carrinho_da_sessao()),
    )


@public_bp.route("/carrinho")
def carrinho():
    if g.tenant is None:
        abort(404)

    linhas = _carrinho_da_sessao()
    itens, subtotal, erro = _itens_calculados(linhas)
    if erro:
        flash(erro, "erro")

    # O cupom é revalidado contra o carrinho ATUAL: remover itens pode derrubar
    # o pedido mínimo, e nesse caso o desconto tem que desaparecer da tela.
    usa_cupons = tenant_libera(g.tenant, "cupons")
    codigo_cupom = session.get(CHAVE_CUPOM) if usa_cupons else None
    desconto = 0.0
    aviso_cupom = None
    if codigo_cupom and itens:
        resultado = validar_cupom(g.tenant.id, codigo_cupom, itens, subtotal)
        if resultado.ok:
            desconto = float(resultado.desconto)
        else:
            aviso_cupom = resultado.mensagem

    return render_template(
        "public/carrinho.html",
        tenant=g.tenant,
        itens=itens,
        subtotal=subtotal,
        desconto=desconto,
        total_sem_entrega=max(0.0, round(subtotal - desconto, 2)),
        codigo_cupom=codigo_cupom,
        aviso_cupom=aviso_cupom,
        usa_cupons=usa_cupons,
        bairros=bairros_ativos(g.tenant.id) if tenant_libera(g.tenant, "bairros") else [],
        formas_pagamento=FORMAS_PAGAMENTO,
        tipos=(TIPO_ENTREGA, TIPO_RETIRADA),
        # Identificador do envio: se o cliente clicar duas vezes em "Finalizar",
        # o segundo POST reencontra o mesmo pedido em vez de criar outro.
        client_request_id=secrets.token_urlsafe(18),
    )


@public_bp.route("/carrinho/cupom", methods=["POST"])
def carrinho_cupom():
    if g.tenant is None:
        abort(404)

    if not tenant_libera(g.tenant, "cupons"):
        flash("Este restaurante não usa cupons de desconto.", "erro")
        return redirect(url_for("public.carrinho"))

    linhas = _carrinho_da_sessao()
    itens, subtotal, erro = _itens_calculados(linhas)
    if erro or not itens:
        flash("Adicione itens ao carrinho antes de aplicar um cupom.", "erro")
        return redirect(url_for("public.carrinho"))

    resultado = validar_cupom(g.tenant.id, request.form.get("cupom"), itens, subtotal)
    if resultado.ok:
        # Guarda só o código; o desconto é sempre recalculado.
        session[CHAVE_CUPOM] = resultado.codigo
        flash(f"Cupom {resultado.codigo} aplicado.", "sucesso")
    else:
        session.pop(CHAVE_CUPOM, None)
        flash(resultado.mensagem, "erro")
    return redirect(url_for("public.carrinho"))


@public_bp.route("/carrinho/cupom/remover", methods=["POST"])
def carrinho_cupom_remover():
    if g.tenant is None:
        abort(404)
    session.pop(CHAVE_CUPOM, None)
    flash("Cupom removido.", "sucesso")
    return redirect(url_for("public.carrinho"))


@public_bp.route("/carrinho/adicionar", methods=["POST"])
def carrinho_adicionar():
    if g.tenant is None:
        abort(404)

    try:
        produto_id = int(request.form.get("produto_id", ""))
    except ValueError:
        flash("Produto inválido.", "erro")
        return redirect(url_for("public.index"))

    produto = Produto.query.filter_by(id=produto_id, tenant_id=g.tenant.id, disponivel=True).first()
    if produto is None:
        flash("Este produto não está disponível.", "erro")
        return redirect(url_for("public.index"))

    try:
        quantidade = max(1, min(int(request.form.get("quantidade", "1")), 30))
    except ValueError:
        quantidade = 1

    # Guarda apenas ids e quantidade. Nenhum preço vem do formulário.
    linhas = list(_carrinho_da_sessao())
    if len(linhas) >= MAX_LINHAS_CARRINHO:
        flash("Seu carrinho está cheio.", "erro")
        return redirect(url_for("public.carrinho"))

    linhas.append(
        {
            "produto_id": produto.id,
            "quantidade": quantidade,
            "adicionais": [
                int(valor) for valor in request.form.getlist("adicionais") if valor.strip().isdigit()
            ],
            "observacao": (request.form.get("observacao") or "").strip()[:180],
        }
    )
    _salvar_carrinho(linhas)
    flash(f"{produto.nome} adicionado ao carrinho.", "sucesso")
    return redirect(url_for("public.index"))


@public_bp.route("/carrinho/remover", methods=["POST"])
def carrinho_remover():
    if g.tenant is None:
        abort(404)

    linhas = list(_carrinho_da_sessao())
    try:
        indice = int(request.form.get("indice", ""))
    except ValueError:
        indice = -1
    if 0 <= indice < len(linhas):
        linhas.pop(indice)
        _salvar_carrinho(linhas)
        flash("Item removido.", "sucesso")
    return redirect(url_for("public.carrinho"))


@public_bp.route("/pedido", methods=["POST"])
def pedido_criar():
    if g.tenant is None:
        abort(404)

    linhas = _carrinho_da_sessao()
    payload = {
        "cliente": request.form.get("cliente"),
        "telefone": request.form.get("telefone"),
        "tipo": request.form.get("tipo"),
        "endereco": request.form.get("endereco"),
        "bairro_id": request.form.get("bairro_id"),
        "pagamento": request.form.get("pagamento"),
        "observacao": request.form.get("observacao"),
        "client_request_id": request.form.get("client_request_id"),
        # O cupom vem da sessão, não do formulário: o cliente não pode injetar
        # um código diferente do que foi validado, nem um desconto.
        "cupom": session.get(CHAVE_CUPOM),
        "carrinho": linhas,
        "origem": "site",
    }

    try:
        pedido = criar_pedido(g.tenant, payload)
    except ValueError as exc:
        flash(str(exc), "erro")
        return redirect(url_for("public.carrinho"))

    _limpar_carrinho()
    return redirect(url_for("public.pedido_acompanhar", token=pedido.public_token))


@public_bp.route("/pedido/<token>")
def pedido_acompanhar(token: str):
    if g.tenant is None:
        abort(404)

    # O token é filtrado junto com o tenant: um token válido de outro
    # restaurante não abre nada neste subdomínio.
    pedido = Pedido.query.filter_by(public_token=token, tenant_id=g.tenant.id).first()
    if pedido is None:
        abort(404)
    return render_template("public/pedido.html", tenant=g.tenant, pedido=pedido)
