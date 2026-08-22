"""Fase 2 — pedidos, cozinha e comanda de mesa.

Os testes centrais aqui são os que garantem que o preço é sempre do servidor e
que nada atravessa a fronteira do tenant: pedido, carrinho, comanda e o link
público de acompanhamento.
"""

from __future__ import annotations

import pytest

from app.extensions import db
from app.models.adicional import Adicional
from app.models.categoria import Categoria
from app.models.pedido import (
    STATUS_CANCELADO,
    STATUS_CONFIRMADO,
    STATUS_EM_PREPARO,
    STATUS_ENTREGUE,
    STATUS_NOVO,
    STATUS_PRONTO,
    STATUS_SAIU_ENTREGA,
    TIPO_ENTREGA,
    TIPO_MESA,
    TIPO_RETIRADA,
    Pedido,
)
from app.models.produto import Produto
from app.services.pedidos import (
    adicionar_itens_comanda,
    calcular_carrinho,
    criar_pedido,
    fechar_comanda,
    mesas_ativas,
    normalizar_mesa,
    transicionar,
)
from tests.conftest import login_tenant

BASE_A = "http://tenant-a.localhost"
BASE_B = "http://tenant-b.localhost"


@pytest.fixture()
def cardapio(app, two_tenants):
    """Cardápio mínimo nos dois tenants, com adicional vinculado a um produto."""
    tenant_a, tenant_b = two_tenants["tenant_a"], two_tenants["tenant_b"]

    categoria_a = Categoria(tenant_id=tenant_a, nome="Burgers", ordem=1)
    db.session.add(categoria_a)
    db.session.flush()

    bacon = Adicional(tenant_id=tenant_a, nome="Bacon", preco=5.0)
    cheddar = Adicional(tenant_id=tenant_a, nome="Cheddar", preco=4.0)
    borda_b = Adicional(tenant_id=tenant_b, nome="Borda", preco=8.0)
    db.session.add_all([bacon, cheddar, borda_b])
    db.session.flush()

    xtudo = Produto(tenant_id=tenant_a, nome="X-Tudo", preco=30.0, categoria_id=categoria_a.id)
    refri = Produto(tenant_id=tenant_a, nome="Refrigerante", preco=6.0)
    pizza_b = Produto(tenant_id=tenant_b, nome="Pizza", preco=40.0)
    db.session.add_all([xtudo, refri, pizza_b])
    db.session.flush()

    # Só o X-Tudo aceita bacon. O refrigerante não aceita nada.
    xtudo.adicionais = [bacon, cheddar]
    pizza_b.adicionais = [borda_b]
    db.session.commit()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "xtudo": xtudo.id,
        "refri": refri.id,
        "bacon": bacon.id,
        "cheddar": cheddar.id,
        "pizza_b": pizza_b.id,
        "borda_b": borda_b.id,
    }


@pytest.fixture()
def tenant_a_obj(two_tenants):
    from app.models.tenant import Tenant

    return db.session.get(Tenant, two_tenants["tenant_a"])


def _payload(carrinho, **extra):
    base = {
        "cliente": "Maria",
        "telefone": "81999998888",
        "tipo": TIPO_RETIRADA,
        "pagamento": "Dinheiro",
        "carrinho": carrinho,
    }
    base.update(extra)
    return base


def _abrir_comanda(tenant, **extra):
    """Abre comanda de mesa como o salão faz.

    `permitir_mesa=True` é o que separa o atendente do cliente: pela vitrine,
    pedido de mesa é recusado (ver criar_pedido).
    """
    dados = {"cliente": "Mesa", "tipo": TIPO_MESA, "mesa": 1}
    dados.update(extra)
    return criar_pedido(tenant, dados, permitir_mesa=True)


def _adicionar_ao_carrinho(client, produto_id, base_url=BASE_A, **campos):
    dados = {"produto_id": produto_id, "quantidade": 1}
    dados.update(campos)
    return client.post("/carrinho/adicionar", data=dados, base_url=base_url, follow_redirects=True)


# --------------------------------------------------------------------------- #
# Preço é sempre do servidor
# --------------------------------------------------------------------------- #


def test_preco_enviado_pelo_cliente_e_ignorado(client, cardapio, tenant_a_obj):
    """O navegador pode mandar o que quiser; vale o preço do cadastro."""
    pedido = criar_pedido(
        tenant_a_obj,
        _payload(
            [
                {
                    "produto_id": cardapio["xtudo"],
                    "quantidade": 2,
                    "preco": 0.01,          # tentativa de forjar preço
                    "preco_unitario": 0.01,
                    "total": 0.02,
                }
            ]
        ),
    )
    assert pedido.itens[0].preco_unitario == 30.0
    assert pedido.total == 60.0


def test_desconto_e_total_enviados_pelo_cliente_sao_ignorados(client, cardapio, tenant_a_obj):
    pedido = criar_pedido(
        tenant_a_obj,
        _payload(
            [{"produto_id": cardapio["refri"], "quantidade": 1}],
            desconto=100.0,
            total=0.0,
            subtotal=0.0,
            taxa_entrega=-50.0,
        ),
    )
    assert pedido.desconto == 0.0
    assert pedido.taxa_entrega == 0.0
    assert pedido.total == 6.0


