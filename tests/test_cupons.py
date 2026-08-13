"""Fase 3 — cupons com reserva de uso e taxa de entrega por bairro.

Os testes centrais: o limite do cupom é respeitado mesmo com pedidos
simultâneos (padrão de reserva), o desconto nunca vem do cliente nem incide
sobre a taxa de entrega, e nada atravessa a fronteira do tenant.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models.cupom import (
    TIPO_FIXO,
    TIPO_PERCENTUAL,
    USO_LIBERADO,
    USO_RESERVADO,
    USO_USADO,
    BairroEntrega,
    Cupom,
    CupomUso,
)
from app.models.pedido import (
    STATUS_CANCELADO,
    STATUS_CONFIRMADO,
    STATUS_EM_PREPARO,
    TIPO_ENTREGA,
    TIPO_RETIRADA,
    Pedido,
)
from app.models.produto import Produto
from app.models.tenant import Tenant
from app.services.cupons import (
    liberar_reservas_expiradas,
    normalizar_codigo,
    usos_disponiveis,
    validar_cupom,
)
from app.services.pedidos import criar_pedido, transicionar
from tests.conftest import login_tenant

BASE_A = "http://tenant-a.localhost"
BASE_B = "http://tenant-b.localhost"


@pytest.fixture()
def cenario(app, two_tenants):
    """Produtos nos dois tenants, um deles marcado como combo promocional."""
    tenant_a, tenant_b = two_tenants["tenant_a"], two_tenants["tenant_b"]

    normal = Produto(tenant_id=tenant_a, nome="X-Tudo", preco=100.0)
    combo = Produto(tenant_id=tenant_a, nome="Combo Promo", preco=50.0, combo_promocional=True)
    pizza_b = Produto(tenant_id=tenant_b, nome="Pizza", preco=80.0)
    db.session.add_all([normal, combo, pizza_b])
    db.session.commit()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "normal": normal.id,
        "combo": combo.id,
        "pizza_b": pizza_b.id,
    }


@pytest.fixture()
def tenant_a_obj(two_tenants):
    return db.session.get(Tenant, two_tenants["tenant_a"])


@pytest.fixture()
def tenant_b_obj(two_tenants):
    return db.session.get(Tenant, two_tenants["tenant_b"])


def _cupom(tenant_id, codigo="DEZ", tipo=TIPO_PERCENTUAL, valor=10.0, **campos):
    cupom = Cupom(tenant_id=tenant_id, codigo=codigo, tipo=tipo, valor=valor, **campos)
    db.session.add(cupom)
    db.session.commit()
    return cupom


def _bairro(tenant_id, nome="Centro", taxa=7.0, **campos):
    bairro = BairroEntrega(tenant_id=tenant_id, nome=nome, taxa=taxa, **campos)
    db.session.add(bairro)
    db.session.commit()
    return bairro


def _payload(produto_id, quantidade=1, **extra):
    base = {
        "cliente": "Maria",
        "telefone": "81999998888",
        "tipo": TIPO_RETIRADA,
        "pagamento": "Dinheiro",
        "carrinho": [{"produto_id": produto_id, "quantidade": quantidade}],
    }
    base.update(extra)
    return base


# --------------------------------------------------------------------------- #
# Cálculo do desconto
# --------------------------------------------------------------------------- #


def test_codigo_e_normalizado():
    assert normalizar_codigo(" bem-vindo10 ") == "BEM-VINDO10"
    assert normalizar_codigo("desc onto!@#") == "DESCONTO"
    assert normalizar_codigo(None) == ""


def test_cupom_percentual(client, cenario, tenant_a_obj):
    _cupom(cenario["tenant_a"], "DEZ", TIPO_PERCENTUAL, 10.0)
    pedido = criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="DEZ"))

    assert pedido.desconto == 10.0  # 10% de 100
    assert pedido.total == 90.0
    assert pedido.cupom_codigo == "DEZ"


def test_cupom_fixo(client, cenario, tenant_a_obj):
    _cupom(cenario["tenant_a"], "MENOS15", TIPO_FIXO, 15.0)
    pedido = criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="MENOS15"))

    assert pedido.desconto == 15.0
    assert pedido.total == 85.0


def test_desconto_nunca_passa_do_valor_dos_itens(client, cenario, tenant_a_obj):
    """Cupom de R$ 500 num pedido de R$ 100 não pode gerar total negativo."""
    _cupom(cenario["tenant_a"], "EXAGERADO", TIPO_FIXO, 500.0)
    pedido = criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="EXAGERADO"))

    assert pedido.desconto == 100.0
    assert pedido.total == 0.0


def test_desconto_nao_incide_sobre_a_taxa_de_entrega(client, cenario, tenant_a_obj):
    """A base do desconto vem só dos itens: o frete nunca é descontado."""
    _bairro(cenario["tenant_a"], "Centro", taxa=20.0)
    _cupom(cenario["tenant_a"], "CEM", TIPO_FIXO, 500.0)  # tenta zerar tudo

    pedido = criar_pedido(
        tenant_a_obj,
        _payload(
            cenario["normal"],
            cupom="CEM",
            tipo=TIPO_ENTREGA,
            endereco="Rua das Flores, 100",
        ),
    )
    assert pedido.subtotal == 100.0
    assert pedido.taxa_entrega == 20.0
    assert pedido.desconto == 100.0, "desconto limitado aos itens"
    assert pedido.total == 20.0, "a taxa de entrega continua devida"


def test_desconto_enviado_pelo_cliente_e_ignorado(client, cenario, tenant_a_obj):
    _cupom(cenario["tenant_a"], "DEZ", TIPO_PERCENTUAL, 10.0)
    pedido = criar_pedido(
        tenant_a_obj, _payload(cenario["normal"], cupom="DEZ", desconto=99.0, total=1.0)
    )
    assert pedido.desconto == 10.0
    assert pedido.total == 90.0


def test_combo_promocional_fica_fora_da_base(client, cenario, tenant_a_obj):
    """50% num carrinho com item normal (100) e combo (50) desconta só 50."""
    _cupom(cenario["tenant_a"], "META", TIPO_PERCENTUAL, 50.0)
    pedido = criar_pedido(
        tenant_a_obj,
        _payload(cenario["normal"], cupom="META")
        | {
            "carrinho": [
                {"produto_id": cenario["normal"], "quantidade": 1},
                {"produto_id": cenario["combo"], "quantidade": 1},
            ]
        },
    )
    assert pedido.subtotal == 150.0
    assert pedido.desconto == 50.0, "50% apenas sobre os R$ 100 elegíveis"
    assert pedido.total == 100.0


def test_cupom_que_permite_combo_desconta_tudo(client, cenario, tenant_a_obj):
    _cupom(cenario["tenant_a"], "TUDO", TIPO_PERCENTUAL, 50.0, permite_combo_promocional=True)
    pedido = criar_pedido(
        tenant_a_obj,
        _payload(cenario["normal"], cupom="TUDO")
        | {
            "carrinho": [
                {"produto_id": cenario["normal"], "quantidade": 1},
                {"produto_id": cenario["combo"], "quantidade": 1},
            ]
        },
    )
    assert pedido.desconto == 75.0  # 50% de 150


def test_carrinho_so_de_combo_recusa_cupom_comum(client, cenario, tenant_a_obj):
    _cupom(cenario["tenant_a"], "DEZ", TIPO_PERCENTUAL, 10.0)
    with pytest.raises(ValueError, match="combos ou produtos promocionais"):
        criar_pedido(tenant_a_obj, _payload(cenario["combo"], cupom="DEZ"))


# --------------------------------------------------------------------------- #
# Validações do cupom
# --------------------------------------------------------------------------- #


def test_cupom_inexistente_derruba_o_pedido(client, cenario, tenant_a_obj):
    with pytest.raises(ValueError, match="inválido ou desativado"):
        criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="NAOEXISTE"))
    assert Pedido.query.count() == 0, "pedido não pode ser criado com cupom inválido"


def test_cupom_desativado_e_recusado(client, cenario, tenant_a_obj):
    _cupom(cenario["tenant_a"], "OFF", ativo=False)
    with pytest.raises(ValueError, match="inválido ou desativado"):
        criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="OFF"))


def test_pedido_minimo_e_respeitado(client, cenario, tenant_a_obj):
    _cupom(cenario["tenant_a"], "MIN200", TIPO_FIXO, 10.0, pedido_minimo=200.0)
    with pytest.raises(ValueError, match="Pedido mínimo"):
        criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="MIN200"))


def test_cupom_fora_da_janela_de_validade(client, cenario, tenant_a_obj):
    agora = datetime.now()
    _cupom(cenario["tenant_a"], "FUTURO", inicio_em=agora + timedelta(days=1))
    _cupom(cenario["tenant_a"], "PASSADO", fim_em=agora - timedelta(days=1))

    with pytest.raises(ValueError, match="ainda não começou"):
        criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="FUTURO"))
    with pytest.raises(ValueError, match="expirou"):
        criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="PASSADO"))


def test_cupom_de_outro_tenant_nao_vale(client, cenario, tenant_a_obj):
    _cupom(cenario["tenant_b"], "SOMENTEB", TIPO_FIXO, 20.0)
    with pytest.raises(ValueError, match="inválido ou desativado"):
        criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="SOMENTEB"))


def test_mesmo_codigo_em_dois_tenants_e_independente(client, cenario, tenant_a_obj, tenant_b_obj):
    """No original o código era unique global — o primeiro a criar travava o resto."""
    _cupom(cenario["tenant_a"], "BEMVINDO", TIPO_FIXO, 10.0)
    _cupom(cenario["tenant_b"], "BEMVINDO", TIPO_FIXO, 30.0)

    pedido_a = criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="BEMVINDO"))
    pedido_b = criar_pedido(tenant_b_obj, _payload(cenario["pizza_b"], cupom="BEMVINDO"))

    assert pedido_a.desconto == 10.0
    assert pedido_b.desconto == 30.0, "cada tenant usa o seu próprio cupom"


def test_cupom_recusado_em_comanda_de_mesa(client, cenario, tenant_a_obj):
    tenant_a_obj.qtd_mesas = 5
    db.session.commit()
    _cupom(cenario["tenant_a"], "DEZ")

    with pytest.raises(ValueError, match="comanda de mesa"):
        criar_pedido(
            tenant_a_obj,
            {
                "cliente": "Mesa 1",
                "tipo": "Mesa",
                "mesa": 1,
                "cupom": "DEZ",
                "carrinho": [{"produto_id": cenario["normal"], "quantidade": 1}],
            },
        )


# --------------------------------------------------------------------------- #
# Reserva de uso — o coração da fase
# --------------------------------------------------------------------------- #


def test_reserva_segura_a_vaga_sem_contar_como_uso(client, cenario, tenant_a_obj):
    cupom = _cupom(cenario["tenant_a"], "UNICO", TIPO_FIXO, 10.0, limite_usos=1)
    criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="UNICO"))

    db.session.refresh(cupom)
    assert cupom.usos_confirmados == 0, "reserva não conta como uso confirmado"
    assert cupom.reservas_ativas == 1
    assert cupom.disponiveis == 0, "mas ocupa a vaga"


def test_limite_respeitado_com_dois_pedidos_seguidos(client, cenario, tenant_a_obj):
    """O segundo cliente não leva o último uso já reservado pelo primeiro."""
    _cupom(cenario["tenant_a"], "UNICO", TIPO_FIXO, 10.0, limite_usos=1)
    criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="UNICO"))

    with pytest.raises(ValueError, match="limite de utilizações"):
        criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="UNICO"))

    assert Pedido.query.count() == 1


def test_confirmar_transforma_reserva_em_uso(client, cenario, tenant_a_obj):
    cupom = _cupom(cenario["tenant_a"], "DEZ", TIPO_FIXO, 10.0, limite_usos=2)
    pedido = criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="DEZ"))

    transicionar(pedido, STATUS_CONFIRMADO)

    db.session.refresh(cupom)
    assert cupom.usos_confirmados == 1
    assert cupom.reservas_ativas == 0
    assert CupomUso.query.filter_by(pedido_id=pedido.id).one().status == USO_USADO


def test_pular_confirmado_ainda_consome_o_cupom(client, cenario, tenant_a_obj):
    """O original só consumia em "Confirmado", mas Novo -> Em preparo é válido:
    a reserva ficava presa, bloqueando a vaga sem nunca contar como uso."""
    cupom = _cupom(cenario["tenant_a"], "DEZ", TIPO_FIXO, 10.0, limite_usos=2)
    pedido = criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="DEZ"))

    transicionar(pedido, STATUS_EM_PREPARO)  # pula Confirmado

    db.session.refresh(cupom)
    assert cupom.usos_confirmados == 1
    assert cupom.reservas_ativas == 0


def test_cancelar_devolve_a_vaga(client, cenario, tenant_a_obj):
    cupom = _cupom(cenario["tenant_a"], "UNICO", TIPO_FIXO, 10.0, limite_usos=1)
    pedido = criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="UNICO"))
    assert cupom.disponiveis == 0

    transicionar(pedido, STATUS_CANCELADO)

    db.session.refresh(cupom)
    assert cupom.usos_confirmados == 0
    assert cupom.disponiveis == 1, "vaga volta para outro cliente"
    assert CupomUso.query.filter_by(pedido_id=pedido.id).one().status == USO_LIBERADO


def test_cupom_usado_nao_volta_ao_cancelar(client, cenario, tenant_a_obj):
    """Uma vez consumido, cancelar não devolve a vaga — o uso já aconteceu."""
    cupom = _cupom(cenario["tenant_a"], "DEZ", TIPO_FIXO, 10.0, limite_usos=1)
    pedido = criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="DEZ"))
    transicionar(pedido, STATUS_CONFIRMADO)
    transicionar(pedido, STATUS_CANCELADO)

    db.session.refresh(cupom)
    assert cupom.usos_confirmados == 1
    assert cupom.disponiveis == 0


def test_limite_liberado_pode_ser_reutilizado(client, cenario, tenant_a_obj):
    _cupom(cenario["tenant_a"], "UNICO", TIPO_FIXO, 10.0, limite_usos=1)
    primeiro = criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="UNICO"))
    transicionar(primeiro, STATUS_CANCELADO)

    segundo = criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="UNICO"))
    assert segundo.desconto == 10.0


def test_reserva_expirada_e_liberada(client, cenario, tenant_a_obj):
    """A expiração não é usada nesta fase, mas a mecânica precisa funcionar
    para a Fase 6 (pedido aguardando pagamento)."""
    cupom = _cupom(cenario["tenant_a"], "UNICO", TIPO_FIXO, 10.0, limite_usos=1)
    pedido = criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="UNICO"))

    uso = CupomUso.query.filter_by(pedido_id=pedido.id).one()
    assert uso.expira_em is None, "reserva não expira nesta fase"

    uso.expira_em = datetime.now() - timedelta(minutes=1)
    db.session.commit()

    assert liberar_reservas_expiradas() == 1
    db.session.refresh(cupom)
    assert cupom.disponiveis == 1
    assert CupomUso.query.filter_by(pedido_id=pedido.id).one().status == USO_LIBERADO


def test_usos_disponiveis_ignora_a_reserva_do_proprio_pedido(client, cenario, tenant_a_obj):
    """Revalidar o cupom de um pedido não pode acusar limite por causa dele mesmo."""
    cupom = _cupom(cenario["tenant_a"], "UNICO", TIPO_FIXO, 10.0, limite_usos=1)
    pedido = criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="UNICO"))

    assert usos_disponiveis(cupom) == 0
    assert usos_disponiveis(cupom, excluir_pedido_id=pedido.id) == 1


# --------------------------------------------------------------------------- #
# Taxa de entrega por bairro
# --------------------------------------------------------------------------- #


def test_taxa_do_bairro_entra_no_total(client, cenario, tenant_a_obj):
    _bairro(cenario["tenant_a"], "Centro", taxa=7.5)
    pedido = criar_pedido(
        tenant_a_obj,
        _payload(cenario["normal"], tipo=TIPO_ENTREGA, endereco="Rua das Flores, 100"),
    )
    assert pedido.taxa_entrega == 7.5
    assert pedido.total == 107.5
    assert pedido.bairro_nome == "Centro"


def test_prazo_do_bairro_soma_na_estimativa(client, cenario, tenant_a_obj):
    _bairro(cenario["tenant_a"], "Longe", taxa=10.0, prazo_adicional_min=25)
    pedido = criar_pedido(
        tenant_a_obj,
        _payload(cenario["normal"], tipo=TIPO_ENTREGA, endereco="Rua Distante, 900"),
    )
    assert pedido.tempo_estimado_min == 40 + 25
    assert pedido.tempo_estimado_max == 60 + 25


def test_sem_bairros_cadastrados_entrega_sai_com_taxa_zero(client, cenario, tenant_a_obj):
    """Tenant novo precisa poder vender entrega antes de cadastrar bairros."""
    pedido = criar_pedido(
        tenant_a_obj,
        _payload(cenario["normal"], tipo=TIPO_ENTREGA, endereco="Rua Qualquer, 12"),
    )
    assert pedido.taxa_entrega == 0.0
    assert pedido.bairro_id is None


def test_com_varios_bairros_a_escolha_e_obrigatoria(client, cenario, tenant_a_obj):
    _bairro(cenario["tenant_a"], "Centro", taxa=5.0)
    _bairro(cenario["tenant_a"], "Bairro Novo", taxa=9.0)

    with pytest.raises(ValueError, match="Selecione o bairro"):
        criar_pedido(
            tenant_a_obj,
            _payload(cenario["normal"], tipo=TIPO_ENTREGA, endereco="Rua Sem Bairro, 1"),
        )


def test_bairro_unico_e_assumido_sem_escolha(client, cenario, tenant_a_obj):
    _bairro(cenario["tenant_a"], "Centro", taxa=5.0)
    pedido = criar_pedido(
        tenant_a_obj,
        _payload(cenario["normal"], tipo=TIPO_ENTREGA, endereco="Rua Unica, 1"),
    )
    assert pedido.bairro_nome == "Centro"
    assert pedido.taxa_entrega == 5.0


def test_bairro_de_outro_tenant_nao_e_aceito(client, cenario, tenant_a_obj):
    bairro_b = _bairro(cenario["tenant_b"], "Centro do B", taxa=1.0)
    _bairro(cenario["tenant_a"], "Centro do A", taxa=5.0)
    _bairro(cenario["tenant_a"], "Outro do A", taxa=8.0)

    with pytest.raises(ValueError, match="Selecione o bairro"):
        criar_pedido(
            tenant_a_obj,
            _payload(
                cenario["normal"],
                tipo=TIPO_ENTREGA,
                endereco="Rua das Flores, 100",
                bairro_id=bairro_b.id,
            ),
        )


def test_bairro_inativo_nao_e_aceito(client, cenario, tenant_a_obj):
    inativo = _bairro(cenario["tenant_a"], "Desativado", taxa=3.0, ativo=False)
    _bairro(cenario["tenant_a"], "Centro", taxa=5.0)
    _bairro(cenario["tenant_a"], "Outro", taxa=8.0)

    with pytest.raises(ValueError, match="Selecione o bairro"):
        criar_pedido(
            tenant_a_obj,
            _payload(
                cenario["normal"],
                tipo=TIPO_ENTREGA,
                endereco="Rua das Flores, 100",
                bairro_id=inativo.id,
            ),
        )


def test_taxa_fica_congelada_no_pedido(client, cenario, tenant_a_obj):
    """Mudar a taxa do bairro depois não altera pedido já feito."""
    bairro = _bairro(cenario["tenant_a"], "Centro", taxa=5.0)
    pedido = criar_pedido(
        tenant_a_obj,
        _payload(cenario["normal"], tipo=TIPO_ENTREGA, endereco="Rua das Flores, 100"),
    )
    numero = pedido.numero

    bairro.taxa = 50.0
    bairro.nome = "Centro Renomeado"
    db.session.commit()

    pedido = Pedido.query.filter_by(numero=numero, tenant_id=cenario["tenant_a"]).one()
    assert pedido.taxa_entrega == 5.0
    assert pedido.bairro_nome == "Centro"
    assert pedido.total == 105.0


def test_excluir_bairro_preserva_o_historico(client, cenario, tenant_a_obj):
    bairro = _bairro(cenario["tenant_a"], "Centro", taxa=5.0)
    pedido = criar_pedido(
        tenant_a_obj,
        _payload(cenario["normal"], tipo=TIPO_ENTREGA, endereco="Rua das Flores, 100"),
    )
    numero = pedido.numero

    db.session.delete(bairro)
    db.session.commit()

    pedido = Pedido.query.filter_by(numero=numero, tenant_id=cenario["tenant_a"]).one()
    assert pedido.bairro_nome == "Centro"
    assert pedido.taxa_entrega == 5.0


# --------------------------------------------------------------------------- #
# Carrinho e telas
# --------------------------------------------------------------------------- #


def _adicionar(client, produto_id, base_url=BASE_A, quantidade=1):
    return client.post(
        "/carrinho/adicionar",
        data={"produto_id": produto_id, "quantidade": quantidade},
        base_url=base_url,
        follow_redirects=True,
    )


def test_aplicar_cupom_no_carrinho_mostra_o_desconto(client, cenario):
    _cupom(cenario["tenant_a"], "DEZ", TIPO_PERCENTUAL, 10.0)
    _adicionar(client, cenario["normal"])

    resposta = client.post(
        "/carrinho/cupom", data={"cupom": "dez"}, base_url=BASE_A, follow_redirects=True
    )
    corpo = resposta.get_data(as_text=True)
    assert "Cupom DEZ aplicado" in corpo
    assert "R$ 10,00" in corpo  # desconto
    assert "R$ 90,00" in corpo  # total


def test_cupom_invalido_no_carrinho_nao_fica_guardado(client, cenario):
    _adicionar(client, cenario["normal"])
    client.post("/carrinho/cupom", data={"cupom": "NAOEXISTE"}, base_url=BASE_A, follow_redirects=True)

    corpo = client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)
    assert "Desconto" not in corpo


def test_remover_item_derruba_cupom_que_perdeu_o_minimo(client, cenario):
    """O cupom é revalidado contra o carrinho atual, não fica preso da aplicação."""
    _cupom(cenario["tenant_a"], "MIN150", TIPO_FIXO, 20.0, pedido_minimo=150.0)
    _adicionar(client, cenario["normal"], quantidade=2)  # 200

    client.post("/carrinho/cupom", data={"cupom": "MIN150"}, base_url=BASE_A, follow_redirects=True)
    assert "Desconto" in client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)

    # Sobra 100: abaixo do mínimo.
    client.post("/carrinho/remover", data={"indice": 0}, base_url=BASE_A, follow_redirects=True)
    _adicionar(client, cenario["normal"], quantidade=1)
    corpo = client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)
    assert "Pedido mínimo" in corpo


def test_cupom_da_sessao_nao_vaza_entre_tenants(client, cenario):
    _cupom(cenario["tenant_a"], "SOA", TIPO_FIXO, 10.0)
    _adicionar(client, cenario["normal"], base_url=BASE_A)
    client.post("/carrinho/cupom", data={"cupom": "SOA"}, base_url=BASE_A, follow_redirects=True)

    _adicionar(client, cenario["pizza_b"], base_url=BASE_B)
    corpo_b = client.get("/carrinho", base_url=BASE_B).get_data(as_text=True)
    assert "SOA" not in corpo_b
    assert "Desconto" not in corpo_b


def test_checkout_usa_o_cupom_da_sessao(client, cenario):
    _cupom(cenario["tenant_a"], "DEZ", TIPO_PERCENTUAL, 10.0)
    _adicionar(client, cenario["normal"])
    client.post("/carrinho/cupom", data={"cupom": "DEZ"}, base_url=BASE_A, follow_redirects=True)

    client.post(
        "/pedido",
        data={"cliente": "Maria", "telefone": "81999998888", "tipo": TIPO_RETIRADA, "pagamento": "Dinheiro"},
        base_url=BASE_A,
        follow_redirects=True,
    )
    pedido = Pedido.query.one()
    assert pedido.cupom_codigo == "DEZ"
    assert pedido.total == 90.0


def test_cupom_e_limpo_apos_o_pedido(client, cenario):
    _cupom(cenario["tenant_a"], "DEZ", TIPO_PERCENTUAL, 10.0, limite_usos=5)
    _adicionar(client, cenario["normal"])
    client.post("/carrinho/cupom", data={"cupom": "DEZ"}, base_url=BASE_A, follow_redirects=True)
    client.post(
        "/pedido",
        data={"cliente": "Maria", "telefone": "81999998888", "tipo": TIPO_RETIRADA, "pagamento": "Dinheiro"},
        base_url=BASE_A,
        follow_redirects=True,
    )

    _adicionar(client, cenario["normal"])
    corpo = client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)
    assert "Desconto" not in corpo, "cupom não pode continuar aplicado no próximo pedido"


def test_admin_nao_ve_cupom_nem_bairro_de_outro_tenant(client, cenario):
    _cupom(cenario["tenant_a"], "CUPOMDOA", TIPO_FIXO, 5.0)
    _cupom(cenario["tenant_b"], "CUPOMDOB", TIPO_FIXO, 5.0)
    _bairro(cenario["tenant_a"], "Bairro do A")
    _bairro(cenario["tenant_b"], "Bairro do B")

    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    cupons = client.get("/admin/cupons", base_url=BASE_A).get_data(as_text=True)
    assert "CUPOMDOA" in cupons and "CUPOMDOB" not in cupons

    bairros = client.get("/admin/bairros", base_url=BASE_A).get_data(as_text=True)
    assert "Bairro do A" in bairros and "Bairro do B" not in bairros


def test_limite_nao_pode_ficar_abaixo_dos_usos_ja_confirmados(client, cenario, tenant_a_obj):
    cupom = _cupom(cenario["tenant_a"], "DEZ", TIPO_FIXO, 10.0, limite_usos=3)
    for _ in range(2):
        transicionar(
            criar_pedido(tenant_a_obj, _payload(cenario["normal"], cupom="DEZ")),
            STATUS_CONFIRMADO,
        )
    db.session.refresh(cupom)
    assert cupom.usos_confirmados == 2

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    resposta = client.post(
        f"/admin/cupons/{cupom.id}/salvar",
        data={"valor": "10,00", "pedido_minimo": "0", "limite_usos": "1", "ativo": "on"},
        base_url=BASE_A,
        follow_redirects=True,
    )
    assert "já foi usado" in resposta.get_data(as_text=True)
    db.session.refresh(cupom)
    assert cupom.limite_usos == 3, "limite não pode cair abaixo dos usos confirmados"


def test_telas_de_cupom_e_bairro_renderizam(client, cenario, tenant_a_obj):
    _cupom(cenario["tenant_a"], "DEZ", TIPO_PERCENTUAL, 10.0, descricao="Boas-vindas")
    _bairro(cenario["tenant_a"], "Centro", taxa=7.0, prazo_adicional_min=10)

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    for url in ("/admin/cupons", "/admin/bairros"):
        assert client.get(url, base_url=BASE_A).status_code == 200

    corpo = client.get("/admin/cupons", base_url=BASE_A).get_data(as_text=True)
    assert "Boas-vindas" in corpo
    assert "Disponíveis" in corpo
