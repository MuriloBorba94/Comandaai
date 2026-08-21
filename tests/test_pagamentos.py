"""Fase 6 — PIX por pedido, por tenant.

Três coisas aqui valem mais que as outras, e todas são sobre dinheiro:

1. **O código de pagamento tem que estar correto.** Um BR Code com um dígito
   errado não dá erro em lugar nenhum: o cliente abre o banco, vê "QR inválido"
   e desiste da compra. Por isso o código é validado lendo de volta campo a
   campo, e não só comparando com uma string esperada.
2. **Pagamento e pedido não podem divergir.** Confirmar o recebimento e liberar
   o pedido é uma operação só.
3. **Nada de comida antes do dinheiro.** Pedido em "Aguardando PIX" não baixa
   estoque, não imprime comanda e não avança por botão de status.
"""

from __future__ import annotations

import pytest

from app.extensions import db
from app.models.pagamento import (
    STATUS_AGUARDANDO,
    STATUS_CANCELADO,
    STATUS_PAGO,
    STATUS_REVISAO,
    Pagamento,
)
from app.models.pedido import (
    STATUS_AGUARDANDO_PIX,
    STATUS_CANCELADO as PEDIDO_CANCELADO,
    STATUS_CONFIRMADO,
    STATUS_NOVO,
    TIPO_ENTREGA,
    TIPO_RETIRADA,
    Pedido,
)
from app.models.produto import Produto
from app.models.tenant import Tenant
from app.services import pagamentos
from app.services.pagamentos import brcode
from app.services.pedidos import (
    PAGAMENTO_PIX_ONLINE,
    criar_pedido,
    formas_de_pagamento,
    proximos_status,
    transicionar,
)
from tests.conftest import login_tenant

BASE_A = "http://tenant-a.localhost"
BASE_B = "http://tenant-b.localhost"


def ler_campos(codigo: str) -> dict[str, str]:
    """Lê um BR Code campo a campo, como o aplicativo do banco faria.

    Se algum campo declarar um tamanho que não bate com o conteúdo, o laço sai
    do lugar e o teste falha — que é exatamente o defeito que se quer pegar.
    """
    campos: dict[str, str] = {}
    posicao = 0
    while posicao < len(codigo):
        identificador = codigo[posicao : posicao + 2]
        tamanho = int(codigo[posicao + 2 : posicao + 4])
        campos[identificador] = codigo[posicao + 4 : posicao + 4 + tamanho]
        posicao += 4 + tamanho
    return campos


@pytest.fixture()
def loja(app, two_tenants):
    tenant_a = db.session.get(Tenant, two_tenants["tenant_a"])
    tenant_b = db.session.get(Tenant, two_tenants["tenant_b"])
    tenant_a.pix_chave = "restaurante@exemplo.com.br"
    tenant_a.pix_cidade = "Vicência"

    xtudo = Produto(tenant_id=tenant_a.id, nome="X-Tudo", preco=30.0)
    pizza = Produto(tenant_id=tenant_b.id, nome="Pizza", preco=40.0)
    db.session.add_all([xtudo, pizza])
    db.session.commit()
    return {"tenant_a": tenant_a, "tenant_b": tenant_b, "xtudo": xtudo.id, "pizza": pizza.id}


def _pedido(tenant, produto_id, pagamento=PAGAMENTO_PIX_ONLINE, quantidade=2, **extra):
    dados = {
        "cliente": "Maria",
        "telefone": "81999998888",
        "tipo": TIPO_RETIRADA,
        "pagamento": pagamento,
        "carrinho": [{"produto_id": produto_id, "quantidade": quantidade}],
    }
    dados.update(extra)
    return criar_pedido(tenant, dados)


# --------------------------------------------------------------------------- #
# O código de pagamento
# --------------------------------------------------------------------------- #


def test_crc_bate_com_o_valor_de_conferencia_da_especificacao():
    """"123456789" -> 29B1 é a constante de teste do CRC-16/CCITT-FALSE.

    Não depende de eu ter copiado certo nenhum documento: é o valor que define
    o algoritmo.
    """
    assert brcode.crc16("123456789") == "29B1"


def test_codigo_fecha_o_proprio_crc():
    codigo = brcode.montar(
        chave="a@b.com", valor=10.0, recebedor="Teste", cidade="Recife", txid="PED1"
    )
    assert brcode.crc16(codigo[:-4]) == codigo[-4:]


