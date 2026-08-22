"""Fase 9 (parte 1) — estoque, ficha técnica e custo da venda.

Os testes que mais importam: a baixa acontece uma única vez e em qualquer avanço
de status (o original só baixava em "Confirmado", e o atalho Novo -> Em preparo
saía sem consumir insumo), o estorno devolve o que saiu, e a ficha técnica não
aceita insumo de outro tenant.
"""

from __future__ import annotations

import pytest

from app.extensions import db
from app.models.estoque import (
    MOV_ENTRADA,
    MOV_ESTORNO,
    MOV_SAIDA,
    FichaTecnica,
    Insumo,
    MovimentacaoEstoque,
)
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
from app.services.estoque import (
    aplicar_baixa,
    definir_ficha,
    estornar_baixa,
    insumos_em_alerta,
    movimentar,
    necessidades_do_pedido,
)
from app.services.pedidos import adicionar_itens_comanda, criar_pedido, transicionar


@pytest.fixture()
def cenario(app, two_tenants):
    """X-Tudo a R$ 30 que consome 150 g de carne (R$ 0,04/g) e 1 pão (R$ 0,50)."""
    tenant_a, tenant_b = two_tenants["tenant_a"], two_tenants["tenant_b"]

    # 5 kg por R$ 200 = R$ 0,04 por grama.
    carne = Insumo(
        tenant_id=tenant_a, nome="Carne", unidade="g",
        preco_compra=200.0, quantidade_compra=5000.0,
        estoque_atual=1000.0, estoque_minimo=300.0,
    )
    # 20 pães por R$ 10 = R$ 0,50 cada.
    pao = Insumo(
        tenant_id=tenant_a, nome="Pão", unidade="un",
        preco_compra=10.0, quantidade_compra=20.0,
        estoque_atual=50.0, estoque_minimo=10.0,
    )
    # Tempero entra no custo mas não movimenta saldo.
    tempero = Insumo(
        tenant_id=tenant_a, nome="Tempero", unidade="g",
        preco_compra=5.0, quantidade_compra=100.0,
        estoque_atual=0.0, controle_estoque=False,
    )
    carne_b = Insumo(
        tenant_id=tenant_b, nome="Carne do B", unidade="g",
        preco_compra=100.0, quantidade_compra=1000.0, estoque_atual=500.0,
    )
    db.session.add_all([carne, pao, tempero, carne_b])
    db.session.flush()

    xtudo = Produto(tenant_id=tenant_a, nome="X-Tudo", preco=30.0)
    refri = Produto(tenant_id=tenant_a, nome="Refrigerante", preco=6.0)
    produto_b = Produto(tenant_id=tenant_b, nome="Pizza", preco=50.0)
    db.session.add_all([xtudo, refri, produto_b])
    db.session.flush()

    db.session.add_all([
        FichaTecnica(produto_id=xtudo.id, insumo_id=carne.id, quantidade_usada=150.0),
        FichaTecnica(produto_id=xtudo.id, insumo_id=pao.id, quantidade_usada=1.0),
        FichaTecnica(produto_id=xtudo.id, insumo_id=tempero.id, quantidade_usada=2.0),
    ])
    db.session.commit()

    return {
        "tenant_a": tenant_a, "tenant_b": tenant_b,
        "carne": carne.id, "pao": pao.id, "tempero": tempero.id, "carne_b": carne_b.id,
        "xtudo": xtudo.id, "refri": refri.id, "produto_b": produto_b.id,
    }


def _tenant(tenant_id):
    return db.session.get(Tenant, tenant_id)


def _pedido(tenant_id, itens, **extra):
    dados = {
        "cliente": "Maria", "telefone": "81999998888",
        "tipo": TIPO_RETIRADA, "pagamento": "Dinheiro",
        "carrinho": [{"produto_id": pid, "quantidade": q} for pid, q in itens],
    }
    dados.update(extra)
    return criar_pedido(_tenant(tenant_id), dados)


def _insumo(insumo_id):
    return db.session.get(Insumo, insumo_id)


# --------------------------------------------------------------------------- #
# Custo do insumo e da receita
# --------------------------------------------------------------------------- #