def test_total_soma_itens_e_adicionais(client, cardapio, tenant_a_obj):
    pedido = criar_pedido(
        tenant_a_obj,
        _payload(
            [
                # 30 + 5 (bacon) + 4 (cheddar) = 39, x2 = 78
                {
                    "produto_id": cardapio["xtudo"],
                    "quantidade": 2,
                    "adicionais": [cardapio["bacon"], cardapio["cheddar"]],
                },
                {"produto_id": cardapio["refri"], "quantidade": 3},  # 18
            ]
        ),
    )
    assert pedido.itens[0].total == 78.0
    assert pedido.subtotal == 96.0
    assert pedido.total == 96.0


def test_quantidade_e_limitada(client, cardapio, tenant_a_obj):
    pedido = criar_pedido(
        tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 9999}])
    )
    assert pedido.itens[0].quantidade == 30, "quantidade absurda deve ser limitada"


def test_carrinho_vazio_e_recusado(client, cardapio, tenant_a_obj):
    with pytest.raises(ValueError, match="carrinho está vazio"):
        criar_pedido(tenant_a_obj, _payload([]))


def test_produto_indisponivel_nao_entra_no_pedido(client, cardapio, tenant_a_obj):
    produto = db.session.get(Produto, cardapio["refri"])
    produto.disponivel = False
    db.session.commit()

    with pytest.raises(ValueError, match="não está mais disponível"):
        criar_pedido(tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))


# --------------------------------------------------------------------------- #
# Fronteira do tenant e do produto
# --------------------------------------------------------------------------- #


def test_produto_de_outro_tenant_nao_entra_no_pedido(client, cardapio, tenant_a_obj):
    with pytest.raises(ValueError, match="não está mais disponível"):
        criar_pedido(tenant_a_obj, _payload([{"produto_id": cardapio["pizza_b"], "quantidade": 1}]))


def test_adicional_de_outro_tenant_e_ignorado(client, cardapio, tenant_a_obj):
    pedido = criar_pedido(
        tenant_a_obj,
        _payload(
            [{"produto_id": cardapio["xtudo"], "quantidade": 1, "adicionais": [cardapio["borda_b"]]}]
        ),
    )
    assert pedido.itens[0].adicionais == []
    assert pedido.total == 30.0, "adicional de outro tenant não pode ser cobrado"


def test_adicional_nao_vinculado_ao_produto_e_ignorado(client, cardapio, tenant_a_obj):
    """Bacon existe no tenant A, mas o refrigerante não o aceita.

    No sistema original a liberação era `if categoria == "Burgers"` e a busca não
    olhava o vínculo, então dava para somar bacon num refrigerante.
    """
    pedido = criar_pedido(
        tenant_a_obj,
        _payload(
            [{"produto_id": cardapio["refri"], "quantidade": 1, "adicionais": [cardapio["bacon"]]}]
        ),
    )
    assert pedido.itens[0].adicionais == []
    assert pedido.total == 6.0


def test_adicional_vinculado_e_cobrado(client, cardapio, tenant_a_obj):
    """Contraprova dos dois testes acima."""
    pedido = criar_pedido(
        tenant_a_obj,
        _payload(
            [{"produto_id": cardapio["xtudo"], "quantidade": 1, "adicionais": [cardapio["bacon"]]}]
        ),
    )
    assert [a.nome for a in pedido.itens[0].adicionais] == ["Bacon"]
    assert pedido.total == 35.0


def test_adicional_indisponivel_nao_e_cobrado(client, cardapio, tenant_a_obj):
    bacon = db.session.get(Adicional, cardapio["bacon"])
    bacon.disponivel = False
    db.session.commit()

    itens, subtotal = calcular_carrinho(
        cardapio["tenant_a"],
        [{"produto_id": cardapio["xtudo"], "quantidade": 1, "adicionais": [cardapio["bacon"]]}],
    )
    assert itens[0].adicionais == []
    assert float(subtotal) == 30.0


# --------------------------------------------------------------------------- #
# Numeração, idempotência e histórico
# --------------------------------------------------------------------------- #


def test_numeracao_reinicia_em_cada_tenant(client, cardapio, two_tenants):
    from app.models.tenant import Tenant

    tenant_a = db.session.get(Tenant, two_tenants["tenant_a"])
    tenant_b = db.session.get(Tenant, two_tenants["tenant_b"])

    primeiro_a = criar_pedido(tenant_a, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))
    segundo_a = criar_pedido(tenant_a, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))
    primeiro_b = criar_pedido(tenant_b, _payload([{"produto_id": cardapio["pizza_b"], "quantidade": 1}]))

    assert (primeiro_a.numero, segundo_a.numero) == (1, 2)
    assert primeiro_b.numero == 1, "cada restaurante tem sua própria numeração"