def test_codigo_tem_os_campos_que_o_banco_espera():
    codigo = brcode.montar(
        chave="restaurante@exemplo.com.br",
        valor=59.9,
        recebedor="Borba's Burguer",
        cidade="Vicência",
        txid="PED42",
    )
    campos = ler_campos(codigo)

    assert campos["00"] == "01"
    assert campos["53"] == "986"  # real
    assert campos["58"] == "BR"
    assert campos["54"] == "59.90"  # sempre duas casas, sempre com ponto
    assert ler_campos(campos["26"])["00"] == "br.gov.bcb.pix"
    assert ler_campos(campos["26"])["01"] == "restaurante@exemplo.com.br"
    assert ler_campos(campos["62"])["05"] == "PED42"


def test_acento_e_apostrofo_saem_do_nome_sem_picotar_a_palavra():
    """"Borba's Burguer" não pode virar "BORBA S BURGUER" na tela do banco."""
    codigo = brcode.montar(
        chave="a@b.com", valor=10.0, recebedor="Borba's Burguer", cidade="Vicência", txid="X"
    )
    campos = ler_campos(codigo)
    assert campos["59"] == "BORBAS BURGUER"
    assert campos["60"] == "VICENCIA"


def test_nome_e_cidade_respeitam_o_limite_do_padrao():
    """Estourar 25/15 faz o aplicativo do banco recusar o código em silêncio."""
    codigo = brcode.montar(
        chave="a@b.com",
        valor=10.0,
        recebedor="Restaurante do Seu Joaquim Comida Caseira Ltda",
        cidade="Sao Jose do Rio Preto do Norte",
        txid="X",
    )
    campos = ler_campos(codigo)
    assert len(campos["59"]) <= 25
    assert len(campos["60"]) <= 15


def test_valor_com_centavo_quebrado_nao_perde_dinheiro():
    codigo = brcode.montar(chave="a@b.com", valor=0.99, recebedor="X", cidade="Y", txid="Z")
    assert ler_campos(codigo)["54"] == "0.99"


def test_codigo_sem_chave_ou_sem_valor_e_recusado():
    with pytest.raises(ValueError):
        brcode.montar(chave="", valor=10.0, recebedor="X", cidade="Y", txid="Z")
    with pytest.raises(ValueError):
        brcode.montar(chave="a@b.com", valor=0, recebedor="X", cidade="Y", txid="Z")


def test_nome_vazio_cai_para_a_reserva_em_vez_de_gerar_campo_vazio():
    codigo = brcode.montar(
        chave="a@b.com", valor=1.0, recebedor="!!!", cidade="", txid="", reserva_recebedor="LOJA"
    )
    campos = ler_campos(codigo)
    assert campos["59"] == "LOJA"
    assert campos["60"] == "BRASIL"
    assert ler_campos(campos["62"])["05"] == "PEDIDO"


def test_qr_desenhado_e_exatamente_a_matriz_do_codificador():
    """O SVG passa por recortes meus (declaração XML, tamanho, aria-label).

    Este teste separa o que é meu do que é da biblioteca: a codificação do QR é
    dela e não se testa aqui; o que se testa é se o desenho que sai da minha
    função ainda representa, módulo a módulo, a matriz que ela calculou. Um
    regex meu que comesse um pedaço do traçado deixaria um QR bonito na tela e
    ilegível na câmera — e ninguém descobriria até um cliente tentar pagar.
    """
    import re

    import qrcode

    from app.services.pagamentos.qr import svg

    codigo = brcode.montar(
        chave="a@b.com", valor=60.0, recebedor="Teste", cidade="Recife", txid="PED1"
    )

    referencia = qrcode.QRCode(border=2, box_size=10)
    referencia.add_data(codigo)
    referencia.make(fit=True)
    matriz = referencia.get_matrix()

    # Cada "Mx,yH..." do traçado é um módulo escuro na posição (linha, coluna).
    desenhados = {(int(y), int(x)) for x, y in re.findall(r"M(\d+),(\d+)H", svg(codigo))}

    divergentes = [
        (linha, coluna)
        for linha, valores in enumerate(matriz)
        for coluna, escuro in enumerate(valores)
        if bool(escuro) != ((linha, coluna) in desenhados)
    ]
    assert not divergentes
    assert desenhados, "o traçado não desenhou módulo nenhum"


