"""Abrir e fechar o turno, e a conferência do dinheiro no fim.

Abrir a loja e abrir o caixa são o mesmo gesto no painel, de propósito — é assim
que a noite começa de verdade: alguém chega, põe o troco na gaveta, e a partir
daí o cardápio aceita pedido.

O fechamento não decide se "bateu": mostra os dois números lado a lado (o que o
sistema somou e o que a pessoa contou) e guarda a diferença. Quem explica a
sobra ou a falta é quem estava lá.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models.auditoria import ACAO_CAIXA_ABERTO, ACAO_CAIXA_FECHADO
from ..models.caixa import Caixa
from ..models.pedido import STATUS_CANCELADO, Pedido
from .auditoria import registrar

# Só o que entra em espécie na gaveta. Cartão e PIX não passam por ela, e
# somá-los aqui faria toda conferência acusar uma falta que não existe.
PAGAMENTO_EM_ESPECIE = "Dinheiro"

# As faixas que aparecem nas duas gavetas do painel.
#
# Faixa, e não número solto, porque é o que o restaurante consegue prometer sem
# mentir: "40 a 60 min" é honesto, "47 min" seria chute com cara de precisão.
# Lista fechada porque quem muda isso é o dono no meio do movimento, com o
# celular na mão — dois toques resolvem, digitar dois números não.
FAIXAS_DE_TEMPO = (
    (15, 25),
    (20, 30),
    (30, 45),
    (40, 60),
    (50, 70),
    (60, 90),
    (90, 120),
)


def rotulo_da_faixa(minimo, maximo) -> str:
    return f"{int(minimo)} a {int(maximo)} min"


def faixas_com_a_atual(minimo, maximo) -> list[tuple[int, int]]:
    """As opções da gaveta, garantindo que a escolha atual esteja entre elas.

    Um restaurante que já tinha 35–55 salvo antes de a lista existir não pode
    abrir o painel e ver a gaveta apontando para outra coisa — seria o sistema
    trocando a promessa dele sem avisar.
    """
    try:
        atual = (int(minimo), int(maximo))
    except (TypeError, ValueError):
        return list(FAIXAS_DE_TEMPO)
    if atual in FAIXAS_DE_TEMPO:
        return list(FAIXAS_DE_TEMPO)
    return sorted([*FAIXAS_DE_TEMPO, atual])


def caixa_aberto(tenant_id: int) -> Caixa | None:
    return (
        Caixa.query.filter_by(tenant_id=tenant_id, fechado_em=None)
        .order_by(Caixa.aberto_em.desc())
        .first()
    )


def loja_esta_aberta(tenant) -> bool:
    """A loja aceita pedido pelo cardápio agora?

    Quem decide é só o interruptor. Exigir também um caixa aberto seria mais
    bonito no papel e péssimo na prática: quem já vende hoje nunca abriu caixa
    nenhum, e a regra fecharia a loja de todo mundo no instante em que subisse.

    A gaveta continua sendo registro do turno, não catraca da porta.
    """
    return bool(getattr(tenant, "loja_aberta", True))


def abrir(tenant, valor_inicial=0.0, *, actor: str | None = None) -> Caixa:
    """Começa o turno. Se já houver um aberto, devolve o mesmo — não duplica."""
    existente = caixa_aberto(tenant.id)
    if existente is not None:
        return existente

    try:
        valor = round(float(str(valor_inicial).replace(",", ".") or 0), 2)
    except (TypeError, ValueError):
        raise ValueError("Valor inicial inválido. Use apenas números.") from None
    if valor < 0:
        raise ValueError("O valor inicial do caixa não pode ser negativo.")

    caixa = Caixa(tenant_id=tenant.id, valor_inicial=valor, aberto_por=actor or None)
    tenant.loja_aberta = True
    db.session.add(caixa)
    try:
        db.session.commit()
    except IntegrityError:
        # Dois cliques ao mesmo tempo. O índice parcial barrou o segundo; quem
        # ganhou a corrida é o turno válido.
        db.session.rollback()
        return caixa_aberto(tenant.id)

    registrar(
        ACAO_CAIXA_ABERTO,
        alvo="Caixa",
        detalhes=f"Abertura com R$ {valor:.2f} em caixa",
        tenant=tenant,
        ator=actor or None,
    )
    return caixa


def resumo(caixa: Caixa) -> dict:
    """O que o sistema somou desde a abertura, para conferir com a gaveta."""
    filtro = (
        Pedido.tenant_id == caixa.tenant_id,
        Pedido.created_at >= caixa.aberto_em,
        Pedido.status != STATUS_CANCELADO,
    )
    if caixa.fechado_em is not None:
        filtro = (*filtro, Pedido.created_at <= caixa.fechado_em)

    linhas = (
        db.session.query(Pedido.pagamento, func.count(Pedido.id), func.sum(Pedido.total))
        .filter(*filtro)
        .group_by(Pedido.pagamento)
        .all()
    )

    por_forma = {(forma or "—"): {"pedidos": qtd, "total": float(soma or 0)} for forma, qtd, soma in linhas}
    especie = por_forma.get(PAGAMENTO_EM_ESPECIE, {}).get("total", 0.0)
    esperado = round(float(caixa.valor_inicial or 0) + especie, 2)

    return {
        "por_forma": por_forma,
        "pedidos": sum(v["pedidos"] for v in por_forma.values()),
        "faturamento": round(sum(v["total"] for v in por_forma.values()), 2),
        "em_especie": round(especie, 2),
        "esperado_na_gaveta": esperado,
        "diferenca": (
            None
            if caixa.valor_contado is None
            else round(float(caixa.valor_contado) - esperado, 2)
        ),
    }


def fechar(caixa: Caixa, valor_contado=None, *, observacao=None, actor: str | None = None) -> dict:
    """Encerra o turno e devolve a conferência.

    Fechar o caixa fecha a loja junto — é isso que a pessoa quer dizer quando
    encerra a noite, e deixar o cardápio no ar depois disso renderia pedido que
    ninguém vai fazer.
    """
    if caixa is None or not caixa.aberto:
        raise ValueError("Não há caixa aberto para fechar.")

    contado = None
    if str(valor_contado or "").strip() != "":
        try:
            contado = round(float(str(valor_contado).replace(",", ".")), 2)
        except (TypeError, ValueError):
            raise ValueError("Valor contado inválido. Use apenas números.") from None
        if contado < 0:
            raise ValueError("O valor contado não pode ser negativo.")

    caixa.valor_contado = contado
    caixa.fechado_em = datetime.now()
    caixa.fechado_por = actor or None
    caixa.observacao = (str(observacao or "").strip() or None) if observacao else None
    if caixa.tenant is not None:
        caixa.tenant.loja_aberta = False
    db.session.commit()

    conferencia = resumo(caixa)
    diferenca = conferencia["diferenca"]
    detalhe = f"Fechamento: {conferencia['pedidos']} pedidos, R$ {conferencia['faturamento']:.2f}"
    if diferenca is not None:
        rotulo = "confere" if abs(diferenca) < 0.01 else f"diferença de R$ {diferenca:.2f}"
        detalhe = f"{detalhe} — {rotulo}"
    registrar(ACAO_CAIXA_FECHADO, alvo="Caixa", detalhes=detalhe, tenant=caixa.tenant, ator=actor or None)
    return conferencia
