"""Contato deixado na página do produto, ao escolher um plano.

Antes o botão "Quero este" rolava a página até a chamada final e parava ali:
quem estava decidido não tinha o que fazer, e do lado de cá não chegava sinal
nenhum — visita interessada e visita que só passou eram a mesma coisa no
servidor.

O teste que mais importa é o do endereço: este formulário é da PLATAFORMA, e
não pode existir dentro do subdomínio de um restaurante.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models.interesse import (
    SITUACAO_DESCARTADO,
    SITUACAO_FECHADO,
    SITUACAO_NOVO,
    Interesse,
)
from app.services.interesses import atualizar, quantos_novos, registrar

BASE_PRODUTO = "http://app.localhost"
BASE_TENANT = "http://tenant-a.localhost"

DADOS = {
    "nome": "Murilo Borba",
    "telefone": "81996353503",
    "email": "murilo@example.com",
    "mensagem": "Tenho uma hamburgueria com 8 mesas.",
    "plano": "Pro",
}


# --------------------------------------------------------------------------- #
# Receber o contato
# --------------------------------------------------------------------------- #


def test_o_contato_e_gravado_com_o_plano_escolhido(app):
    contato = registrar(DADOS)

    assert contato.nome == "Murilo Borba"
    assert contato.plano == "Pro"
    assert contato.situacao == SITUACAO_NOVO


def test_o_plano_e_texto_congelado_e_nao_chave(app):
    """O catálogo muda de nome e de preço com o tempo; o que interessa aqui é o
    que a pessoa viu na tela naquele dia."""
    contato = registrar({**DADOS, "plano": "Starter"})

    assert isinstance(contato.plano, str)
    assert not hasattr(contato, "plano_id")


def test_sem_nome_e_recusado(app):
    with pytest.raises(ValueError, match="seu nome"):
        registrar({**DADOS, "nome": "   "})


def test_telefone_curto_demais_e_recusado(app):
    """Dez dígitos é o mínimo de um número com DDD. Sem isso, o contato chega
    sem o único caminho de retorno que funciona."""
    with pytest.raises(ValueError, match="telefone com DDD"):
        registrar({**DADOS, "telefone": "99999"})


def test_email_estranho_nao_bloqueia_o_contato(app):
    """Um formulário de vendas que recusa a venda para provar um ponto sobre
    formato de e-mail perde a venda. O retorno vai pelo WhatsApp."""
    contato = registrar({**DADOS, "email": "murilo(arroba)example"})

    assert contato.id is not None
    assert contato.email == "murilo(arroba)example"


def test_clicar_duas_vezes_nao_cria_dois_contatos(app):
    """Dedo nervoso ou robô — nos dois casos uma linha basta."""
    primeiro = registrar(DADOS)
    segundo = registrar({**DADOS, "mensagem": "outra coisa"})

    assert primeiro.id == segundo.id
    assert Interesse.query.count() == 1


def test_o_mesmo_telefone_no_dia_seguinte_e_contato_novo(app):
    """A janela é curta de propósito: quem voltou com outra dúvida merece
    aparecer de novo, senão o segundo contato some."""
    antigo = registrar(DADOS)
    antigo.criado_em = datetime.now() - timedelta(hours=3)
    db.session.commit()

    novo = registrar(DADOS)

    assert novo.id != antigo.id
    assert Interesse.query.count() == 2


def test_texto_longo_demais_e_cortado_e_nao_recusado(app):
    contato = registrar({**DADOS, "mensagem": "x" * 5000})

    assert len(contato.mensagem) == 1000


# --------------------------------------------------------------------------- #
# Onde o formulário existe
# --------------------------------------------------------------------------- #


def test_o_endereco_responde_na_pagina_do_produto(client, app):
    resposta = client.post("/interesse", data=DADOS, base_url=BASE_PRODUTO)

    assert resposta.status_code == 200
    assert resposta.get_json()["status"] == "ok"
    assert Interesse.query.count() == 1


def test_o_endereco_nao_existe_dentro_de_um_restaurante(client, two_tenants):
    """Ali quem visita é cliente de lanche: um formulário de vendas da
    plataforma não tem o que fazer no meio do cardápio."""
    resposta = client.post("/interesse", data=DADOS, base_url=BASE_TENANT)

    assert resposta.status_code == 404
    assert Interesse.query.count() == 0


def test_contato_invalido_responde_com_a_razao(client, app):
    resposta = client.post("/interesse", data={**DADOS, "telefone": "1"}, base_url=BASE_PRODUTO)

    assert resposta.status_code == 400
    assert "DDD" in resposta.get_json()["mensagem"]


# --------------------------------------------------------------------------- #
# A página do produto
# --------------------------------------------------------------------------- #


def test_a_calculadora_de_comissao_saiu_da_pagina(client, app):
    corpo = client.get("/", base_url=BASE_PRODUTO).get_data(as_text=True)

    assert "saida-comissao" not in corpo
    assert "Calcular o que economizo" not in corpo


def test_o_botao_do_plano_abre_a_janela_em_vez_de_rolar(client, app):
    from app.models.assinatura import Plano

    db.session.add(Plano(slug="pro", nome="Pro", preco_mensal=149.0, ativo=True))
    db.session.commit()

    corpo = client.get("/", base_url=BASE_PRODUTO).get_data(as_text=True)

    assert 'data-plano="Pro"' in corpo
    assert 'id="lead-form"' in corpo


def test_plano_gratuito_nao_aparece_na_pagina(client, app):
    """Anunciar o grátis faz o visitante escolher o grátis e nunca conversar com
    ninguém. O teste passou a ser liberado no contato."""
    from app.models.assinatura import Plano

    db.session.add(Plano(slug="teste", nome="Teste", preco_mensal=0.0, ativo=True))
    db.session.add(Plano(slug="starter", nome="Starter", preco_mensal=89.0, ativo=True))
    db.session.commit()

    corpo = client.get("/", base_url=BASE_PRODUTO).get_data(as_text=True)

    assert 'data-plano="Starter"' in corpo
    assert 'data-plano="Teste"' not in corpo
    # E o plano continua no catálogo: é ele que sustenta o período de teste.
    assert Plano.query.filter_by(slug="teste").count() == 1


# --------------------------------------------------------------------------- #
# A caixa de entrada da plataforma
# --------------------------------------------------------------------------- #


def test_a_tela_da_plataforma_lista_os_contatos(client, platform_admin, app):
    registrar(DADOS)
    client.post(
        "/plataforma/login",
        data={"username": "admin", "password": "senha-super-admin-123"},
        base_url=BASE_PRODUTO,
        follow_redirects=True,
    )

    corpo = client.get("/plataforma/interesses", base_url=BASE_PRODUTO).get_data(as_text=True)

    assert "Murilo Borba" in corpo
    assert "Tenho uma hamburgueria" in corpo
    # O telefone vira link do WhatsApp: copiar número à mão é onde se erra um
    # dígito, e o retorno acontece por lá de qualquer jeito.
    assert "wa.me/5581996353503" in corpo


def test_um_restaurante_nao_ve_os_contatos_da_plataforma(client, two_tenants):
    from tests.conftest import login_tenant

    registrar(DADOS)
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.get("/plataforma/interesses", base_url=BASE_TENANT)

    assert resposta.status_code in (302, 403, 404)
    assert "Murilo Borba" not in resposta.get_data(as_text=True)


def test_o_contador_do_menu_conta_so_os_sem_resposta(app):
    primeiro = registrar(DADOS)
    registrar({**DADOS, "telefone": "81988887777"})
    assert quantos_novos() == 2

    atualizar(primeiro, situacao=SITUACAO_FECHADO)

    assert quantos_novos() == 1


def test_situacao_invalida_e_recusada(app):
    contato = registrar(DADOS)

    with pytest.raises(ValueError, match="Situação inválida"):
        atualizar(contato, situacao="virou_amigo")

    assert contato.situacao == SITUACAO_NOVO


def test_anotacao_fica_guardada_com_o_contato(app):
    contato = registrar(DADOS)

    atualizar(contato, situacao=SITUACAO_DESCARTADO, anotacao="Já usa outro sistema.")

    assert contato.anotacao == "Já usa outro sistema."
    assert contato.situacao == SITUACAO_DESCARTADO
