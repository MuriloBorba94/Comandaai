"""Fase 4 — cobrança automática da mensalidade pelo Asaas.

O webhook é o único endereço do sistema que um estranho pode chamar e que mexe
em dinheiro, então a maior parte destes testes é sobre ele:

1. **Ninguém marca mensalidade como paga sem o token.**
2. **Reenvio não é erro.** O Asaas repete eventos; repetir não pode virar
   cobrança paga duas vezes nem resposta de erro que o faça repetir para sempre.
3. **O gateway fora do ar não impede a cobrança de existir.** Mês sem cobrança
   não bloqueia ninguém — some com a receita da plataforma em silêncio, que é
   pior.
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.extensions import db
from app.models.assinatura import (
    COBRANCA_CANCELADA,
    COBRANCA_PAGA,
    COBRANCA_PENDENTE,
    PROVEDOR_ASAAS,
    PROVEDOR_MANUAL,
    Cobranca,
    Plano,
)
from app.models.tenant import Tenant
from app.services import faturamento_saas
from app.services.cobrancas import provedor_do_tenant
from app.services.cobrancas.asaas import Asaas

TOKEN = "token-do-webhook-de-teste"
BASE_PLATAFORMA = "http://app.localhost"


@pytest.fixture()
def plataforma(app, two_tenants):
    """Um tenant com plano pago e CNPJ, pronto para ser cobrado."""
    app.config["ASAAS_WEBHOOK_TOKEN"] = TOKEN
    app.config["ASAAS_API_KEY"] = "chave-de-teste"
    app.config["ASAAS_AMBIENTE"] = "sandbox"

    db.session.add(Plano(slug="pro", nome="Pro", preco_mensal=199.90))
    tenant = db.session.get(Tenant, two_tenants["tenant_a"])
    tenant.plano = "pro"
    tenant.status = "active"
    tenant.cnpj = "12.345.678/0001-99"
    tenant.assinatura_provider = PROVEDOR_ASAAS
    # Só é cobrado quem já saiu do teste grátis (ver deve_cobrar).
    tenant.trial_termina_em = datetime.combine(date.today() - timedelta(days=1), datetime.min.time())
    db.session.commit()
    return tenant


class _Falso(BaseHTTPRequestHandler):
    """Responde como a API do Asaas. As respostas vêm de `_Falso.roteiro`."""

    roteiro: dict = {}
    recebido: list = []

    def do_POST(self):
        corpo = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _Falso.recebido.append(
            {
                "caminho": self.path,
                "token": self.headers.get("access_token"),
                "corpo": json.loads(corpo) if corpo else {},
            }
        )
        codigo, resposta = _Falso.roteiro.get(self.path, (200, {}))
        dados = json.dumps(resposta).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def log_message(self, *args):  # silencia o log no pytest
        pass


def _asaas_falso(monkeypatch, roteiro: dict, chamadas: int = 2):
    """Sobe um Asaas de mentira e aponta o provedor para ele."""
    from app.services.cobrancas import asaas as modulo

    _Falso.roteiro = roteiro
    _Falso.recebido = []
    servidor = HTTPServer(("127.0.0.1", 0), _Falso)

    def atender():
        for _ in range(chamadas):
            servidor.handle_request()

    threading.Thread(target=atender, daemon=True).start()
    monkeypatch.setattr(
        modulo, "ENDERECOS", {"sandbox": f"http://127.0.0.1:{servidor.server_port}"}
    )
    return servidor


# --------------------------------------------------------------------------- #
# Escolha do provedor
# --------------------------------------------------------------------------- #


def test_sem_chave_de_api_a_cobranca_cai_para_o_manual(app, plataforma):
    """Mês sem cobrança some com a receita em silêncio; manual é o piso."""
    app.config["ASAAS_API_KEY"] = ""

    assert provedor_do_tenant(plataforma).slug == PROVEDOR_MANUAL


def test_com_chave_o_asaas_atende(plataforma):
    assert provedor_do_tenant(plataforma).slug == PROVEDOR_ASAAS


def test_tenant_no_manual_nao_e_levado_para_o_gateway(plataforma):
    plataforma.assinatura_provider = PROVEDOR_MANUAL
    db.session.commit()

    assert provedor_do_tenant(plataforma).slug == PROVEDOR_MANUAL


# --------------------------------------------------------------------------- #
# Emitir a fatura
# --------------------------------------------------------------------------- #


def test_emitir_cria_cliente_e_fatura_e_guarda_o_link(plataforma, monkeypatch):
    servidor = _asaas_falso(
        monkeypatch,
        {
            "/customers": (200, {"id": "cus_123"}),
            "/payments": (
                200,
                {"id": "pay_456", "invoiceUrl": "https://asaas.test/i/pay_456"},
            ),
        },
    )

    cobranca = faturamento_saas.gerar_cobranca(plataforma)
    servidor.server_close()

    assert cobranca.provedor == PROVEDOR_ASAAS
    assert cobranca.id_externo == "pay_456"
    assert cobranca.url_pagamento == "https://asaas.test/i/pay_456"
    # O id do cliente fica no tenant, para o mês seguinte não criar outro.
    assert plataforma.asaas_customer_id == "cus_123"

    cliente, fatura = _Falso.recebido
    assert cliente["token"] == "chave-de-teste"
    assert cliente["corpo"]["cpfCnpj"] == "12345678000199"
    assert fatura["corpo"]["customer"] == "cus_123"
    assert fatura["corpo"]["value"] == pytest.approx(199.90)
    assert fatura["corpo"]["externalReference"] == f"cobranca:{cobranca.id}"


def test_cliente_do_asaas_e_reaproveitado_no_mes_seguinte(plataforma, monkeypatch):
    plataforma.asaas_customer_id = "cus_ja_existe"
    db.session.commit()
    servidor = _asaas_falso(
        monkeypatch, {"/payments": (200, {"id": "pay_1", "invoiceUrl": "x"})}, chamadas=1
    )

    faturamento_saas.gerar_cobranca(plataforma)
    servidor.server_close()

    # Uma chamada só: nada de criar cliente duplicado a cada mês.
    assert [c["caminho"] for c in _Falso.recebido] == ["/payments"]


def test_gateway_fora_do_ar_nao_impede_a_cobranca_de_existir(plataforma, monkeypatch):
    from app.services.cobrancas import asaas as modulo

    # Porta onde não há ninguém escutando.
    monkeypatch.setattr(modulo, "ENDERECOS", {"sandbox": "http://127.0.0.1:9"})

    cobranca = faturamento_saas.gerar_cobranca(plataforma)

    assert cobranca is not None
    assert cobranca.status == COBRANCA_PENDENTE
    assert cobranca.id_externo is None
    # O motivo fica gravado, para o comando de reemissão e para a tela.
    assert "Gateway" in (cobranca.observacao or "")


def test_erro_do_asaas_chega_inteiro(plataforma, monkeypatch):
    servidor = _asaas_falso(
        monkeypatch,
        {
            "/customers": (
                400,
                {"errors": [{"code": "invalid_cpfCnpj", "description": "O CPF/CNPJ informado é inválido."}]},
            )
        },
        chamadas=1,
    )

    cobranca = faturamento_saas.gerar_cobranca(plataforma)
    servidor.server_close()

    assert "CPF/CNPJ informado é inválido" in cobranca.observacao


def test_restaurante_sem_documento_nao_vai_para_o_gateway(plataforma, monkeypatch):
    """O Asaas exige CPF/CNPJ; melhor dizer isso do que falhar lá."""
    plataforma.cnpj = None
    db.session.commit()
    from app.services.cobrancas import asaas as modulo

    monkeypatch.setattr(modulo, "ENDERECOS", {"sandbox": "http://127.0.0.1:9"})

    cobranca = faturamento_saas.gerar_cobranca(plataforma)

    assert "CPF ou CNPJ" in cobranca.observacao
    assert cobranca.id_externo is None


def test_comando_reemite_a_fatura_que_ficou_para_tras(app, plataforma, monkeypatch):
    from app.services.cobrancas import asaas as modulo

    monkeypatch.setattr(modulo, "ENDERECOS", {"sandbox": "http://127.0.0.1:9"})
    cobranca = faturamento_saas.gerar_cobranca(plataforma)
    assert cobranca.id_externo is None

    servidor = _asaas_falso(
        monkeypatch,
        {
            "/customers": (200, {"id": "cus_9"}),
            "/payments": (200, {"id": "pay_9", "invoiceUrl": "https://asaas.test/i/pay_9"}),
        },
    )
    resultado = app.test_cli_runner().invoke(args=["reemitir-no-gateway"])
    servidor.server_close()

    assert "ok" in resultado.output
    db.session.refresh(cobranca)
    assert cobranca.id_externo == "pay_9"
    assert cobranca.observacao is None


# --------------------------------------------------------------------------- #
# O webhook
# --------------------------------------------------------------------------- #


def _cobranca_no_gateway(tenant, identificador="pay_1") -> Cobranca:
    cobranca = Cobranca(
        tenant_id=tenant.id,
        competencia=date(2026, 8, 1),
        vencimento=date(2026, 8, 10),
        plano_slug="pro",
        valor=199.90,
        status=COBRANCA_PENDENTE,
        provedor=PROVEDOR_ASAAS,
        id_externo=identificador,
    )
    db.session.add(cobranca)
    db.session.commit()
    return cobranca


def _avisar(client, evento="PAYMENT_RECEIVED", token=TOKEN, **pagamento):
    corpo = {"event": evento, "payment": {"id": "pay_1", "value": 199.90, **pagamento}}
    cabecalhos = {"asaas-access-token": token} if token is not None else {}
    return client.post(
        "/webhooks/asaas", json=corpo, headers=cabecalhos, base_url=BASE_PLATAFORMA
    )


def test_webhook_sem_token_nao_marca_nada_como_pago(client, plataforma):
    cobranca = _cobranca_no_gateway(plataforma)

    assert _avisar(client, token=None).status_code == 401
    assert _avisar(client, token="token-errado").status_code == 401
    assert cobranca.status == COBRANCA_PENDENTE


def test_webhook_recusa_tudo_quando_nao_ha_token_configurado(app, client, plataforma):
    """Não existe modo aberto: endereço público que mexe em dinheiro não pode."""
    app.config["ASAAS_WEBHOOK_TOKEN"] = ""
    cobranca = _cobranca_no_gateway(plataforma)

    assert _avisar(client, token="qualquer").status_code == 401
    assert cobranca.status == COBRANCA_PENDENTE


def test_webhook_marca_a_mensalidade_como_paga(client, plataforma):
    cobranca = _cobranca_no_gateway(plataforma)

    resposta = _avisar(client, billingType="PIX")

    assert resposta.status_code == 200
    assert cobranca.status == COBRANCA_PAGA
    assert cobranca.valor_pago == pytest.approx(199.90)
    assert cobranca.metodo_pagamento == "PIX"


def test_pagar_libera_o_restaurante_que_estava_suspenso(client, plataforma):
    plataforma.status = "suspended"
    cobranca = _cobranca_no_gateway(plataforma)
    cobranca.vencimento = date.today() - timedelta(days=30)
    db.session.commit()

    _avisar(client)

    # O ponto da fase inteira: o acesso volta sozinho quando o dinheiro entra.
    assert plataforma.status == "active"


def test_reenvio_do_mesmo_evento_nao_e_erro(client, plataforma):
    """O Asaas repete; devolver erro faria a fila dele girar para sempre."""
    cobranca = _cobranca_no_gateway(plataforma)

    primeira = _avisar(client)
    segunda = _avisar(client)

    assert primeira.status_code == 200
    assert segunda.status_code == 200
    assert segunda.get_json()["status"] == "ja_estava_paga"
    assert cobranca.valor_pago == pytest.approx(199.90)


def test_evento_desconhecido_responde_ok_sem_fazer_nada(client, plataforma):
    cobranca = _cobranca_no_gateway(plataforma)

    resposta = _avisar(client, evento="PAYMENT_UPDATED")

    assert resposta.status_code == 200
    assert cobranca.status == COBRANCA_PENDENTE


def test_pagamento_sem_cobranca_correspondente_nao_estoura(client, plataforma):
    resposta = _avisar(client, id="pay_que_nao_existe")

    assert resposta.status_code == 200
    assert resposta.get_json()["status"] == "sem_cobranca"


def test_cobranca_e_reencontrada_pela_referencia_quando_falta_o_id(client, plataforma):
    """Se a queda foi entre criar no Asaas e gravar o id, o cliente pagou assim mesmo."""
    cobranca = _cobranca_no_gateway(plataforma, identificador=None)

    resposta = _avisar(client, id="pay_novo", externalReference=f"cobranca:{cobranca.id}")

    assert resposta.status_code == 200
    assert cobranca.status == COBRANCA_PAGA


def test_valor_diferente_do_cobrado_e_registrado_em_vez_de_escondido(client, plataforma):
    cobranca = _cobranca_no_gateway(plataforma)

    _avisar(client, value=150.00)

    assert cobranca.status == COBRANCA_PAGA
    assert cobranca.valor_pago == pytest.approx(150.00)
    assert "diferente do cobrado" in cobranca.observacao


def test_estorno_e_registrado_mas_nao_bloqueia_ninguem(client, plataforma):
    """Derrubar a loja de alguém por causa de um webhook é martelo grande demais."""
    cobranca = _cobranca_no_gateway(plataforma)
    _avisar(client)
    assert cobranca.status == COBRANCA_PAGA

    resposta = _avisar(client, evento="PAYMENT_REFUNDED")

    assert resposta.status_code == 200
    assert cobranca.status == COBRANCA_PAGA
    assert "PAYMENT_REFUNDED" in cobranca.observacao
    assert plataforma.status == "active"


def test_webhook_nao_reabre_cobranca_cancelada(client, plataforma):
    cobranca = _cobranca_no_gateway(plataforma)
    cobranca.status = COBRANCA_CANCELADA
    db.session.commit()

    resposta = _avisar(client)

    assert resposta.status_code == 200
    assert cobranca.status == COBRANCA_CANCELADA


def test_webhook_funciona_com_csrf_ligado():
    """O Asaas não é navegador: não tem cookie nem token de formulário."""
    from app import create_app
    from tests.conftest import TestConfig

    class ComCSRF(TestConfig):
        WTF_CSRF_ENABLED = True
        ASAAS_WEBHOOK_TOKEN = TOKEN

    aplicacao = create_app(ComCSRF)
    with aplicacao.app_context():
        db.create_all()
        resposta = aplicacao.test_client().post(
            "/webhooks/asaas",
            json={"event": "PAYMENT_UPDATED", "payment": {}},
            headers={"asaas-access-token": TOKEN},
            base_url=BASE_PLATAFORMA,
        )
        assert resposta.status_code == 200
        db.drop_all()


# --------------------------------------------------------------------------- #
# O que o restaurante vê
# --------------------------------------------------------------------------- #


def test_restaurante_ve_o_link_da_fatura_no_aviso(client, plataforma):
    from tests.conftest import login_tenant

    cobranca = _cobranca_no_gateway(plataforma)
    cobranca.url_pagamento = "https://asaas.test/i/pay_1"
    db.session.commit()

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    texto = client.get("/admin/", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert "https://asaas.test/i/pay_1" in texto
    assert "Pagar agora" in texto


def test_tela_de_bloqueio_oferece_o_link_de_pagamento(client, plataforma):
    cobranca = _cobranca_no_gateway(plataforma)
    cobranca.vencimento = date.today() - timedelta(days=60)
    cobranca.url_pagamento = "https://asaas.test/i/pay_1"
    plataforma.status = "suspended"
    db.session.commit()

    resposta = client.get("/", base_url="http://tenant-a.localhost")

    assert resposta.status_code == 402
    texto = resposta.get_data(as_text=True)
    assert "https://asaas.test/i/pay_1" in texto
    assert "volta sozinho" in texto


def test_ambiente_padrao_e_o_sandbox(app):
    """Configuração pela metade não pode sair cobrando ninguém de verdade."""
    from app.services.cobrancas.asaas import em_producao, endereco_base

    app.config["ASAAS_AMBIENTE"] = "sandbox"
    assert "sandbox" in endereco_base()
    assert em_producao() is False

    app.config["ASAAS_AMBIENTE"] = "producao"
    assert endereco_base() == "https://api.asaas.com/v3"
    assert em_producao() is True