def test_mesmo_client_request_id_nao_duplica_pedido(client, cardapio, tenant_a_obj):
    dados = _payload(
        [{"produto_id": cardapio["refri"], "quantidade": 1}], client_request_id="abc-123"
    )
    primeiro = criar_pedido(tenant_a_obj, dados)
    segundo = criar_pedido(tenant_a_obj, dados)

    assert primeiro.id == segundo.id
    assert Pedido.query.filter_by(tenant_id=cardapio["tenant_a"]).count() == 1


def test_duplo_clique_no_checkout_cria_um_pedido_so(client, cardapio):
    _adicionar_ao_carrinho(client, cardapio["refri"])
    dados = {
        "cliente": "Maria",
        "telefone": "81999998888",
        "tipo": TIPO_RETIRADA,
        "pagamento": "Dinheiro",
        "client_request_id": "envio-unico",
    }
    client.post("/pedido", data=dados, base_url=BASE_A, follow_redirects=True)
    # O carrinho é limpo no primeiro envio; o segundo POST reencontra o pedido.
    client.post("/pedido", data=dados, base_url=BASE_A, follow_redirects=True)

    assert Pedido.query.count() == 1


def test_item_guarda_nome_e_preco_da_epoca_da_venda(client, cardapio, tenant_a_obj):
    pedido = criar_pedido(
        tenant_a_obj, _payload([{"produto_id": cardapio["xtudo"], "quantidade": 1}])
    )

    produto = db.session.get(Produto, cardapio["xtudo"])
    produto.nome = "X-Tudo Especial"
    produto.preco = 99.0
    db.session.commit()

    db.session.refresh(pedido)
    assert pedido.itens[0].nome == "X-Tudo"
    assert pedido.itens[0].preco_unitario == 30.0
    assert pedido.total == 30.0


def test_excluir_produto_preserva_o_historico_do_pedido(client, cardapio, tenant_a_obj):
    pedido = criar_pedido(
        tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 2}])
    )
    numero = pedido.numero

    db.session.delete(db.session.get(Produto, cardapio["refri"]))
    db.session.commit()

    pedido = Pedido.query.filter_by(numero=numero, tenant_id=cardapio["tenant_a"]).one()
    assert pedido.itens[0].nome == "Refrigerante"
    assert pedido.total == 12.0


# --------------------------------------------------------------------------- #
# Máquina de estados
# --------------------------------------------------------------------------- #


def test_transicao_fora_do_fluxo_e_recusada(client, cardapio, tenant_a_obj):
    pedido = criar_pedido(tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))
    assert pedido.status == STATUS_NOVO

    with pytest.raises(ValueError, match="Não é possível mudar"):
        transicionar(pedido, STATUS_PRONTO)  # Novo -> Pronto não existe


def test_fluxo_completo_registra_os_horarios(client, cardapio, tenant_a_obj):
    pedido = criar_pedido(
        tenant_a_obj,
        _payload(
            [{"produto_id": cardapio["refri"], "quantidade": 1}],
            tipo=TIPO_ENTREGA,
            endereco="Rua das Flores, 100, Centro",
        ),
    )
    for status in (STATUS_CONFIRMADO, STATUS_EM_PREPARO, STATUS_PRONTO, STATUS_SAIU_ENTREGA, STATUS_ENTREGUE):
        transicionar(pedido, status)
        assert pedido.status == status

    assert pedido.confirmado_em is not None
    assert pedido.em_preparo_em is not None
    assert pedido.pronto_em is not None
    assert pedido.saiu_entrega_em is not None
    assert pedido.entregue_em is not None


def test_pedido_entregue_nao_muda_mais(client, cardapio, tenant_a_obj):
    pedido = criar_pedido(tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))
    transicionar(pedido, STATUS_CONFIRMADO)
    transicionar(pedido, STATUS_EM_PREPARO)
    transicionar(pedido, STATUS_PRONTO)
    transicionar(pedido, STATUS_ENTREGUE)

    with pytest.raises(ValueError):
        transicionar(pedido, STATUS_EM_PREPARO)


def test_retirada_nao_pode_sair_para_entrega(client, cardapio, tenant_a_obj):
    """O original permitia, e um pedido de balcão aparecia como se estivesse na rua."""
    pedido = criar_pedido(tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))
    transicionar(pedido, STATUS_CONFIRMADO)
    transicionar(pedido, STATUS_EM_PREPARO)
    transicionar(pedido, STATUS_PRONTO)

    with pytest.raises(ValueError, match="Somente pedidos de entrega"):
        transicionar(pedido, STATUS_SAIU_ENTREGA)


def test_cancelamento_fecha_comanda(client, cardapio, tenant_a_obj):
    tenant_a_obj.qtd_mesas = 5
    db.session.commit()
    pedido = criar_pedido(
        tenant_a_obj,
        {"cliente": "Mesa 3", "tipo": TIPO_MESA, "mesa": 3, "carrinho": [{"produto_id": cardapio["refri"], "quantidade": 1}]}, permitir_mesa=True)
    assert pedido.comanda_aberta is True

    transicionar(pedido, STATUS_CANCELADO)
    assert pedido.comanda_aberta is False


