"""Avisos que o gateway manda para a plataforma.

Este é o único endereço do sistema que um estranho pode chamar e que mexe em
dinheiro. Por isso ele é conservador em três pontos:

1. **Sem token configurado, recusa tudo.** Não existe modo "aberto para
   facilitar o teste": um endereço público que marca mensalidade como paga não
   pode aceitar chamada de qualquer um.
2. **Responde 200 para o que não entende.** O Asaas reenvia o que falha, e
   ficar devolvendo erro para um evento que a plataforma não trata faria a fila
   dele girar para sempre — atrasando os eventos que importam.
3. **Não desfaz pagamento sozinho.** Estorno e cobrança apagada ficam
   registrados e visíveis, mas não bloqueiam o restaurante automaticamente.
   Derrubar a loja de alguém por causa de um webhook é um martelo grande demais
   para uma decisão que sempre tem contexto humano atrás.

Não tem tenant: quem paga aqui é o restaurante, mas a conta é da plataforma. Por
isso o blueprint fica de fora de `TENANT_REQUIRED_BLUEPRINTS`.
"""

from __future__ import annotations

import secrets

from flask import Blueprint, current_app, jsonify, request

from ..extensions import csrf, limiter

webhooks_bp = Blueprint("webhooks", __name__, url_prefix="/webhooks")

# Eventos que significam "o dinheiro entrou".
EVENTOS_PAGOS = {"PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"}
# Eventos que desfazem um recebimento. Só registram; ver o item 3 acima.
EVENTOS_REVERTIDOS = {
    "PAYMENT_REFUNDED",
    "PAYMENT_DELETED",
    "PAYMENT_CHARGEBACK_REQUESTED",
    "PAYMENT_REVERSED",
}


def _token_confere() -> bool:
    esperado = (current_app.config.get("ASAAS_WEBHOOK_TOKEN") or "").strip()
    if not esperado:
        return False
    recebido = (request.headers.get("asaas-access-token") or "").strip()
    return bool(recebido) and secrets.compare_digest(recebido, esperado)


def _cobranca_do_evento(pagamento: dict):
    """Acha a cobrança pelo id do gateway, ou pela referência que gravamos.

    A segunda via existe para o caso de o id externo não ter sido gravado — a
    cobrança é criada no Asaas e só depois o id volta para o banco, e uma queda
    entre as duas coisas deixaria a cobrança órfã justamente quando o cliente
    pagou.
    """
    from ..models.assinatura import Cobranca

    identificador = str(pagamento.get("id") or "").strip()
    if identificador:
        achada = Cobranca.query.filter_by(id_externo=identificador).first()
        if achada is not None:
            return achada

    referencia = str(pagamento.get("externalReference") or "").strip()
    if referencia.startswith("cobranca:"):
        _, _, numero = referencia.partition(":")
        if numero.isdigit():
            from ..extensions import db

            return db.session.get(Cobranca, int(numero))
    return None


@webhooks_bp.post("/asaas")
@csrf.exempt
@limiter.limit("120 per minute")
def asaas():
    if not _token_confere():
        # Sem detalhe no corpo: quem chamou não precisa saber se o token está
        # errado ou se não existe token configurado.
        current_app.logger.warning("Webhook do Asaas recusado (token inválido).")
        return jsonify(status="erro"), 401

    payload = request.get_json(silent=True) or {}
    evento = str(payload.get("event") or "").strip().upper()
    pagamento = payload.get("payment") or {}

    if evento not in EVENTOS_PAGOS and evento not in EVENTOS_REVERTIDOS:
        return jsonify(status="ignorado", evento=evento), 200

    cobranca = _cobranca_do_evento(pagamento)
    if cobranca is None:
        # 200, e não 404: reenviar não vai fazer a cobrança aparecer. O log é o
        # que permite investigar depois.
        current_app.logger.warning(
            "Webhook do Asaas sem cobrança correspondente: evento=%s pagamento=%s",
            evento,
            pagamento.get("id"),
        )
        return jsonify(status="sem_cobranca"), 200

    from ..extensions import db
    from ..models.assinatura import COBRANCA_PAGA
    from ..services.faturamento_saas import registrar_pagamento

    if evento in EVENTOS_REVERTIDOS:
        cobranca.observacao = (
            f"Atenção: o Asaas informou {evento} em "
            f"{pagamento.get('id') or 'pagamento sem id'}. Confira antes de liberar."
        )[:300]
        db.session.commit()
        current_app.logger.warning(
            "Pagamento revertido no Asaas: tenant=%s competencia=%s evento=%s",
            cobranca.tenant.slug,
            cobranca.rotulo_competencia,
            evento,
        )
        return jsonify(status="registrado", evento=evento), 200

    if cobranca.status == COBRANCA_PAGA:
        # O Asaas reenvia o mesmo evento; repetir não é erro.
        return jsonify(status="ja_estava_paga"), 200

    try:
        valor = float(pagamento.get("value") or cobranca.valor)
    except (TypeError, ValueError):
        valor = float(cobranca.valor)

    observacao = None
    if abs(valor - float(cobranca.valor)) >= 0.01:
        # Não recusa o pagamento — o dinheiro entrou. Mas o registro precisa
        # dizer que veio diferente do que foi cobrado.
        observacao = (
            f"Valor recebido (R$ {valor:.2f}) diferente do cobrado "
            f"(R$ {float(cobranca.valor):.2f})."
        )

    try:
        registrar_pagamento(
            cobranca,
            valor=valor,
            metodo=str(pagamento.get("billingType") or "Asaas")[:40],
            observacao=observacao,
        )
    except ValueError as exc:
        # Cobrança cancelada que recebeu pagamento, por exemplo. Fica no log
        # para conferência, e o Asaas não precisa reenviar.
        current_app.logger.warning(
            "Webhook do Asaas não pôde registrar pagamento: tenant=%s motivo=%s",
            cobranca.tenant.slug,
            exc,
        )
        return jsonify(status="nao_registrado", motivo=str(exc)), 200

    current_app.logger.info(
        "Mensalidade paga pelo Asaas: tenant=%s competencia=%s valor=%.2f",
        cobranca.tenant.slug,
        cobranca.rotulo_competencia,
        valor,
    )
    return jsonify(status="ok"), 200