def test_qr_sai_pronto_para_ir_dentro_da_pagina():
    from app.services.pagamentos.qr import svg

    marcacao = svg(
        brcode.montar(chave="a@b.com", valor=1.0, recebedor="X", cidade="Y", txid="Z")
    )
    # Declaração XML no meio de um HTML não serve para nada e atrapalha.
    assert not marcacao.startswith("<?xml")
    # Tamanho relativo: o QR acompanha a caixa no celular e no computador.
    assert 'width="100%"' in marcacao
    assert "45mm" not in marcacao


# --------------------------------------------------------------------------- #
# A fábrica de provedores
# --------------------------------------------------------------------------- #


def test_restaurante_sem_chave_nao_tem_provedor(loja):
    assert pagamentos.provedor_do_tenant(loja["tenant_a"]) is not None
    assert pagamentos.provedor_do_tenant(loja["tenant_b"]) is None
    assert "chave PIX" in pagamentos.por_que_nao(loja["tenant_b"])


def test_checkout_so_oferece_pix_para_quem_pode_receber(loja):
    assert PAGAMENTO_PIX_ONLINE in formas_de_pagamento(loja["tenant_a"])
    assert PAGAMENTO_PIX_ONLINE not in formas_de_pagamento(loja["tenant_b"])


def test_plano_sem_pix_nao_oferece_mesmo_com_chave_cadastrada(loja):
    from app.models.assinatura import Plano

    db.session.add(Plano(slug="basico", nome="Básico", recursos="cozinha"))
    loja["tenant_a"].plano = "basico"
    db.session.commit()

    assert PAGAMENTO_PIX_ONLINE not in formas_de_pagamento(loja["tenant_a"])


# --------------------------------------------------------------------------- #
# Criar o pedido com cobrança
# --------------------------------------------------------------------------- #


def test_pedido_com_pix_nasce_esperando_pagamento_e_com_codigo(loja):
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])

    assert pedido.status == STATUS_AGUARDANDO_PIX
    pagamento = pedido.pagamento_online
    assert pagamento is not None
    assert pagamento.status == STATUS_AGUARDANDO
    assert pagamento.valor_centavos == 6000
    assert ler_campos(pagamento.brcode)["54"] == "60.00"
    assert pagamento.txid == f"PED{pedido.numero}"


def test_pedido_comum_nao_cria_cobranca(loja):
    pedido = _pedido(loja["tenant_a"], loja["xtudo"], pagamento="Dinheiro")

    assert pedido.status == STATUS_NOVO
    assert pedido.pagamento_online is None


def test_pix_escolhido_por_formulario_forjado_e_recusado(loja):
    """A opção não aparece na tela do tenant B — mas a tela não é a trava."""
    with pytest.raises(ValueError, match="chave PIX"):
        _pedido(loja["tenant_b"], loja["pizza"])

    assert Pedido.query.filter_by(tenant_id=loja["tenant_b"].id).count() == 0


def test_cobranca_e_do_total_ja_com_desconto(loja):
    from app.models.cupom import Cupom

    db.session.add(
        Cupom(tenant_id=loja["tenant_a"].id, codigo="DEZ", tipo="percentual", valor=10, ativo=True)
    )
    db.session.commit()

    pedido = _pedido(loja["tenant_a"], loja["xtudo"], cupom="DEZ")

    assert pedido.desconto == 6.0
    assert pedido.total == 54.0
    # O que o cliente vai pagar no banco tem que ser o que ele viu na tela.
    assert ler_campos(pedido.pagamento_online.brcode)["54"] == "54.00"
    assert pedido.pagamento_online.valor_centavos == 5400


def test_pedido_nao_fica_gravado_sem_codigo_para_pagar(loja, monkeypatch):
    """Se a cobrança falhar, o pedido inteiro é desfeito.

    O contrário deixaria o cliente numa tela pedindo pagamento sem nada para
    copiar — um beco sem saída de onde ele só sai desistindo.
    """
    monkeypatch.setattr(
        pagamentos.registro.PixDireto, "criar",
        lambda self, pedido: pagamentos.Cobranca(False, erro="provedor fora do ar"),
    )

    with pytest.raises(ValueError, match="provedor fora do ar"):
        _pedido(loja["tenant_a"], loja["xtudo"])

    assert Pedido.query.count() == 0
    assert Pagamento.query.count() == 0


# --------------------------------------------------------------------------- #
# Nada de comida antes do dinheiro
# --------------------------------------------------------------------------- #