# --------------------------------------------------------------------------- #
# Carrinho na sessão
# --------------------------------------------------------------------------- #


def test_carrinho_nao_vaza_entre_tenants(client, cardapio):
    """O cookie é host-only, e há um cheque de tenant como defesa extra."""
    _adicionar_ao_carrinho(client, cardapio["xtudo"], base_url=BASE_A)
    corpo_a = client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)
    assert "X-Tudo" in corpo_a

    corpo_b = client.get("/carrinho", base_url=BASE_B).get_data(as_text=True)
    assert "X-Tudo" not in corpo_b
    assert "carrinho está vazio" in corpo_b


def test_carrinho_recusa_produto_de_outro_tenant(client, cardapio):
    resposta = _adicionar_ao_carrinho(client, cardapio["pizza_b"], base_url=BASE_A)
    assert "não está disponível" in resposta.get_data(as_text=True)
    assert "carrinho está vazio" in client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)


def test_remover_item_do_carrinho(client, cardapio):
    _adicionar_ao_carrinho(client, cardapio["xtudo"])
    _adicionar_ao_carrinho(client, cardapio["refri"])

    client.post("/carrinho/remover", data={"indice": 0}, base_url=BASE_A, follow_redirects=True)
    corpo = client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)
    assert "X-Tudo" not in corpo
    assert "Refrigerante" in corpo


def test_carrinho_mostra_o_mesmo_preco_que_sera_cobrado(client, cardapio):
    _adicionar_ao_carrinho(
        client, cardapio["xtudo"], quantidade=2, adicionais=[cardapio["bacon"]]
    )
    corpo = client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)
    assert "R$ 70,00" in corpo  # (30 + 5) x 2

    client.post(
        "/pedido",
        data={"cliente": "Maria", "telefone": "81999998888", "tipo": TIPO_RETIRADA, "pagamento": "Dinheiro"},
        base_url=BASE_A,
        follow_redirects=True,
    )
    assert Pedido.query.one().total == 70.0


def test_checkout_limpa_o_carrinho(client, cardapio):
    _adicionar_ao_carrinho(client, cardapio["refri"])
    client.post(
        "/pedido",
        data={"cliente": "Maria", "telefone": "81999998888", "tipo": TIPO_RETIRADA, "pagamento": "Dinheiro"},
        base_url=BASE_A,
        follow_redirects=True,
    )
    assert "carrinho está vazio" in client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)


def test_entrega_exige_endereco(client, cardapio):
    _adicionar_ao_carrinho(client, cardapio["refri"])
    resposta = client.post(
        "/pedido",
        data={
            "cliente": "Maria",
            "telefone": "81999998888",
            "tipo": TIPO_ENTREGA,
            "pagamento": "Dinheiro",
            "endereco": "rua",
        },
        base_url=BASE_A,
        follow_redirects=True,
    )
    assert "endereço completo" in resposta.get_data(as_text=True)
    assert Pedido.query.count() == 0


def test_telefone_invalido_e_recusado(client, cardapio, tenant_a_obj):
    with pytest.raises(ValueError, match="WhatsApp"):
        criar_pedido(
            tenant_a_obj,
            _payload([{"produto_id": cardapio["refri"], "quantidade": 1}], telefone="123"),
        )


# --------------------------------------------------------------------------- #
# Acompanhamento público
# --------------------------------------------------------------------------- #


def test_link_de_acompanhamento_mostra_o_pedido(client, cardapio, tenant_a_obj):
    pedido = criar_pedido(tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))
    corpo = client.get(f"/pedido/{pedido.public_token}", base_url=BASE_A).get_data(as_text=True)
    assert f"#{pedido.numero}" in corpo
    assert "Refrigerante" in corpo
    assert STATUS_NOVO in corpo


def test_token_de_outro_tenant_nao_abre(client, cardapio, two_tenants):
    from app.models.tenant import Tenant

    tenant_b = db.session.get(Tenant, two_tenants["tenant_b"])
    pedido_b = criar_pedido(tenant_b, _payload([{"produto_id": cardapio["pizza_b"], "quantidade": 1}]))

    # Mesmo com o token correto, o host do tenant A não pode abrir o pedido do B.
    assert client.get(f"/pedido/{pedido_b.public_token}", base_url=BASE_A).status_code == 404
    assert client.get(f"/pedido/{pedido_b.public_token}", base_url=BASE_B).status_code == 200


def test_token_inexistente_da_404(client, cardapio):
    assert client.get("/pedido/token-que-nao-existe", base_url=BASE_A).status_code == 404


# --------------------------------------------------------------------------- #
# Cozinha
# --------------------------------------------------------------------------- #


def test_cozinha_exige_login(client, cardapio):
    resposta = client.get("/cozinha", base_url=BASE_A)
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]


