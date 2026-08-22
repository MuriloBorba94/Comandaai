"""O mapa do salão: cada mesa com a cor do que está acontecendo nela.

A cor é a informação desta tela — ela é lida de relance, a três metros, por
alguém carregando pratos. Então o que os testes protegem é a honestidade da
cor: uma mesa não pode aparecer ociosa logo depois de pedir, nem continuar
amarela depois de voltar a consumir.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models.pedido import MINUTOS_PARA_OCIOSA, TIPO_MESA, Pedido
from app.models.produto import Produto
from app.models.tenant import Tenant
from app.services.pedidos import (
    adicionar_itens_comanda,
    criar_pedido,
    fechar_comanda,
    pedir_conta,
)
from tests.conftest import login_tenant

BASE_A = "http://tenant-a.localhost"
BASE_B = "http://tenant-b.localhost"


@pytest.fixture()
def salao(app, two_tenants):
    tenant = db.session.get(Tenant, two_tenants["tenant_a"])
    outro = db.session.get(Tenant, two_tenants["tenant_b"])
    tenant.qtd_mesas = 6
    outro.qtd_mesas = 4
    db.session.add_all(
        [
            Produto(tenant_id=tenant.id, nome="X-Tudo", preco=30.0),
            Produto(tenant_id=outro.id, nome="Pizza", preco=40.0),
        ]
    )
    db.session.commit()
    return {"tenant_a": tenant, "tenant_b": outro}


def _abrir(tenant, mesa=1, quantidade=1):
    produto = Produto.query.filter_by(tenant_id=tenant.id).first()
    return criar_pedido(
        tenant,
        {
            "cliente": f"Mesa {mesa:02d}",
            "tipo": TIPO_MESA,
            "mesa": mesa,
            "carrinho": [{"produto_id": produto.id, "quantidade": quantidade}],
            "origem": "mesa",
        },
        permitir_mesa=True,
    )


def _envelhecer(pedido, minutos):
    pedido.ultimo_consumo_em = datetime.now() - timedelta(minutes=minutos)
    db.session.commit()


# --------------------------------------------------------------------------- #
# Os quatro estados
# --------------------------------------------------------------------------- #


def test_comanda_recem_aberta_esta_em_consumo(salao):
    pedido = _abrir(salao["tenant_a"])

    assert pedido.estado_no_salao == "consumo"
    assert pedido.minutos_sem_consumo == 0


def test_mesa_parada_vira_ociosa(salao):
    pedido = _abrir(salao["tenant_a"])
    _envelhecer(pedido, MINUTOS_PARA_OCIOSA)

    assert pedido.estado_no_salao == "ociosa"


def test_um_minuto_antes_do_limite_ainda_esta_em_consumo(salao):
    """A fronteira exata: 9 minutos parados não é mesa abandonada."""
    pedido = _abrir(salao["tenant_a"])
    _envelhecer(pedido, MINUTOS_PARA_OCIOSA - 1)

    assert pedido.estado_no_salao == "consumo"


def test_lancar_item_reinicia_o_relogio(salao):
    pedido = _abrir(salao["tenant_a"])
    _envelhecer(pedido, 30)
    assert pedido.estado_no_salao == "ociosa"

    produto = Produto.query.filter_by(tenant_id=salao["tenant_a"].id).first()
    adicionar_itens_comanda(pedido, [{"produto_id": produto.id, "quantidade": 1}])

    assert pedido.estado_no_salao == "consumo"
    assert pedido.minutos_sem_consumo == 0


def test_pedir_a_conta_deixa_a_mesa_amarela(salao):
    pedido = _abrir(salao["tenant_a"])

    pedir_conta(pedido)

    assert pedido.estado_no_salao == "conta"
    assert pedido.conta_pedida_em is not None


def test_quem_pediu_a_conta_continua_amarelo_mesmo_parado(salao):
    """Urgência vence tempo: alguém está esperando para ir embora."""
    pedido = _abrir(salao["tenant_a"])
    pedir_conta(pedido)
    _envelhecer(pedido, 60)

    assert pedido.estado_no_salao == "conta"


def test_pedir_mais_uma_coisa_desfaz_o_pedido_de_conta(salao):
    """Quem pede outra cerveja não está mais esperando para ir embora."""
    pedido = _abrir(salao["tenant_a"])
    pedir_conta(pedido)

    produto = Produto.query.filter_by(tenant_id=salao["tenant_a"].id).first()
    adicionar_itens_comanda(pedido, [{"produto_id": produto.id, "quantidade": 1}])

    assert pedido.conta_pedida_em is None
    assert pedido.estado_no_salao == "consumo"


def test_desfazer_a_conta_quando_foi_engano(salao):
    pedido = _abrir(salao["tenant_a"])
    pedir_conta(pedido)

    pedir_conta(pedido, pedida=False)

    assert pedido.estado_no_salao == "consumo"


def test_comanda_fechada_nao_ocupa_mais_a_mesa(salao):
    pedido = _abrir(salao["tenant_a"])
    fechar_comanda(pedido, "Dinheiro")

    from app.services.pedidos import mesas_ativas

    assert 1 not in mesas_ativas(salao["tenant_a"].id)


# --------------------------------------------------------------------------- #
# Mesa é do salão, não do cardápio
# --------------------------------------------------------------------------- #


def test_vitrine_nao_oferece_mesa(client, salao):
    """O cliente escolhe entre retirar e receber. Mesa quem abre é o atendente."""
    produto = Produto.query.filter_by(tenant_id=salao["tenant_a"].id).first()
    # Carrinho vazio não mostra o formulário de fechamento; precisa de um item.
    client.post(
        "/carrinho/adicionar",
        data={"produto_id": produto.id, "quantidade": 1},
        base_url=BASE_A,
    )
    texto = client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)

    assert 'value="Retirada"' in texto
    assert 'value="Entrega"' in texto
    assert 'value="Mesa"' not in texto


def test_pedido_de_mesa_forjado_pela_vitrine_e_recusado(client, salao):
    """Tela não é trava: o que recusa é o servidor.

    Sem isto, um POST montado à mão abriria comanda numa mesa qualquer — até
    numa já ocupada por outra pessoa — e ela apareceria no mapa do salão como
    se alguém tivesse sentado ali.
    """
    produto = Produto.query.filter_by(tenant_id=salao["tenant_a"].id).first()
    client.post(
        "/carrinho/adicionar",
        data={"produto_id": produto.id, "quantidade": 1},
        base_url=BASE_A,
    )

    resposta = client.post(
        "/pedido",
        data={
            "cliente": "Espertinho",
            "telefone": "81999998888",
            "tipo": TIPO_MESA,
            "mesa": "3",
            "pagamento": "Dinheiro",
        },
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert "aberta pelo atendente" in resposta.get_data(as_text=True)
    assert Pedido.query.filter_by(tipo=TIPO_MESA).count() == 0


def test_mesa_ocupada_nao_recebe_segunda_comanda(salao):
    """A segunda pessoa que senta na mesa 1 entra na comanda que já existe."""
    _abrir(salao["tenant_a"], mesa=1)

    with pytest.raises(ValueError, match="já tem uma comanda aberta"):
        _abrir(salao["tenant_a"], mesa=1)


def test_atendente_continua_abrindo_comanda_normalmente(client, salao):
    """A trava é para a vitrine; o salão tem que seguir funcionando."""
    produto = Produto.query.filter_by(tenant_id=salao["tenant_a"].id).first()
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/mesas/4/comanda",
        json={"carrinho": [{"produto_id": produto.id, "quantidade": 1}]},
        base_url=BASE_A,
    )

    from app.services.pedidos import mesas_ativas

    assert 4 in mesas_ativas(salao["tenant_a"].id)


# --------------------------------------------------------------------------- #
# A tela
# --------------------------------------------------------------------------- #


def test_mapa_mostra_as_quatro_cores_e_a_contagem(client, salao):
    em_consumo = _abrir(salao["tenant_a"], mesa=1)
    parada = _abrir(salao["tenant_a"], mesa=2)
    _envelhecer(parada, 40)
    esperando = _abrir(salao["tenant_a"], mesa=3)
    pedir_conta(esperando)

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    texto = client.get("/mesas", base_url=BASE_A).get_data(as_text=True)

    assert "mesa-consumo" in texto
    assert "mesa-ociosa" in texto
    assert "mesa-conta" in texto
    assert "mesa-disponivel" in texto  # as três mesas restantes
    # A contagem da legenda é o número que o dono olha primeiro.
    assert "Pediu a conta" in texto


def test_cartao_traz_tempo_e_valor(client, salao):
    pedido = _abrir(salao["tenant_a"], mesa=1, quantidade=2)

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    texto = client.get("/mesas", base_url=BASE_A).get_data(as_text=True)

    assert "R$ 60,00" in texto
    assert "mesa-tempo" in texto
    assert f'data-conta="0"' in texto


def test_botao_de_conta_pela_tela(client, salao):
    pedido = _abrir(salao["tenant_a"], mesa=2)
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post("/mesas/2/conta-pedida", base_url=BASE_A, follow_redirects=True)
    assert pedido.estado_no_salao == "conta"

    client.post(
        "/mesas/2/conta-pedida", data={"desfazer": "1"}, base_url=BASE_A, follow_redirects=True
    )
    assert pedido.estado_no_salao == "consumo"


def test_conta_de_um_restaurante_nao_e_pedida_pelo_outro(client, salao):
    pedido_b = _abrir(salao["tenant_b"], mesa=1)
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    # A mesa 1 existe nos dois salões: o que separa é o tenant da sessão.
    client.post("/mesas/1/conta-pedida", base_url=BASE_A, follow_redirects=True)

    assert pedido_b.conta_pedida_em is None


def test_mesa_sem_comanda_nao_quebra_o_pedido_de_conta(client, salao):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.post("/mesas/5/conta-pedida", base_url=BASE_A, follow_redirects=True)

    assert "não tem comanda aberta" in resposta.get_data(as_text=True)


def test_tempo_curto_e_longo_saem_legiveis(app):
    """"0h11m" cabe no cartão; "77d 22h39m" grita que a comanda ficou esquecida."""
    from datetime import datetime, timedelta

    with app.test_request_context():
        desde = app.jinja_env.filters["desde"]
        assert desde(datetime.now() - timedelta(minutes=11)) == "0h11m"
        assert desde(datetime.now() - timedelta(hours=3, minutes=5)) == "3h05m"
        assert desde(datetime.now() - timedelta(days=77, hours=22, minutes=39)) == "77d 22h39m"
        assert desde(None) == ""
