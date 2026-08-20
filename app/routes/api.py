"""API do agente de impressão.

É a única parte do sistema que responde a um programa, e não a um navegador.
Três decisões importantes:

1. **O restaurante vem do endereço.** O agente chama
   `https://borbas.comandaai.app.br/api/...`, e o `resolve_tenant` já
   identificou o tenant antes desta rota rodar. O código de ativação precisa
   ser o daquele restaurante: um código válido apontado para o subdomínio do
   vizinho não abre nada. São duas travas, não uma.

2. **Sem CSRF.** O agente não tem sessão nem cookie — ele se identifica por
   `Authorization: Bearer`. Exigir token de formulário aqui seria exigir que
   ele fosse um navegador.

3. **Erro sempre em JSON.** Do outro lado não há ninguém para ler uma página de
   erro; a mensagem precisa chegar ao log do agente, que é o que o dono do
   restaurante vai me mandar quando algo não sair no papel.
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from ..extensions import csrf, limiter
from ..services import impressao as servico

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _token() -> str:
    autorizacao = request.headers.get("Authorization", "")
    if autorizacao.lower().startswith("bearer "):
        return autorizacao[7:].strip()
    return request.headers.get("X-Print-Agent-Token", "").strip()


def _agente():
    """Devolve (agente, resposta_de_erro). Só um dos dois é preenchido."""
    tenant = g.get("tenant")
    if tenant is None:  # pragma: no cover - o resolve_tenant já responde 404
        return None, (jsonify(status="erro", mensagem="Restaurante não identificado."), 404)

    agente = servico.autenticar(tenant, _token())
    if agente is None:
        return None, (
            jsonify(
                status="erro",
                mensagem="Código de ativação inválido ou revogado. Gere outro no painel, no menu Impressão.",
            ),
            401,
        )
    return agente, None


@api_bp.post("/impressao/agente/ping")
@csrf.exempt
@limiter.limit("60 per minute")
def agente_ping():
    """Diz ao servidor que o agente está vivo, sem pegar trabalho.

    Usado pelo configurador para testar o código antes de gravar o arquivo: sem
    isto a única forma de saber se o código está certo seria esperar um pedido.
    """
    agente, erro = _agente()
    if erro:
        return erro
    servico.registrar_contato(agente, request.get_json(silent=True) or {})
    from ..extensions import db

    db.session.commit()
    return jsonify(status="sucesso", mensagem="Agente conectado ao servidor.", restaurante=g.tenant.nome_fantasia)


@api_bp.post("/impressao/agente/proximo")
@csrf.exempt
@limiter.limit("120 per minute")
def agente_proximo():
    """Entrega o próximo papel a imprimir, ou nada."""
    agente, erro = _agente()
    if erro:
        return erro
    # O heartbeat vai junto: quem está pedindo trabalho está, por definição,
    # conectado. Ver reservar_proximo().
    payload = request.get_json(silent=True) or {}
    return jsonify(status="sucesso", trabalho=servico.reservar_proximo(agente, payload))


@api_bp.post("/impressao/agente/resultado")
@csrf.exempt
@limiter.limit("120 per minute")
def agente_resultado():
    """Recebe a confirmação de que o papel saiu — ou o motivo de não ter saído."""
    agente, erro = _agente()
    if erro:
        return erro

    payload = request.get_json(silent=True) or {}
    try:
        job = servico.concluir(
            agente,
            int(payload.get("job_id")),
            str(payload.get("claim_token") or ""),
            payload.get("ok") is True,
            str(payload.get("error") or ""),
        )
    except (TypeError, ValueError) as exc:
        # 409, não 400: quase sempre é a reserva que expirou porque o agente
        # demorou, e nesse caso ele deve seguir em frente, não repetir.
        return jsonify(status="erro", mensagem=str(exc)), 409
    return jsonify(status="sucesso", job_id=job.id, situacao=job.status)