def test_cozinha_mostra_so_pedidos_do_proprio_tenant(client, cardapio, two_tenants):
    from app.models.tenant import Tenant

    tenant_a = db.session.get(Tenant, two_tenants["tenant_a"])
    tenant_b = db.session.get(Tenant, two_tenants["tenant_b"])
    criar_pedido(tenant_a, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}], cliente="Cliente do A"))
    criar_pedido(tenant_b, _payload([{"produto_id": cardapio["pizza_b"], "quantidade": 1}], cliente="Cliente do B"))

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/cozinha", base_url=BASE_A).get_data(as_text=True)
    assert "Cliente do A" in corpo
    assert "Cliente do B" not in corpo


def test_cozinha_avanca_o_status(client, cardapio, tenant_a_obj):
    pedido = criar_pedido(tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    client.post(
        f"/cozinha/pedidos/{pedido.id}/status",
        data={"status": STATUS_CONFIRMADO},
        base_url=BASE_A,
        follow_redirects=True,
    )
    db.session.refresh(pedido)
    assert pedido.status == STATUS_CONFIRMADO


def test_cozinha_nao_muda_pedido_de_outro_tenant(client, cardapio, two_tenants):
    from app.models.tenant import Tenant

    tenant_b = db.session.get(Tenant, two_tenants["tenant_b"])
    pedido_b = criar_pedido(tenant_b, _payload([{"produto_id": cardapio["pizza_b"], "quantidade": 1}]))

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    resposta = client.post(
        f"/cozinha/pedidos/{pedido_b.id}/status",
        data={"status": STATUS_CONFIRMADO},
        base_url=BASE_A,
        follow_redirects=True,
    )
    assert "não encontrado" in resposta.get_data(as_text=True)
    db.session.refresh(pedido_b)
    assert pedido_b.status == STATUS_NOVO


def test_cozinha_nao_oferece_transicao_invalida(client, cardapio, tenant_a_obj):
    """A tela só mostra botões que o serviço aceita — retirada não sai para entrega."""
    pedido = criar_pedido(tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))
    transicionar(pedido, STATUS_CONFIRMADO)
    transicionar(pedido, STATUS_EM_PREPARO)
    transicionar(pedido, STATUS_PRONTO)

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/cozinha", base_url=BASE_A).get_data(as_text=True)
    # Procura o BOTÃO, não o texto: "Saiu para entrega" também aparece como
    # título da coluna, então buscar a string solta não provaria nada.
    assert f'value="{STATUS_SAIU_ENTREGA}"' not in corpo
    assert f'value="{STATUS_ENTREGUE}"' in corpo


# --------------------------------------------------------------------------- #
# Mesa e comanda
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("valor", ["0", "-1", "99", "abc", ""])
def test_mesa_invalida_e_recusada(valor):
    with pytest.raises(ValueError):
        normalizar_mesa(valor, 10)


def test_mesa_sem_salao_configurado_e_recusada():
    with pytest.raises(ValueError, match="não atende pedidos de mesa"):
        normalizar_mesa("1", 0)


def test_comanda_abre_acumula_e_fecha(client, cardapio, tenant_a_obj):
    tenant_a_obj.qtd_mesas = 10
    db.session.commit()

    pedido = criar_pedido(
        tenant_a_obj,
        {
            "cliente": "Mesa 4",
            "tipo": TIPO_MESA,
            "mesa": 4,
            "carrinho": [{"produto_id": cardapio["refri"], "quantidade": 1}],
        }, permitir_mesa=True)
    assert pedido.comanda_aberta is True
    assert pedido.total == 6.0
    assert pedido.pagamento == "Comanda Aberta"

    adicionar_itens_comanda(pedido, [{"produto_id": cardapio["xtudo"], "quantidade": 1}])
    assert pedido.total == 36.0
    assert len(pedido.itens) == 2

    fechar_comanda(pedido, "Cartão na entrega")
    assert pedido.comanda_aberta is False
    assert pedido.status == STATUS_ENTREGUE
    assert pedido.pagamento == "Cartão na entrega"


def test_nao_pode_adicionar_em_comanda_fechada(client, cardapio, tenant_a_obj):
    tenant_a_obj.qtd_mesas = 10
    db.session.commit()
    pedido = criar_pedido(
        tenant_a_obj,
        {"cliente": "Mesa 1", "tipo": TIPO_MESA, "mesa": 1, "carrinho": [{"produto_id": cardapio["refri"], "quantidade": 1}]}, permitir_mesa=True)
    fechar_comanda(pedido, "Dinheiro")

    with pytest.raises(ValueError, match="não está aberta"):
        adicionar_itens_comanda(pedido, [{"produto_id": cardapio["refri"], "quantidade": 1}])


def test_mesa_ja_ocupada_nao_abre_segunda_comanda(client, cardapio, tenant_a_obj):
    tenant_a_obj.qtd_mesas = 10
    db.session.commit()
    dados = {
        "cliente": "Mesa 2",
        "tipo": TIPO_MESA,
        "mesa": 2,
        "carrinho": [{"produto_id": cardapio["refri"], "quantidade": 1}],
    }
    criar_pedido(tenant_a_obj, dados, permitir_mesa=True)

    with pytest.raises(ValueError, match="já tem uma comanda aberta"):
        criar_pedido(tenant_a_obj, dados, permitir_mesa=True)