def test_custo_unitario_vem_do_pacote_de_compra(cenario):
    """R$ 200 por 5000 g = R$ 0,04 por grama."""
    assert _insumo(cenario["carne"]).custo_unitario == pytest.approx(0.04)
    assert _insumo(cenario["pao"]).custo_unitario == pytest.approx(0.50)


def test_quantidade_de_compra_zero_nao_estoura(cenario):
    """Divisão por zero aqui zeraria o custo de todo prato sem avisar."""
    insumo = Insumo(tenant_id=cenario["tenant_a"], nome="Sem qtd", preco_compra=50.0,
                    quantidade_compra=0.0)
    db.session.add(insumo)
    db.session.commit()
    assert insumo.custo_unitario == 0.0


def test_custo_do_produto_soma_a_ficha(cenario):
    """150 g x 0,04 + 1 pão x 0,50 + 2 g tempero x 0,05 = 6,60."""
    produto = db.session.get(Produto, cenario["xtudo"])
    assert produto.custo_por_ficha == pytest.approx(6.60)
    assert produto.margem == pytest.approx(23.40)


def test_produto_sem_ficha_nao_tem_margem(cenario):
    produto = db.session.get(Produto, cenario["refri"])
    assert produto.custo_por_ficha == 0.0
    assert produto.margem is None, "sem ficha não há como afirmar lucro"


# --------------------------------------------------------------------------- #
# Ficha técnica e fronteira do tenant
# --------------------------------------------------------------------------- #


def test_ficha_recusa_insumo_de_outro_tenant(cenario):
    """A tabela liga produto e insumo sem tenant_id: a barreira é código."""
    produto = db.session.get(Produto, cenario["xtudo"])
    definir_ficha(produto, [(cenario["carne_b"], 100.0)])
    db.session.commit()

    assert [linha.insumo_id for linha in produto.ficha] == [], "insumo de outro tenant entrou"


def test_ficha_aceita_insumo_do_proprio_tenant(cenario):
    """Contraprova do teste acima."""
    produto = db.session.get(Produto, cenario["refri"])
    definir_ficha(produto, [(cenario["pao"], 2.0)])
    db.session.commit()

    assert [linha.insumo_id for linha in produto.ficha] == [cenario["pao"]]
    assert produto.custo_por_ficha == pytest.approx(1.00)


def test_ficha_com_quantidade_zero_remove_a_linha(cenario):
    produto = db.session.get(Produto, cenario["xtudo"])
    definir_ficha(produto, [(cenario["carne"], 150.0), (cenario["pao"], 0)])
    db.session.commit()

    assert {linha.insumo_id for linha in produto.ficha} == {cenario["carne"]}


def test_ficha_atualiza_quantidade_existente(cenario):
    produto = db.session.get(Produto, cenario["xtudo"])
    definir_ficha(produto, [(cenario["carne"], 200.0), (cenario["pao"], 1.0),
                            (cenario["tempero"], 2.0)])
    db.session.commit()

    linha = next(l for l in produto.ficha if l.insumo_id == cenario["carne"])
    assert linha.quantidade_usada == 200.0
    assert produto.custo_por_ficha == pytest.approx(8.60)


def test_necessidades_multiplicam_pela_quantidade(cenario):
    pedido = _pedido(cenario["tenant_a"], [(cenario["xtudo"], 3)])
    necessario = necessidades_do_pedido(pedido)

    assert necessario[cenario["carne"]] == pytest.approx(450.0)
    assert necessario[cenario["pao"]] == pytest.approx(3.0)


# --------------------------------------------------------------------------- #
# Baixa ao vender
# --------------------------------------------------------------------------- #


def test_confirmar_pedido_baixa_estoque_e_grava_custo(cenario):
    pedido = _pedido(cenario["tenant_a"], [(cenario["xtudo"], 2)])
    transicionar(pedido, STATUS_CONFIRMADO)

    assert _insumo(cenario["carne"]).estoque_atual == pytest.approx(1000 - 300)
    assert _insumo(cenario["pao"]).estoque_atual == pytest.approx(50 - 2)
    assert pedido.custo_produtos == pytest.approx(13.20)   # 2 x 6,60
    assert pedido.lucro_bruto == pytest.approx(60 - 13.20)
    assert pedido.estoque_baixado is True


