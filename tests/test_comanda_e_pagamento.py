"""Quem fecha a comanda de mesa, e quem conserta a forma de pagamento.

Os dois vieram de erro visto em operação:

1. A cozinha marcava "Entregue" e a mesa sumia do mapa com gente ainda sentada.
   Para mesa, "Entregue" quer dizer que o prato chegou — não que o cliente
   pagou e foi embora.
2. O atendente escolhe a forma de pagamento correndo, no balcão, e escolhe
   errado. O número errado só aparece no fechamento do caixa, quando ninguém
   mais lembra qual pedido foi.
"""

from __future__ import annotations

import pytest

from app.extensions import db
from app.models.auditoria import ACAO_PAGAMENTO_CORRIGIDO, Auditoria
from app.models.pedido import (
    STATUS_CANCELADO,
    STATUS_CONFIRMADO,
    STATUS_EM_PREPARO,
    STATUS_ENTREGUE,
    STATUS_PRONTO,
    TIPO_MESA,
    TIPO_RETIRADA,
)
from app.models.produto import Produto
from app.models.tenant import Tenant
from app.models.usuario import ROLE_ATENDENTE, Usuario
from app.services.pedidos import (
    adicionar_itens_comanda,
    corrigir_pagamento,
    criar_pedido,
    fechar_comanda,
    mesas_ativas,
    transicionar,
)
from tests.conftest import login_tenant

BASE_A = "http://tenant-a.localhost"


@pytest.fixture()
def salao(app, two_tenants):
    tenant = db.session.get(Tenant, two_tenants["tenant_a"])
    tenant.qtd_mesas = 6
    db.session.add(Produto(tenant_id=tenant.id, nome="X-Tudo", preco=30.0))
    db.session.commit()
    return tenant


def _comanda(tenant, mesa=1):
    produto = Produto.query.filter_by(tenant_id=tenant.id).first()
    return criar_pedido(
        tenant,
        {
            "cliente": f"Mesa {mesa:02d}",
            "tipo": TIPO_MESA,
            "mesa": mesa,
            "carrinho": [{"produto_id": produto.id, "quantidade": 1}],
            "origem": "mesa",
        },
        permitir_mesa=True,
    )


def _balcao(tenant, pagamento="Dinheiro"):
    produto = Produto.query.filter_by(tenant_id=tenant.id).first()
    return criar_pedido(
        tenant,
        {
            "cliente": "Maria",
            "telefone": "81999998888",
            "tipo": TIPO_RETIRADA,
            "pagamento": pagamento,
            "carrinho": [{"produto_id": produto.id, "quantidade": 1}],
        },
    )


# --------------------------------------------------------------------------- #
# A comanda só fecha na mão do atendente
# --------------------------------------------------------------------------- #


def test_prato_entregue_na_mesa_nao_fecha_a_comanda(salao):
    """As pessoas continuam sentadas, e vão pedir sobremesa."""
    pedido = _comanda(salao, mesa=2)
    for status in (STATUS_CONFIRMADO, STATUS_EM_PREPARO, STATUS_PRONTO, STATUS_ENTREGUE):
        transicionar(pedido, status)

    assert pedido.status == STATUS_ENTREGUE
    assert pedido.comanda_aberta is True
    # E a mesa continua no mapa do salão.
    assert 2 in mesas_ativas(salao.id)


def test_mesa_entregue_ainda_recebe_item_novo(salao):
    """Sem isto, o item seguinte abriria comanda nova: a conta partida em duas."""
    pedido = _comanda(salao, mesa=2)
    for status in (STATUS_CONFIRMADO, STATUS_EM_PREPARO, STATUS_PRONTO, STATUS_ENTREGUE):
        transicionar(pedido, status)

    produto = Produto.query.filter_by(tenant_id=salao.id).first()
    adicionar_itens_comanda(pedido, [{"produto_id": produto.id, "quantidade": 1}])

    assert pedido.total == 60.0
    # Voltou para a fila da cozinha, porque há prato novo a fazer.
    assert pedido.status == STATUS_CONFIRMADO


def test_fechar_a_comanda_libera_a_mesa(salao):
    """O caminho legítimo: o atendente fecha, com a forma de pagamento."""
    pedido = _comanda(salao, mesa=2)
    transicionar(pedido, STATUS_CONFIRMADO)

    fechar_comanda(pedido, "Cartão na entrega")

    assert pedido.comanda_aberta is False
    assert 2 not in mesas_ativas(salao.id)


