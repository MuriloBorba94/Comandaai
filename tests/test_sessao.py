"""Tempo de vida da sessão de quem opera o sistema.

Duas garantias diferentes, e o teste precisa distinguir as duas:

1. O cookie não leva validade, então o navegador o descarta ao fechar.
2. Passado o limite de inatividade, o servidor descarta a sessão sozinho — é o
   que cobre o navegador configurado para restaurar sessão ao reabrir, e o
   painel esquecido aberto no balcão.
"""

from __future__ import annotations

import time

import pytest

from app import create_app
from app.extensions import db
from tests.conftest import TestConfig, login_tenant

BASE_A = "http://tenant-a.localhost"
BASE_PLATAFORMA = "http://app.localhost"


@pytest.fixture()
def app():
    """Substitui a `app` do conftest: um minuto de inatividade, para o teste não
    precisar esperar de verdade. `client` e `two_tenants` dependem desta fixture,
    então todo mundo aqui compartilha o mesmo banco."""

    class SessaoCurta(TestConfig):
        SESSION_IDLE_MINUTES = 5

    aplicacao = create_app(SessaoCurta)
    with aplicacao.app_context():
        db.create_all()
        yield aplicacao
        db.drop_all()


# --------------------------------------------------------------------------- #
# O cookie morre com o navegador
# --------------------------------------------------------------------------- #


def _cookie_de_sessao(resposta):
    for bruto in resposta.headers.getlist("Set-Cookie"):
        if bruto.startswith("session="):
            return bruto
    return None


def test_cookie_de_login_nao_leva_validade(client, two_tenants):
    """Sem Expires nem Max-Age, o navegador descarta o cookie ao fechar."""
    resposta = login_tenant(client, "tenant-a", "admin", "senha-a-123")

    cookie = _cookie_de_sessao(resposta)
    assert cookie is not None, "o login precisa gravar o cookie de sessão"
    assert "Expires=" not in cookie
    assert "Max-Age=" not in cookie


def test_cookie_continua_httponly_e_samesite(client, two_tenants):
    cookie = _cookie_de_sessao(login_tenant(client, "tenant-a", "admin", "senha-a-123"))

    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


# --------------------------------------------------------------------------- #
# Inatividade derruba a sessão
# --------------------------------------------------------------------------- #


def test_sessao_parada_alem_do_limite_cai(client, two_tenants):
    cliente = client
    login_tenant(cliente, "tenant-a", "admin", "senha-a-123")
    assert cliente.get("/admin/", base_url=BASE_A).status_code == 200

    # Empurra o carimbo para trás em vez de dormir de verdade.
    with cliente.session_transaction(base_url=BASE_A) as sessao:
        sessao["visto_em"] = time.time() - 3600

    resposta = cliente.get("/admin/", base_url=BASE_A)

    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]


def test_uso_recente_mantem_a_sessao_viva(client, two_tenants):
    """Quem está trabalhando não pode ser deslogado no meio do turno."""
    cliente = client
    login_tenant(cliente, "tenant-a", "admin", "senha-a-123")

    with cliente.session_transaction(base_url=BASE_A) as sessao:
        sessao["visto_em"] = time.time() - 10

    assert cliente.get("/admin/", base_url=BASE_A).status_code == 200


def test_cada_acesso_reinicia_a_contagem(client, two_tenants):
    """O carimbo avança com o uso; senão a sessão cairia com o dono na frente."""
    cliente = client
    login_tenant(cliente, "tenant-a", "admin", "senha-a-123")

    with cliente.session_transaction(base_url=BASE_A) as sessao:
        sessao["visto_em"] = time.time() - 90  # além do intervalo de regravação

    cliente.get("/admin/", base_url=BASE_A)

    with cliente.session_transaction(base_url=BASE_A) as sessao:
        assert time.time() - sessao["visto_em"] < 5


def test_sessao_expirada_e_descartada_inteira(client, two_tenants):
    """Sobrar meia sessão é como um erro de autorização costuma nascer."""
    cliente = client
    login_tenant(cliente, "tenant-a", "admin", "senha-a-123")

    with cliente.session_transaction(base_url=BASE_A) as sessao:
        sessao["visto_em"] = time.time() - 3600

    cliente.get("/admin/", base_url=BASE_A)

    with cliente.session_transaction(base_url=BASE_A) as sessao:
        assert not any(sessao.get(c) for c in ("logged_in", "username", "tenant_id", "role"))


def test_plataforma_tambem_expira(client, platform_admin):
    cliente = client
    cliente.post(
        "/plataforma/login",
        data={"username": "admin", "password": "senha-super-admin-123"},
        base_url=BASE_PLATAFORMA,
    )
    assert cliente.get("/plataforma/", base_url=BASE_PLATAFORMA).status_code == 200

    with cliente.session_transaction(base_url=BASE_PLATAFORMA) as sessao:
        sessao["visto_em"] = time.time() - 3600

    resposta = cliente.get("/plataforma/", base_url=BASE_PLATAFORMA)
    assert resposta.status_code == 302
    assert "/plataforma/login" in resposta.headers["Location"]