def test_insumo_sem_controle_entra_no_custo_sem_mover_saldo(cenario):
    pedido = _pedido(cenario["tenant_a"], [(cenario["xtudo"], 1)])
    transicionar(pedido, STATUS_CONFIRMADO)

    assert _insumo(cenario["tempero"]).estoque_atual == 0.0
    saidas = {m.insumo_id for m in MovimentacaoEstoque.query.filter_by(tipo=MOV_SAIDA)}
    assert cenario["tempero"] not in saidas
    # Mas o tempero está no custo: 0,10 dos 6,60.
    assert pedido.custo_produtos == pytest.approx(6.60)


def test_pular_confirmado_ainda_baixa_o_estoque(cenario):
    """O original só baixava em "Confirmado", e Novo -> Em preparo é válido:
    o pedido saía sem consumir insumo e com custo zerado."""
    pedido = _pedido(cenario["tenant_a"], [(cenario["xtudo"], 1)])
    transicionar(pedido, STATUS_EM_PREPARO)

    assert pedido.estoque_baixado is True
    assert pedido.custo_produtos == pytest.approx(6.60)
    assert _insumo(cenario["carne"]).estoque_atual == pytest.approx(850.0)


def test_baixa_acontece_uma_unica_vez(cenario):
    pedido = _pedido(cenario["tenant_a"], [(cenario["xtudo"], 1)])
    for status in (STATUS_CONFIRMADO, STATUS_EM_PREPARO, STATUS_PRONTO, STATUS_ENTREGUE):
        transicionar(pedido, status)

    assert _insumo(cenario["carne"]).estoque_atual == pytest.approx(850.0)
    assert MovimentacaoEstoque.query.filter_by(pedido_id=pedido.id, tipo=MOV_SAIDA).count() == 2


def test_produto_sem_ficha_nao_gera_custo(cenario):
    pedido = _pedido(cenario["tenant_a"], [(cenario["refri"], 5)])
    transicionar(pedido, STATUS_CONFIRMADO)

    assert pedido.custo_produtos == 0.0
    assert pedido.lucro_bruto == pytest.approx(30.0)
    assert MovimentacaoEstoque.query.count() == 0


def test_saldo_pode_ficar_negativo(cenario):
    """Venda sem entrada registrada é realidade; recusar o registro seria pior."""
    carne = _insumo(cenario["carne"])
    carne.estoque_atual = 100.0
    db.session.commit()

    pedido = _pedido(cenario["tenant_a"], [(cenario["xtudo"], 2)])
    transicionar(pedido, STATUS_CONFIRMADO)

    assert _insumo(cenario["carne"]).estoque_atual == pytest.approx(-200.0)
    assert _insumo(cenario["carne"]).negativo is True


def test_lucro_desconta_o_cupom_e_ignora_a_taxa_de_entrega(cenario):
    """Lucro bruto é margem de produto: frete não é margem de cozinha."""
    from app.models.cupom import BairroEntrega, Cupom

    db.session.add(BairroEntrega(tenant_id=cenario["tenant_a"], nome="Centro", taxa=10.0))
    db.session.add(Cupom(tenant_id=cenario["tenant_a"], codigo="DEZ", tipo="fixo",
                         valor=5.0, limite_usos=9))
    db.session.commit()

    pedido = _pedido(
        cenario["tenant_a"], [(cenario["xtudo"], 1)],
        tipo="Entrega", endereco="Rua das Flores, 100", cupom="DEZ",
    )
    transicionar(pedido, STATUS_CONFIRMADO)

    assert pedido.subtotal == pytest.approx(30.0)
    assert pedido.taxa_entrega == pytest.approx(10.0)
    assert pedido.desconto == pytest.approx(5.0)
    # 30 (produtos) - 6,60 (custo) - 5 (desconto) = 18,40. A taxa fica fora.
    assert pedido.lucro_bruto == pytest.approx(18.40)


# --------------------------------------------------------------------------- #
# Estorno no cancelamento
# --------------------------------------------------------------------------- #