def test_cancelar_continua_liberando_a_mesa(salao):
    """Pedido cancelado é mesa livre de verdade."""
    pedido = _comanda(salao, mesa=3)
    transicionar(pedido, STATUS_CANCELADO)

    assert pedido.comanda_aberta is False
    assert 3 not in mesas_ativas(salao.id)


def test_pedido_de_balcao_continua_fechando_ao_ser_entregue(salao):
    """A mudança é só para mesa: retirada e entrega seguem como estavam."""
    pedido = _balcao(salao)
    for status in (STATUS_CONFIRMADO, STATUS_EM_PREPARO, STATUS_PRONTO, STATUS_ENTREGUE):
        transicionar(pedido, status)

    assert pedido.comanda_aberta is False


# --------------------------------------------------------------------------- #
# Corrigir a forma de pagamento
# --------------------------------------------------------------------------- #


def test_admin_corrige_a_forma_de_pagamento(client, salao):
    pedido = _balcao(salao, pagamento="Dinheiro")
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        f"/cozinha/pedidos/{pedido.id}/pagamento/corrigir",
        data={"pagamento": "PIX na entrega"},
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert pedido.pagamento == "PIX na entrega"


def test_correcao_fica_no_diario_com_o_antes_e_o_depois(salao):
    """Quem consertou, quando, e o que estava lá antes."""
    pedido = _balcao(salao, pagamento="Dinheiro")

    corrigir_pagamento(pedido, "Cartão na entrega", actor="carlos")

    registro = Auditoria.query.filter_by(acao=ACAO_PAGAMENTO_CORRIGIDO).one()
    assert registro.alvo == f"Pedido #{pedido.numero}"
    assert "Dinheiro" in registro.detalhes
    assert "Cartão na entrega" in registro.detalhes


def test_corrigir_para_a_mesma_forma_nao_registra_nada(salao):
    """Salvar sem mudar não é correção, e encheria o diário de linha vazia."""
    pedido = _balcao(salao, pagamento="Dinheiro")

    corrigir_pagamento(pedido, "Dinheiro")

    assert Auditoria.query.filter_by(acao=ACAO_PAGAMENTO_CORRIGIDO).count() == 0


def test_corrigir_nao_desfaz_pagamento_online_ja_recebido(salao):
    """Trocar o rótulo não desfaz dinheiro que entrou."""
    from app.models.pagamento import STATUS_PAGO, Pagamento

    pedido = _balcao(salao)
    pagamento = Pagamento(
        tenant_id=salao.id, pedido_id=pedido.id, provedor="pix_direto",
        status=STATUS_PAGO, valor_centavos=3000,
    )
    db.session.add(pagamento)
    db.session.commit()

    corrigir_pagamento(pedido, "Dinheiro")

    assert pagamento.status == STATUS_PAGO
    assert pedido.status != STATUS_CANCELADO


def test_forma_vazia_e_recusada(salao):
    pedido = _balcao(salao)

    with pytest.raises(ValueError, match="Escolha a forma"):
        corrigir_pagamento(pedido, "   ")


def test_atendente_nao_corrige_pagamento(client, salao):
    """Deixar quem errou desfazer sozinho tira o registro de que houve erro."""
    pedido = _balcao(salao, pagamento="Dinheiro")
    ana = Usuario(tenant_id=salao.id, nome="Ana", username="ana", role=ROLE_ATENDENTE)
    ana.set_password("senha-da-ana")
    db.session.add(ana)
    db.session.commit()

    login_tenant(client, "tenant-a", "ana", "senha-da-ana")
    client.post(
        f"/cozinha/pedidos/{pedido.id}/pagamento/corrigir",
        data={"pagamento": "PIX na entrega"},
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert pedido.pagamento == "Dinheiro"


def test_correcao_nao_alcanca_pedido_de_outro_restaurante(client, salao, two_tenants):
    outro = db.session.get(Tenant, two_tenants["tenant_b"])
    db.session.add(Produto(tenant_id=outro.id, nome="Pizza", preco=40.0))
    db.session.commit()
    pedido_b = _balcao(outro, pagamento="Dinheiro")

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    client.post(
        f"/cozinha/pedidos/{pedido_b.id}/pagamento/corrigir",
        data={"pagamento": "PIX na entrega"},
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert pedido_b.pagamento == "Dinheiro"
