"""Validação e reserva de cupons, portado do coupon_service.py original.

O padrão de três estados é o ponto central e foi mantido:

    reservado  → segura a vaga durante o checkout, sem contar como consumo
    usado      → confirmado, incrementa usos_confirmados
    liberado   → devolvido (pedido cancelado ou reserva expirada)

`disponiveis = limite - usados - reservas ativas`, então dois checkouts
simultâneos não levam o mesmo último uso.

Duas diferenças em relação ao original:

1. Tudo é filtrado por tenant. O código do cupom passou a ser único por tenant,
   e não global — num SaaS, o primeiro cliente a criar "BEMVINDO" não pode
   bloquear o código para todos os outros restaurantes.
2. A reserva nasce SEM prazo de expiração. No original ela expirava em 35 min
   porque o pedido podia ficar preso "Aguardando PIX"; aqui o pedido já nasce
   confirmável, e expirar a reserva enquanto a cozinha demora liberaria o cupom
   para outra pessoa com o desconto já concedido. A expiração volta a ser usada
   quando existir pagamento online (Fase 6).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, or_

from ..extensions import db
from ..models.cupom import (
    TIPO_FIXO,
    USO_LIBERADO,
    USO_RESERVADO,
    USO_USADO,
    Cupom,
    CupomUso,
)
from ..models.produto import Produto


def _dinheiro(valor) -> Decimal:
    return Decimal(str(valor or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class ResultadoCupom:
    ok: bool
    codigo: str = ""
    desconto: Decimal = field(default_factory=lambda: Decimal("0.00"))
    mensagem: str = ""
    cupom: Cupom | None = None
    disponiveis: int = 0
    base_elegivel: Decimal = field(default_factory=lambda: Decimal("0.00"))


def normalizar_codigo(valor: str | None) -> str:
    return re.sub(r"[^A-Z0-9_-]", "", (str(valor or "").strip().upper()))[:40]


def _reservas_ativas(cupom_id: int, excluir_pedido_id: int | None = None) -> int:
    agora = datetime.now()
    consulta = CupomUso.query.filter(
        CupomUso.cupom_id == cupom_id,
        CupomUso.status == USO_RESERVADO,
        or_(CupomUso.expira_em.is_(None), CupomUso.expira_em > agora),
    )
    if excluir_pedido_id:
        consulta = consulta.filter(CupomUso.pedido_id != excluir_pedido_id)
    return consulta.count()


def usos_disponiveis(cupom: Cupom, excluir_pedido_id: int | None = None) -> int:
    return max(
        0,
        int(cupom.limite_usos or 0)
        - int(cupom.usos_confirmados or 0)
        - _reservas_ativas(cupom.id, excluir_pedido_id),
    )


def calcular_base_elegivel(cupom: Cupom, itens) -> Decimal:
    """Soma sobre a qual o desconto pode incidir.

    Note que a base vem apenas dos ITENS: a taxa de entrega nunca entra, então o
    cupom não desconta o frete.
    """
    if not itens:
        return Decimal("0.00")
    if cupom.permite_combo_promocional:
        return sum((_dinheiro(item.total) for item in itens), Decimal("0.00"))

    # Uma consulta só para descobrir quais produtos são promocionais.
    ids = {item.produto_id for item in itens if item.produto_id}
    promocionais = set()
    if ids:
        promocionais = {
            produto_id
            for (produto_id,) in db.session.query(Produto.id).filter(
                Produto.id.in_(ids), Produto.combo_promocional.is_(True)
            )
        }

    base = Decimal("0.00")
    for item in itens:
        if item.produto_id not in promocionais:
            base += _dinheiro(item.total)
    return base


def calcular_desconto(cupom: Cupom, base_elegivel) -> Decimal:
    base = _dinheiro(base_elegivel)
    if cupom.tipo == TIPO_FIXO:
        desconto = _dinheiro(cupom.valor)
    else:
        percentual = max(Decimal("0"), min(Decimal("100"), _dinheiro(cupom.valor)))
        desconto = base * percentual / Decimal("100")
    # Nunca desconta mais que a base elegível: o total não pode ficar negativo.
    return min(base, desconto).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validar_cupom(
    tenant_id: int,
    codigo: str | None,
    itens,
    subtotal,
    *,
    excluir_pedido_id: int | None = None,
) -> ResultadoCupom:
    """Valida o cupom para este carrinho, devolvendo o desconto calculado."""
    normalizado = normalizar_codigo(codigo)
    if not normalizado:
        return ResultadoCupom(False, mensagem="Digite o código do cupom.")

    cupom = Cupom.query.filter(
        Cupom.tenant_id == tenant_id, func.upper(Cupom.codigo) == normalizado
    ).first()
    if cupom is None or not cupom.ativo:
        return ResultadoCupom(False, codigo=normalizado, mensagem="Cupom inválido ou desativado.")

    agora = datetime.now()
    if cupom.inicio_em and agora < cupom.inicio_em:
        return ResultadoCupom(False, codigo=normalizado, mensagem="Este cupom ainda não começou.")
    if cupom.fim_em and agora > cupom.fim_em:
        return ResultadoCupom(False, codigo=normalizado, mensagem="Este cupom expirou.")

    subtotal_decimal = _dinheiro(subtotal)
    minimo = _dinheiro(cupom.pedido_minimo)
    if subtotal_decimal < minimo:
        return ResultadoCupom(
            False,
            codigo=normalizado,
            mensagem=f"Pedido mínimo de R$ {f'{minimo:.2f}'.replace('.', ',')} para usar este cupom.",
        )

    disponiveis = usos_disponiveis(cupom, excluir_pedido_id)
    if disponiveis <= 0:
        return ResultadoCupom(
            False, codigo=normalizado, mensagem="Este cupom atingiu o limite de utilizações."
        )

    base = calcular_base_elegivel(cupom, itens)
    if base <= 0:
        return ResultadoCupom(
            False,
            codigo=normalizado,
            mensagem=(
                "Este cupom não vale para combos ou produtos promocionais. "
                "Adicione um item elegível ao carrinho."
            ),
            cupom=cupom,
            disponiveis=disponiveis,
        )

    desconto = calcular_desconto(cupom, base)
    if desconto <= 0:
        return ResultadoCupom(
            False, codigo=normalizado, mensagem="Este cupom não gerou desconto para o pedido."
        )

    return ResultadoCupom(
        True, normalizado, desconto, "Cupom aplicado!", cupom, disponiveis, base
    )


def reservar_para_pedido(pedido, codigo: str | None, itens=None) -> ResultadoCupom:
    """Reserva o uso do cupom para este pedido e aplica o desconto.

    Levanta ValueError se o pedido já tiver outro cupom vinculado.
    """
    itens = itens if itens is not None else pedido.itens
    resultado = validar_cupom(
        pedido.tenant_id, codigo, itens, pedido.subtotal, excluir_pedido_id=pedido.id
    )
    if not resultado.ok or resultado.cupom is None:
        return resultado

    existente = CupomUso.query.filter_by(pedido_id=pedido.id).first() if pedido.id else None
    if existente is not None:
        if existente.status in (USO_RESERVADO, USO_USADO) and existente.cupom_id == resultado.cupom.id:
            return resultado
        raise ValueError("Este pedido já tem um cupom vinculado.")

    db.session.add(
        CupomUso(
            cupom_id=resultado.cupom.id,
            pedido_id=pedido.id,
            status=USO_RESERVADO,
            desconto=float(resultado.desconto),
        )
    )
    pedido.cupom_id = resultado.cupom.id
    pedido.cupom_codigo = resultado.codigo
    pedido.desconto = float(resultado.desconto)
    return resultado


def consumir(pedido) -> bool:
    """Confirma o uso: a reserva vira consumo e conta no limite do cupom."""
    uso = CupomUso.query.filter_by(pedido_id=pedido.id).first()
    if uso is None or uso.status != USO_RESERVADO:
        return False

    cupom = db.session.get(Cupom, uso.cupom_id)
    if cupom is None:
        uso.status = USO_LIBERADO
        uso.liberado_em = datetime.now()
        return False

    uso.status = USO_USADO
    uso.usado_em = datetime.now()
    uso.expira_em = None
    cupom.usos_confirmados = int(cupom.usos_confirmados or 0) + 1
    return True


def liberar(pedido) -> bool:
    """Devolve a vaga ao cupom (pedido cancelado)."""
    uso = CupomUso.query.filter_by(pedido_id=pedido.id).first()
    if uso is None or uso.status != USO_RESERVADO:
        return False
    uso.status = USO_LIBERADO
    uso.liberado_em = datetime.now()
    uso.expira_em = None
    return True


def liberar_reservas_expiradas(limite: int = 100) -> int:
    """Devolve reservas que passaram do prazo.

    Nesta fase nenhuma reserva nasce com prazo, então a função não tem efeito na
    prática — ela existe pronta para a Fase 6, onde o pedido pode ficar
    aguardando pagamento. Diferente do original, não cancela o pedido: aqui
    quem cancela é a cozinha.
    """
    agora = datetime.now()
    expiradas = (
        CupomUso.query.filter(
            CupomUso.status == USO_RESERVADO,
            CupomUso.expira_em.isnot(None),
            CupomUso.expira_em <= agora,
        )
        .order_by(CupomUso.id.asc())
        .limit(limite)
        .all()
    )
    for uso in expiradas:
        uso.status = USO_LIBERADO
        uso.liberado_em = agora
        uso.expira_em = None
    if expiradas:
        db.session.commit()
    return len(expiradas)
