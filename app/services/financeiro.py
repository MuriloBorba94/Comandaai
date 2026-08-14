"""Resultado do restaurante: receita, custo dos produtos, despesas e lucro.

Portado do finance_service.py do sistema single-tenant, com a mesma estrutura de
resumo. Duas diferenças que valem registrar:

1. **CMV e compra de insumo não se somam.** O original permitia lançar a compra
   de insumos como despesa, e o custo desses mesmos insumos já entrava pelo CMV —
   o mesmo dinheiro contado duas vezes, derrubando o lucro. Aqui não existe
   categoria de despesa para compra de insumo: ela é ENTRADA de estoque.
2. **Faturado é só pedido entregue**, igual aos relatórios de venda. Se as duas
   telas usassem definições diferentes, elas se contradiriam — o que é pior que
   qualquer uma das escolhas.

O CMV vem de `Pedido.custo_produtos`, gravado na baixa de estoque. Pedido de um
produto sem ficha técnica entra com custo zero, e nesse caso o lucro mostrado é
só a receita — está certo, mas significa "não sei o custo", não "custo zero".
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import func

from ..extensions import db
from ..models.financeiro import Despesa, ReceitaAvulsa
from ..models.pedido import STATUS_ENTREGUE, Pedido


def _intervalo(inicio: date, fim: date) -> tuple[datetime, datetime]:
    """Intervalo semiaberto que cobre os dois dias inteiros."""
    return datetime.combine(inicio, time.min), datetime.combine(fim + timedelta(days=1), time.min)


def resumo(tenant_id: int, inicio: date, fim: date) -> dict:
    """Resultado do período: da receita ao lucro líquido."""
    desde, ate = _intervalo(inicio, fim)

    pedidos, receita_pedidos, cmv, lucro_de_pedidos, descontos, taxas = (
        db.session.query(
            func.count(Pedido.id),
            func.coalesce(func.sum(Pedido.total), 0.0),
            func.coalesce(func.sum(Pedido.custo_produtos), 0.0),
            func.coalesce(func.sum(Pedido.lucro_bruto), 0.0),
            func.coalesce(func.sum(Pedido.desconto), 0.0),
            func.coalesce(func.sum(Pedido.taxa_entrega), 0.0),
        )
        .filter(
            Pedido.tenant_id == tenant_id,
            Pedido.status == STATUS_ENTREGUE,
            Pedido.created_at >= desde,
            Pedido.created_at < ate,
        )
        .one()
    )

    receita_avulsa = float(
        db.session.query(func.coalesce(func.sum(ReceitaAvulsa.valor), 0.0))
        .filter(
            ReceitaAvulsa.tenant_id == tenant_id,
            ReceitaAvulsa.data_registro >= inicio,
            ReceitaAvulsa.data_registro <= fim,
        )
        .scalar()
        or 0.0
    )

    despesas = (
        Despesa.query.filter(
            Despesa.tenant_id == tenant_id,
            Despesa.data_vencimento >= inicio,
            Despesa.data_vencimento <= fim,
        ).all()
    )
    total_despesas = sum(float(d.valor or 0) for d in despesas)
    pagas = sum(float(d.valor or 0) for d in despesas if d.paga)

    receita = float(receita_pedidos or 0) + receita_avulsa
    # A taxa de entrega e a receita avulsa entram inteiras no lucro bruto: não
    # têm CMV associado.
    lucro_bruto = float(lucro_de_pedidos or 0) + receita_avulsa + float(taxas or 0)
    lucro_liquido = lucro_bruto - total_despesas

    return {
        "pedidos": int(pedidos or 0),
        "receita_pedidos": round(float(receita_pedidos or 0), 2),
        "receita_avulsa": round(receita_avulsa, 2),
        "receita": round(receita, 2),
        "cmv": round(float(cmv or 0), 2),
        "lucro_bruto": round(lucro_bruto, 2),
        "despesas": round(total_despesas, 2),
        "despesas_pagas": round(pagas, 2),
        "despesas_pendentes": round(total_despesas - pagas, 2),
        "lucro_liquido": round(lucro_liquido, 2),
        "margem_liquida": round(lucro_liquido / receita * 100, 1) if receita else 0.0,
        "margem_bruta": round(lucro_bruto / receita * 100, 1) if receita else 0.0,
        "ticket": round(receita / int(pedidos), 2) if pedidos else 0.0,
        "descontos": round(float(descontos or 0), 2),
        "taxas_entrega": round(float(taxas or 0), 2),
        # Quanto da receita ainda não tem custo conhecido: pedido de produto sem
        # ficha técnica entra com custo zero, e o lucro sai otimista.
        "pedidos_sem_custo": Pedido.query.filter(
            Pedido.tenant_id == tenant_id,
            Pedido.status == STATUS_ENTREGUE,
            Pedido.created_at >= desde,
            Pedido.created_at < ate,
            Pedido.custo_produtos == 0,
        ).count(),
    }


def despesas_por_categoria(tenant_id: int, inicio: date, fim: date) -> list[dict]:
    linhas = (
        db.session.query(
            Despesa.categoria,
            func.count(Despesa.id),
            func.coalesce(func.sum(Despesa.valor), 0.0),
        )
        .filter(
            Despesa.tenant_id == tenant_id,
            Despesa.data_vencimento >= inicio,
            Despesa.data_vencimento <= fim,
        )
        .group_by(Despesa.categoria)
        .order_by(func.coalesce(func.sum(Despesa.valor), 0.0).desc())
        .all()
    )
    return [
        {"categoria": categoria or "Outros", "quantidade": int(qtd or 0), "valor": round(float(valor or 0), 2)}
        for categoria, qtd, valor in linhas
    ]


# Paleta do donut e das barras de pagamento. Fixa e ciclada por índice, como no
# original: cor de categoria precisa ser estável entre um carregamento e outro,
# senão o gráfico troca de cor a cada F5 e ninguém consegue comparar.
PALETA = (
    "#c8102e", "#c2620a", "#1d4ed8", "#15803d", "#7c3aed",
    "#0891b2", "#b45309", "#be185d", "#4d7c0f", "#64748b",
)


def _cor(indice: int) -> str:
    return PALETA[indice % len(PALETA)]


def serie_diaria(tenant_id: int, inicio: date, fim: date) -> dict:
    """Faturamento e lucro por dia, para o gráfico do painel financeiro.

    Devolve as três listas alinhadas por índice que o canvas espera. Dias sem
    venda entram com zero em vez de sumir: um buraco no eixo faria o gráfico
    mentir sobre o ritmo do período.
    """
    desde, ate = _intervalo(inicio, fim)
    linhas = (
        db.session.query(
            func.date(Pedido.created_at),
            func.coalesce(func.sum(Pedido.total), 0.0),
            func.coalesce(func.sum(Pedido.lucro_bruto), 0.0),
        )
        .filter(
            Pedido.tenant_id == tenant_id,
            Pedido.status == STATUS_ENTREGUE,
            Pedido.created_at >= desde,
            Pedido.created_at < ate,
        )
        .group_by(func.date(Pedido.created_at))
        .all()
    )
    por_dia = {str(dia): (float(total or 0), float(lucro or 0)) for dia, total, lucro in linhas}

    rotulos, faturamento, lucros = [], [], []
    dia = inicio
    while dia <= fim:
        total, lucro = por_dia.get(dia.isoformat(), (0.0, 0.0))
        rotulos.append(dia.strftime("%d/%m"))
        faturamento.append(round(total, 2))
        lucros.append(round(lucro, 2))
        dia += timedelta(days=1)

    return {"labels": rotulos, "revenue": faturamento, "profit": lucros}


def despesas_com_cor(tenant_id: int, inicio: date, fim: date) -> list[dict]:
    """Despesas por categoria, já com percentual e cor para o donut."""
    linhas = despesas_por_categoria(tenant_id, inicio, fim)
    total = sum(linha["valor"] for linha in linhas)
    return [
        {
            "label": linha["categoria"],
            "value": linha["valor"],
            "percent": round(linha["valor"] / total * 100, 1) if total else 0.0,
            "color": _cor(indice),
        }
        for indice, linha in enumerate(linhas)
    ]


def formas_de_pagamento(tenant_id: int, inicio: date, fim: date) -> list[dict]:
    """Participação de cada forma de pagamento no faturamento do período."""
    desde, ate = _intervalo(inicio, fim)
    linhas = (
        db.session.query(Pedido.pagamento, func.coalesce(func.sum(Pedido.total), 0.0))
        .filter(
            Pedido.tenant_id == tenant_id,
            Pedido.status == STATUS_ENTREGUE,
            Pedido.created_at >= desde,
            Pedido.created_at < ate,
        )
        .group_by(Pedido.pagamento)
        .order_by(func.coalesce(func.sum(Pedido.total), 0.0).desc())
        .all()
    )
    total = sum(float(valor or 0) for _, valor in linhas)
    return [
        {
            "label": pagamento or "Não informado",
            "value": round(float(valor or 0), 2),
            "percent": round(float(valor or 0) / total * 100, 1) if total else 0.0,
            "color": _cor(indice),
        }
        for indice, (pagamento, valor) in enumerate(linhas)
    ]


def produtos_lucrativos(tenant_id: int, inicio: date, fim: date, limite: int = 8) -> list[dict]:
    """Ranking de lucro por produto, estimado pela ficha técnica ATUAL.

    O lucro por pedido é gravado no fechamento, mas não por item — reconstruir
    item a item exige o custo de hoje. Por isso é estimativa, e a tela diz isso:
    trocar o preço de um insumo muda o ranking do passado.
    """
    from ..models.pedido import PedidoItem
    from ..models.produto import Produto

    desde, ate = _intervalo(inicio, fim)
    linhas = (
        db.session.query(
            PedidoItem.produto_id,
            func.coalesce(func.sum(PedidoItem.quantidade), 0),
            func.coalesce(func.sum(PedidoItem.total), 0.0),
        )
        .join(Pedido, Pedido.id == PedidoItem.pedido_id)
        .filter(
            Pedido.tenant_id == tenant_id,
            Pedido.status == STATUS_ENTREGUE,
            Pedido.created_at >= desde,
            Pedido.created_at < ate,
            PedidoItem.produto_id.isnot(None),
        )
        .group_by(PedidoItem.produto_id)
        .all()
    )
    if not linhas:
        return []

    produtos = {
        produto.id: produto
        for produto in Produto.query.filter(
            Produto.tenant_id == tenant_id,
            Produto.id.in_([produto_id for produto_id, _, _ in linhas]),
        ).all()
    }

    ranking = []
    for produto_id, quantidade, receita in linhas:
        produto = produtos.get(produto_id)
        # Produto de outro tenant não deveria aparecer aqui, mas a checagem é
        # barata e a consulta cruza duas tabelas.
        if produto is None:
            continue
        quantidade = int(quantidade or 0)
        receita = float(receita or 0)
        custo = produto.custo_por_ficha * quantidade
        lucro = receita - custo
        ranking.append(
            {
                "name": produto.nome,
                "quantity": quantidade,
                "profit": round(lucro, 2),
                "margin": round(lucro / receita * 100, 1) if receita else 0.0,
            }
        )

    ranking.sort(key=lambda item: item["profit"], reverse=True)
    return ranking[:limite]


def fluxo_de_caixa(tenant_id: int, inicio: date, fim: date, limite: int = 120) -> list[dict]:
    """Entradas e saídas do período numa lista só, da mais recente para a antiga.

    Junta três origens que vivem em tabelas diferentes — venda, receita avulsa e
    despesa — porque quem olha caixa quer ver o dinheiro, não o modelo de dados.
    """
    desde, ate = _intervalo(inicio, fim)
    movimentos: list[dict] = []

    vendas = (
        Pedido.query.filter(
            Pedido.tenant_id == tenant_id,
            Pedido.status == STATUS_ENTREGUE,
            Pedido.created_at >= desde,
            Pedido.created_at < ate,
        )
        .order_by(Pedido.created_at.desc())
        .all()
    )
    for pedido in vendas:
        movimentos.append(
            {
                "quando": pedido.created_at.date(),
                "date": pedido.created_at.strftime("%d/%m/%Y"),
                "description": f"Pedido #{pedido.numero} — {pedido.cliente or 'cliente'}",
                "category": pedido.pagamento or "Venda",
                "type": "Entrada",
                "value": round(float(pedido.total or 0), 2),
                "status": "Recebido",
                "tone": "success",
            }
        )

    receitas = ReceitaAvulsa.query.filter(
        ReceitaAvulsa.tenant_id == tenant_id,
        ReceitaAvulsa.data_registro >= inicio,
        ReceitaAvulsa.data_registro <= fim,
    ).all()
    for receita in receitas:
        movimentos.append(
            {
                "quando": receita.data_registro,
                "date": receita.data_registro.strftime("%d/%m/%Y"),
                "description": receita.descricao or "Receita avulsa",
                "category": receita.categoria or "Outras receitas",
                "type": "Entrada",
                "value": round(float(receita.valor or 0), 2),
                "status": "Recebido",
                "tone": "success",
            }
        )

    hoje = date.today()
    despesas = Despesa.query.filter(
        Despesa.tenant_id == tenant_id,
        Despesa.data_vencimento >= inicio,
        Despesa.data_vencimento <= fim,
    ).all()
    for despesa in despesas:
        if despesa.paga:
            status, tone = "Pago", "success"
        elif despesa.data_vencimento < hoje:
            status, tone = "Atrasado", "danger"
        else:
            status, tone = "Pendente", "warning"
        movimentos.append(
            {
                "quando": despesa.data_vencimento,
                "date": despesa.data_vencimento.strftime("%d/%m/%Y"),
                "description": despesa.descricao,
                "category": despesa.categoria or "Outros",
                "type": "Saída",
                "value": round(float(despesa.valor or 0), 2),
                "status": status,
                "tone": tone,
            }
        )

    movimentos.sort(key=lambda item: item["quando"], reverse=True)
    return movimentos[:limite]


def a_pagar(tenant_id: int, hoje: date | None = None, limite: int = 30) -> list[Despesa]:
    """Despesas em aberto, das mais atrasadas para as mais distantes."""
    return (
        Despesa.query.filter_by(tenant_id=tenant_id, paga=False)
        .order_by(Despesa.data_vencimento)
        .limit(limite)
        .all()
    )


def painel(tenant_id: int, inicio: date, fim: date, hoje: date | None = None) -> dict:
    """Tudo que a tela financeira mostra, mais o comparativo com o período anterior."""
    hoje = hoje or date.today()
    dias = (fim - inicio).days + 1
    inicio_anterior = inicio - timedelta(days=dias)
    fim_anterior = inicio - timedelta(days=1)

    atual = resumo(tenant_id, inicio, fim)
    anterior = resumo(tenant_id, inicio_anterior, fim_anterior)

    def variacao(chave: str) -> float | None:
        base = anterior[chave]
        if not base:
            return None
        return round((atual[chave] - base) / abs(base) * 100, 1)

    contas = a_pagar(tenant_id, hoje)
    return {
        "inicio": inicio,
        "fim": fim,
        "dias": dias,
        "atual": atual,
        "anterior": anterior,
        "variacao_receita": variacao("receita"),
        "variacao_lucro": variacao("lucro_liquido"),
        "variacao_cmv": variacao("cmv"),
        "variacao_despesas": variacao("despesas"),
        "variacao_ticket": variacao("ticket"),
        # Margem já é percentual: a variação dela se lê em pontos percentuais,
        # não em "percentual de percentual", que ninguém consegue interpretar.
        "variacao_margem_pp": round(atual["margem_liquida"] - anterior["margem_liquida"], 1),
        "por_categoria": despesas_por_categoria(tenant_id, inicio, fim),
        "despesas_grafico": despesas_com_cor(tenant_id, inicio, fim),
        "pagamentos": formas_de_pagamento(tenant_id, inicio, fim),
        "grafico": serie_diaria(tenant_id, inicio, fim),
        "produtos_lucrativos": produtos_lucrativos(tenant_id, inicio, fim),
        "fluxo": fluxo_de_caixa(tenant_id, inicio, fim),
        "a_pagar": contas,
        "hoje": hoje,
    }


def periodo_escolhido(chave: str | None, de: str | None, ate: str | None, hoje: date) -> tuple[date, date, str, str]:
    """Traduz os presets da barra de período (hoje / 7 dias / mês / custom).

    Devolve início, fim, a chave normalizada e o rótulo. Datas inválidas caem no
    preset padrão em vez de estourar: o valor vem da query string, que é dado do
    cliente.
    """
    def _data(texto: str | None) -> date | None:
        try:
            return date.fromisoformat((texto or "").strip())
        except ValueError:
            return None

    if chave == "today":
        return hoje, hoje, "today", "hoje"
    if chave == "month":
        return hoje.replace(day=1), hoje, "month", "este mês"
    if chave == "custom":
        inicio, fim = _data(de), _data(ate)
        if inicio and fim and inicio <= fim:
            return inicio, fim, "custom", f"{inicio.strftime('%d/%m')} a {fim.strftime('%d/%m')}"
    return hoje - timedelta(days=6), hoje, "7days", "os últimos 7 dias"