def test_cancelar_devolve_o_estoque(cenario):
    pedido = _pedido(cenario["tenant_a"], [(cenario["xtudo"], 2)])
    transicionar(pedido, STATUS_CONFIRMADO)
    assert _insumo(cenario["carne"]).estoque_atual == pytest.approx(700.0)

    transicionar(pedido, STATUS_CANCELADO)

    assert _insumo(cenario["carne"]).estoque_atual == pytest.approx(1000.0)
    assert _insumo(cenario["pao"]).estoque_atual == pytest.approx(50.0)
    assert pedido.estoque_baixado is False
    assert MovimentacaoEstoque.query.filter_by(pedido_id=pedido.id, tipo=MOV_ESTORNO).count() == 2


def test_cancelar_sem_baixa_nao_faz_nada(cenario):
    pedido = _pedido(cenario["tenant_a"], [(cenario["xtudo"], 1)])
    transicionar(pedido, STATUS_CANCELADO)

    assert _insumo(cenario["carne"]).estoque_atual == pytest.approx(1000.0)
    assert MovimentacaoEstoque.query.count() == 0


def test_estorno_e_idempotente(cenario):
    pedido = _pedido(cenario["tenant_a"], [(cenario["xtudo"], 1)])
    transicionar(pedido, STATUS_CONFIRMADO)
    estornar_baixa(pedido)
    saldo = _insumo(cenario["carne"]).estoque_atual

    estornar_baixa(pedido)  # segunda chamada não pode devolver de novo
    assert _insumo(cenario["carne"]).estoque_atual == pytest.approx(saldo)


# --------------------------------------------------------------------------- #
# Comanda que cresce
# --------------------------------------------------------------------------- #


def test_comanda_que_cresce_baixa_so_a_diferenca(cenario):
    tenant = _tenant(cenario["tenant_a"])
    tenant.qtd_mesas = 5
    db.session.commit()

    pedido = criar_pedido(tenant, {
        "cliente": "Mesa 1", "tipo": TIPO_MESA, "mesa": 1,
        "carrinho": [{"produto_id": cenario["xtudo"], "quantidade": 1}],
    }, permitir_mesa=True)
    transicionar(pedido, STATUS_CONFIRMADO)
    assert _insumo(cenario["carne"]).estoque_atual == pytest.approx(850.0)

    adicionar_itens_comanda(pedido, [{"produto_id": cenario["xtudo"], "quantidade": 2}])

    # Total do pedido agora são 3 unidades: 450 g de carne.
    assert _insumo(cenario["carne"]).estoque_atual == pytest.approx(550.0)
    assert pedido.custo_produtos == pytest.approx(19.80)
    # Continua UMA linha de saída por insumo, com o consumo total.
    saidas = MovimentacaoEstoque.query.filter_by(pedido_id=pedido.id, tipo=MOV_SAIDA).all()
    assert len(saidas) == 2
    carne_saida = next(s for s in saidas if s.insumo_id == cenario["carne"])
    assert carne_saida.quantidade == pytest.approx(450.0)


def test_comanda_com_insumo_novo_cria_a_linha(cenario):
    tenant = _tenant(cenario["tenant_a"])
    tenant.qtd_mesas = 5
    db.session.commit()
    # O refrigerante ganha ficha só depois da comanda aberta.
    pedido = criar_pedido(tenant, {
        "cliente": "Mesa 2", "tipo": TIPO_MESA, "mesa": 2,
        "carrinho": [{"produto_id": cenario["refri"], "quantidade": 1}],
    }, permitir_mesa=True)
    transicionar(pedido, STATUS_CONFIRMADO)

    adicionar_itens_comanda(pedido, [{"produto_id": cenario["xtudo"], "quantidade": 1}])

    assert _insumo(cenario["carne"]).estoque_atual == pytest.approx(850.0)
    assert pedido.custo_produtos == pytest.approx(6.60)


# --------------------------------------------------------------------------- #
# Movimentação manual e alertas
# --------------------------------------------------------------------------- #


