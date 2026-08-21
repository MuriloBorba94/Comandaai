"""Equipe, entregas e o rastreio na tela do cliente.

O desenho da tela de entregas veio de um dado do sistema antigo: de 774
entregas, só 37 registraram posição, e nenhuma depois de 16/08. A hipótese é
que a página do entregador só servia ao cliente — alguém tinha que manter uma
tela aberta em benefício de outra pessoa. Aqui ela é a ferramenta de trabalho
(endereço, rota, baixa) e a posição vai junto.

Os testes que mais importam:

1. **Ninguém se tranca para fora.** O último admin ativo não pode se rebaixar
   nem se desativar — a saída seria eu entrar no servidor.
2. **A posição é do pedido, não da pessoa.** Ela some quando a entrega acaba, e
   posição velha não vira ponto parado no mapa do cliente.
3. **Entregador de um restaurante não enxerga o outro.**
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models.pedido import (
    STATUS_CONFIRMADO,
    STATUS_EM_PREPARO,
    STATUS_ENTREGUE,
    STATUS_PRONTO,
    STATUS_SAIU_ENTREGA,
    TIPO_ENTREGA,
    Pedido,
)
from app.models.produto import Produto
from app.models.tenant import Tenant
from app.models.usuario import ROLE_ADMIN, ROLE_ATENDENTE, ROLE_ENTREGADOR, Usuario
from app.services.pedidos import criar_pedido, transicionar
from tests.conftest import login_tenant

BASE_A = "http://tenant-a.localhost"
BASE_B = "http://tenant-b.localhost"


@pytest.fixture()
def loja(app, two_tenants):
    tenant = db.session.get(Tenant, two_tenants["tenant_a"])
    outro = db.session.get(Tenant, two_tenants["tenant_b"])
    db.session.add_all(
        [
            Produto(tenant_id=tenant.id, nome="X-Tudo", preco=30.0),
            Produto(tenant_id=outro.id, nome="Pizza", preco=40.0),
        ]
    )
    db.session.commit()
    return {"tenant_a": tenant, "tenant_b": outro}


def _entrega(tenant, **extra):
    produto = Produto.query.filter_by(tenant_id=tenant.id).first()
    dados = {
        "cliente": "Maria",
        "telefone": "81999998888",
        "tipo": TIPO_ENTREGA,
        "endereco": "Rua das Flores, 123",
        "pagamento": "Dinheiro",
        "carrinho": [{"produto_id": produto.id, "quantidade": 1}],
    }
    dados.update(extra)
    return criar_pedido(tenant, dados)


def _pronto(tenant):
    pedido = _entrega(tenant)
    transicionar(pedido, STATUS_CONFIRMADO)
    transicionar(pedido, STATUS_EM_PREPARO)
    transicionar(pedido, STATUS_PRONTO)
    return pedido


# --------------------------------------------------------------------------- #
# Equipe
# --------------------------------------------------------------------------- #


def test_admin_cria_um_entregador(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/equipe",
        data={"nome": "João", "username": "joao", "senha": "senha-do-joao", "role": ROLE_ENTREGADOR},
        base_url=BASE_A,
        follow_redirects=True,
    )

    joao = Usuario.query.filter_by(tenant_id=loja["tenant_a"].id, username="joao").one()
    assert joao.role == ROLE_ENTREGADOR
    assert joao.check_password("senha-do-joao")


def test_senha_curta_e_recusada(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/equipe",
        data={"nome": "João", "username": "joao", "senha": "1234", "role": ROLE_ENTREGADOR},
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert Usuario.query.filter_by(username="joao").first() is None


def test_username_repetido_no_mesmo_restaurante_e_recusado(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.post(
        "/admin/equipe",
        data={"nome": "Outro", "username": "admin", "senha": "senha-boa-123", "role": ROLE_ATENDENTE},
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert "Já existe" in resposta.get_data(as_text=True)
    assert Usuario.query.filter_by(tenant_id=loja["tenant_a"].id, username="admin").count() == 1


def test_o_ultimo_admin_nao_consegue_se_trancar_para_fora(client, loja):
    """Sem esta trava, o restaurante fica sem ninguém que mexa nas configurações."""
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    admin = Usuario.query.filter_by(tenant_id=loja["tenant_a"].id, username="admin").one()

    # Tentar virar atendente...
    resposta = client.post(
        f"/admin/equipe/{admin.id}/salvar",
        data={"nome": "Admin A", "role": ROLE_ATENDENTE, "ativo": "on"},
        base_url=BASE_A,
        follow_redirects=True,
    )
    assert "único administrador" in resposta.get_data(as_text=True)
    assert admin.role == ROLE_ADMIN

    # ...e tentar se desativar.
    client.post(
        f"/admin/equipe/{admin.id}/salvar",
        data={"nome": "Admin A", "role": ROLE_ADMIN},
        base_url=BASE_A,
        follow_redirects=True,
    )
    assert admin.ativo is True


def test_com_outro_admin_a_troca_e_permitida(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    segundo = Usuario(
        tenant_id=loja["tenant_a"].id, nome="Segundo", username="segundo", role=ROLE_ADMIN
    )
    segundo.set_password("senha-do-segundo")
    db.session.add(segundo)
    db.session.commit()

    admin = Usuario.query.filter_by(tenant_id=loja["tenant_a"].id, username="admin").one()
    client.post(
        f"/admin/equipe/{admin.id}/salvar",
        data={"nome": "Admin A", "role": ROLE_ATENDENTE, "ativo": "on"},
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert admin.role == ROLE_ATENDENTE


def test_limite_de_usuarios_do_plano_e_aplicado(client, loja):
    """O limite existia no catálogo e não limitava nada até haver esta tela."""
    from app.models.assinatura import Plano

    plano = Plano(slug="basico", nome="Básico")
    plano.definir_limites({"max_usuarios": 1})
    db.session.add(plano)
    loja["tenant_a"].plano = "basico"
    db.session.commit()

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    resposta = client.post(
        "/admin/equipe",
        data={"nome": "João", "username": "joao", "senha": "senha-do-joao", "role": ROLE_ENTREGADOR},
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert "permite até 1" in resposta.get_data(as_text=True)
    assert Usuario.query.filter_by(username="joao").first() is None


def test_desativar_alguem_devolve_a_vaga_do_plano(client, loja):
    from app.services.recursos import uso_do_tenant

    outro = Usuario(tenant_id=loja["tenant_a"].id, nome="Ana", username="ana", role=ROLE_ATENDENTE)
    outro.set_password("senha-da-ana")
    db.session.add(outro)
    db.session.commit()

    antes = next(x for x in uso_do_tenant(loja["tenant_a"]) if x["chave"] == "max_usuarios")["usado"]
    outro.ativo = False
    db.session.commit()
    depois = next(x for x in uso_do_tenant(loja["tenant_a"]) if x["chave"] == "max_usuarios")["usado"]

    assert depois == antes - 1


def test_atendente_nao_abre_a_tela_de_equipe(client, loja):
    """Quem pode criar usuário pode criar um admin: é a porta do sistema."""
    ana = Usuario(tenant_id=loja["tenant_a"].id, nome="Ana", username="ana", role=ROLE_ATENDENTE)
    ana.set_password("senha-da-ana")
    db.session.add(ana)
    db.session.commit()

    login_tenant(client, "tenant-a", "ana", "senha-da-ana")
    resposta = client.get("/admin/equipe", base_url=BASE_A)

    assert resposta.status_code in (302, 403)


def test_equipe_de_um_restaurante_nao_alcanca_o_outro(client, loja, two_tenants):
    alvo = Usuario.query.filter_by(tenant_id=two_tenants["tenant_b"]).one()
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        f"/admin/equipe/{alvo.id}/salvar",
        data={"nome": "Invadido", "role": ROLE_ENTREGADOR, "ativo": "on"},
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert alvo.nome != "Invadido"
    assert alvo.role == ROLE_ADMIN


# --------------------------------------------------------------------------- #
# A tela do entregador
# --------------------------------------------------------------------------- #


def test_entrega_pronta_aparece_para_alguem_pegar(client, loja):
    pedido = _pronto(loja["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    texto = client.get("/entregas/", base_url=BASE_A).get_data(as_text=True)

    assert f"#{pedido.numero}" in texto
    assert "Rua das Flores" in texto
    assert "Vou levar" in texto


def test_botao_de_rota_leva_o_endereco_para_o_mapa_do_celular(client, loja):
    """Sem geocodificação nossa: quem sabe ler "perto da igreja" é uma pessoa."""
    from app.routes.entregas import url_da_rota

    pedido = _pronto(loja["tenant_a"])
    with client.application.test_request_context(base_url=BASE_A):
        from flask import g

        g.tenant = loja["tenant_a"]
        url = url_da_rota(pedido)

    assert url.startswith("https://www.google.com/maps/dir/?api=1&destination=")
    assert "Rua%20das%20Flores" in url or "Rua+das+Flores" in url


def test_assumir_poe_o_pedido_no_meu_nome_e_na_rua(client, loja):
    pedido = _pronto(loja["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(f"/entregas/{pedido.id}/assumir", base_url=BASE_A, follow_redirects=True)

    assert pedido.status == STATUS_SAIU_ENTREGA
    assert pedido.entregador_id is not None


def test_posicao_vai_para_as_entregas_que_estao_comigo(client, loja):
    pedido = _pronto(loja["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    client.post(f"/entregas/{pedido.id}/assumir", base_url=BASE_A)

    resposta = client.post(
        "/entregas/posicao", json={"lat": -7.657, "lng": -35.321}, base_url=BASE_A
    )

    assert resposta.get_json() == {"status": "ok", "pedidos": 1}
    assert pedido.entrega_lat == pytest.approx(-7.657)
    assert pedido.entrega_atualizado_em is not None


def test_posicao_fora_do_mundo_e_recusada(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.post("/entregas/posicao", json={"lat": 999, "lng": 0}, base_url=BASE_A)

    assert resposta.status_code == 400


def test_entregar_apaga_a_posicao(client, loja):
    """Depois da entrega, saber onde a pessoa está é rastrear ela, não o pedido."""
    pedido = _pronto(loja["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    client.post(f"/entregas/{pedido.id}/assumir", base_url=BASE_A)
    client.post("/entregas/posicao", json={"lat": -7.657, "lng": -35.321}, base_url=BASE_A)

    client.post(f"/entregas/{pedido.id}/entregue", base_url=BASE_A, follow_redirects=True)

    assert pedido.status == STATUS_ENTREGUE
    assert pedido.entrega_lat is None
    assert pedido.entrega_atualizado_em is None


def test_entregador_de_um_restaurante_nao_move_pedido_do_outro(client, loja):
    pedido_b = _pronto(loja["tenant_b"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(f"/entregas/{pedido_b.id}/assumir", base_url=BASE_A, follow_redirects=True)

    assert pedido_b.status == STATUS_PRONTO
    assert pedido_b.entregador_id is None


# --------------------------------------------------------------------------- #
# O mapa que o cliente vê
# --------------------------------------------------------------------------- #


def test_cliente_ve_a_posicao_enquanto_o_pedido_esta_a_caminho(client, loja):
    pedido = _pronto(loja["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    client.post(f"/entregas/{pedido.id}/assumir", base_url=BASE_A)
    client.post("/entregas/posicao", json={"lat": -7.657, "lng": -35.321}, base_url=BASE_A)

    dados = client.get(
        f"/pedido/{pedido.public_token}/rastreio.json", base_url=BASE_A
    ).get_json()

    assert dados["lat"] == pytest.approx(-7.657)
    assert dados["status"] == STATUS_SAIU_ENTREGA


def test_posicao_velha_nao_vira_ponto_parado_no_mapa(client, loja):
    """Celular que perdeu sinal faria o cliente concluir que o entregador empacou."""
    pedido = _pronto(loja["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    client.post(f"/entregas/{pedido.id}/assumir", base_url=BASE_A)
    client.post("/entregas/posicao", json={"lat": -7.657, "lng": -35.321}, base_url=BASE_A)

    pedido.entrega_atualizado_em = datetime.now() - timedelta(minutes=20)
    db.session.commit()

    dados = client.get(
        f"/pedido/{pedido.public_token}/rastreio.json", base_url=BASE_A
    ).get_json()

    assert dados["lat"] is None


def test_mapa_so_aparece_a_caminho(client, loja):
    pedido = _pronto(loja["tenant_a"])

    antes = client.get(f"/pedido/{pedido.public_token}", base_url=BASE_A).get_data(as_text=True)
    assert "mapa-entrega" not in antes

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    client.post(f"/entregas/{pedido.id}/assumir", base_url=BASE_A)

    durante = client.get(f"/pedido/{pedido.public_token}", base_url=BASE_A).get_data(as_text=True)
    assert "mapa-entrega" in durante
    # Servido do próprio domínio: a página é aberta no 3G da rua.
    assert "vendor/leaflet.js" in durante


def test_rastreio_de_um_pedido_nao_abre_no_outro_restaurante(client, loja):
    pedido = _pronto(loja["tenant_a"])

    resposta = client.get(f"/pedido/{pedido.public_token}/rastreio.json", base_url=BASE_B)

    assert resposta.status_code == 404