def test_pedido_esperando_pix_nao_baixa_estoque(loja):
    from app.models.estoque import FichaTecnica, Insumo, MovimentacaoEstoque

    insumo = Insumo(
        tenant_id=loja["tenant_a"].id, nome="Carne", unidade="kg", estoque_atual=10.0,
        quantidade_compra=1.0, preco_compra=40.0,
    )
    db.session.add(insumo)
    db.session.flush()
    db.session.add(FichaTecnica(produto_id=loja["xtudo"], insumo_id=insumo.id, quantidade_usada=0.2))
    db.session.commit()

    _pedido(loja["tenant_a"], loja["xtudo"])

    assert insumo.estoque_atual == 10.0
    assert MovimentacaoEstoque.query.count() == 0


def test_cozinha_nao_oferece_botao_que_pula_o_pagamento(loja):
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])

    oferecidos = proximos_status(pedido)
    assert STATUS_CONFIRMADO not in oferecidos
    assert PEDIDO_CANCELADO in oferecidos


def test_pedido_esperando_pix_nao_imprime_comanda(loja):
    from app.models.impressao import ImpressaoJob
    from app.services.impressao import parear

    parear(loja["tenant_a"])
    _pedido(loja["tenant_a"], loja["xtudo"])

    assert ImpressaoJob.query.count() == 0


# --------------------------------------------------------------------------- #
# Confirmar o recebimento
# --------------------------------------------------------------------------- #


def test_confirmar_recebimento_libera_o_pedido_na_mesma_acao(loja):
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])

    assert pagamentos.confirmar_recebimento(pedido.pagamento_online, actor="carlos") is True

    assert pedido.status == STATUS_CONFIRMADO
    assert pedido.pagamento_online.status == STATUS_PAGO
    assert pedido.pagamento_online.confirmado_por == "carlos"
    assert pedido.pagamento_online.pago_em is not None
    # Quem olhar o pedido depois precisa ver que já foi pago sem abrir outra tela.
    assert "pago" in pedido.pagamento


def test_confirmar_duas_vezes_nao_reclama_nem_duplica(loja):
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    pagamentos.confirmar_recebimento(pedido.pagamento_online)

    # Dois cliques no mesmo botão é acidente comum de quem está atendendo.
    assert pagamentos.confirmar_recebimento(pedido.pagamento_online) is False
    assert pedido.status == STATUS_CONFIRMADO


def test_pagamento_confirmado_baixa_estoque_e_imprime_como_qualquer_pedido(loja):
    from app.models.estoque import Insumo, FichaTecnica
    from app.models.impressao import ImpressaoJob
    from app.services.impressao import parear

    insumo = Insumo(
        tenant_id=loja["tenant_a"].id, nome="Carne", unidade="kg", estoque_atual=10.0,
        quantidade_compra=1.0, preco_compra=40.0,
    )
    db.session.add(insumo)
    db.session.flush()
    db.session.add(FichaTecnica(produto_id=loja["xtudo"], insumo_id=insumo.id, quantidade_usada=0.2))
    parear(loja["tenant_a"])
    db.session.commit()

    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    pagamentos.confirmar_recebimento(pedido.pagamento_online)

    assert insumo.estoque_atual == pytest.approx(9.6)
    assert ImpressaoJob.query.count() == 1


def test_dinheiro_que_chega_depois_do_cancelamento_vai_para_conferencia(loja):
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, PEDIDO_CANCELADO)

    with pytest.raises(ValueError, match="cancelado"):
        pagamentos.confirmar_recebimento(pedido.pagamento_online)

    # Não vira "pago" sozinho: devolver ou refazer o pedido é decisão de gente.
    assert pedido.pagamento_online.status == STATUS_REVISAO
    assert pedido.status == PEDIDO_CANCELADO


def test_cancelar_pedido_encerra_a_cobranca_aberta(loja):
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    transicionar(pedido, PEDIDO_CANCELADO)

    assert pedido.pagamento_online.status == STATUS_CANCELADO


def test_cancelar_nao_mexe_em_cobranca_ja_paga(loja):
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    pagamentos.confirmar_recebimento(pedido.pagamento_online)
    transicionar(pedido, PEDIDO_CANCELADO)

    # O dinheiro entrou. Marcar como cancelado apagaria o rastro de uma devolução
    # que ainda precisa acontecer.
    assert pedido.pagamento_online.status == STATUS_PAGO