def test_entrada_manual_soma_ao_saldo(cenario):
    insumo = _insumo(cenario["carne"])
    movimentar(insumo, 500.0, MOV_ENTRADA, usuario="joao", observacao="compra semanal")
    db.session.commit()

    assert insumo.estoque_atual == pytest.approx(1500.0)
    linha = MovimentacaoEstoque.query.order_by(MovimentacaoEstoque.id.desc()).first()
    assert linha.saldo_anterior == pytest.approx(1000.0)
    assert linha.saldo_posterior == pytest.approx(1500.0)
    assert linha.usuario == "joao"
    assert linha.tenant_id == cenario["tenant_a"]


def test_perda_subtrai_do_saldo(cenario):
    insumo = _insumo(cenario["pao"])
    movimentar(insumo, 5.0, "perda", observacao="vencido")
    db.session.commit()
    assert insumo.estoque_atual == pytest.approx(45.0)


def test_quantidade_invalida_e_recusada(cenario):
    insumo = _insumo(cenario["carne"])
    for quantidade in (0, -10):
        with pytest.raises(ValueError, match="maior que zero"):
            movimentar(insumo, quantidade, MOV_ENTRADA)


def test_tipo_invalido_e_recusado(cenario):
    with pytest.raises(ValueError, match="Tipo de movimentação inválido"):
        movimentar(_insumo(cenario["carne"]), 10.0, "inventado")


def test_alerta_lista_quem_esta_no_minimo(cenario):
    carne = _insumo(cenario["carne"])
    carne.estoque_atual = 200.0  # mínimo é 300
    db.session.commit()

    alertas = insumos_em_alerta(cenario["tenant_a"])
    assert [i.nome for i in alertas] == ["Carne"]


def test_alerta_ignora_insumo_sem_controle(cenario):
    """Tempero está com saldo zero, mas não é controlado."""
    alertas = insumos_em_alerta(cenario["tenant_a"])
    assert "Tempero" not in [i.nome for i in alertas]


def test_alerta_nao_mistura_tenants(cenario):
    carne_b = _insumo(cenario["carne_b"])
    carne_b.estoque_minimo = 9999.0
    db.session.commit()

    assert [i.nome for i in insumos_em_alerta(cenario["tenant_a"])] == []
    assert [i.nome for i in insumos_em_alerta(cenario["tenant_b"])] == ["Carne do B"]


def test_baixa_ignora_insumo_de_outro_tenant(cenario):
    """Mesmo com a ficha forjada no banco, o insumo do outro tenant não é tocado.

    Grava a linha direto para simular dado corrompido — a barreira do serviço
    precisa valer também nesse caso, não só no formulário.
    """
    db.session.add(
        FichaTecnica(produto_id=cenario["xtudo"], insumo_id=cenario["carne_b"],
                     quantidade_usada=100.0)
    )
    db.session.commit()

    pedido = _pedido(cenario["tenant_a"], [(cenario["xtudo"], 1)])
    aplicar_baixa(pedido)
    db.session.commit()

    assert _insumo(cenario["carne_b"]).estoque_atual == pytest.approx(500.0)
    # E o insumo de fora não entra no custo.
    assert pedido.custo_produtos == pytest.approx(6.60)


# --------------------------------------------------------------------------- #
# Telas
# --------------------------------------------------------------------------- #


def _liberar_estoque(tenant_id, liberado=True):
    from app.models.assinatura import Plano

    # "custos" saiu de dentro de "estoque" e virou recurso próprio: a ficha
    # técnica vive lá, então os dois precisam estar liberados aqui.
    recursos = ["cozinha", "relatorios"] + (["estoque", "custos"] if liberado else [])
    plano = Plano(slug="starter", nome="Starter", preco_mensal=99.0)
    plano.definir_recursos(recursos)
    db.session.add(plano)
    _tenant(tenant_id).plano = "starter"
    db.session.commit()


def test_telas_de_estoque_renderizam(cenario, client):
    from tests.conftest import login_tenant

    _liberar_estoque(cenario["tenant_a"])
    pedido = _pedido(cenario["tenant_a"], [(cenario["xtudo"], 1)])
    transicionar(pedido, STATUS_CONFIRMADO)  # gera movimentação para a listagem
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    estoque = client.get("/admin/insumos", base_url="http://tenant-a.localhost")
    assert estoque.status_code == 200
    corpo = estoque.get_data(as_text=True)
    assert "Carne" in corpo
    assert "Histórico recente" in corpo
    assert "Saída (venda)" in corpo

    ficha = client.get(
        f"/admin/produtos/{cenario['xtudo']}/ficha", base_url="http://tenant-a.localhost"
    )
    assert ficha.status_code == 200
    assert "R$ 6,60" in ficha.get_data(as_text=True)


