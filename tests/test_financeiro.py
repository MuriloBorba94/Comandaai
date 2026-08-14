"""Fase 9 (parte 2) — resultado do restaurante: receita, CMV, despesa e lucro.

A decisão contábil que mais importa: compra de insumo NÃO é despesa. O custo dos
insumos entra pelo CMV quando são consumidos numa venda, e lançar a compra também
como despesa contaria o mesmo dinheiro duas vezes. Por isso não existe categoria
de despesa para insumo, e há teste garantindo isso.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models.estoque import FichaTecnica, Insumo
from app.models.financeiro import CATEGORIAS_DESPESA, Despesa, ReceitaAvulsa
from app.models.pedido import (
    STATUS_CONFIRMADO,
    STATUS_EM_PREPARO,
    STATUS_ENTREGUE,
    STATUS_PRONTO,
    TIPO_ENTREGA,
    TIPO_RETIRADA,
)
from app.models.produto import Produto
from app.models.tenant import Tenant
from app.services.financeiro import a_pagar, despesas_por_categoria, painel, resumo
from app.services.pedidos import criar_pedido, transicionar
from tests.conftest import login_tenant

BASE_A = "http://tenant-a.localhost"
HOJE = date.today()
INICIO = HOJE.replace(day=1)


@pytest.fixture()
def cenario(app, two_tenants):
    """X-Tudo a R$ 30 com custo de R$ 6,00 pela ficha (150 g a R$ 0,04)."""
    tenant_a, tenant_b = two_tenants["tenant_a"], two_tenants["tenant_b"]

    carne = Insumo(
        tenant_id=tenant_a, nome="Carne", unidade="g",
        preco_compra=200.0, quantidade_compra=5000.0, estoque_atual=10000.0,
    )
    db.session.add(carne)
    db.session.flush()

    xtudo = Produto(tenant_id=tenant_a, nome="X-Tudo", preco=30.0)
    refri = Produto(tenant_id=tenant_a, nome="Refrigerante", preco=10.0)
    produto_b = Produto(tenant_id=tenant_b, nome="Pizza", preco=50.0)
    db.session.add_all([xtudo, refri, produto_b])
    db.session.flush()

    db.session.add(FichaTecnica(produto_id=xtudo.id, insumo_id=carne.id, quantidade_usada=150.0))
    db.session.commit()

    return {
        "tenant_a": tenant_a, "tenant_b": tenant_b,
        "xtudo": xtudo.id, "refri": refri.id, "produto_b": produto_b.id, "carne": carne.id,
    }


def _tenant(tenant_id):
    return db.session.get(Tenant, tenant_id)


def _vender(tenant_id, produto_id, quantidade=1, entregar=True, **extra):
    dados = {
        "cliente": "Maria", "telefone": "81999998888",
        "tipo": TIPO_RETIRADA, "pagamento": "Dinheiro",
        "carrinho": [{"produto_id": produto_id, "quantidade": quantidade}],
    }
    dados.update(extra)
    pedido = criar_pedido(_tenant(tenant_id), dados)
    if entregar:
        for status in (STATUS_CONFIRMADO, STATUS_EM_PREPARO, STATUS_PRONTO, STATUS_ENTREGUE):
            transicionar(pedido, status)
    return pedido


def _despesa(tenant_id, valor=100.0, paga=False, vencimento=None, categoria="Aluguel", descricao="Aluguel"):
    despesa = Despesa(
        tenant_id=tenant_id, descricao=descricao, valor=valor, categoria=categoria,
        data_vencimento=vencimento or HOJE, paga=paga,
        data_pagamento=(vencimento or HOJE) if paga else None,
    )
    db.session.add(despesa)
    db.session.commit()
    return despesa


# --------------------------------------------------------------------------- #
# Formação do resultado
# --------------------------------------------------------------------------- #


def test_receita_e_cmv_saem_dos_pedidos_entregues(cenario):
    _vender(cenario["tenant_a"], cenario["xtudo"], 2)  # 60 de venda, 12 de custo

    numeros = resumo(cenario["tenant_a"], INICIO, HOJE)
    assert numeros["receita_pedidos"] == pytest.approx(60.0)
    assert numeros["cmv"] == pytest.approx(12.0)
    assert numeros["lucro_bruto"] == pytest.approx(48.0)
    assert numeros["pedidos"] == 1


def test_pedido_em_andamento_nao_entra_no_resultado(cenario):
    """Mesma definição dos relatórios de venda: faturado é o que foi entregue."""
    _vender(cenario["tenant_a"], cenario["xtudo"], 1, entregar=False)

    numeros = resumo(cenario["tenant_a"], INICIO, HOJE)
    assert numeros["receita"] == 0.0
    assert numeros["cmv"] == 0.0


def test_despesa_reduz_o_lucro_liquido(cenario):
    _vender(cenario["tenant_a"], cenario["xtudo"], 2)  # lucro bruto 48
    _despesa(cenario["tenant_a"], valor=20.0)

    numeros = resumo(cenario["tenant_a"], INICIO, HOJE)
    assert numeros["despesas"] == pytest.approx(20.0)
    assert numeros["lucro_liquido"] == pytest.approx(28.0)


def test_despesa_conta_no_resultado_mesmo_em_aberto(cenario):
    """Regime de competência: a conta do mês pesa no mês, paga ou não."""
    _vender(cenario["tenant_a"], cenario["xtudo"], 2)
    _despesa(cenario["tenant_a"], valor=30.0, paga=False)

    numeros = resumo(cenario["tenant_a"], INICIO, HOJE)
    assert numeros["despesas"] == pytest.approx(30.0)
    assert numeros["despesas_pagas"] == 0.0
    assert numeros["despesas_pendentes"] == pytest.approx(30.0)
    assert numeros["lucro_liquido"] == pytest.approx(18.0)


def test_receita_avulsa_entra_inteira_no_lucro(cenario):
    """Não tem CMV associado: o valor todo é margem."""
    db.session.add(
        ReceitaAvulsa(tenant_id=cenario["tenant_a"], valor=200.0,
                      categoria="Evento", data_registro=HOJE)
    )
    db.session.commit()

    numeros = resumo(cenario["tenant_a"], INICIO, HOJE)
    assert numeros["receita_avulsa"] == pytest.approx(200.0)
    assert numeros["receita"] == pytest.approx(200.0)
    assert numeros["lucro_bruto"] == pytest.approx(200.0)


def test_taxa_de_entrega_entra_no_lucro_bruto(cenario):
    """A taxa não é margem de cozinha, mas é dinheiro sem custo de insumo."""
    from app.models.cupom import BairroEntrega

    db.session.add(BairroEntrega(tenant_id=cenario["tenant_a"], nome="Centro", taxa=10.0))
    db.session.commit()

    _vender(cenario["tenant_a"], cenario["xtudo"], 1,
            tipo=TIPO_ENTREGA, endereco="Rua das Flores, 100")

    numeros = resumo(cenario["tenant_a"], INICIO, HOJE)
    assert numeros["receita_pedidos"] == pytest.approx(40.0)  # 30 + 10 de taxa
    assert numeros["cmv"] == pytest.approx(6.0)
    assert numeros["taxas_entrega"] == pytest.approx(10.0)
    # 30 - 6 (lucro de produto) + 10 (taxa) = 34
    assert numeros["lucro_bruto"] == pytest.approx(34.0)


def test_margens_e_ticket(cenario):
    _vender(cenario["tenant_a"], cenario["xtudo"], 2)  # 60
    _despesa(cenario["tenant_a"], valor=18.0)

    numeros = resumo(cenario["tenant_a"], INICIO, HOJE)
    assert numeros["ticket"] == pytest.approx(60.0)
    assert numeros["margem_bruta"] == pytest.approx(80.0)   # 48/60
    assert numeros["margem_liquida"] == pytest.approx(50.0)  # 30/60


def test_sem_movimento_nao_divide_por_zero(cenario):
    numeros = resumo(cenario["tenant_a"], INICIO, HOJE)
    assert numeros["receita"] == 0.0
    assert numeros["ticket"] == 0.0
    assert numeros["margem_liquida"] == 0.0


def test_lucro_pode_ser_negativo(cenario):
    _vender(cenario["tenant_a"], cenario["xtudo"], 1)  # lucro bruto 24
    _despesa(cenario["tenant_a"], valor=500.0)

    numeros = resumo(cenario["tenant_a"], INICIO, HOJE)
    assert numeros["lucro_liquido"] == pytest.approx(-476.0)


def test_pedido_sem_ficha_e_sinalizado(cenario):
    """Custo zero é "custo desconhecido", e o lucro sai otimista."""
    _vender(cenario["tenant_a"], cenario["refri"], 3)  # sem ficha técnica

    numeros = resumo(cenario["tenant_a"], INICIO, HOJE)
    assert numeros["cmv"] == 0.0
    assert numeros["pedidos_sem_custo"] == 1
    assert numeros["lucro_bruto"] == pytest.approx(30.0)


# --------------------------------------------------------------------------- #
# A armadilha do duplo lançamento
# --------------------------------------------------------------------------- #


def test_nao_existe_categoria_de_despesa_para_insumo(cenario):
    """Se existisse, o custo do insumo seria contado no CMV e na despesa."""
    proibidas = {"insumo", "insumos", "mercadoria", "matéria-prima", "compra de insumos"}
    assert not {c.lower() for c in CATEGORIAS_DESPESA} & proibidas


def test_categoria_invalida_cai_em_outros(cenario, client):
    _liberar(cenario["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/financeiro/despesas",
        data={"descricao": "Compra de carne", "valor": "200,00",
              "categoria": "Insumos", "data_vencimento": HOJE.isoformat()},
        base_url=BASE_A,
        follow_redirects=True,
    )
    assert Despesa.query.one().categoria == "Outros"


# --------------------------------------------------------------------------- #
# Período e isolamento
# --------------------------------------------------------------------------- #


def test_despesa_fora_do_periodo_nao_conta(cenario):
    _despesa(cenario["tenant_a"], valor=100.0, vencimento=INICIO - timedelta(days=1))

    numeros = resumo(cenario["tenant_a"], INICIO, HOJE)
    assert numeros["despesas"] == 0.0


def test_resultado_nao_mistura_tenants(cenario):
    _vender(cenario["tenant_a"], cenario["xtudo"], 1)      # 30 no A
    _vender(cenario["tenant_b"], cenario["produto_b"], 4)  # 200 no B
    _despesa(cenario["tenant_b"], valor=999.0)

    numeros_a = resumo(cenario["tenant_a"], INICIO, HOJE)
    numeros_b = resumo(cenario["tenant_b"], INICIO, HOJE)
    assert numeros_a["receita"] == pytest.approx(30.0)
    assert numeros_a["despesas"] == 0.0
    assert numeros_b["receita"] == pytest.approx(200.0)
    assert numeros_b["despesas"] == pytest.approx(999.0)


def test_a_pagar_lista_so_o_que_esta_aberto(cenario):
    _despesa(cenario["tenant_a"], valor=50.0, paga=True, descricao="Paga")
    _despesa(cenario["tenant_a"], valor=80.0, paga=False, descricao="Aberta")

    abertas = a_pagar(cenario["tenant_a"])
    assert [d.descricao for d in abertas] == ["Aberta"]


def test_a_pagar_ordena_pelo_vencimento(cenario):
    _despesa(cenario["tenant_a"], vencimento=HOJE + timedelta(days=10), descricao="Depois")
    _despesa(cenario["tenant_a"], vencimento=HOJE - timedelta(days=3), descricao="Atrasada")

    assert [d.descricao for d in a_pagar(cenario["tenant_a"])] == ["Atrasada", "Depois"]


def test_dias_de_atraso_da_despesa(cenario):
    despesa = _despesa(cenario["tenant_a"], vencimento=HOJE - timedelta(days=4))
    assert despesa.dias_de_atraso(HOJE) == 4

    despesa.paga = True
    assert despesa.dias_de_atraso(HOJE) == 0, "paga não está em atraso"


def test_quebra_por_categoria(cenario):
    _despesa(cenario["tenant_a"], valor=1000.0, categoria="Aluguel")
    _despesa(cenario["tenant_a"], valor=300.0, categoria="Energia")
    _despesa(cenario["tenant_a"], valor=200.0, categoria="Energia")

    linhas = despesas_por_categoria(cenario["tenant_a"], INICIO, HOJE)
    assert linhas[0]["categoria"] == "Aluguel"
    assert linhas[0]["valor"] == pytest.approx(1000.0)
    energia = next(l for l in linhas if l["categoria"] == "Energia")
    assert energia["valor"] == pytest.approx(500.0)
    assert energia["quantidade"] == 2


def test_painel_compara_com_periodo_anterior(cenario):
    """Janelas de mesmo tamanho, para o comparativo fazer sentido."""
    _vender(cenario["tenant_a"], cenario["xtudo"], 1)

    dados = painel(cenario["tenant_a"], HOJE, HOJE, hoje=HOJE)
    assert dados["dias"] == 1
    assert dados["atual"]["receita"] == pytest.approx(30.0)
    assert dados["anterior"]["receita"] == 0.0
    assert dados["variacao_receita"] is None, "sem base no período anterior"


# --------------------------------------------------------------------------- #
# Telas
# --------------------------------------------------------------------------- #


def _liberar(tenant_id, liberado=True):
    from app.models.assinatura import Plano

    recursos = ["cozinha", "estoque"] + (["financeiro"] if liberado else [])
    plano = Plano(slug="starter", nome="Starter", preco_mensal=99.0)
    plano.definir_recursos(recursos)
    db.session.add(plano)
    _tenant(tenant_id).plano = "starter"
    db.session.commit()


def test_tela_financeira_renderiza(cenario, client):
    _liberar(cenario["tenant_a"])
    _vender(cenario["tenant_a"], cenario["xtudo"], 2)
    _despesa(cenario["tenant_a"], valor=20.0)
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.get("/admin/financeiro", base_url=BASE_A)
    assert resposta.status_code == 200
    corpo = resposta.get_data(as_text=True)
    assert "Como o lucro se forma" in corpo
    assert "R$ 60,00" in corpo   # receita
    assert "R$ 12,00" in corpo   # CMV
    assert "R$ 28,00" in corpo   # lucro líquido
    assert "Contas a pagar" in corpo


def test_tela_financeira_bloqueada_fora_do_plano(cenario, client):
    _liberar(cenario["tenant_a"], liberado=False)
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.get("/admin/financeiro", base_url=BASE_A, follow_redirects=True)
    assert "não está incluído no plano" in resposta.get_data(as_text=True)


def test_tela_avisa_sobre_pedido_sem_ficha(cenario, client):
    _liberar(cenario["tenant_a"])
    _vender(cenario["tenant_a"], cenario["refri"], 1)
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    corpo = client.get("/admin/financeiro", base_url=BASE_A).get_data(as_text=True)
    assert "custo zero" in corpo
    assert "custo desconhecido" in corpo


def test_tela_avisa_para_nao_lancar_insumo_como_despesa(cenario, client):
    _liberar(cenario["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    corpo = " ".join(
        client.get("/admin/financeiro", base_url=BASE_A).get_data(as_text=True).split()
    )
    # O aviso vive no formulário de despesa, ao lado do campo que erraria.
    assert "não</strong> se lança aqui" in corpo
    assert "já entra pelo CMV" in corpo
    assert "entrada no estoque" in corpo


def test_lancar_despesa_pela_tela(cenario, client):
    _liberar(cenario["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/financeiro/despesas",
        data={"descricao": "Energia", "valor": "1.250,90", "categoria": "Energia",
              "data_vencimento": HOJE.isoformat()},
        base_url=BASE_A,
        follow_redirects=True,
    )
    despesa = Despesa.query.one()
    assert despesa.valor == pytest.approx(1250.90)
    assert despesa.paga is False


def test_despesa_com_valor_zero_e_recusada(cenario, client):
    _liberar(cenario["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.post(
        "/admin/financeiro/despesas",
        data={"descricao": "Nada", "valor": "0", "data_vencimento": HOJE.isoformat()},
        base_url=BASE_A,
        follow_redirects=True,
    )
    assert "maior que zero" in resposta.get_data(as_text=True)
    assert Despesa.query.count() == 0


def test_despesa_sem_vencimento_e_recusada(cenario, client):
    _liberar(cenario["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.post(
        "/admin/financeiro/despesas",
        data={"descricao": "Sem data", "valor": "50,00", "data_vencimento": ""},
        base_url=BASE_A,
        follow_redirects=True,
    )
    assert "data de vencimento" in resposta.get_data(as_text=True)
    assert Despesa.query.count() == 0


def test_marcar_despesa_paga_pela_tela(cenario, client):
    _liberar(cenario["tenant_a"])
    despesa = _despesa(cenario["tenant_a"], valor=80.0)
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        f"/admin/financeiro/despesas/{despesa.id}/pagar",
        base_url=BASE_A,
        follow_redirects=True,
    )
    db.session.refresh(despesa)
    assert despesa.paga is True
    assert despesa.data_pagamento == HOJE


def test_nao_paga_despesa_de_outro_tenant(cenario, client):
    """Filtro inclui tenant_id: id na URL não dá acesso ao dado do vizinho."""
    _liberar(cenario["tenant_a"])
    despesa_b = _despesa(cenario["tenant_b"], valor=999.0, descricao="Do B")
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.post(
        f"/admin/financeiro/despesas/{despesa_b.id}/pagar",
        base_url=BASE_A,
        follow_redirects=True,
    )
    assert "não encontrada" in resposta.get_data(as_text=True)
    db.session.refresh(despesa_b)
    assert despesa_b.paga is False


def test_lancar_receita_avulsa_pela_tela(cenario, client):
    _liberar(cenario["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/financeiro/receitas",
        data={"descricao": "Evento", "valor": "500,00", "categoria": "Evento"},
        base_url=BASE_A,
        follow_redirects=True,
    )
    receita = ReceitaAvulsa.query.one()
    assert receita.valor == pytest.approx(500.0)
    assert receita.data_registro == HOJE


def test_periodo_invertido_na_url_e_corrigido(cenario, client):
    """De 20/08 até 01/08 é o mesmo intervalo — não pode dar tela vazia."""
    _liberar(cenario["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.get(
        f"/admin/financeiro?de={HOJE.isoformat()}&ate={INICIO.isoformat()}", base_url=BASE_A
    )
    assert resposta.status_code == 200
