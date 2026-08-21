"""A tela de quem leva o pedido até a porta.

Uma lição do sistema antigo está no desenho desta tela. Lá o rastreio existia,
foi usado em três dias seguidos e depois praticamente parou: de 774 entregas,
só 37 registraram posição, e nenhuma depois de 16/08. A causa provável é que a
página do entregador **só servia ao cliente** — alguém tinha que deixar uma tela
aberta no celular em benefício de outra pessoa, e isso não sobrevive a um sábado
cheio.

Aqui a tela é a **ferramenta de trabalho do entregador**: as entregas dele, o
endereço, o botão que abre a rota no mapa do celular e a baixa quando entrega. A
posição vai junto porque a tela já está aberta — ela é consequência do trabalho,
não uma tarefa a mais. Se essa aposta estiver errada, o rastreio volta a morrer,
e aí a resposta é tirar o rastreio, não insistir.

Sobre a rota: o endereço digitado é entregue ao aplicativo de mapa do próprio
celular, sem geocodificação nossa. Testei os cinco bairros que o Borba's atende
num geocodificador gratuito e nenhum foi encontrado — endereço de cidade pequena
é descrito por referência, não por rua e número mapeáveis. Quem sabe ler "perto
da igreja" é uma pessoa, e o app dela.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

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
from ..extensions import db
from ..models.pedido import (
    STATUS_ENTREGUE,
    STATUS_PRONTO,
    STATUS_SAIU_ENTREGA,
    TIPO_ENTREGA,
    Pedido,
)
from ..services.pedidos import transicionar

entregas_bp = Blueprint("entregas", __name__, url_prefix="/entregas")


def url_da_rota(pedido: Pedido) -> str:
    """Link que abre o mapa do celular já com o destino preenchido.

    `api=1` é o formato universal do Google Maps: funciona no aplicativo quando
    ele está instalado e no navegador quando não está, sem precisar detectar o
    aparelho.
    """
    partes = [pedido.endereco or "", pedido.bairro_nome or "", g.tenant.pix_cidade or ""]
    destino = ", ".join(parte.strip() for parte in partes if parte and parte.strip())
    if not destino:
        return ""
    return f"https://www.google.com/maps/dir/?api=1&destination={quote(destino)}"


def _minhas_entregas() -> list[Pedido]:
    """O que está comigo agora, mais o que está pronto esperando alguém pegar."""
    usuario_id = session.get("usuario_id")
    return (
        Pedido.query.filter(
            Pedido.tenant_id == g.tenant.id,
            Pedido.tipo == TIPO_ENTREGA,
            db.or_(
                db.and_(
                    Pedido.status == STATUS_SAIU_ENTREGA,
                    Pedido.entregador_id == usuario_id,
                ),
                Pedido.status == STATUS_PRONTO,
            ),
        )
        .order_by(Pedido.status.desc(), Pedido.id)
        .all()
    )


@entregas_bp.route("/")
@login_required
def lista():
    """A tela que o entregador deixa aberta enquanto trabalha.

    Aberta a qualquer usuário logado, e não só ao papel `entregador`: em
    restaurante pequeno quem entrega costuma ser o dono ou o atendente, e exigir
    um usuário separado para isso faria a tela não ser usada.
    """
    pedidos = _minhas_entregas()
    return render_template(
        "entregas/lista.html",
        tenant=g.tenant,
        pedidos=pedidos,
        rotas={pedido.id: url_da_rota(pedido) for pedido in pedidos},
        comigo=session.get("usuario_id"),
    )


@entregas_bp.post("/<int:pedido_id>/assumir")
@login_required
def assumir(pedido_id: int):
    """Pega a entrega: marca "saiu para entrega" e põe o pedido no meu nome."""
    pedido = Pedido.query.filter_by(
        id=pedido_id, tenant_id=g.tenant.id, tipo=TIPO_ENTREGA
    ).first()
    if pedido is None:
        flash("Entrega não encontrada.", "erro")
        return redirect(url_for("entregas.lista"))

    pedido.entregador_id = session.get("usuario_id")
    try:
        transicionar(pedido, STATUS_SAIU_ENTREGA, actor=session.get("username"))
        flash(f"Pedido #{pedido.numero} é seu. Boa entrega!", "sucesso")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "erro")
    return redirect(url_for("entregas.lista"))


@entregas_bp.post("/<int:pedido_id>/entregue")
@login_required
def entregue(pedido_id: int):
    pedido = Pedido.query.filter_by(
        id=pedido_id, tenant_id=g.tenant.id, tipo=TIPO_ENTREGA
    ).first()
    if pedido is None:
        flash("Entrega não encontrada.", "erro")
        return redirect(url_for("entregas.lista"))

    try:
        transicionar(pedido, STATUS_ENTREGUE, actor=session.get("username"))
        # A posição para de fazer sentido assim que a entrega acaba, e guardar
        # onde o entregador estava depois disso seria rastrear a pessoa, não o
        # pedido.
        pedido.entrega_lat = None
        pedido.entrega_lng = None
        pedido.entrega_atualizado_em = None
        db.session.commit()
        flash(f"Pedido #{pedido.numero} entregue.", "sucesso")
    except ValueError as exc:
        flash(str(exc), "erro")
    return redirect(url_for("entregas.lista"))


@entregas_bp.post("/posicao")
@login_required
def posicao():
    """Recebe onde o entregador está e repassa às entregas que estão com ele.

    Não guarda trajeto: cada envio sobrescreve o anterior. O cliente precisa
    saber onde o pedido dele está agora; o caminho percorrido pelo entregador
    não é assunto de ninguém.
    """
    dados = request.get_json(silent=True) or {}
    try:
        lat = float(dados.get("lat"))
        lng = float(dados.get("lng"))
    except (TypeError, ValueError):
        return jsonify(status="erro", mensagem="Localização inválida."), 400
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return jsonify(status="erro", mensagem="Localização fora do mundo."), 400

    agora = datetime.now()
    quantos = Pedido.query.filter_by(
        tenant_id=g.tenant.id,
        entregador_id=session.get("usuario_id"),
        status=STATUS_SAIU_ENTREGA,
    ).update(
        {"entrega_lat": lat, "entrega_lng": lng, "entrega_atualizado_em": agora},
        synchronize_session=False,
    )
    db.session.commit()
    return jsonify(status="ok", pedidos=quantos)
