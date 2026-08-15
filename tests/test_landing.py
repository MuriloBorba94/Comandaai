"""Página inicial do produto — o cartão de visita do Comanda ai.

Duas coisas importam aqui e nenhuma é estética:

1. **Ela não pode prometer o que o sistema não faz.** Os planos e os preços saem
   do catálogo real; se a página inventasse, o cliente assinaria esperando algo
   que não existe. Já aconteceu uma vez de uma descrição de plano anunciar
   "relatórios" antes da tela existir.
2. **Ela não pode oferecer o painel fora do host da plataforma.** No subdomínio
   de um restaurante quem entra é o cliente final, que nunca deve ver caminho
   para a área administrativa.
"""

from __future__ import annotations

import pytest

from app.extensions import db
from app.models.assinatura import Plano
from app.models.produto import Produto
from app.models.tenant import Tenant

BASE_PLATAFORMA = "http://app.localhost"


@pytest.fixture()
def catalogo(app):
    """Dois planos ativos e um desativado, para provar o filtro."""
    gratis = Plano(slug="teste", nome="Teste", preco_mensal=0.0, ordem=0)
    gratis.definir_recursos(["cozinha"])

    pago = Plano(slug="pro", nome="Pro", preco_mensal=199.90, ordem=1,
                 descricao="Para quem já vende todo dia")
    pago.definir_recursos(["cozinha", "mesas", "financeiro"])
    pago.definir_limites({"max_produtos": 80})

    fora = Plano(slug="antigo", nome="Antigo", preco_mensal=49.0, ordem=2, ativo=False)
    fora.definir_recursos(["cozinha"])

    db.session.add_all([gratis, pago, fora])
    db.session.commit()
    return {"gratis": gratis, "pago": pago, "fora": fora}


def _landing(client):
    return client.get("/", base_url=BASE_PLATAFORMA).get_data(as_text=True)


def test_landing_abre_no_host_da_plataforma(client):
    resposta = client.get("/", base_url=BASE_PLATAFORMA)

    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert "Comanda ai" in corpo
    assert "0% de comissão" in corpo


def test_planos_saem_do_catalogo_real(client, catalogo):
    corpo = _landing(client)

    assert "Pro" in corpo
    assert "199,90" in corpo
    assert "Para quem já vende todo dia" in corpo
    assert "Grátis" in corpo, "plano sem preço aparece como grátis"


def test_plano_desativado_nao_aparece(client, catalogo):
    """Tirar um plano do ar tem que tirá-lo da página de vendas também."""
    corpo = _landing(client)

    assert "Antigo" not in corpo
    assert "49,00" not in corpo


def test_cada_plano_mostra_os_recursos_que_libera(client, catalogo):
    corpo = _landing(client)

    # O plano Pro libera financeiro; o Teste, não. Os rótulos vêm do catálogo.
    assert "Financeiro" in corpo
    assert "Salão e comanda de mesa" in corpo
    # Nada de recurso que nenhum dos dois planos libera.
    assert "Cupons de desconto" not in corpo


def test_limite_do_plano_aparece_na_pagina(client, catalogo):
    assert "até 80" in _landing(client)


def test_pagina_nao_promete_o_que_nao_existe(client, catalogo):
    """PIX automático, WhatsApp e impressão estão no roadmap, não no produto."""
    corpo = _landing(client).lower()

    for promessa in ("pix autom", "whatsapp autom", "impressão autom", "aplicativo para android"):
        assert promessa not in corpo, f"a página promete {promessa!r}, que ainda não existe"


def test_calculadora_usa_o_plano_pago_mais_barato(client, catalogo):
    """A referência da conta precisa ser um preço real do catálogo."""
    barato = Plano(slug="starter", nome="Starter", preco_mensal=99.90, ordem=1)
    barato.definir_recursos(["cozinha"])
    db.session.add(barato)
    db.session.commit()

    corpo = _landing(client)

    assert "const MENSALIDADE = 99.9" in corpo
    assert "plano Starter" in corpo


def test_sem_plano_no_catalogo_a_secao_de_precos_some(client):
    """Página de vendas sem preço nenhum é pior do que sem seção de preço."""
    corpo = _landing(client)

    assert 'id="planos"' not in corpo
    assert "Mensalidade fixa" not in corpo


# --------------------------------------------------------------------------- #
# A fronteira que importa: o painel não se oferece fora da plataforma
# --------------------------------------------------------------------------- #


def test_subdominio_de_restaurante_mostra_a_vitrine_e_nao_a_landing(client, app):
    tenant = Tenant(slug="pizzaria", nome_fantasia="Pizzaria Teste",
                    email_contato="p@example.com", status="active")
    db.session.add(tenant)
    db.session.flush()
    db.session.add(Produto(tenant_id=tenant.id, nome="Pizza", preco=40.0, disponivel=True))
    db.session.commit()

    corpo = client.get("/", base_url="http://pizzaria.localhost").get_data(as_text=True)

    assert "store-banner" in corpo, "o cliente final vê o cardápio"
    assert "0% de comissão" not in corpo, "a página de vendas não é para o cliente do restaurante"
    assert "/plataforma/" not in corpo, "nenhum caminho para a área administrativa"


def test_landing_da_plataforma_oferece_entrada_no_painel(client):
    corpo = _landing(client)

    assert "/plataforma/" in corpo