def test_visitante_da_vitrine_nao_e_expulso(client, two_tenants):
    """A sessão do cliente final guarda o carrinho e não tem tempo de vida."""
    cliente = client
    with cliente.session_transaction(base_url=BASE_A) as sessao:
        sessao["carrinho"] = [{"produto_id": 1, "quantidade": 1}]
        sessao["carrinho_tenant"] = two_tenants["tenant_a"]
        sessao["visto_em"] = time.time() - 99999

    assert cliente.get("/", base_url=BASE_A).status_code == 200
    with cliente.session_transaction(base_url=BASE_A) as sessao:
        assert sessao.get("carrinho"), "o carrinho não pode ser descartado"


# --------------------------------------------------------------------------- #
# Sair encerra de verdade
# --------------------------------------------------------------------------- #


def test_sair_derruba_a_sessao_do_restaurante(client, two_tenants):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    assert client.get("/admin/", base_url=BASE_A).status_code == 200

    client.get("/logout", base_url=BASE_A)

    resposta = client.get("/admin/", base_url=BASE_A)
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]
    with client.session_transaction(base_url=BASE_A) as sessao:
        assert not any(sessao.get(c) for c in ("logged_in", "username", "tenant_id", "role"))


def test_sair_da_plataforma_limpa_tudo(client, platform_admin):
    client.post(
        "/plataforma/login",
        data={"username": "admin", "password": "senha-super-admin-123"},
        base_url=BASE_PLATAFORMA,
    )
    client.get("/plataforma/logout", base_url=BASE_PLATAFORMA)

    assert client.get("/plataforma/", base_url=BASE_PLATAFORMA).status_code == 302
    with client.session_transaction(base_url=BASE_PLATAFORMA) as sessao:
        assert not sessao.get("platform_admin_id")


# --------------------------------------------------------------------------- #
# Quem fez a ação: o login precisa registrar o usuário
# --------------------------------------------------------------------------- #


def test_login_registra_o_usuario_na_sessao(client, two_tenants):
    """`username` alimenta o chip do painel E o autor das movimentações.

    Ele não era gravado: o histórico de estoque e os itens lançados na comanda
    saíam todos sem dono.
    """
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    with client.session_transaction(base_url=BASE_A) as sessao:
        assert sessao["username"] == "admin"
        assert sessao["usuario_id"]
        assert sessao["role"] == "admin"


def test_painel_mostra_quem_esta_logado(client, two_tenants):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    html = client.get("/admin/", base_url=BASE_A).get_data(as_text=True)

    assert "Logado como" in html
    assert "admin" in html


def test_movimentacao_de_estoque_registra_o_autor(client, two_tenants):
    """Contraprova do bug: a coluna "quem fez" precisa sair preenchida."""
    from app.models.assinatura import Plano
    from app.models.estoque import Insumo
    from app.models.tenant import Tenant

    tenant = db.session.get(Tenant, two_tenants["tenant_a"])
    plano = Plano(slug="starter", nome="Starter", preco_mensal=99.0)
    plano.definir_recursos(["estoque"])
    tenant.plano = "starter"
    insumo = Insumo(tenant_id=tenant.id, nome="Carne", unidade="g",
                    preco_compra=100.0, quantidade_compra=1000.0, estoque_atual=0.0)
    db.session.add_all([plano, insumo])
    db.session.commit()

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    client.post(
        f"/admin/insumos/{insumo.id}/movimentar",
        data={"tipo": "entrada", "quantidade": "500", "observacao": "compra"},
        base_url=BASE_A,
    )

    from app.models.estoque import MovimentacaoEstoque

    movimento = MovimentacaoEstoque.query.filter_by(insumo_id=insumo.id).first()
    assert movimento is not None
    assert movimento.usuario == "admin", "a movimentação saiu sem dono"


def test_o_padrao_de_ociosidade_cobre_o_navegador_restaurado():
    """Eram 240 minutos, e era por isso que fechar o navegador e voltar não
    pedia senha.

    Chrome e Edge com "continuar de onde parou" restauram o cookie de sessão —
    nenhuma configuração no servidor impede isso. O que o servidor controla é
    por quanto tempo aquele cookie restaurado ainda vale, e quatro horas era
    tempo de sobra para o cliente sair, voltar e continuar dentro.

    Trinta minutos é o corte. Quem está com o painel aberto não cai: a tela da
    cozinha consulta o servidor sozinha, e isso conta como atividade.
    """
    from app.config import Config

    assert Config.SESSION_IDLE_MINUTES == 30


def test_a_pagina_do_produto_nao_tem_atalho_para_o_painel(client, app):
    """Ela é o cartão de visita: quem entra ainda não é cliente. Quem já é entra
    pelo endereço do próprio restaurante."""
    corpo = client.get("/", base_url="http://app.localhost").get_data(as_text=True)

    assert "Já sou cliente" not in corpo
    assert "/plataforma" not in corpo