def test_item_novo_devolve_comanda_concluida_para_a_fila(client, cardapio, tenant_a_obj):
    tenant_a_obj.qtd_mesas = 10
    db.session.commit()
    pedido = criar_pedido(
        tenant_a_obj,
        {"cliente": "Mesa 5", "tipo": TIPO_MESA, "mesa": 5, "carrinho": [{"produto_id": cardapio["refri"], "quantidade": 1}]}, permitir_mesa=True)
    transicionar(pedido, STATUS_CONFIRMADO)
    transicionar(pedido, STATUS_EM_PREPARO)
    transicionar(pedido, STATUS_PRONTO)

    adicionar_itens_comanda(pedido, [{"produto_id": cardapio["xtudo"], "quantidade": 1}])
    assert pedido.status == STATUS_CONFIRMADO, "item novo precisa voltar para a cozinha"


def test_mesas_de_tenants_diferentes_nao_conflitam(client, cardapio, two_tenants):
    from app.models.tenant import Tenant

    tenant_a = db.session.get(Tenant, two_tenants["tenant_a"])
    tenant_b = db.session.get(Tenant, two_tenants["tenant_b"])
    tenant_a.qtd_mesas = 10
    tenant_b.qtd_mesas = 10
    db.session.commit()

    criar_pedido(tenant_a, {"cliente": "Mesa do A", "tipo": TIPO_MESA, "mesa": 1, "carrinho": [{"produto_id": cardapio["refri"], "quantidade": 1}]}, permitir_mesa=True)
    # A mesa 1 do tenant B é outra mesa: precisa abrir normalmente.
    pedido_b = criar_pedido(tenant_b, {"cliente": "Mesa do B", "tipo": TIPO_MESA, "mesa": 1, "carrinho": [{"produto_id": cardapio["pizza_b"], "quantidade": 1}]}, permitir_mesa=True)
    assert pedido_b.mesa == 1 and pedido_b.comanda_aberta is True


def test_reduzir_salao_com_comanda_aberta_e_bloqueado(client, cardapio, tenant_a_obj):
    """Sem isso, a comanda sumiria do mapa e ninguém conseguiria fechá-la."""
    tenant_a_obj.qtd_mesas = 10
    db.session.commit()
    criar_pedido(
        tenant_a_obj,
        {"cliente": "Mesa 9", "tipo": TIPO_MESA, "mesa": 9, "carrinho": [{"produto_id": cardapio["refri"], "quantidade": 1}]}, permitir_mesa=True)

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    resposta = client.post(
        "/admin/configuracoes",
        data={"qtd_mesas": "4", "tempo_estimado_min": "40", "tempo_estimado_max": "60"},
        base_url=BASE_A,
        follow_redirects=True,
    )
    assert "Feche primeiro as comandas" in resposta.get_data(as_text=True)
    db.session.refresh(tenant_a_obj)
    assert tenant_a_obj.qtd_mesas == 10


def test_telas_de_operacao_renderizam(client, cardapio, tenant_a_obj):
    """Cobre cozinha, salão, comanda e configurações — nenhum outro teste
    renderiza essas telas com dados dentro."""
    tenant_a_obj.qtd_mesas = 6
    db.session.commit()
    criar_pedido(
        tenant_a_obj,
        {"cliente": "Mesa 2", "tipo": TIPO_MESA, "mesa": 2, "carrinho": [{"produto_id": cardapio["xtudo"], "quantidade": 1, "adicionais": [cardapio["bacon"]]}]}, permitir_mesa=True)
    criar_pedido(
        tenant_a_obj,
        _payload([{"produto_id": cardapio["refri"], "quantidade": 2}], tipo=TIPO_ENTREGA, endereco="Rua A, 10, Centro"),
    )

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    for url in ("/cozinha", "/mesas", "/mesas/2", "/mesas/5", "/admin/configuracoes", "/admin/"):
        resposta = client.get(url, base_url=BASE_A)
        assert resposta.status_code == 200, f"{url} devolveu {resposta.status_code}"

    comanda = client.get("/mesas/2", base_url=BASE_A).get_data(as_text=True)
    assert "Comanda aberta" in comanda
    assert "Bacon" in comanda


# --------------------------------------------------------------------------- #
# PDV de mesa em modal (o carrinho montado no salão)
#
# É uma rota JSON que abre ou complementa comanda. O que precisa ser provado: o
# preço vem do servidor (não do corpo enviado), o carrinho é validado, e um
# produto de outro tenant não entra na comanda deste.
# --------------------------------------------------------------------------- #


def _abrir_comanda(client, numero, carrinho, base_url=BASE_A, **extra):
    corpo = {"carrinho": carrinho}
    corpo.update(extra)
    return client.post(f"/mesas/{numero}/comanda", json=corpo, base_url=base_url)


