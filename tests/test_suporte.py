"""Fase 10 — entrar como um restaurante para dar suporte.

É o código mais delicado do sistema: um jeito de alguém da plataforma operar
dentro da conta de um cliente. Ele não fica seguro por ser restrito — fica
seguro por ser curto, visível e registrado. Estes testes provam as três coisas,
e cada teste corresponde a uma trava:

- o passe **expira**, **serve uma vez** e é **preso a um restaurante**;
- só quem é da plataforma consegue emitir;
- a faixa aparece em toda página;
- entrar e sair vão para o diário, e o que for feito dentro sai com o nome de
  quem entrou, não com o do dono do restaurante.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models.auditoria import (
    ACAO_IMPERSONACAO_FIM,
    ACAO_IMPERSONACAO_INICIO,
    ACAO_PEDIDO_CANCELADO,
    Auditoria,
)
from app.models.pedido import STATUS_CANCELADO, TIPO_RETIRADA
from app.models.produto import Produto
from app.models.suporte import PasseSuporte
from app.models.tenant import Tenant
from app.services import suporte
from app.services.pedidos import criar_pedido
from tests.conftest import login_tenant

BASE_A = "http://tenant-a.localhost"
BASE_B = "http://tenant-b.localhost"
BASE_PLATAFORMA = "http://app.localhost"


@pytest.fixture()
def loja(app, two_tenants):
    tenant = db.session.get(Tenant, two_tenants["tenant_a"])
    db.session.add(Produto(tenant_id=tenant.id, nome="X-Tudo", preco=30.0))
    db.session.commit()
    return tenant


def _entrar_na_plataforma(client):
    return client.post(
        "/plataforma/login",
        data={"username": "admin", "password": "senha-super-admin-123"},
        base_url=BASE_PLATAFORMA,
    )


def _token_de(client, tenant_id: int) -> str:
    """Pede o passe e extrai o token do redirecionamento."""
    resposta = client.post(
        f"/plataforma/tenants/{tenant_id}/suporte", base_url=BASE_PLATAFORMA
    )
    return resposta.headers["Location"].rsplit("/", 1)[-1]


# --------------------------------------------------------------------------- #
# As três travas do passe
# --------------------------------------------------------------------------- #


def test_passe_expira(client, platform_admin, loja):
    _entrar_na_plataforma(client)
    token = _token_de(client, loja.id)

    passe = PasseSuporte.query.one()
    passe.expira_em = datetime.now() - timedelta(seconds=1)
    db.session.commit()

    with pytest.raises(suporte.PasseInvalido, match="expirou"):
        suporte.consumir(loja, token)


def test_passe_serve_uma_vez_so(client, platform_admin, loja):
    _entrar_na_plataforma(client)
    token = _token_de(client, loja.id)

    primeira = client.get(f"/suporte/{token}", base_url=BASE_A, follow_redirects=True)
    assert "Modo suporte" in primeira.get_data(as_text=True)

    segunda = client.get(f"/suporte/{token}", base_url=BASE_A, follow_redirects=True)
    assert "já foi usado" in segunda.get_data(as_text=True)


def test_passe_de_um_restaurante_nao_abre_o_outro(client, platform_admin, loja, two_tenants):
    """Não é engano provável — é o que alguém tentaria de propósito."""
    _entrar_na_plataforma(client)
    token = _token_de(client, loja.id)
    outro = db.session.get(Tenant, two_tenants["tenant_b"])

    with pytest.raises(suporte.PasseInvalido, match="não é deste restaurante"):
        suporte.consumir(outro, token)


def test_token_e_guardado_como_hash(client, platform_admin, loja):
    """Quem lê o banco não consegue entrar em restaurante nenhum."""
    _entrar_na_plataforma(client)
    token = _token_de(client, loja.id)

    passe = PasseSuporte.query.one()
    assert passe.token_hash != token
    assert len(passe.token_hash) == 64


def test_link_de_suporte_inventado_nao_abre_nada(client, loja):
    resposta = client.get("/suporte/token-inventado", base_url=BASE_A, follow_redirects=True)

    texto = resposta.get_data(as_text=True)
    assert "não existe" in texto
    # Parou na tela de login, e não dentro do painel.
    assert "Entrar" in texto


# --------------------------------------------------------------------------- #
# Quem pode emitir
# --------------------------------------------------------------------------- #


def test_so_a_plataforma_emite_passe(client, loja):
    """Sem login de super-admin, o botão não existe e a rota também não abre."""
    resposta = client.post(
        f"/plataforma/tenants/{loja.id}/suporte", base_url=BASE_PLATAFORMA
    )

    assert resposta.status_code in (302, 401, 403)
    assert PasseSuporte.query.count() == 0


def test_usuario_de_restaurante_nao_emite_passe(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(f"/plataforma/tenants/{loja.id}/suporte", base_url=BASE_PLATAFORMA)

    assert PasseSuporte.query.count() == 0


# --------------------------------------------------------------------------- #
# Dentro da sessão de suporte
# --------------------------------------------------------------------------- #


def test_fluxo_completo_entra_no_painel_do_restaurante(client, platform_admin, loja):
    _entrar_na_plataforma(client)
    token = _token_de(client, loja.id)

    resposta = client.get(f"/suporte/{token}", base_url=BASE_A, follow_redirects=True)

    texto = resposta.get_data(as_text=True)
    assert "Modo suporte" in texto
    assert loja.nome_fantasia in texto
    # Entrou de fato: o painel do restaurante está aberto.
    assert "Central de Gestão" in texto


def test_faixa_de_suporte_aparece_em_toda_pagina(client, platform_admin, loja):
    _entrar_na_plataforma(client)
    token = _token_de(client, loja.id)
    client.get(f"/suporte/{token}", base_url=BASE_A)

    for caminho in ("/admin/", "/admin/produtos", "/admin/configuracoes"):
        texto = client.get(caminho, base_url=BASE_A).get_data(as_text=True)
        assert "Modo suporte" in texto, caminho


def test_o_que_o_suporte_faz_sai_com_o_nome_dele_no_diario(client, platform_admin, loja):
    """Atribuir ao dono do restaurante algo que a plataforma fez seria mentira."""
    _entrar_na_plataforma(client)
    token = _token_de(client, loja.id)
    client.get(f"/suporte/{token}", base_url=BASE_A)

    produto = Produto.query.filter_by(tenant_id=loja.id).first()
    pedido = criar_pedido(
        loja,
        {
            "cliente": "Maria",
            "telefone": "81999998888",
            "tipo": TIPO_RETIRADA,
            "pagamento": "Dinheiro",
            "carrinho": [{"produto_id": produto.id, "quantidade": 1}],
        },
    )
    client.post(
        f"/cozinha/pedidos/{pedido.id}/status",
        data={"status": STATUS_CANCELADO},
        base_url=BASE_A,
        follow_redirects=True,
    )

    registro = Auditoria.query.filter_by(acao=ACAO_PEDIDO_CANCELADO).one()
    assert registro.ator == "admin (suporte)"
    assert registro.ator_tipo == "plataforma"


def test_entrada_e_saida_ficam_no_diario(client, platform_admin, loja):
    _entrar_na_plataforma(client)
    token = _token_de(client, loja.id)
    client.get(f"/suporte/{token}", base_url=BASE_A)

    client.post("/suporte/sair", base_url=BASE_A, follow_redirects=True)

    assert Auditoria.query.filter_by(acao=ACAO_IMPERSONACAO_INICIO).count() == 1
    assert Auditoria.query.filter_by(acao=ACAO_IMPERSONACAO_FIM).count() == 1


def test_passe_pedido_e_nao_usado_tambem_fica_registrado(client, platform_admin, loja):
    """Alguém quis entrar na conta daquele cliente — isso também é informação."""
    _entrar_na_plataforma(client)
    _token_de(client, loja.id)

    assert Auditoria.query.filter_by(acao=ACAO_IMPERSONACAO_INICIO).count() == 1


def test_sair_pelo_botao_comum_tambem_registra_o_fim(client, platform_admin, loja):
    """Senão o diário mostra uma entrada sem fim correspondente."""
    _entrar_na_plataforma(client)
    token = _token_de(client, loja.id)
    client.get(f"/suporte/{token}", base_url=BASE_A)

    client.get("/logout", base_url=BASE_A, follow_redirects=True)

    assert Auditoria.query.filter_by(acao=ACAO_IMPERSONACAO_FIM).count() == 1


def test_sessao_de_suporte_termina_sozinha(client, platform_admin, loja):
    """O relógio não para enquanto a pessoa mexe: ela deve terminar sozinha."""
    import time

    _entrar_na_plataforma(client)
    token = _token_de(client, loja.id)
    client.get(f"/suporte/{token}", base_url=BASE_A)

    # base_url importa: o cookie de sessão é POR HOST, e sem isto a alteração
    # iria para a sessão de "localhost" em vez da do restaurante.
    with client.session_transaction(base_url=BASE_A) as sessao:
        sessao[suporte.CHAVE_ATE] = time.time() - 1

    resposta = client.get("/admin/", base_url=BASE_A, follow_redirects=False)
    assert resposta.status_code == 302  # foi para o login

    with client.session_transaction(base_url=BASE_A) as sessao:
        assert not sessao.get("logged_in")


def test_sair_do_suporte_encerra_a_sessao_no_restaurante(client, platform_admin, loja):
    _entrar_na_plataforma(client)
    token = _token_de(client, loja.id)
    client.get(f"/suporte/{token}", base_url=BASE_A)

    client.post("/suporte/sair", base_url=BASE_A)

    resposta = client.get("/admin/", base_url=BASE_A, follow_redirects=False)
    assert resposta.status_code == 302


def test_sessao_de_suporte_nao_vale_em_outro_restaurante(client, platform_admin, loja):
    """A separação por host continua valendo: é ela que protege um do outro."""
    _entrar_na_plataforma(client)
    token = _token_de(client, loja.id)
    client.get(f"/suporte/{token}", base_url=BASE_A)

    resposta = client.get("/admin/", base_url=BASE_B, follow_redirects=False)

    assert resposta.status_code == 302
