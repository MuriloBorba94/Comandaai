"""Relatórios de venda de um restaurante.

Nenhum modelo novo: tudo sai de Pedido e PedidoItem, que já guardam nome e preço
congelados na venda. Isso importa aqui — produto renomeado ou excluído do
cardápio não distorce nem apaga o histórico.

Duas decisões que mudam o número na tela:

1. **Faturado é só o que foi entregue.** No meio do turno a maioria dos pedidos
   ainda está em preparo, então "em andamento" aparece separado. Um número só
   ficaria enganosamente baixo às 20h de um sábado cheio.
2. **Cancelado aparece.** É informação de operação: muito cancelamento é sinal
   de problema, e esconder isso numa soma de faturamento perderia o sinal.

Limitação conhecida: os períodos usam a data/hora do servidor, e o campo
`Tenant.timezone` não é considerado. Como todo pedido é gravado com o horário
local do servidor, a conta é coerente; se um dia houver tenants em fusos
diferentes, isto precisa ser revisto junto com a gravação em UTC.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import func

from ..extensions import db
from ..models.pedido import (
    STATUS_ATIVOS,
    STATUS_CANCELADO,
    STATUS_ENTREGUE,
    TIPOS,
    Pedido,
    PedidoItem,
)

# Períodos oferecidos na quebra por pagamento/tipo e nos mais vendidos.
PERIODOS = (7, 30, 90)


def _intervalo_do_dia(dia: date) -> tuple[datetime, datetime]:
    inicio = datetime.combine(dia, time.min)
    return inicio, inicio + timedelta(days=1)


def _filtro(tenant_id: int, inicio: datetime, fim: datetime):
    return (Pedido.tenant_id == tenant_id, Pedido.created_at >= inicio, Pedido.created_at < fim)


def _soma(tenant_id: int, inicio: datetime, fim: datetime, statuses) -> tuple[int, float]:
    """Quantidade e valor total dos pedidos nos status pedidos."""
    quantidade, total = (
        db.session.query(func.count(Pedido.id), func.coalesce(func.sum(Pedido.total), 0.0))
        .filter(*_filtro(tenant_id, inicio, fim), Pedido.status.in_(statuses))
        .one()
    )
    return int(quantidade or 0), round(float(total or 0.0), 2)


def resumo_do_periodo(tenant_id: int, inicio: datetime, fim: datetime) -> dict:
    """Faturado, em andamento e cancelado de um intervalo."""
    faturado_qtd, faturado_valor = _soma(tenant_id, inicio, fim, [STATUS_ENTREGUE])
    andamento_qtd, andamento_valor = _soma(tenant_id, inicio, fim, list(STATUS_ATIVOS))
    cancelado_qtd, cancelado_valor = _soma(tenant_id, inicio, fim, [STATUS_CANCELADO])

    return {
        "faturado_qtd": faturado_qtd,
        "faturado": faturado_valor,
        "andamento_qtd": andamento_qtd,
        "andamento": andamento_valor,
        "cancelado_qtd": cancelado_qtd,
        "cancelado": cancelado_valor,
        # Ticket médio só sobre o que fechou: incluir pedido em aberto daria uma
        # média que muda para baixo a cada pedido novo.
        "ticket_medio": round(faturado_valor / faturado_qtd, 2) if faturado_qtd else 0.0,
    }


def variacao(atual: float, anterior: float) -> float | None:
    """Variação percentual entre dois períodos. None quando não há base."""
    if not anterior:
        return None
    return round((atual - anterior) / anterior * 100, 1)


def mais_vendidos(tenant_id: int, inicio: datetime, fim: datetime, limite: int = 10) -> list[dict]:
    """Itens mais vendidos, agrupados pelo nome gravado na venda.

    Agrupar pelo nome congelado (e não pelo produto_id) mantém no relatório o
    que foi vendido de produto já excluído do cardápio.
    """
    linhas = (
        db.session.query(
            PedidoItem.nome,
            func.sum(PedidoItem.quantidade).label("unidades"),
            func.sum(PedidoItem.total).label("valor"),
        )
        .join(Pedido, Pedido.id == PedidoItem.pedido_id)
        .filter(*_filtro(tenant_id, inicio, fim), Pedido.status != STATUS_CANCELADO)
        .group_by(PedidoItem.nome)
        .order_by(func.sum(PedidoItem.quantidade).desc(), PedidoItem.nome)
        .limit(limite)
        .all()
    )
    return [
        {"nome": nome, "unidades": int(unidades or 0), "valor": round(float(valor or 0.0), 2)}
        for nome, unidades, valor in linhas
    ]


def _quebra_por(coluna, tenant_id: int, inicio: datetime, fim: datetime) -> list[dict]:
    linhas = (
        db.session.query(
            coluna,
            func.count(Pedido.id),
            func.coalesce(func.sum(Pedido.total), 0.0),
        )
        .filter(*_filtro(tenant_id, inicio, fim), Pedido.status != STATUS_CANCELADO)
        .group_by(coluna)
        .order_by(func.coalesce(func.sum(Pedido.total), 0.0).desc())
        .all()
    )
    return [
        {"rotulo": rotulo or "—", "pedidos": int(qtd or 0), "valor": round(float(valor or 0.0), 2)}
        for rotulo, qtd, valor in linhas
    ]


def por_forma_de_pagamento(tenant_id: int, inicio: datetime, fim: datetime) -> list[dict]:
    return _quebra_por(Pedido.pagamento, tenant_id, inicio, fim)


def por_tipo(tenant_id: int, inicio: datetime, fim: datetime) -> list[dict]:
    return _quebra_por(Pedido.tipo, tenant_id, inicio, fim)


def vendas_por_dia(tenant_id: int, dias: int, hoje: date | None = None) -> list[dict]:
    """Faturamento de cada um dos últimos dias, para ver a tendência."""
    hoje = hoje or date.today()
    serie = []
    for recuo in range(dias - 1, -1, -1):
        dia = hoje - timedelta(days=recuo)
        inicio, fim = _intervalo_do_dia(dia)
        quantidade, total = _soma(tenant_id, inicio, fim, [STATUS_ENTREGUE])
        serie.append({"dia": dia, "pedidos": quantidade, "valor": total})
    return serie


def historico(
    tenant_id: int,
    inicio: date | None = None,
    fim: date | None = None,
    limite: int = 2000,
) -> list:
    """Pedidos do período, do mais recente para o mais antigo.

    É o "Relatório de Vendas Histórico" da Gestão original: a lista crua dos
    pedidos, e não um agregado. Serve para conferir uma venda específica, achar
    o pedido de um cliente e exportar o período para a contabilidade.

    Sem filtro de data, devolve os últimos pedidos em vez da tabela inteira —
    um restaurante com dois anos de operação tem dezenas de milhares de linhas,
    e nenhuma tela precisa carregar isso para mostrar as últimas vendas.
    """
    from ..models.pedido import Pedido

    consulta = Pedido.query.filter_by(tenant_id=tenant_id)
    if inicio is not None:
        consulta = consulta.filter(Pedido.created_at >= datetime.combine(inicio, time.min))
    if fim is not None:
        # Intervalo semiaberto até o dia seguinte: com `<= fim` os pedidos do
        # próprio dia final ficariam de fora por causa da hora.
        consulta = consulta.filter(
            Pedido.created_at < datetime.combine(fim + timedelta(days=1), time.min)
        )

    if inicio is None and fim is None:
        limite = min(limite, 250)

    return consulta.order_by(Pedido.created_at.desc()).limit(limite).all()


def totais_do_historico(pedidos: list) -> dict:
    """Soma da lista que está na tela, para o rodapé do relatório.

    Calculado sobre os pedidos já carregados, e não com uma consulta nova: o que
    o rodapé mostra tem que ser a soma exata do que está sendo exibido, senão a
    tela se contradiz quando o limite corta a lista.
    """
    from ..models.pedido import STATUS_CANCELADO

    validos = [pedido for pedido in pedidos if pedido.status != STATUS_CANCELADO]
    faturado = sum(float(pedido.total or 0) for pedido in validos)
    return {
        "quantidade": len(pedidos),
        "validos": len(validos),
        "cancelados": len(pedidos) - len(validos),
        "faturado": round(faturado, 2),
        "ticket": round(faturado / len(validos), 2) if validos else 0.0,
    }


def painel(tenant_id: int, dias: int = 7, hoje: date | None = None) -> dict:
    """Tudo que a tela de relatórios mostra, numa chamada."""
    hoje = hoje or date.today()
    if dias not in PERIODOS:
        dias = PERIODOS[0]

    inicio_hoje, fim_hoje = _intervalo_do_dia(hoje)
    inicio_ontem, fim_ontem = _intervalo_do_dia(hoje - timedelta(days=1))

    # Semana atual = últimos 7 dias contando hoje; a anterior são os 7 antes
    # dela, para o comparativo ser entre janelas de mesmo tamanho.
    inicio_semana = datetime.combine(hoje - timedelta(days=6), time.min)
    inicio_semana_anterior = inicio_semana - timedelta(days=7)

    inicio_mes = datetime.combine(hoje.replace(day=1), time.min)
    inicio_periodo = datetime.combine(hoje - timedelta(days=dias - 1), time.min)

    semana = resumo_do_periodo(tenant_id, inicio_semana, fim_hoje)
    semana_anterior = resumo_do_periodo(tenant_id, inicio_semana_anterior, inicio_semana)

    return {
        "hoje": resumo_do_periodo(tenant_id, inicio_hoje, fim_hoje),
        "ontem": resumo_do_periodo(tenant_id, inicio_ontem, fim_ontem),
        "semana": semana,
        "semana_anterior": semana_anterior,
        "variacao_semana": variacao(semana["faturado"], semana_anterior["faturado"]),
        "mes": resumo_do_periodo(tenant_id, inicio_mes, fim_hoje),
        "dias": dias,
        "mais_vendidos": mais_vendidos(tenant_id, inicio_periodo, fim_hoje),
        "por_pagamento": por_forma_de_pagamento(tenant_id, inicio_periodo, fim_hoje),
        "por_tipo": por_tipo(tenant_id, inicio_periodo, fim_hoje),
        "serie": vendas_por_dia(tenant_id, min(dias, 30), hoje),
        "periodos": PERIODOS,
        "tipos": TIPOS,
    }
