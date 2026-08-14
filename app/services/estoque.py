"""Estoque e custo: baixa dos insumos ao vender, e o lucro daquela venda.

Portado do inventory_service.py do sistema single-tenant, cuja lógica central é
boa e foi mantida: a saída de um pedido é tratada como "o quanto este pedido
consumiu no total", o que permite reajustar quando uma comanda de mesa cresce, e
o estorno é idempotente.

Três adaptações:

1. As necessidades do pedido saem de `PedidoItem`, não de um JSON dentro do
   pedido — o modelo daqui já guarda os itens em tabela.
2. Tudo é filtrado por tenant. A ficha técnica liga produto e insumo sem carregar
   tenant_id, então `definir_ficha` é a única barreira contra montar a receita de
   um produto com o insumo de outro restaurante.
3. A baixa acontece em QUALQUER avanço de status, não só em "Confirmado". O
   original só baixava ali, mas o fluxo permite Novo -> Em preparo direto: nesse
   atalho o pedido saía sem nunca consumir insumo, e o custo ficava zerado. É o
   mesmo defeito que existia no consumo de cupom.

Saldo pode ficar negativo, de propósito: significa que houve venda sem entrada
registrada. Bloquear a venda por falta de estoque seria pior — o pedido já
aconteceu, e o sistema não pode se recusar a registrar a realidade. O saldo
negativo aparece como alerta na tela de estoque.
"""

from __future__ import annotations

from collections import defaultdict

from flask import current_app

from ..extensions import db
from ..models.estoque import (
    MOV_ESTORNO,
    MOV_SAIDA,
    TIPOS_MOVIMENTACAO,
    TIPOS_QUE_SOMAM,
    FichaTecnica,
    Insumo,
    MovimentacaoEstoque,
)
from ..models.produto import Produto


def movimentar(
    insumo: Insumo,
    quantidade: float,
    tipo: str,
    *,
    pedido=None,
    usuario: str | None = None,
    observacao: str | None = None,
) -> MovimentacaoEstoque:
    """Aplica uma movimentação e devolve a linha do razão."""
    quantidade = float(quantidade or 0)
    if quantidade <= 0:
        raise ValueError("A quantidade precisa ser maior que zero.")
    if tipo not in TIPOS_MOVIMENTACAO:
        raise ValueError("Tipo de movimentação inválido.")

    antes = float(insumo.estoque_atual or 0)
    depois = antes + quantidade if tipo in TIPOS_QUE_SOMAM else antes - quantidade
    insumo.estoque_atual = round(depois, 4)

    movimentacao = MovimentacaoEstoque(
        tenant_id=insumo.tenant_id,
        insumo_id=insumo.id,
        pedido_id=pedido.id if pedido is not None else None,
        tipo=tipo,
        quantidade=quantidade,
        saldo_anterior=antes,
        saldo_posterior=insumo.estoque_atual,
        custo_unitario=insumo.custo_unitario,
        observacao=(observacao or "")[:250] or None,
        usuario=(usuario or "sistema")[:80],
    )
    db.session.add(movimentacao)
    return movimentacao


def definir_ficha(produto: Produto, linhas) -> None:
    """Substitui a ficha técnica de um produto.

    `linhas` é uma sequência de (insumo_id, quantidade). Insumo de outro tenant é
    ignorado: a tabela de ligação não carrega tenant_id, então este filtro é a
    única barreira. O filtro usa o tenant do próprio produto, nunca um id vindo
    do formulário.
    """
    desejadas: dict[int, float] = {}
    for insumo_id, quantidade in linhas or []:
        try:
            chave = int(insumo_id)
            valor = float(str(quantidade).replace(",", "."))
        except (TypeError, ValueError):
            continue
        if valor > 0:
            desejadas[chave] = valor

    if desejadas:
        validos = {
            insumo.id
            for insumo in Insumo.query.filter(
                Insumo.id.in_(desejadas), Insumo.tenant_id == produto.tenant_id
            )
        }
        desejadas = {k: v for k, v in desejadas.items() if k in validos}

    atuais = {linha.insumo_id: linha for linha in produto.ficha}
    for insumo_id, quantidade in desejadas.items():
        if insumo_id in atuais:
            atuais[insumo_id].quantidade_usada = quantidade
        else:
            produto.ficha.append(
                FichaTecnica(insumo_id=insumo_id, quantidade_usada=quantidade)
            )
    for insumo_id, linha in atuais.items():
        if insumo_id not in desejadas:
            produto.ficha.remove(linha)


def necessidades_do_pedido(pedido) -> dict[int, float]:
    """Quanto de cada insumo o pedido inteiro consome."""
    necessario: dict[int, float] = defaultdict(float)
    for item in pedido.itens:
        if not item.produto_id:
            # Produto excluído do cardápio: o item histórico continua valendo,
            # mas não há mais ficha técnica para consultar.
            continue
        quantidade = max(1, int(item.quantidade or 1))
        fichas = FichaTecnica.query.filter_by(produto_id=item.produto_id).all()
        for ficha in fichas:
            necessario[ficha.insumo_id] += float(ficha.quantidade_usada or 0) * quantidade
    return dict(necessario)


def _insumos_do_tenant(ids, tenant_id: int) -> dict[int, Insumo]:
    if not ids:
        return {}
    return {
        insumo.id: insumo
        for insumo in Insumo.query.filter(Insumo.id.in_(ids), Insumo.tenant_id == tenant_id)
    }