def test_pdv_abre_a_mesa_e_depois_complementa(client, cardapio, tenant_a_obj):
    tenant_a_obj.qtd_mesas = 10
    db.session.commit()
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = _abrir_comanda(
        client, 3, [{"produto_id": cardapio["refri"], "quantidade": 2}], observacao="sem gelo"
    )
    assert resposta.status_code == 200
    assert resposta.get_json()["status"] == "ok"

    pedido = mesas_ativas(tenant_a_obj.id)[3]
    assert pedido.comanda_aberta is True
    assert pedido.total == pytest.approx(12.0)   # 2 x 6,00

    assert _abrir_comanda(client, 3, [{"produto_id": cardapio["refri"], "quantidade": 1}]).status_code == 200
    assert mesas_ativas(tenant_a_obj.id)[3].total == pytest.approx(18.0)


def test_pdv_ignora_preco_enviado_pelo_cliente(client, cardapio, tenant_a_obj):
    """O corpo é dado do navegador: mandar preço não pode virar desconto."""
    tenant_a_obj.qtd_mesas = 10
    db.session.commit()
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    _abrir_comanda(
        client, 4,
        [{"produto_id": cardapio["refri"], "quantidade": 1, "preco": 0.01, "total": 0.01}],
    )
    assert mesas_ativas(tenant_a_obj.id)[4].total == pytest.approx(6.0)


def test_pdv_recusa_produto_de_outro_tenant(client, cardapio, tenant_a_obj):
    """A barreira aqui é código: o carrinho vem por id, sem tenant junto."""
    tenant_a_obj.qtd_mesas = 10
    db.session.commit()
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = _abrir_comanda(client, 5, [{"produto_id": cardapio["pizza_b"], "quantidade": 1}])

    assert resposta.status_code == 400
    assert 5 not in mesas_ativas(tenant_a_obj.id)


@pytest.mark.parametrize("carrinho", [[], "nao sou lista", None])
def test_pdv_recusa_carrinho_invalido(client, cardapio, tenant_a_obj, carrinho):
    tenant_a_obj.qtd_mesas = 10
    db.session.commit()
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = _abrir_comanda(client, 6, carrinho)

    assert resposta.status_code == 400
    assert 6 not in mesas_ativas(tenant_a_obj.id)


def test_pdv_recusa_mesa_fora_do_salao(client, cardapio, tenant_a_obj):
    """Reduzir o salão não pode deixar abrir comanda numa mesa que não existe."""
    tenant_a_obj.qtd_mesas = 4
    db.session.commit()
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = _abrir_comanda(client, 90, [{"produto_id": cardapio["refri"], "quantidade": 1}])

    assert resposta.status_code == 400
    assert 90 not in mesas_ativas(tenant_a_obj.id)


def test_pdv_exige_login(client, cardapio, tenant_a_obj):
    tenant_a_obj.qtd_mesas = 10
    db.session.commit()

    resposta = _abrir_comanda(client, 7, [{"produto_id": cardapio["refri"], "quantidade": 1}])

    assert resposta.status_code in (302, 303)
    assert 7 not in mesas_ativas(tenant_a_obj.id)


def test_mapa_de_mesas_traz_o_pdv_e_o_cardapio(client, cardapio, tenant_a_obj):
    tenant_a_obj.qtd_mesas = 6
    db.session.commit()
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    html = client.get("/mesas", base_url=BASE_A).get_data(as_text=True)

    for modal in ("modal-acoes-mesa", "modal-add-comanda", "modal-fechar-comanda"):
        assert modal in html
    assert "comanda-cardapio" in html
    assert "X-Tudo" in html, "o catálogo do PDV precisa vir embutido"


def test_cardapio_do_pdv_nao_vaza_entre_tenants(client, cardapio, tenant_a_obj):
    """O catálogo vai embutido no HTML: um produto do vizinho ali seria vazamento."""
    import json
    import re

    tenant_a_obj.qtd_mesas = 4
    db.session.commit()
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    html = client.get("/mesas", base_url=BASE_A).get_data(as_text=True)
    bruto = re.search(r'id="comanda-cardapio">(.*?)</script>', html, re.S).group(1)
    ids = {item["id"] for item in json.loads(bruto)}

    assert cardapio["xtudo"] in ids
    assert cardapio["pizza_b"] not in ids, "produto do tenant B entrou no PDV do A"


# --------------------------------------------------------------------------- #
# Numeração: o caminho de colisão
# --------------------------------------------------------------------------- #


