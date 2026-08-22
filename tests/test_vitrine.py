"""O cardápio e o fechamento do pedido, do lado do cliente.

Esta é a única tela do sistema usada por quem não foi treinado em nada: alguém
com fome, no celular, no 3G da rua. O que os testes protegem aqui é o caminho
até o pedido sair — cada campo a mais é gente desistindo no meio.
"""

from __future__ import annotations

import pytest

from app.extensions import db
from app.models.categoria import Categoria
from app.models.cupom import BairroEntrega
from app.models.pedido import TIPO_ENTREGA, TIPO_MESA, TIPO_RETIRADA, Pedido
from app.models.produto import Produto
from app.models.tenant import Tenant

BASE_A = "http://tenant-a.localhost"


@pytest.fixture()
def vitrine(app, two_tenants):
    tenant = db.session.get(Tenant, two_tenants["tenant_a"])
    categoria = Categoria(tenant_id=tenant.id, nome="Burgers", ordem=1)
    db.session.add(categoria)
    db.session.flush()
    db.session.add_all(
        [
            Produto(
                tenant_id=tenant.id,
                nome="X-Tudo",
                preco=30.0,
                categoria_id=categoria.id,
                imagem="tenant-a/xtudo.webp",
                descricao="Hambúrguer, queijo, bacon, ovo e salada.",
            ),
            Produto(tenant_id=tenant.id, nome="Refrigerante", preco=6.0, categoria_id=categoria.id),
            BairroEntrega(tenant_id=tenant.id, nome="Centro", taxa=0.0, ativo=True),
        ]
    )
    db.session.commit()
    return tenant


def _com_item(client, vitrine, quantidade=2):
    produto = Produto.query.filter_by(tenant_id=vitrine.id).first()
    client.post(
        "/carrinho/adicionar",
        data={"produto_id": produto.id, "quantidade": quantidade},
        base_url=BASE_A,
    )
    return produto


# --------------------------------------------------------------------------- #
# A foto
# --------------------------------------------------------------------------- #


def test_foto_do_produto_pode_ser_ampliada(client, vitrine):
    """Foto grande é o que decide a compra num cardápio de hambúrguer."""
    texto = client.get("/", base_url=BASE_A).get_data(as_text=True)

    assert 'id="image-lightbox"' in texto
    # A miniatura é marcada como ampliável; o produto sem foto não é.
    assert "data-foto" in texto


def test_produto_sem_foto_nao_oferece_ampliar(client, vitrine):
    """Ampliar um "sem foto" seria um clique que não leva a lugar nenhum."""
    texto = client.get("/", base_url=BASE_A).get_data(as_text=True)

    # Conta o ATRIBUTO na tag, não a palavra solta: ela também aparece no
    # seletor do script, e contar as duas daria um teste que passa por engano.
    import re

    marcadas = re.findall(r"<img[^>]*\sdata-foto[^>]*>", texto)
    assert len(marcadas) == 1
    assert "thumb-vazia" in texto


# --------------------------------------------------------------------------- #
# O fechamento
# --------------------------------------------------------------------------- #


def test_checkout_oferece_so_entrega_e_retirada(client, vitrine):
    """Comanda de mesa quem abre é o atendente, no salão."""
    _com_item(client, vitrine)
    texto = client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)

    assert 'value="Entrega"' in texto
    assert 'value="Retirada"' in texto
    assert 'value="Mesa"' not in texto


def test_endereco_e_bairro_ficam_num_bloco_que_some(client, vitrine):
    """Pedir rua a quem vai buscar no balcão faz a pessoa desistir no meio."""
    _com_item(client, vitrine)
    texto = client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)

    # O bloco existe e é identificável pelo script que o esconde.
    assert 'id="campos-entrega"' in texto
    assert 'id="endereco"' in texto
    assert 'id="bairro_id"' in texto
    # E o script que faz o `required` acompanhar está na página: sem ele, o
    # navegador barraria o envio por causa de um campo escondido.
    assert "endereco.required" in texto


def test_observacao_comeca_fechada(client, vitrine):
    """Campo de texto sempre aberto sugere que alguém precisa preenchê-lo."""
    _com_item(client, vitrine)
    texto = client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)

    assert "detalhe-observacao" in texto
    assert "<summary>" in texto


def test_botao_final_mostra_o_valor(client, vitrine):
    """A última confirmação de quanto vai sair, onde o dedo já está."""
    _com_item(client, vitrine, quantidade=2)
    texto = client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)

    assert "Fazer pedido" in texto
    assert "R$ 60,00" in texto


def test_cupom_ficou_junto_da_sacola(client, vitrine):
    """Era um cartão só dele: mais um bloco para rolar antes de fechar."""
    from app.models.cupom import Cupom

    db.session.add(Cupom(tenant_id=vitrine.id, codigo="DEZ", tipo="fixo", valor=10.0, ativo=True))
    db.session.commit()
    _com_item(client, vitrine)

    texto = client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)
    assert "linha-cupom" in texto
    # Um cartão de pedido e um de fechamento — não três.
    assert texto.count('class="card-admin"') == 2


# --------------------------------------------------------------------------- #
# O pedido sai
# --------------------------------------------------------------------------- #


def test_retirada_sem_endereco_e_aceita(client, vitrine):
    """O caminho que o esconder-campos precisa deixar funcionando."""
    _com_item(client, vitrine)

    client.post(
        "/pedido",
        data={
            "cliente": "Maria",
            "telefone": "81999998888",
            "tipo": TIPO_RETIRADA,
            "pagamento": "Dinheiro",
        },
        base_url=BASE_A,
        follow_redirects=True,
    )

    pedido = Pedido.query.one()
    assert pedido.tipo == TIPO_RETIRADA
    assert pedido.total == 60.0


def test_entrega_sem_endereco_e_recusada(client, vitrine):
    """Esconder o campo não pode virar aceitar entrega sem endereço."""
    _com_item(client, vitrine)

    resposta = client.post(
        "/pedido",
        data={
            "cliente": "Maria",
            "telefone": "81999998888",
            "tipo": TIPO_ENTREGA,
            "pagamento": "Dinheiro",
        },
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert "endereço completo" in resposta.get_data(as_text=True)
    assert Pedido.query.count() == 0


def test_entrega_com_endereco_soma_a_taxa_do_bairro(client, vitrine):
    bairro = BairroEntrega.query.filter_by(tenant_id=vitrine.id).first()
    bairro.taxa = 5.0
    db.session.commit()
    _com_item(client, vitrine)

    client.post(
        "/pedido",
        data={
            "cliente": "Maria",
            "telefone": "81999998888",
            "tipo": TIPO_ENTREGA,
            "endereco": "Rua das Flores, 123",
            "bairro_id": bairro.id,
            "pagamento": "Dinheiro",
        },
        base_url=BASE_A,
        follow_redirects=True,
    )

    pedido = Pedido.query.one()
    assert pedido.taxa_entrega == 5.0
    assert pedido.total == 65.0
