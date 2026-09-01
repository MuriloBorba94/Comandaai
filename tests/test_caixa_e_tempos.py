"""Abrir e fechar a loja, o dinheiro da gaveta e o tempo prometido ao cliente.

O teste que mais importa aqui é o do restaurante que nunca abriu caixa nenhum:
o Borba's vende hoje sem saber que este código existe, e subir uma regra que
exija turno aberto fecharia a porta dele no primeiro deploy.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models.auditoria import ACAO_CAIXA_ABERTO, ACAO_CAIXA_FECHADO, Auditoria
from app.models.caixa import Caixa
from app.models.pedido import STATUS_CANCELADO, TIPO_RETIRADA
from app.models.produto import Produto
from app.models.tenant import Tenant
from app.services import caixa as caixa_service
from app.services.pedidos import calcular_estimativa, criar_pedido, transicionar
from tests.conftest import login_tenant

BASE_A = "http://tenant-a.localhost"


@pytest.fixture()
def loja(app, two_tenants):
    tenant = db.session.get(Tenant, two_tenants["tenant_a"])
    db.session.add(Produto(tenant_id=tenant.id, nome="X-Tudo", preco=30.0))
    db.session.commit()
    return tenant


def _pedido(tenant, pagamento="Dinheiro"):
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
# A loja aberta
# --------------------------------------------------------------------------- #


def test_restaurante_que_nunca_abriu_caixa_continua_vendendo(loja):
    """A regra nova não pode fechar a porta de quem já vendia."""
    assert Caixa.query.filter_by(tenant_id=loja.id).count() == 0
    assert loja.loja_aberta is True
    assert caixa_service.loja_esta_aberta(loja) is True


def test_abrir_a_loja_registra_o_troco_da_gaveta(loja):
    caixa = caixa_service.abrir(loja, "150,50", actor="murilo")

    assert caixa.valor_inicial == 150.50
    assert caixa.aberto_por == "murilo"
    assert caixa.aberto is True
    assert loja.loja_aberta is True


def test_abrir_duas_vezes_nao_cria_dois_turnos(loja):
    """Dois cliques no botão contariam as vendas do dia duas vezes."""
    primeiro = caixa_service.abrir(loja, 100)
    segundo = caixa_service.abrir(loja, 999)

    assert primeiro.id == segundo.id
    assert segundo.valor_inicial == 100
    assert Caixa.query.filter_by(tenant_id=loja.id).count() == 1


def test_fechar_a_loja_tira_o_cardapio_do_ar(loja):
    caixa = caixa_service.abrir(loja, 100)
    caixa_service.fechar(caixa)

    assert loja.loja_aberta is False
    assert caixa_service.loja_esta_aberta(loja) is False
    assert caixa_service.caixa_aberto(loja.id) is None


def test_valor_inicial_negativo_e_recusado(loja):
    with pytest.raises(ValueError, match="não pode ser negativo"):
        caixa_service.abrir(loja, -10)


def test_valor_inicial_com_letra_e_recusado(loja):
    with pytest.raises(ValueError, match="inválido"):
        caixa_service.abrir(loja, "cem reais")


# --------------------------------------------------------------------------- #
# A conferência da gaveta
# --------------------------------------------------------------------------- #


def test_so_o_dinheiro_passa_pela_gaveta(loja):
    """Cartão e PIX entram no faturamento, mas não na gaveta."""
    caixa_service.abrir(loja, 100)
    _pedido(loja, pagamento="Dinheiro")
    _pedido(loja, pagamento="Cartão na entrega")

    conferencia = caixa_service.resumo(caixa_service.caixa_aberto(loja.id))

    assert conferencia["faturamento"] == 60.0
    assert conferencia["em_especie"] == 30.0
    assert conferencia["esperado_na_gaveta"] == 130.0


def test_pedido_cancelado_nao_conta_na_conferencia(loja):
    caixa_service.abrir(loja, 100)
    pedido = _pedido(loja)
    transicionar(pedido, STATUS_CANCELADO)

    conferencia = caixa_service.resumo(caixa_service.caixa_aberto(loja.id))

    assert conferencia["pedidos"] == 0
    assert conferencia["esperado_na_gaveta"] == 100.0


def test_pedido_de_antes_da_abertura_nao_entra_no_turno(loja):
    """O turno começa quando a gaveta é montada, não quando o banco foi criado."""
    antigo = _pedido(loja)
    antigo.created_at = datetime.now() - timedelta(days=2)
    db.session.commit()

    caixa_service.abrir(loja, 100)
    _pedido(loja)

    conferencia = caixa_service.resumo(caixa_service.caixa_aberto(loja.id))
    assert conferencia["pedidos"] == 1


def test_fechamento_calcula_a_diferenca(loja):
    caixa = caixa_service.abrir(loja, 100)
    _pedido(loja, pagamento="Dinheiro")

    conferencia = caixa_service.fechar(caixa, "125,00")

    # Esperado: 100 de troco + 30 de venda = 130. Contados 125 → faltam 5.
    assert conferencia["esperado_na_gaveta"] == 130.0
    assert conferencia["diferenca"] == -5.0


def test_fechar_sem_contar_nao_inventa_diferenca(loja):
    caixa = caixa_service.abrir(loja, 100)

    conferencia = caixa_service.fechar(caixa, "")

    assert conferencia["diferenca"] is None
    assert caixa.valor_contado is None


def test_fechar_caixa_ja_fechado_e_recusado(loja):
    caixa = caixa_service.abrir(loja, 100)
    caixa_service.fechar(caixa)

    with pytest.raises(ValueError, match="Não há caixa aberto"):
        caixa_service.fechar(caixa)


def test_abertura_e_fechamento_ficam_no_diario(loja):
    caixa = caixa_service.abrir(loja, 100, actor="murilo")
    caixa_service.fechar(caixa, "130", actor="murilo")

    assert Auditoria.query.filter_by(acao=ACAO_CAIXA_ABERTO).count() == 1
    fechamento = Auditoria.query.filter_by(acao=ACAO_CAIXA_FECHADO).one()
    assert "R$" in fechamento.detalhes


def test_caixa_de_um_restaurante_nao_ve_o_pedido_do_outro(loja, two_tenants):
    outro = db.session.get(Tenant, two_tenants["tenant_b"])
    db.session.add(Produto(tenant_id=outro.id, nome="Pizza", preco=40.0))
    db.session.commit()

    caixa_service.abrir(loja, 0)
    _pedido(outro)

    conferencia = caixa_service.resumo(caixa_service.caixa_aberto(loja.id))
    assert conferencia["pedidos"] == 0


# --------------------------------------------------------------------------- #
# Os dois tempos
# --------------------------------------------------------------------------- #


def test_retirada_usa_o_tempo_proprio_quando_definido(loja):
    loja.tempo_estimado_min, loja.tempo_estimado_max = 40, 60
    loja.tempo_retirada_min, loja.tempo_retirada_max = 15, 25
    db.session.commit()

    assert calcular_estimativa(loja, TIPO_RETIRADA) == (15, 25)


def test_sem_tempo_proprio_a_retirada_continua_derivada(loja):
    """Quem nunca preencheu o campo novo não pode ver o prazo mudar sozinho."""
    loja.tempo_estimado_min, loja.tempo_estimado_max = 40, 60
    loja.tempo_retirada_min = loja.tempo_retirada_max = None
    db.session.commit()

    assert calcular_estimativa(loja, TIPO_RETIRADA) == (30, 50)


def test_as_duas_gavetas_salvam_tempos_diferentes(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/loja/tempos",
        data={"entrega": "50-70", "retirada": "20-30"},
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert (loja.tempo_estimado_min, loja.tempo_estimado_max) == (50, 70)
    assert (loja.tempo_retirada_min, loja.tempo_retirada_max) == (20, 30)


def test_faixa_invalida_nao_salva_nada(client, loja):
    loja.tempo_estimado_min, loja.tempo_estimado_max = 40, 60
    db.session.commit()
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/loja/tempos",
        data={"entrega": "sei-la", "retirada": "20-30"},
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert (loja.tempo_estimado_min, loja.tempo_estimado_max) == (40, 60)


def test_a_faixa_salva_fora_da_lista_continua_aparecendo_na_gaveta(loja):
    """Um restaurante com 35–55 salvo não pode ver a gaveta apontar outra coisa."""
    opcoes = caixa_service.faixas_com_a_atual(35, 55)

    assert (35, 55) in opcoes


# --------------------------------------------------------------------------- #
# Pelas telas
# --------------------------------------------------------------------------- #


def test_os_controles_do_turno_ficam_no_inicio_da_barra(client, loja):
    """São ajustes do ambiente, como o tema — não conteúdo da página."""
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    corpo = client.get("/admin/", base_url=BASE_A).get_data(as_text=True)
    barra = corpo.split('class="v17-commandbar-actions"', 1)[1].split("</header>", 1)[0]

    assert "bd-estado" in barra
    # As gavetas pelo `name`, não pelo rótulo visível: o rótulo virou ícone,
    # e um teste preso ao texto quebraria a cada ajuste de layout sem nada
    # ter deixado de funcionar.
    assert 'name="entrega"' in barra
    assert 'name="retirada"' in barra
    # Toda gaveta precisa de nome acessível: sem rótulo visível, é ele que
    # sobra para quem usa leitor de tela.
    assert 'aria-label="Tempo estimado para entrega"' in barra
    assert 'aria-label="Tempo estimado para retirada"' in barra
    # E na frente da identificação de quem está logado, que é o vizinho pedido.
    # Era o botão de tema; ele saiu quando o tema do sistema passou a ser um só.
    assert barra.index("bd-estado") < barra.index("v17-user-chip")


def test_a_barra_de_comando_cabe_em_uma_linha(client, loja):
    """O atalho "Cardápio" saiu da barra.

    Eram três caminhos para a mesma vitrine — o atalho, o painel de menu e o
    "Ver como cliente" dos atalhos do dia — e a barra não tinha largura para os
    três: com a marca do restaurante e o botão do menu, ela quebrava em duas
    linhas e ia de 60px para 102px de altura.
    """
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    corpo = client.get("/admin/", base_url=BASE_A).get_data(as_text=True)
    barra = corpo.split('class="v17-commandbar-actions"', 1)[1].split("</header>", 1)[0]

    assert ">Cardápio<" not in barra
    # E o que precisa estar continua: turno, usuário e saída.
    assert "bd-estado" in barra
    assert "v17-user-chip" in barra
    # O botão de alternar tema também saiu, e por não fazer nada: o tema do
    # sistema é um só, e o interruptor trocava um atributo que nenhuma folha
    # de estilo ainda lia.
    assert 'id="theme-toggle"' not in barra


def test_abrir_a_loja_pela_tela(client, loja):
    loja.loja_aberta = False
    db.session.commit()
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/loja/abrir",
        data={"valor_inicial": "200"},
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert loja.loja_aberta is True
    assert caixa_service.caixa_aberto(loja.id).valor_inicial == 200.0


def test_fechar_a_loja_sem_caixa_aberto_ainda_funciona(client, loja):
    """O interruptor existe desde antes do caixa; fechar tem de valer mesmo assim."""
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post("/admin/loja/fechar", base_url=BASE_A, follow_redirects=True)

    assert loja.loja_aberta is False


def test_atendente_nao_abre_nem_fecha_a_loja(client, loja):
    from app.models.usuario import ROLE_ATENDENTE, Usuario

    ana = Usuario(tenant_id=loja.id, nome="Ana", username="ana", role=ROLE_ATENDENTE)
    ana.set_password("senha-da-ana")
    loja.loja_aberta = False
    db.session.add(ana)
    db.session.commit()

    login_tenant(client, "tenant-a", "ana", "senha-da-ana")
    client.post(
        "/admin/loja/abrir", data={"valor_inicial": "500"}, base_url=BASE_A, follow_redirects=True
    )

    assert loja.loja_aberta is False


def test_loja_fechada_recusa_pedido_pelo_cardapio(client, loja):
    """Sem isto a tarja "Fechado" seria enfeite, e o pedido cairia na cozinha."""
    from app.models.pedido import Pedido

    produto = Produto.query.filter_by(tenant_id=loja.id).first()
    loja.loja_aberta = False
    db.session.commit()

    client.post(
        "/carrinho/adicionar",
        data={"produto_id": produto.id, "quantidade": 1},
        base_url=BASE_A,
        follow_redirects=True,
    )
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

    assert Pedido.query.filter_by(tenant_id=loja.id).count() == 0


def test_loja_fechada_nao_impede_o_atendente(loja):
    """Fechar a porta é parar de receber pela internet, não parar de atender."""
    loja.loja_aberta = False
    db.session.commit()

    pedido = _pedido(loja)

    assert pedido.id is not None


def test_cardapio_fechado_mostra_a_tarja_certa(client, loja):
    loja.loja_aberta = False
    db.session.commit()

    corpo = client.get("/", base_url=BASE_A).get_data(as_text=True)

    assert "status-closed" in corpo
    assert "Fechado no momento" in corpo


def test_os_formularios_da_barra_levam_csrf(client, loja):
    """O cliente de teste roda com CSRF desligado, então só a marcação denuncia.

    Sem esta verificação o formulário sobe sem o campo, passa em todos os
    testes, e quebra com 400 na primeira vez que alguém clica de verdade — foi
    exatamente o que aconteceu ao abrir a tela no navegador.
    """
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    # Os dois estados, porque "abrir" e "fechar" nunca aparecem juntos: testar
    # só um deixaria o outro subir sem o campo.
    vistas = set()
    for aberta in (True, False):
        loja.loja_aberta = aberta
        db.session.commit()

        corpo = client.get("/admin/", base_url=BASE_A).get_data(as_text=True)
        formularios = re.findall(
            r'<form method="post" action="/admin/loja/(\w+)"[^>]*>(.*?)</form>', corpo, re.S
        )

        assert len(formularios) == 2, "a barra tem a ação do estado e os tempos"
        for acao, corpo_do_form in formularios:
            vistas.add(acao)
            assert 'name="csrf_token"' in corpo_do_form, acao

    assert vistas == {"abrir", "fechar", "tempos"}


def test_configuracoes_salva_a_janela_de_retirada(client, loja):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/configuracoes",
        data={
            "qtd_mesas": 0,
            "tempo_estimado_min": 40,
            "tempo_estimado_max": 60,
            "tempo_retirada_min": 15,
            "tempo_retirada_max": 25,
        },
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert (loja.tempo_retirada_min, loja.tempo_retirada_max) == (15, 25)


def test_retirada_em_branco_volta_ao_calculo_derivado(client, loja):
    """Vazio é "calcule pela entrega", e não zero minuto."""
    loja.tempo_retirada_min, loja.tempo_retirada_max = 15, 25
    db.session.commit()
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/configuracoes",
        data={
            "qtd_mesas": 0,
            "tempo_estimado_min": 40,
            "tempo_estimado_max": 60,
            "tempo_retirada_min": "",
            "tempo_retirada_max": "",
        },
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert loja.tempo_retirada_min is None
    assert calcular_estimativa(loja, TIPO_RETIRADA) == (30, 50)


def test_meia_janela_de_retirada_nao_e_janela(client, loja):
    """Só o mínimo preenchido prometeria um teto que ninguém definiu."""
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    client.post(
        "/admin/configuracoes",
        data={
            "qtd_mesas": 0,
            "tempo_estimado_min": 40,
            "tempo_estimado_max": 60,
            "tempo_retirada_min": 15,
            "tempo_retirada_max": "",
        },
        base_url=BASE_A,
        follow_redirects=True,
    )

    assert loja.tempo_retirada_min is None
    assert loja.tempo_retirada_max is None


# --------------------------------------------------------------------------- #
# A loja fechada precisa PARECER fechada, e não só recusar no fim
# --------------------------------------------------------------------------- #


def _fechar(loja):
    loja.loja_aberta = False
    db.session.commit()


def test_cardapio_fechado_esconde_o_botao_de_adicionar(client, loja):
    """Botão que existe para não funcionar é convite à frustração."""
    _fechar(loja)

    corpo = client.get("/", base_url=BASE_A).get_data(as_text=True)

    # O botão em si, não a menção no script — o JS continua referenciando a
    # classe (com `?.`), e casar pelo nome solto daria um teste sempre verde.
    assert 'class="product-add"' not in corpo
    assert "Estamos fechados no momento" in corpo


def test_cardapio_fechado_nao_oferece_adicionar_no_modal(client, loja):
    _fechar(loja)

    corpo = client.get("/", base_url=BASE_A).get_data(as_text=True)

    assert 'id="add-to-cart-btn"' not in corpo


def test_carrinho_fechado_nao_mostra_o_formulario_de_finalizar(client, loja):
    """A página é alcançável por link direto; esconder só no cardápio não basta."""
    produto = Produto.query.filter_by(tenant_id=loja.id).first()
    client.post(
        "/carrinho/adicionar",
        data={"produto_id": produto.id, "quantidade": 1},
        base_url=BASE_A,
        follow_redirects=True,
    )
    _fechar(loja)

    corpo = client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)

    assert 'id="form-pedido"' not in corpo
    assert "Estamos fechados no momento" in corpo
    # E os itens continuam lá: fechar a loja não é esvaziar a sacola de ninguém.
    assert produto.nome in corpo


def test_carrinho_aberto_continua_com_o_formulario(client, loja):
    produto = Produto.query.filter_by(tenant_id=loja.id).first()
    client.post(
        "/carrinho/adicionar",
        data={"produto_id": produto.id, "quantidade": 1},
        base_url=BASE_A,
        follow_redirects=True,
    )

    corpo = client.get("/carrinho", base_url=BASE_A).get_data(as_text=True)

    assert 'id="form-pedido"' in corpo


def test_botao_do_whatsapp_fica_no_cabecalho_com_o_visual_do_painel(client, loja):
    loja.telefone_contato = "81996353503"
    db.session.commit()

    corpo = client.get("/", base_url=BASE_A).get_data(as_text=True)
    cabecalho = corpo.split('class="acoes-topbar"', 1)[1].split("</div>", 1)[0]

    assert "https://wa.me/5581996353503" in cabecalho
    # Mesmas classes do botão Painel: é isso que "igual ao botão painel" quer
    # dizer, e um visual próprio voltaria a divergir na primeira mudança de tema.
    assert cabecalho.count("btn btn-muted btn-ghost-mobile") == 2
    # O número em si não aparece escrito na tela.
    assert "81996353503" not in cabecalho.replace("wa.me/5581996353503", "")


def test_sem_telefone_cadastrado_o_cabecalho_nao_inventa_botao(client, loja):
    loja.telefone_contato = None
    db.session.commit()

    corpo = client.get("/", base_url=BASE_A).get_data(as_text=True)

    assert "wa.me" not in corpo


def test_a_conferencia_mora_dentro_da_janelinha_de_fechar(client, loja):
    """O número só interessa na hora de fechar; o resto do dia ele ocupa a tela."""
    caixa_service.abrir(loja, 150)
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    corpo = client.get("/admin/", base_url=BASE_A).get_data(as_text=True)
    janelinha = corpo.split('action="/admin/loja/fechar"', 1)[1].split("</form>", 1)[0]

    assert "Esperado na gaveta" in janelinha
    # E em português: era "R$ 150.00", com o ponto do inglês.
    assert "R$ 150,00" in janelinha


def test_o_dinheiro_sai_no_formato_brasileiro(loja):
    from app.utils import reais

    assert reais(1234.5) == "1.234,50"
    assert reais(150) == "150,00"
    assert reais(None) == "0,00"


def test_a_janelinha_nao_nasce_aberta(client, loja):
    """`<details open>` mostraria o formulário sem ninguém ter clicado."""
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    corpo = client.get("/admin/", base_url=BASE_A).get_data(as_text=True)

    assert '<details class="bd-acao" open' not in corpo
    assert '<details class="bd-acao">' in corpo


def test_a_lista_da_gaveta_tem_fundo_opaco(client, loja):
    """Foi o defeito reportado, e depois se mostrou geral.

    A `option` herdava fundo transparente do select, e quem pintava a lista era
    o sistema — de branco — com o texto quase branco por cima. A correção virou
    regra do sistema inteiro (`--gaveta-fundo`), e esta pílula continua com a
    sua porque o select dela é transparente de propósito: sem declarar, a lista
    herdaria a transparência de volta.
    """
    from pathlib import Path

    css = Path("app/static/css/comanda.css").read_text(encoding="utf-8")
    regra = css.split(".bd-tempo select option", 1)[1].split("}", 1)[0]

    assert "background-color: var(--gaveta-fundo)" in regra
    assert "color: var(--text)" in regra


def test_o_tema_declara_color_scheme():
    """A metade da correção que vale para TODO select do painel.

    Sem `color-scheme`, o navegador desenha a lista nativa, a barra de rolagem
    e o seletor de data com a cara clara enquanto a página é escura.
    """
    from pathlib import Path

    css = Path("app/static/css/comanda.css").read_text(encoding="utf-8")

    assert "color-scheme: dark" in css
    assert "color-scheme: light" in css


def test_a_gaveta_nao_usa_aparencia_nativa(client, loja):
    """Com aparência nativa, alternar o tema não repintava o fundo do controle.

    Media 1,07:1 de contraste — texto escuro sobre fundo escuro — e só voltava
    ao normal recarregando a página.
    """
    from pathlib import Path

    css = Path("app/static/css/comanda.css").read_text(encoding="utf-8")
    regra = css.split(".bd-tempo select {", 1)[1].split("}", 1)[0]

    assert "appearance: none" in regra


def test_os_controles_ficam_em_todas_as_paginas_do_painel(client, loja):
    """Perceber às 22h que o cardápio continua aberto acontece no meio de outra
    tarefa. Obrigar a voltar ao painel inicial transforma um toque em três."""
    login_tenant(client, "tenant-a", "admin", "senha-a-123")

    for url in ("/admin/", "/admin/produtos", "/admin/categorias", "/admin/configuracoes"):
        corpo = client.get(url, base_url=BASE_A).get_data(as_text=True)
        barra = corpo.split('class="v17-commandbar-actions"', 1)[1].split("</header>", 1)[0]
        assert "bd-estado" in barra, url
        assert 'name="entrega"' in barra, url
        assert "/admin/loja/" in barra, url


def test_o_cliente_do_cardapio_nao_ve_os_controles(client, loja):
    """A vitrine não tem barra de comando, e o botão de fechar a loja não pode
    vazar para quem está pedindo um lanche."""
    corpo = client.get("/", base_url=BASE_A).get_data(as_text=True)

    assert "bd-estado" not in corpo
    assert "/admin/loja/" not in corpo


def test_a_conferencia_e_consultada_uma_vez_por_pagina(client, loja, monkeypatch):
    """A memoização em `g` existe para isto: a conferência é uma consulta
    agrupada sobre os pedidos, e um descuido de template cobraria o preço de
    novo a cada chamada."""
    from app.services import caixa as servico

    caixa_service.abrir(loja, 100)
    chamadas = []
    original = servico.resumo
    monkeypatch.setattr(servico, "resumo", lambda c: chamadas.append(1) or original(c))

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    client.get("/admin/produtos", base_url=BASE_A)

    assert len(chamadas) == 1