def test_retry_recupera_de_colisao_de_numero(client, cardapio, tenant_a_obj, monkeypatch):
    """Força a colisão que a unique constraint (tenant_id, numero) barra.

    O número é calculado com MAX+1, então dois pedidos simultâneos podem tentar
    o mesmo. O retry existe para isso, mas nenhum teste exercitava esse caminho:
    era uma afirmação sem prova, e o rollback poderia ter perdido os itens do
    pedido no caminho.
    """
    from app.services import pedidos as servico

    criar_pedido(tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))

    original = servico._proximo_numero
    chamadas = []

    def numero_colidindo(tenant_id):
        chamadas.append(tenant_id)
        # Na primeira tentativa devolve 1, que já existe -> IntegrityError.
        return 1 if len(chamadas) == 1 else original(tenant_id)

    monkeypatch.setattr(servico, "_proximo_numero", numero_colidindo)

    pedido = criar_pedido(
        tenant_a_obj, _payload([{"produto_id": cardapio["xtudo"], "quantidade": 2}])
    )

    assert len(chamadas) >= 2, "o retry não aconteceu"
    assert pedido.numero == 2
    # O rollback do meio não pode ter comido os itens nem o total.
    assert [(item.nome, item.quantidade) for item in pedido.itens] == [("X-Tudo", 2)]
    assert pedido.total == 60.0
    assert Pedido.query.filter_by(tenant_id=cardapio["tenant_a"]).count() == 2


def test_colisao_persistente_falha_com_mensagem_clara(client, cardapio, tenant_a_obj, monkeypatch):
    """Se o número colidir sempre, o cliente recebe erro tratado, não exceção crua."""
    from app.services import pedidos as servico

    criar_pedido(tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))
    monkeypatch.setattr(servico, "_proximo_numero", lambda tenant_id: 1)

    with pytest.raises(ValueError, match="Não foi possível registrar o pedido"):
        criar_pedido(tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))


# --------------------------------------------------------------------------- #
# Recarga automática da cozinha
# --------------------------------------------------------------------------- #


def test_eventos_da_cozinha_exigem_login(client, cardapio):
    resposta = client.get("/cozinha/eventos", base_url=BASE_A)
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]


def test_versao_muda_quando_entra_pedido_novo(client, cardapio, tenant_a_obj):
    from app.services.pedidos import versao_da_fila

    antes = versao_da_fila(cardapio["tenant_a"])
    criar_pedido(tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))
    assert versao_da_fila(cardapio["tenant_a"]) != antes


def test_versao_muda_quando_o_status_avanca(client, cardapio, tenant_a_obj):
    from app.services.pedidos import versao_da_fila

    pedido = criar_pedido(tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))
    antes = versao_da_fila(cardapio["tenant_a"])
    transicionar(pedido, STATUS_CONFIRMADO)
    assert versao_da_fila(cardapio["tenant_a"]) != antes


def test_versao_nao_muda_por_pedido_de_outro_tenant(client, cardapio, two_tenants):
    """Sem isto, o painel de um restaurante recarregaria a cada pedido do outro."""
    from app.models.tenant import Tenant
    from app.services.pedidos import versao_da_fila

    tenant_b = db.session.get(Tenant, two_tenants["tenant_b"])
    antes = versao_da_fila(cardapio["tenant_a"])
    criar_pedido(tenant_b, _payload([{"produto_id": cardapio["pizza_b"], "quantidade": 1}]))
    assert versao_da_fila(cardapio["tenant_a"]) == antes


def test_eventos_contam_so_os_pedidos_aguardando_do_proprio_tenant(client, cardapio, two_tenants):
    from app.models.tenant import Tenant

    tenant_a = db.session.get(Tenant, two_tenants["tenant_a"])
    tenant_b = db.session.get(Tenant, two_tenants["tenant_b"])
    criar_pedido(tenant_a, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))
    criar_pedido(tenant_a, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))
    criar_pedido(tenant_b, _payload([{"produto_id": cardapio["pizza_b"], "quantidade": 1}]))

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    dados = client.get("/cozinha/eventos", base_url=BASE_A).get_json()
    assert dados["aguardando"] == 2, "o pedido do outro tenant não pode contar"
    assert dados["versao"]


def test_aguardando_cai_quando_a_cozinha_assume_o_pedido(client, cardapio, tenant_a_obj):
    """É a diferença desse número que dispara o aviso sonoro de pedido novo."""
    pedido = criar_pedido(tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    assert client.get("/cozinha/eventos", base_url=BASE_A).get_json()["aguardando"] == 1

    transicionar(pedido, STATUS_CONFIRMADO)
    assert client.get("/cozinha/eventos", base_url=BASE_A).get_json()["aguardando"] == 0


def test_eventos_de_um_tenant_nao_respondem_no_outro(client, cardapio, two_tenants):
    """Sessão do tenant A não pode consultar a fila do tenant B."""
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    resposta = client.get("/cozinha/eventos", base_url=BASE_B)
    assert resposta.status_code == 302, "sem sessão válida no B, precisa mandar para o login"


def test_painel_traz_a_versao_e_o_script_de_recarga(client, cardapio, tenant_a_obj):
    """O JS depende de #painel com data-versao; se o template perder isso, a
    recarga automática morre silenciosamente."""
    criar_pedido(tenant_a_obj, _payload([{"produto_id": cardapio["refri"], "quantidade": 1}]))

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/cozinha", base_url=BASE_A).get_data(as_text=True)
    assert 'id="painel"' in corpo
    assert "data-versao=" in corpo
    assert "/cozinha/eventos" in corpo
    assert 'id="estado-live"' in corpo
