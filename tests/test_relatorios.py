"""Relatórios de venda do restaurante.

Nenhum modelo novo: tudo é agregação sobre Pedido/PedidoItem. Os testes que mais
importam são os de contagem — o que entra e o que fica de fora do faturamento —
e o isolamento: venda de um restaurante não pode aparecer no relatório do outro.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models.assinatura import Plano
from app.models.pedido import (
    STATUS_CANCELADO,
    STATUS_CONFIRMADO,
    STATUS_EM_PREPARO,
    STATUS_ENTREGUE,
    STATUS_PRONTO,
    TIPO_ENTREGA,
    TIPO_RETIRADA,
    Pedido,
)
from app.models.produto import Produto
from app.models.tenant import Tenant
from app.services.pedidos import criar_pedido, transicionar
from app.services.relatorios import (
    mais_vendidos,
    painel,
    por_forma_de_pagamento,
    por_tipo,
    resumo_do_periodo,
    variacao,
    vendas_por_dia,
)
from tests.conftest import login_tenant

BASE_A = "http://tenant-a.localhost"
HOJE = date.today()


@pytest.fixture()
def cardapio(app, two_tenants):
    tenant_a, tenant_b = two_tenants["tenant_a"], two_tenants["tenant_b"]
    xtudo = Produto(tenant_id=tenant_a, nome="X-Tudo", preco=30.0)
    refri = Produto(tenant_id=tenant_a, nome="Refrigerante", preco=10.0)
    pizza_b = Produto(tenant_id=tenant_b, nome="Pizza", preco=50.0)
    db.session.add_all([xtudo, refri, pizza_b])
    db.session.commit()
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "xtudo": xtudo.id,
        "refri": refri.id,
        "pizza_b": pizza_b.id,
    }


def _tenant(tenant_id):
    return db.session.get(Tenant, tenant_id)


def _pedido(tenant_id, itens, tipo=TIPO_RETIRADA, pagamento="Dinheiro", entregar=False, **extra):
    """Cria um pedido; entregar=True leva até "Entregue" (conta no faturamento)."""
    carrinho = [{"produto_id": pid, "quantidade": qtd} for pid, qtd in itens]
    dados = {
        "cliente": "Maria",
        "telefone": "81999998888",
        "tipo": tipo,
        "pagamento": pagamento,
        "carrinho": carrinho,
    }
    if tipo == TIPO_ENTREGA:
        dados["endereco"] = "Rua das Flores, 100"
    dados.update(extra)
    pedido = criar_pedido(_tenant(tenant_id), dados)
    if entregar:
        for status in (STATUS_CONFIRMADO, STATUS_EM_PREPARO, STATUS_PRONTO, STATUS_ENTREGUE):
            transicionar(pedido, status)
    return pedido


def _hoje_completo():
    inicio = datetime.combine(HOJE, datetime.min.time())
    return inicio, inicio + timedelta(days=1)


# --------------------------------------------------------------------------- #
# O que entra e o que fica de fora do faturamento
# --------------------------------------------------------------------------- #


def test_faturado_conta_so_pedido_entregue(cardapio):
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 1)], entregar=True)   # 30
    _pedido(cardapio["tenant_a"], [(cardapio["refri"], 1)])                  # em aberto

    resumo = resumo_do_periodo(cardapio["tenant_a"], *_hoje_completo())
    assert resumo["faturado"] == 30.0
    assert resumo["faturado_qtd"] == 1


def test_em_andamento_aparece_separado(cardapio):
    """No meio do turno a maioria não fechou; um número só seria enganoso."""
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 2)])  # 60, em aberto

    resumo = resumo_do_periodo(cardapio["tenant_a"], *_hoje_completo())
    assert resumo["faturado"] == 0.0
    assert resumo["andamento"] == 60.0
    assert resumo["andamento_qtd"] == 1


def test_cancelado_nao_entra_no_faturamento_mas_e_reportado(cardapio):
    pedido = _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 1)])
    transicionar(pedido, STATUS_CANCELADO)

    resumo = resumo_do_periodo(cardapio["tenant_a"], *_hoje_completo())
    assert resumo["faturado"] == 0.0
    assert resumo["andamento"] == 0.0
    assert resumo["cancelado"] == 30.0
    assert resumo["cancelado_qtd"] == 1


def test_ticket_medio_usa_so_o_que_fechou(cardapio):
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 1)], entregar=True)  # 30
    _pedido(cardapio["tenant_a"], [(cardapio["refri"], 5)], entregar=True)  # 50
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 10)])                # 300 em aberto

    resumo = resumo_do_periodo(cardapio["tenant_a"], *_hoje_completo())
    assert resumo["faturado"] == 80.0
    assert resumo["ticket_medio"] == 40.0


def test_ticket_medio_sem_venda_nao_estoura(cardapio):
    resumo = resumo_do_periodo(cardapio["tenant_a"], *_hoje_completo())
    assert resumo["ticket_medio"] == 0.0


def test_taxa_de_entrega_entra_no_faturamento(cardapio):
    """O total do pedido inclui a taxa: é dinheiro que a loja recebeu."""
    from app.models.cupom import BairroEntrega

    db.session.add(BairroEntrega(tenant_id=cardapio["tenant_a"], nome="Centro", taxa=8.0))
    db.session.commit()

    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 1)], tipo=TIPO_ENTREGA, entregar=True)

    resumo = resumo_do_periodo(cardapio["tenant_a"], *_hoje_completo())
    assert resumo["faturado"] == 38.0


# --------------------------------------------------------------------------- #
# Isolamento entre tenants
# --------------------------------------------------------------------------- #


def test_venda_de_um_tenant_nao_aparece_no_relatorio_do_outro(cardapio):
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 1)], entregar=True)   # 30 no A
    _pedido(cardapio["tenant_b"], [(cardapio["pizza_b"], 4)], entregar=True)  # 200 no B

    resumo_a = resumo_do_periodo(cardapio["tenant_a"], *_hoje_completo())
    resumo_b = resumo_do_periodo(cardapio["tenant_b"], *_hoje_completo())
    assert resumo_a["faturado"] == 30.0
    assert resumo_b["faturado"] == 200.0


def test_mais_vendidos_nao_mistura_tenants(cardapio):
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 3)], entregar=True)
    _pedido(cardapio["tenant_b"], [(cardapio["pizza_b"], 9)], entregar=True)

    itens = mais_vendidos(cardapio["tenant_a"], *_hoje_completo())
    assert [item["nome"] for item in itens] == ["X-Tudo"]


# --------------------------------------------------------------------------- #
# Mais vendidos
# --------------------------------------------------------------------------- #


def test_mais_vendidos_ordena_por_unidades(cardapio):
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 2), (cardapio["refri"], 5)], entregar=True)
    _pedido(cardapio["tenant_a"], [(cardapio["refri"], 3)], entregar=True)

    itens = mais_vendidos(cardapio["tenant_a"], *_hoje_completo())
    assert itens[0]["nome"] == "Refrigerante"
    assert itens[0]["unidades"] == 8
    assert itens[0]["valor"] == 80.0
    assert itens[1]["nome"] == "X-Tudo"


def test_mais_vendidos_ignora_cancelado(cardapio):
    pedido = _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 50)])
    transicionar(pedido, STATUS_CANCELADO)
    _pedido(cardapio["tenant_a"], [(cardapio["refri"], 1)], entregar=True)

    itens = mais_vendidos(cardapio["tenant_a"], *_hoje_completo())
    assert [item["nome"] for item in itens] == ["Refrigerante"]


def test_mais_vendidos_sobrevive_a_exclusao_do_produto(cardapio):
    """O nome fica congelado na venda, então o histórico não desaparece."""
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 4)], entregar=True)
    db.session.delete(db.session.get(Produto, cardapio["xtudo"]))
    db.session.commit()

    itens = mais_vendidos(cardapio["tenant_a"], *_hoje_completo())
    assert itens[0]["nome"] == "X-Tudo"
    assert itens[0]["unidades"] == 4


def test_mais_vendidos_conta_pedido_em_andamento(cardapio):
    """Item já lançado conta para saber o que está saindo agora na cozinha."""
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 2)])

    itens = mais_vendidos(cardapio["tenant_a"], *_hoje_completo())
    assert itens[0]["unidades"] == 2


# --------------------------------------------------------------------------- #
# Quebras e série
# --------------------------------------------------------------------------- #


def test_quebra_por_forma_de_pagamento(cardapio):
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 1)], pagamento="Dinheiro", entregar=True)
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 2)], pagamento="PIX na entrega", entregar=True)

    linhas = por_forma_de_pagamento(cardapio["tenant_a"], *_hoje_completo())
    por_rotulo = {linha["rotulo"]: linha for linha in linhas}
    assert por_rotulo["PIX na entrega"]["valor"] == 60.0
    assert por_rotulo["Dinheiro"]["valor"] == 30.0
    # Ordenado do maior para o menor faturamento.
    assert linhas[0]["rotulo"] == "PIX na entrega"


def test_quebra_por_tipo_de_pedido(cardapio):
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 1)], tipo=TIPO_RETIRADA, entregar=True)
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 1)], tipo=TIPO_ENTREGA, entregar=True)

    linhas = por_tipo(cardapio["tenant_a"], *_hoje_completo())
    assert {linha["rotulo"] for linha in linhas} == {TIPO_RETIRADA, TIPO_ENTREGA}


def test_serie_por_dia_tem_um_ponto_por_dia(cardapio):
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 1)], entregar=True)

    serie = vendas_por_dia(cardapio["tenant_a"], dias=7, hoje=HOJE)
    assert len(serie) == 7
    assert serie[-1]["dia"] == HOJE
    assert serie[-1]["valor"] == 30.0
    assert serie[0]["valor"] == 0.0, "dia sem venda entra com zero, não é omitido"


def test_variacao_percentual():
    assert variacao(150, 100) == 50.0
    assert variacao(50, 100) == -50.0
    assert variacao(100, 0) is None, "sem base não há percentual"


def test_painel_reune_tudo(cardapio):
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 1)], entregar=True)

    dados = painel(cardapio["tenant_a"], dias=7, hoje=HOJE)
    assert dados["hoje"]["faturado"] == 30.0
    assert dados["semana"]["faturado"] == 30.0
    assert dados["mes"]["faturado"] == 30.0
    assert dados["mais_vendidos"][0]["nome"] == "X-Tudo"
    assert len(dados["serie"]) == 7


def test_painel_recusa_periodo_invalido(cardapio):
    dados = painel(cardapio["tenant_a"], dias=999, hoje=HOJE)
    assert dados["dias"] == 7


# --------------------------------------------------------------------------- #
# Tela
# --------------------------------------------------------------------------- #


def _liberar_relatorios(tenant_id, liberado=True):
    recursos = ["cozinha", "mesas", "cupons", "bairros", "fotos"]
    if liberado:
        recursos.append("relatorios")
    plano = Plano(slug="starter", nome="Starter", preco_mensal=99.0)
    plano.definir_recursos(recursos)
    db.session.add(plano)
    tenant = _tenant(tenant_id)
    tenant.plano = "starter"
    db.session.commit()


def test_tela_de_relatorios_exige_login(cardapio, client):
    resposta = client.get("/admin/relatorios", base_url=BASE_A)
    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]


def test_tela_mostra_os_numeros(cardapio, client):
    _liberar_relatorios(cardapio["tenant_a"])
    _pedido(cardapio["tenant_a"], [(cardapio["xtudo"], 2)], entregar=True)  # 60
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    corpo = client.get("/admin/relatorios", base_url=BASE_A).get_data(as_text=True)
    assert "R$ 60,00" in corpo
    assert "X-Tudo" in corpo
    assert "Mais vendidos" in corpo


def test_tela_bloqueada_quando_fora_do_plano(cardapio, client):
    _liberar_relatorios(cardapio["tenant_a"], liberado=False)
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.get("/admin/relatorios", base_url=BASE_A, follow_redirects=True)
    corpo = resposta.get_data(as_text=True)
    assert "não está incluído no plano" in corpo
    assert "Mais vendidos" not in corpo


def test_menu_esconde_relatorios_fora_do_plano(cardapio, client):
    _liberar_relatorios(cardapio["tenant_a"], liberado=False)
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    corpo = client.get("/admin/", base_url=BASE_A).get_data(as_text=True)
    assert "/admin/relatorios" not in corpo


def test_troca_de_periodo_na_tela(cardapio, client):
    _liberar_relatorios(cardapio["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    corpo = client.get("/admin/relatorios?dias=30", base_url=BASE_A).get_data(as_text=True)
    assert "últimos 30 dias" in corpo or "30 dias" in corpo


def test_periodo_invalido_na_url_nao_quebra(cardapio, client):
    _liberar_relatorios(cardapio["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    assert client.get("/admin/relatorios?dias=abc", base_url=BASE_A).status_code == 200


def test_tela_sem_nenhuma_venda(cardapio, client):
    _liberar_relatorios(cardapio["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    corpo = client.get("/admin/relatorios", base_url=BASE_A).get_data(as_text=True)
    assert "Nenhum pedido entregue" in corpo
    assert "Nenhum item vendido" in corpo