def test_tela_de_estoque_bloqueada_fora_do_plano(cenario, client):
    from tests.conftest import login_tenant

    _liberar_estoque(cenario["tenant_a"], liberado=False)
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    for url in ("/admin/insumos", f"/admin/produtos/{cenario['xtudo']}/ficha"):
        corpo = client.get(url, base_url="http://tenant-a.localhost", follow_redirects=True)
        assert "não está incluído no plano" in corpo.get_data(as_text=True), url


def test_lancar_entrada_pela_tela(cenario, client):
    from tests.conftest import login_tenant

    _liberar_estoque(cenario["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.post(
        f"/admin/insumos/{cenario['carne']}/movimentar",
        data={"tipo": "entrada", "quantidade": "2.500", "observacao": "compra"},
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )
    assert resposta.status_code == 200
    # "2.500" com ponto de milhar precisa virar 2500, não 2,5.
    assert _insumo(cenario["carne"]).estoque_atual == pytest.approx(3500.0)


def test_tela_nao_permite_lancar_saida_na_mao(cenario, client):
    """Saída e estorno pertencem ao pedido; lançar na mão faria o razão divergir."""
    from tests.conftest import login_tenant

    _liberar_estoque(cenario["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.post(
        f"/admin/insumos/{cenario['carne']}/movimentar",
        data={"tipo": "saida", "quantidade": "100"},
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )
    assert "inválido para lançamento manual" in resposta.get_data(as_text=True)
    assert _insumo(cenario["carne"]).estoque_atual == pytest.approx(1000.0)


def test_insumo_com_historico_nao_pode_ser_excluido(cenario, client):
    """Apagar deixaria furo no razão do estoque."""
    from tests.conftest import login_tenant

    _liberar_estoque(cenario["tenant_a"])
    pedido = _pedido(cenario["tenant_a"], [(cenario["xtudo"], 1)])
    transicionar(pedido, STATUS_CONFIRMADO)
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.post(
        f"/admin/insumos/{cenario['carne']}/excluir",
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )
    assert "não pode ser excluído" in resposta.get_data(as_text=True)
    assert _insumo(cenario["carne"]) is not None


def test_insumo_sem_historico_pode_ser_excluido(cenario, client):
    from tests.conftest import login_tenant

    _liberar_estoque(cenario["tenant_a"])
    novo = Insumo(tenant_id=cenario["tenant_a"], nome="Alface", preco_compra=3.0,
                  quantidade_compra=10.0)
    db.session.add(novo)
    db.session.commit()
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        f"/admin/insumos/{novo.id}/excluir",
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )
    assert Insumo.query.filter_by(nome="Alface").first() is None


def test_quantidade_de_compra_zero_e_recusada_na_tela(cenario, client):
    from tests.conftest import login_tenant

    _liberar_estoque(cenario["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    resposta = client.post(
        "/admin/insumos",
        data={"nome": "Queijo", "unidade": "g", "preco_compra": "50,00",
              "quantidade_compra": "0"},
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )
    assert "maior que zero" in resposta.get_data(as_text=True)
    assert Insumo.query.filter_by(nome="Queijo").first() is None


def test_salvar_ficha_pela_tela(cenario, client):
    from tests.conftest import login_tenant

    _liberar_estoque(cenario["tenant_a"])
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        f"/admin/produtos/{cenario['refri']}/ficha",
        data={
            "insumo_id": [str(cenario["carne"]), str(cenario["pao"])],
            f"quantidade_{cenario['carne']}": "",
            f"quantidade_{cenario['pao']}": "2",
        },
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )

    produto = db.session.get(Produto, cenario["refri"])
    assert [linha.insumo_id for linha in produto.ficha] == [cenario["pao"]]
    assert produto.custo_por_ficha == pytest.approx(1.00)