# --------------------------------------------------------------------------- #
# As telas
# --------------------------------------------------------------------------- #


def test_cliente_ve_o_codigo_e_o_qr_na_tela_do_pedido(client, loja):
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])

    resposta = client.get(f"/pedido/{pedido.public_token}", base_url=BASE_A)
    texto = resposta.get_data(as_text=True)

    assert pedido.pagamento_online.brcode in texto
    assert "<svg" in texto
    assert "copia e cola" in texto


def test_codigo_some_da_tela_depois_de_pago(client, loja):
    """Código à vista num pedido quitado é convite para pagar duas vezes."""
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    codigo = pedido.pagamento_online.brcode
    pagamentos.confirmar_recebimento(pedido.pagamento_online)

    texto = client.get(f"/pedido/{pedido.public_token}", base_url=BASE_A).get_data(as_text=True)
    assert codigo not in texto
    assert "Pagamento confirmado" in texto


def test_consulta_de_pagamento_responde_o_estado(client, loja):
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])
    url = f"/pedido/{pedido.public_token}/pagamento.json"

    antes = client.get(url, base_url=BASE_A).get_json()
    assert antes == {"pago": False, "status": STATUS_AGUARDANDO, "pedido_status": STATUS_AGUARDANDO_PIX}

    pagamentos.confirmar_recebimento(pedido.pagamento_online)
    depois = client.get(url, base_url=BASE_A).get_json()
    assert depois["pago"] is True


def test_pedido_de_um_restaurante_nao_abre_no_outro(client, loja):
    """O token é filtrado junto com o tenant, aqui como no resto da vitrine."""
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])

    assert client.get(f"/pedido/{pedido.public_token}", base_url=BASE_B).status_code == 404
    assert client.get(f"/pedido/{pedido.public_token}/pagamento.json", base_url=BASE_B).status_code == 404


def test_botao_da_cozinha_confirma_o_recebimento(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    pedido = _pedido(loja["tenant_a"], loja["xtudo"])

    resposta = client.post(
        f"/cozinha/pedidos/{pedido.id}/pagamento", base_url=BASE_A, follow_redirects=True
    )

    assert "pagamento confirmado" in resposta.get_data(as_text=True)
    assert pedido.status == STATUS_CONFIRMADO
    assert pedido.pagamento_online.confirmado_por == "admin"


def test_cozinha_nao_confirma_pagamento_de_outro_restaurante(client, loja):
    loja["tenant_b"].pix_chave = "outro@exemplo.com"
    db.session.commit()
    pedido_b = _pedido(loja["tenant_b"], loja["pizza"])

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    client.post(f"/cozinha/pedidos/{pedido_b.id}/pagamento", base_url=BASE_A, follow_redirects=True)

    assert pedido_b.pagamento_online.status == STATUS_AGUARDANDO
    assert pedido_b.status == STATUS_AGUARDANDO_PIX


def test_painel_salva_a_chave_e_mostra_como_o_cliente_vai_ver(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/configuracoes/pix",
        data={"pix_chave": "12345678000199", "pix_recebedor": "Borba's Burguer", "pix_cidade": "Vicência"},
        base_url=BASE_A,
    )
    assert loja["tenant_a"].pix_chave == "12345678000199"

    texto = client.get("/admin/configuracoes", base_url=BASE_A).get_data(as_text=True)
    # A prévia existe porque o dono não tem como adivinhar o corte e a limpeza
    # que o padrão do Banco Central impõe.
    assert "BORBAS BURGUER" in texto
    assert "VICENCIA" in texto


def test_apagar_a_chave_desliga_o_pix_do_cardapio(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post("/admin/configuracoes/pix", data={"pix_chave": ""}, base_url=BASE_A)

    assert loja["tenant_a"].pix_chave is None
    assert PAGAMENTO_PIX_ONLINE not in formas_de_pagamento(loja["tenant_a"])


def test_chave_de_um_restaurante_nao_e_editavel_pelo_outro(client, loja):
    """A rota escreve sempre em g.tenant — não existe id de tenant no formulário."""
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    antes = loja["tenant_b"].pix_chave

    client.post("/admin/configuracoes/pix", data={"pix_chave": "invadida@exemplo.com"}, base_url=BASE_A)

    assert loja["tenant_b"].pix_chave == antes
    assert loja["tenant_a"].pix_chave == "invadida@exemplo.com"
