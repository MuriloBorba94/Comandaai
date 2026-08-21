"""Cobrança da própria plataforma: mensalidade que cada tenant paga a você.

Diferente das fases anteriores, isto não é porte: o sistema single-tenant não tem
nada parecido, porque atende um restaurante só.

O modo de operação inicial é o provedor "manual": você recebe o PIX e registra o
pagamento. A integração com gateway (Asaas) entra depois no mesmo lugar, sem
mexer no restante — `criar_no_provedor` é o ponto de encaixe.

Política de bloqueio, do mais brando ao mais duro:

    trial      → dentro do período de teste
    active     → em dia
    past_due   → venceu, mas dentro da carência: continua funcionando
    suspended  → passou da carência: acesso bloqueado, inclusive a vitrine

Duas coisas que o ciclo automático NUNCA mexe:

- `Tenant.ativo`, que é o bloqueio manual do super-admin (suspender por abuso
  sem relação com pagamento).
- Status `canceled`, que é o fim deliberado da relação e só sai de lá pela mão
  do super-admin.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from flask import current_app

from ..extensions import db
from ..models.assinatura import (
    COBRANCA_CANCELADA,
    COBRANCA_PAGA,
    COBRANCA_PENDENTE,
    PROVEDOR_ASAAS,
    PROVEDOR_MANUAL,
    Cobranca,
    Plano,
    primeiro_dia,
    somar_um_mes,
)
from ..models.tenant import Tenant

# Prazo mínimo entre a criação da cobrança e o vencimento. Sem isso, o ciclo
# rodando depois do dia de vencimento criaria cobranças já vencidas, e o cliente
# seria bloqueado por um atraso que nunca teve chance de evitar.
PRAZO_MINIMO_DIAS = 3

STATUS_TRIAL = "trial"
STATUS_ATIVO = "active"
STATUS_ATRASADO = "past_due"
STATUS_SUSPENSO = "suspended"
STATUS_CANCELADO = "canceled"


def _dinheiro(valor) -> Decimal:
    return Decimal(str(valor or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def plano_do_tenant(tenant: Tenant) -> Plano | None:
    """Plano do catálogo correspondente ao slug guardado no tenant."""
    if not tenant.plano:
        return None
    return Plano.query.filter_by(slug=tenant.plano).first()


def valor_mensal(tenant: Tenant) -> Decimal:
    plano = plano_do_tenant(tenant)
    return _dinheiro(plano.preco_mensal) if plano else Decimal("0.00")


def criar_no_provedor(cobranca: Cobranca) -> str | None:
    """Registra a cobrança no provedor externo e devolve o id de lá.

    Falhar aqui NÃO derruba a emissão. A cobrança precisa existir de qualquer
    jeito: se o gateway estiver fora do ar no dia do ciclo, o mês não pode
    simplesmente não ser cobrado — isso não bloqueia ninguém, mas some com a
    receita da plataforma em silêncio. O erro fica gravado na observação, e
    `flask reemitir-no-gateway` tenta de novo depois.
    """
    from .cobrancas import provedor_do_tenant

    escolhido = provedor_do_tenant(cobranca.tenant)
    # O provedor efetivo pode diferir do pedido (chave de API faltando derruba
    # para o manual). Gravar o efetivo é o que faz a tela dizer a verdade.
    cobranca.provedor = escolhido.slug

    resultado = escolhido.criar(cobranca)
    if not resultado.ok:
        cobranca.observacao = (f"Gateway: {resultado.erro or 'falha desconhecida'}")[:300]
        current_app.logger.warning(
            "Cobrança emitida sem o gateway: tenant=%s competencia=%s motivo=%s",
            cobranca.tenant.slug,
            cobranca.rotulo_competencia,
            resultado.erro,
        )
        return None

    if resultado.url_pagamento:
        cobranca.url_pagamento = resultado.url_pagamento
    return resultado.id_externo


def cobranca_da_competencia(
    tenant_id: int, competencia: date, *, incluir_canceladas: bool = False
) -> Cobranca | None:
    """Cobrança do mês. Por padrão ignora canceladas.

    Ignorar é o comportamento correto em quase todo uso: uma cobrança cancelada
    não está mais em jogo, e tratá-la como existente impedia reemitir o mês
    depois de cancelar por engano.
    """
    consulta = Cobranca.query.filter(
        Cobranca.tenant_id == tenant_id,
        Cobranca.competencia == primeiro_dia(competencia),
    )
    if not incluir_canceladas:
        consulta = consulta.filter(Cobranca.status != COBRANCA_CANCELADA)
    return consulta.first()


def cobrancas_em_aberto(tenant_id: int) -> list[Cobranca]:
    return (
        Cobranca.query.filter_by(tenant_id=tenant_id, status=COBRANCA_PENDENTE)
        .order_by(Cobranca.vencimento)
        .all()
    )


def _vencimento_para(competencia: date, hoje: date) -> date:
    dia = int(current_app.config.get("DIA_VENCIMENTO", 10))
    dia = max(1, min(dia, 28))  # 28 evita o problema de fevereiro
    padrao = primeiro_dia(competencia).replace(day=dia)
    return max(padrao, hoje + timedelta(days=PRAZO_MINIMO_DIAS))


def deve_cobrar(tenant: Tenant, hoje: date | None = None) -> bool:
    """Diz se este tenant já entrou na fase de pagar.

    Tenant sem `trial_termina_em` é tratado como teste sem prazo e não é cobrado
    — é o que protege os tenants criados antes desta fase de serem suspensos de
    surpresa.
    """
    hoje = hoje or date.today()
    if not tenant.ativo or tenant.status == STATUS_CANCELADO:
        return False
    if valor_mensal(tenant) <= 0:
        return False
    if tenant.trial_termina_em is None:
        return False
    fim_trial = tenant.trial_termina_em
    if isinstance(fim_trial, datetime):
        fim_trial = fim_trial.date()
    return hoje > fim_trial


def gerar_cobranca(
    tenant: Tenant, competencia: date | None = None, hoje: date | None = None
) -> Cobranca | None:
    """Emite a mensalidade de uma competência, uma única vez.

    Devolve a cobrança existente se já houver uma viva para o mês — o índice
    único parcial garante que rodar o ciclo duas vezes no mesmo dia não gere
    cobrança duplicada. Cobrança cancelada não conta: o mês pode ser reemitido.
    """
    hoje = hoje or date.today()
    competencia = primeiro_dia(competencia or hoje)

    existente = cobranca_da_competencia(tenant.id, competencia)
    if existente is not None:
        return existente
    if not deve_cobrar(tenant, hoje):
        return None

    plano = plano_do_tenant(tenant)
    cobranca = Cobranca(
        tenant_id=tenant.id,
        competencia=competencia,
        vencimento=_vencimento_para(competencia, hoje),
        plano_slug=tenant.plano,
        valor=float(_dinheiro(plano.preco_mensal if plano else 0)),
        status=COBRANCA_PENDENTE,
        provedor=tenant.assinatura_provider or PROVEDOR_MANUAL,
    )
    db.session.add(cobranca)
    db.session.flush()

    id_externo = criar_no_provedor(cobranca)
    if id_externo:
        cobranca.id_externo = id_externo

    db.session.commit()
    current_app.logger.info(
        "Cobrança emitida: tenant=%s competencia=%s valor=%.2f venc=%s",
        tenant.slug,
        cobranca.rotulo_competencia,
        cobranca.valor,
        cobranca.vencimento,
    )
    return cobranca


def registrar_pagamento(
    cobranca: Cobranca,
    *,
    valor: float | None = None,
    metodo: str = "PIX",
    observacao: str | None = None,
    hoje: date | None = None,
) -> Cobranca:
    """Marca a cobrança como paga e reavalia o acesso do tenant."""
    if cobranca.status == COBRANCA_PAGA:
        raise ValueError("Esta cobrança já está paga.")
    if cobranca.status == COBRANCA_CANCELADA:
        raise ValueError("Esta cobrança foi cancelada.")

    cobranca.status = COBRANCA_PAGA
    cobranca.pago_em = datetime.now()
    cobranca.valor_pago = float(_dinheiro(valor if valor is not None else cobranca.valor))
    cobranca.metodo_pagamento = (metodo or "").strip()[:40] or "PIX"
    if observacao:
        cobranca.observacao = observacao.strip()[:300]

    tenant = cobranca.tenant
    # A próxima cobrança fica visível na tela do tenant, para você saber quando
    # ela deve sair.
    proxima = somar_um_mes(cobranca.competencia)
    tenant.proxima_cobranca_em = datetime.combine(
        _vencimento_para(proxima, proxima), datetime.min.time()
    )
    avaliar_status(tenant, hoje=hoje, commit=False)
    db.session.commit()

    current_app.logger.info(
        "Pagamento registrado: tenant=%s competencia=%s valor=%.2f metodo=%s",
        tenant.slug,
        cobranca.rotulo_competencia,
        cobranca.valor_pago,
        cobranca.metodo_pagamento,
    )
    return cobranca


def cancelar_cobranca(cobranca: Cobranca, observacao: str | None = None) -> Cobranca:
    """Cancela uma cobrança (emitida por engano, acordo comercial, etc.)."""
    if cobranca.status == COBRANCA_PAGA:
        raise ValueError("Cobrança paga não pode ser cancelada. Registre um estorno por fora.")
    cobranca.status = COBRANCA_CANCELADA
    if observacao:
        cobranca.observacao = observacao.strip()[:300]
    avaliar_status(cobranca.tenant, commit=False)
    db.session.commit()
    return cobranca


def avaliar_status(tenant: Tenant, hoje: date | None = None, commit: bool = True) -> str:
    """Recalcula o status da assinatura do tenant a partir das cobranças.

    Não toca em `Tenant.ativo` (bloqueio manual) nem em tenant `canceled`.
    """
    if tenant.status == STATUS_CANCELADO:
        return tenant.status

    hoje = hoje or date.today()
    carencia = int(current_app.config.get("CARENCIA_DIAS", 5))

    atraso_maximo = 0
    for cobranca in cobrancas_em_aberto(tenant.id):
        atraso_maximo = max(atraso_maximo, cobranca.dias_de_atraso(hoje))

    if atraso_maximo > carencia:
        novo = STATUS_SUSPENSO
    elif atraso_maximo > 0:
        novo = STATUS_ATRASADO
    else:
        fim_trial = tenant.trial_termina_em
        if isinstance(fim_trial, datetime):
            fim_trial = fim_trial.date()
        novo = STATUS_TRIAL if (fim_trial and hoje <= fim_trial) else STATUS_ATIVO

    if tenant.status != novo:
        current_app.logger.info(
            "Status de assinatura: tenant=%s %s -> %s (atraso=%s dias)",
            tenant.slug,
            tenant.status,
            novo,
            atraso_maximo,
        )
        tenant.status = novo
        if commit:
            db.session.commit()
    return novo


def executar_ciclo(hoje: date | None = None) -> dict:
    """Emite as mensalidades do mês e reavalia o acesso de todos os tenants.

    Pensado para rodar uma vez por dia. É idempotente: rodar de novo no mesmo dia
    não cria cobrança repetida nem muda status sem motivo.
    """
    hoje = hoje or date.today()
    resumo = {"emitidas": 0, "suspensos": 0, "atrasados": 0, "avaliados": 0}

    for tenant in Tenant.query.order_by(Tenant.slug).all():
        if tenant.status == STATUS_CANCELADO:
            continue

        antes = tenant.status
        if deve_cobrar(tenant, hoje):
            criada = cobranca_da_competencia(tenant.id, hoje) is None
            if criada and gerar_cobranca(tenant, hoje=hoje) is not None:
                resumo["emitidas"] += 1

        depois = avaliar_status(tenant, hoje=hoje, commit=False)
        resumo["avaliados"] += 1
        if depois != antes:
            if depois == STATUS_SUSPENSO:
                resumo["suspensos"] += 1
            elif depois == STATUS_ATRASADO:
                resumo["atrasados"] += 1

    db.session.commit()
    return resumo


def aviso_de_assinatura(tenant: Tenant, hoje: date | None = None) -> dict | None:
    """O que avisar ao dono do restaurante sobre a mensalidade dele.

    Devolve None quando não há nada a dizer. Existe porque o cliente não é
    notificado por e-mail nem WhatsApp: sem este aviso, ele descobre que devia
    no dia em que a loja para de funcionar.
    """
    if tenant is None:
        return None

    hoje = hoje or date.today()
    abertas = cobrancas_em_aberto(tenant.id)
    if not abertas:
        return None

    carencia = int(current_app.config.get("CARENCIA_DIAS", 5))
    atraso = max(cobranca.dias_de_atraso(hoje) for cobranca in abertas)
    mais_antiga = abertas[0]
    total = float(sum(_dinheiro(c.valor) for c in abertas))

    if atraso > carencia:
        nivel = "bloqueado"
    elif atraso > 0:
        nivel = "urgente"
    else:
        nivel = "informativo"

    return {
        "nivel": nivel,
        "cobranca": mais_antiga,
        "quantidade": len(abertas),
        "total": total,
        "dias_de_atraso": atraso,
        # Quantos dias ainda faltam para o bloqueio. Dizer isso é o que dá ao
        # cliente a chance de agir antes de perder a loja.
        "dias_para_bloqueio": max(0, carencia - atraso + 1) if atraso else None,
        "contato": current_app.config.get("PLATFORM_CONTATO") or "",
    }


def resumo_do_tenant(tenant: Tenant, hoje: date | None = None) -> dict:
    """Números da assinatura de um tenant, para a tela da plataforma."""
    hoje = hoje or date.today()
    abertas = cobrancas_em_aberto(tenant.id)
    pagas = [c for c in tenant.cobrancas if c.status == COBRANCA_PAGA]
    return {
        "plano": plano_do_tenant(tenant),
        "valor_mensal": float(valor_mensal(tenant)),
        "em_aberto": abertas,
        "total_em_aberto": float(sum(_dinheiro(c.valor) for c in abertas)),
        "maior_atraso": max((c.dias_de_atraso(hoje) for c in abertas), default=0),
        "total_pago": float(sum(_dinheiro(c.valor_pago or c.valor) for c in pagas)),
        "qtd_pagas": len(pagas),
    }
