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

    return {
        "inicio": inicio,
        "fim": fim,
        "dias": dias,
        "atual": atual,
        "anterior": anterior,
        "variacao_receita": variacao("receita"),
        "variacao_lucro": variacao("lucro_liquido"),
        "por_categoria": despesas_por_categoria(tenant_id, inicio, fim),
        "a_pagar": a_pagar(tenant_id, hoje),
        "hoje": hoje,
    }