def _gravar_custo(pedido, custo: float) -> None:
    pedido.custo_produtos = round(custo, 2)
    # Lucro bruto compara o que os PRODUTOS renderam com o que custaram: a taxa de
    # entrega não é margem de cozinha, e o desconto sai da receita.
    pedido.lucro_bruto = round(
        float(pedido.subtotal or 0) - custo - float(pedido.desconto or 0), 2
    )


def aplicar_baixa(pedido, usuario: str | None = None) -> None:
    """Consome os insumos do pedido e grava custo e lucro. Idempotente."""
    if pedido.estoque_baixado:
        return

    necessario = necessidades_do_pedido(pedido)
    insumos = _insumos_do_tenant(necessario.keys(), pedido.tenant_id)

    custo = 0.0
    for insumo_id, quantidade in necessario.items():
        insumo = insumos.get(insumo_id)
        if insumo is None:
            continue
        custo += insumo.custo_unitario * quantidade
        if insumo.controle_estoque:
            movimentar(
                insumo,
                quantidade,
                MOV_SAIDA,
                pedido=pedido,
                usuario=usuario,
                observacao=f"Baixa do pedido #{pedido.numero}",
            )

    _gravar_custo(pedido, custo)
    pedido.estoque_baixado = True
    current_app.logger.info(
        "Estoque baixado: pedido #%s tenant=%s custo=%.2f", pedido.numero, pedido.tenant_id, custo
    )


def sincronizar_baixa(pedido, usuario: str | None = None) -> None:
    """Reajusta a baixa de um pedido que cresceu — o caso da comanda de mesa.

    A linha de saída existente é tratada como o consumo TOTAL do pedido: quando o
    garçom lança mais itens, ela cresce e só a diferença sai do estoque. Uma
    segunda saída para o mesmo par pedido/insumo esbarraria na trava
    uq_movimento_pedido_insumo_tipo.
    """
    if not pedido.estoque_baixado:
        aplicar_baixa(pedido, usuario)
        return

    existentes = {
        linha.insumo_id: linha
        for linha in MovimentacaoEstoque.query.filter_by(
            pedido_id=pedido.id, tipo=MOV_SAIDA
        ).all()
    }
    necessario = necessidades_do_pedido(pedido)
    insumos = _insumos_do_tenant(necessario.keys(), pedido.tenant_id)

    custo = 0.0
    for insumo_id, quantidade in necessario.items():
        insumo = insumos.get(insumo_id)
        if insumo is None:
            continue
        custo += insumo.custo_unitario * quantidade
        if not insumo.controle_estoque:
            continue

        linha = existentes.pop(insumo_id, None)
        if linha is None:
            # Insumo que não existia na comanda antes.
            movimentar(
                insumo,
                quantidade,
                MOV_SAIDA,
                pedido=pedido,
                usuario=usuario,
                observacao=f"Baixa do pedido #{pedido.numero}",
            )
            continue

        diferenca = quantidade - float(linha.quantidade or 0)
        if abs(diferenca) < 1e-9:
            continue
        insumo.estoque_atual = round(float(insumo.estoque_atual or 0) - diferenca, 4)
        linha.quantidade = quantidade
        linha.saldo_posterior = insumo.estoque_atual
        linha.custo_unitario = insumo.custo_unitario
        linha.observacao = f"Baixa do pedido #{pedido.numero} (comanda atualizada)"[:250]
        linha.usuario = (usuario or "sistema")[:80]

    _gravar_custo(pedido, custo)
    current_app.logger.info(
        "Estoque reajustado: pedido #%s custo=%.2f", pedido.numero, custo
    )


def estornar_baixa(pedido, usuario: str | None = None) -> None:
    """Devolve ao estoque o que um pedido cancelado havia consumido."""
    if not pedido.estoque_baixado:
        return

    saidas = MovimentacaoEstoque.query.filter_by(pedido_id=pedido.id, tipo=MOV_SAIDA).all()
    ja_estornados = {
        linha.insumo_id
        for linha in MovimentacaoEstoque.query.filter_by(
            pedido_id=pedido.id, tipo=MOV_ESTORNO
        ).all()
    }

    for saida in saidas:
        if saida.insumo_id in ja_estornados:
            continue
        insumo = db.session.get(Insumo, saida.insumo_id)
        if insumo is None or insumo.tenant_id != pedido.tenant_id:
            continue
        movimentar(
            insumo,
            saida.quantidade,
            MOV_ESTORNO,
            pedido=pedido,
            usuario=usuario,
            observacao=f"Estorno do pedido cancelado #{pedido.numero}",
        )

    pedido.estoque_baixado = False
    current_app.logger.info("Estoque estornado: pedido #%s", pedido.numero)


def insumos_em_alerta(tenant_id: int) -> list[Insumo]:
    """Insumos no mínimo ou negativos, do mais crítico para o menos."""
    controlados = (
        Insumo.query.filter_by(tenant_id=tenant_id, controle_estoque=True)
        .order_by(Insumo.nome)
        .all()
    )
    alertas = [insumo for insumo in controlados if insumo.abaixo_do_minimo]
    return sorted(alertas, key=lambda i: float(i.estoque_atual or 0))


def historico(tenant_id: int, insumo_id: int | None = None, limite: int = 100):
    consulta = MovimentacaoEstoque.query.filter_by(tenant_id=tenant_id)
    if insumo_id:
        consulta = consulta.filter_by(insumo_id=insumo_id)
    return consulta.order_by(MovimentacaoEstoque.created_at.desc(), MovimentacaoEstoque.id.desc()).limit(limite).all()
