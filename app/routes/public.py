from __future__ import annotations

import secrets

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..extensions import limiter
from ..models.assinatura import LIMITES
from ..models.categoria import Categoria
from ..models.pedido import Pedido, TIPO_ENTREGA, TIPO_RETIRADA
from ..models.produto import Produto
from ..services.cupons import validar_cupom
from ..services.recursos import tenant_libera
from ..services.pedidos import (
    FORMAS_PAGAMENTO,
    formas_de_pagamento,
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


def _dados_da_landing() -> dict:
    """Conteúdo da página inicial do produto.

    Os planos saem do catálogo real, com o preço e os recursos que cada um
    libera de fato. É o que evita a página de vendas prometer o que o sistema
    não entrega — foi assim que uma descrição de plano já anunciou "relatórios"
    quando a tela ainda não existia.
    """
    from ..models.assinatura import RECURSOS, Plano

    rotulos = {slug: (rotulo, explicacao) for slug, rotulo, explicacao in RECURSOS}
    planos = []
    for plano in Plano.query.filter_by(ativo=True).order_by(Plano.ordem, Plano.preco_mensal).all():
        liberados = plano.recursos_liberados
        planos.append(
            {
                "nome": plano.nome,
                "preco": plano.preco_mensal or 0.0,
                "descricao": plano.descricao,
                "gratuito": plano.gratuito,
                # Na ordem do catálogo, não na ordem que o plano gravou: assim as
                # colunas ficam alinhadas quando estão lado a lado.
                "inclui": [rotulos[slug][0] for slug, _, _ in RECURSOS if slug in liberados],
                "limites": [
                    f"até {teto} {rotulo.lower()}"
                    for teto, rotulo in (
                        (plano.limite(chave), rotulo) for chave, rotulo, _ in LIMITES
                    )
                    if teto
                ],
            }
        )

    # Só os planos que se contrata. O plano gratuito continua existindo no
    # catálogo — é ele que sustenta o período de teste —, mas anunciá-lo na
    # página faz o visitante escolher o grátis e nunca conversar com ninguém.
    # O teste passou a ser liberado no contato, e é por isso que ele sai daqui.
    return {
        "planos": [plano for plano in planos if not plano["gratuito"]],
        "recursos": RECURSOS,
    }


@public_bp.route("/")
def index():
    if g.tenant is None:
        return render_template("public/landing.html", **_dados_da_landing())

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

    from ..services.caixa import loja_esta_aberta
    from ..services.pedidos import calcular_estimativa

    # Os dois prazos saem de calcular_estimativa, e não dos campos crus, porque
    # é ela que soma a fila do momento. O banner prometendo 40 min enquanto a
    # tela do pedido diz 70 é a mesma loja se contradizendo na mesma visita.
    return render_template(
        "public/index.html",
        tenant=g.tenant,
        grupos=grupos,
        loja_aberta=loja_esta_aberta(g.tenant),
        prazo_entrega=calcular_estimativa(g.tenant, TIPO_ENTREGA),
        prazo_retirada=calcular_estimativa(g.tenant, TIPO_RETIRADA),
        qtd_carrinho=sum(int(linha.get("quantidade", 1)) for linha in _carrinho_da_sessao()),
        # Adicionais vão por produto: no original a tela oferecia a lista inteira
        # e o servidor recusava depois. Aqui só aparece o que o produto aceita.
        extras_por_produto={
            produto.id: [
                {"id": extra.id, "nome": extra.nome, "preco": float(extra.preco or 0)}
                for extra in sorted(produto.adicionais, key=lambda a: a.nome)
                if extra.disponivel
            ]
            for produto in produtos
        },
    )


@public_bp.route("/interesse", methods=["POST"])
@limiter.limit("8 per hour; 30 per day")
def interesse():
    """Contato deixado na página do produto, ao escolher um plano.

    Só existe na página do PRODUTO. Num subdomínio de restaurante o endereço
    não responde: ali quem visita é cliente de lanche, e um formulário de
    vendas da plataforma não teria o que fazer no meio do cardápio.

    Responde JSON porque o envio é por fetch — a pessoa acabou de abrir uma
    janelinha sobre a página, e recarregar tudo para dizer "recebido" a
    devolveria ao topo, longe do plano que ela estava olhando.
    """
    if g.tenant is not None:
        abort(404)

    from ..services.interesses import registrar as registrar_interesse

    try:
        contato = registrar_interesse(request.form, ip=request.remote_addr)
    except ValueError as erro:
        return jsonify(status="erro", mensagem=str(erro)), 400

    current_app.logger.info(
        "Interesse recebido: %s · plano=%s", contato.nome, contato.plano or "—"
    )
    return jsonify(status="ok", mensagem="Recebido! Retorno em breve pelo WhatsApp.")


@public_bp.route("/saude")
def saude():
    """"Está de pé?" — para um monitor externo perguntar de minuto em minuto.

    A resposta é curta de propósito. Um endereço aberto que contasse qual é o
    banco, quanto disco resta e quantos clientes existem ajudaria quem quer
    atacar e não ajudaria mais ninguém. O quadro completo fica na área da
    plataforma, atrás de login.

    503 significa "não adianta mandar cliente para cá": banco fora do ar ou
    publicação pela metade. Fila de impressão parada e disco em 80% são avisos e
    NÃO derrubam esta resposta — alarme que dispara por qualquer coisa é alarme
    que as pessoas aprendem a ignorar.
    """
    from ..services.saude import checar

    resultado = checar()
    return (
        jsonify(status="ok" if resultado["ok"] else "erro"),
        200 if resultado["ok"] else 503,
    )


@public_bp.route("/carrinho")
def carrinho():
    if g.tenant is None:
        abort(404)

    from ..services.caixa import loja_esta_aberta

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
        loja_aberta=loja_esta_aberta(g.tenant),
        itens=itens,
        subtotal=subtotal,
        desconto=desconto,
        total_sem_entrega=max(0.0, round(subtotal - desconto, 2)),
        codigo_cupom=codigo_cupom,
        aviso_cupom=aviso_cupom,
        usa_cupons=usa_cupons,
        bairros=bairros_ativos(g.tenant.id) if tenant_libera(g.tenant, "bairros") else [],
        formas_pagamento=formas_de_pagamento(g.tenant),
        tipos=(TIPO_ENTREGA, TIPO_RETIRADA),
        # Identificador do envio: se o cliente clicar duas vezes em "Finalizar",
        # o segundo POST reencontra o mesmo pedido em vez de criar outro.
        client_request_id=secrets.token_urlsafe(18),
        # O mesmo limite que o servidor aplica, para a tela não dizer
        # "compartilhado" sobre um ponto que a rota vai descartar depois.
        precisao_maxima=Pedido.PRECISAO_MAXIMA_M,
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


def _quer_json() -> bool:
    """A vitrine em modal fala JSON; o formulário sem script continua em HTML.

    As duas usam as MESMAS rotas de propósito: assim o carrinho tem um caminho
    só no servidor, e quem estiver sem JavaScript ainda consegue pedir.
    """
    return request.headers.get("X-Requested-With") == "fetch"


def _carrinho_json(mensagem: str | None = None, erro: str | None = None, status: int = 200):
    """Estado atual do carrinho, já com preços calculados pelo servidor."""
    linhas = _carrinho_da_sessao()
    itens, subtotal, falha = _itens_calculados(linhas)
    return (
        jsonify(
            status="erro" if erro else "ok",
            mensagem=erro or mensagem,
            aviso=falha,
            quantidade=sum(item.quantidade for item in itens),
            subtotal=round(subtotal, 2),
            itens=[
                {
                    "indice": indice,
                    "nome": item.nome,
                    "quantidade": item.quantidade,
                    "total": round(item.total, 2),
                    "detalhes": " · ".join(
                        parte
                        for parte in (
                            ("+ " + ", ".join(extra.nome for extra in item.adicionais))
                            if item.adicionais
                            else "",
                            f"obs.: {item.observacao}" if item.observacao else "",
                        )
                        if parte
                    ),
                }
                for indice, item in enumerate(itens)
            ],
        ),
        status,
    )


@public_bp.route("/carrinho.json")
def carrinho_json():
    if g.tenant is None:
        abort(404)
    return _carrinho_json()


@public_bp.route("/carrinho/adicionar", methods=["POST"])
def carrinho_adicionar():
    if g.tenant is None:
        abort(404)

    try:
        produto_id = int(request.form.get("produto_id", ""))
    except ValueError:
        if _quer_json():
            return _carrinho_json(erro="Produto inválido.", status=400)
        flash("Produto inválido.", "erro")
        return redirect(url_for("public.index"))

    produto = Produto.query.filter_by(id=produto_id, tenant_id=g.tenant.id, disponivel=True).first()
    if produto is None:
        if _quer_json():
            return _carrinho_json(erro="Este produto não está disponível.", status=400)
        flash("Este produto não está disponível.", "erro")
        return redirect(url_for("public.index"))

    try:
        quantidade = max(1, min(int(request.form.get("quantidade", "1")), 30))
    except ValueError:
        quantidade = 1

    # Guarda apenas ids e quantidade. Nenhum preço vem do formulário.
    linhas = list(_carrinho_da_sessao())
    if len(linhas) >= MAX_LINHAS_CARRINHO:
        if _quer_json():
            return _carrinho_json(erro="Seu carrinho está cheio.", status=400)
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
    if _quer_json():
        return _carrinho_json(mensagem=f"{produto.nome} adicionado.")
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
        if _quer_json():
            return _carrinho_json(mensagem="Item removido.")
        flash("Item removido.", "sucesso")
    elif _quer_json():
        return _carrinho_json(erro="Item não encontrado no carrinho.", status=400)
    return redirect(url_for("public.carrinho"))


@public_bp.route("/pedido", methods=["POST"])
def pedido_criar():
    if g.tenant is None:
        abort(404)

    # A trava fica aqui, e não em criar_pedido(), porque quem fecha a loja fecha
    # o CARDÁPIO. O atendente continua lançando comanda de mesa e pedido de
    # balcão com a porta fechada — é justamente assim que se termina a noite,
    # atendendo quem já está dentro sem receber pedido novo pela internet.
    #
    # Sem esta linha a tarja "Fechado no momento" seria enfeite: o cliente
    # leria "fechado", pediria assim mesmo, e o pedido cairia na cozinha.
    from ..services.caixa import loja_esta_aberta

    if not loja_esta_aberta(g.tenant):
        flash("A loja está fechada no momento e não está recebendo pedidos.", "erro")
        return redirect(url_for("public.carrinho"))

    linhas = _carrinho_da_sessao()
    payload = {
        "cliente": request.form.get("cliente"),
        "telefone": request.form.get("telefone"),
        "tipo": request.form.get("tipo"),
        "endereco": request.form.get("endereco"),
        "bairro_id": request.form.get("bairro_id"),
        # Opcionais, e sem validação aqui: quem julga a coordenada é
        # criar_pedido(), que descarta em silêncio o que não serve. Um ponto
        # estranho não pode derrubar o pedido — o endereço escrito é que leva a
        # comida ao lugar.
        "cliente_lat": request.form.get("cliente_lat"),
        "cliente_lng": request.form.get("cliente_lng"),
        "cliente_local_precisao": request.form.get("cliente_local_precisao"),
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

    pagamento = pedido.pagamento_online
    # O QR só é desenhado enquanto há o que pagar. Depois de pago ele some da
    # tela: código de pagamento visível num pedido já quitado é convite para o
    # cliente pagar duas vezes.
    qr = None
    if pagamento is not None and pagamento.status == "aguardando" and pagamento.brcode:
        from ..services.pagamentos.qr import svg

        qr = svg(pagamento.brcode)

    return render_template(
        "public/pedido.html", tenant=g.tenant, pedido=pedido, pagamento=pagamento, qr=qr
    )


@public_bp.route("/pedido/<token>/rastreio.json")
def pedido_rastreio_json(token: str):
    """Onde o pedido está agora, para o mapa da tela de acompanhamento.

    Devolve posição só enquanto o pedido está a caminho E a leitura é recente
    (ver `Pedido.rastreavel`). Ponto parado por causa de celular que perdeu
    sinal faz o cliente concluir que o entregador empacou — melhor dizer que
    não há posição do que mostrar uma velha.
    """
    if g.tenant is None:
        abort(404)

    pedido = Pedido.query.filter_by(public_token=token, tenant_id=g.tenant.id).first()
    if pedido is None:
        abort(404)

    if not pedido.rastreavel:
        return jsonify(status=pedido.status, lat=None, lng=None)
    return jsonify(
        status=pedido.status,
        lat=pedido.entrega_lat,
        lng=pedido.entrega_lng,
    )


@public_bp.route("/pedido/<token>/pagamento.json")
def pedido_pagamento_json(token: str):
    """Diz se o pagamento já foi confirmado, para a tela se atualizar sozinha.

    Resposta minúscula de propósito: enquanto espera o PIX, a página pergunta de
    poucos em poucos segundos, e recarregar tudo a cada consulta seria desperdício
    do lado do cliente e do servidor.
    """
    if g.tenant is None:
        abort(404)

    pedido = Pedido.query.filter_by(public_token=token, tenant_id=g.tenant.id).first()
    if pedido is None:
        abort(404)

    pagamento = pedido.pagamento_online
    if pagamento is None:
        return jsonify(pago=False, status=None, pedido_status=pedido.status)

    return jsonify(
        pago=pagamento.status == "pago",
        status=pagamento.status,
        pedido_status=pedido.status,
    )
