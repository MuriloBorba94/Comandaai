"""O que faz um formulário do painel empilhar de verdade.

Estes testes olham o CSS como texto. É uma forma pobre de testar aparência, e
existe por um motivo específico: as regras aqui não são gosto, são a diferença
entre um formulário que empilha e um que se acomoda por acidente. Cada uma
custou uma medição no navegador para ser encontrada, e nenhuma é óbvia lendo o
arquivo — quem editar depois não tem como adivinhar.
"""

from __future__ import annotations

from pathlib import Path

import re

import pytest

from tests.conftest import login_tenant

CSS = Path("app/static/css/comanda.css")
BASE = "http://tenant-a.localhost"


@pytest.fixture(scope="module")
def css() -> str:
    return CSS.read_text(encoding="utf-8")


def _regra(css: str, seletor: str) -> str:
    """O corpo da primeira regra que começa exatamente com este seletor."""
    marca = seletor + " {"
    assert marca in css, f"regra ausente: {seletor}"
    return css.split(marca, 1)[1].split("}", 1)[0]


def test_o_rotulo_e_bloco(css):
    """Sem isto, o rótulo é inline e o campo de largura total quebra linha por
    conta própria.

    O resultado medido era um empilhamento acidental: o topo do campo ficava
    ACIMA do topo do próprio rótulo (campo em 262px, rótulo em 277px), e a
    margem posta no campo não separava nada. Daí vinha a sensação de paredão —
    não havia grupo "rótulo + campo", havia texto e caixas se acomodando em
    linhas.
    """
    assert "display: block" in _regra(css, "\nlabel")


def test_todo_rotulo_em_linha_declara_o_proprio_display(css):
    """O que torna a regra acima segura.

    `label` também embrulha caixa de seleção e interruptor, que precisam ficar
    em linha. Eles sobrevivem porque declaram o próprio `display` — se alguém
    criar um contêiner novo e esquecer, o controle quebra para baixo do texto.
    """
    for seletor in (
        ".check-inline",
        ".checks label",
        ".recursos label",
        ".option-item",
        ".bd-tempo",
        ".switch",
        ".comanda-rotulo",
        ".field label",
    ):
        assert "display:" in _regra(css, seletor), seletor


def test_o_campo_tem_ritmo_assimetrico(css):
    """5px acima e 18px abaixo. Espaço igual dos dois lados faz o olho não saber
    qual rótulo pertence a qual campo."""
    regra = _regra(css, "input:where(:not([type=checkbox]):not([type=radio]):not([type=hidden])), select, textarea")

    assert "margin: 5px 0 18px" in regra


def test_o_campo_tem_teto_de_largura(css):
    """Um campo de "Preço" com 900px de largura é o que mais denuncia
    formulário não desenhado."""
    regra = _regra(css, "input:where(:not([type=checkbox]):not([type=radio]):not([type=hidden])), select, textarea")

    assert "max-width: var(--campo-max)" in regra
    assert "--campo-max:" in css


def test_a_busca_do_cardapio_escapa_do_teto(css):
    """Ela é uma barra sobre a grade toda, não um campo de digitação.

    Com o teto aplicado ela encolhia de 1160px para 560px — metade da tela.
    """
    assert "max-width: none" in _regra(css, ".menu-search")


def test_o_codigo_pix_escapa_do_teto(css):
    """Aqui a largura não é conforto de leitura: é um código que se quebra em
    muitas linhas se apertado."""
    assert "max-width: none" in _regra(css, ".pix-copia textarea")


def test_o_teto_do_rotulo_so_alcanca_filho_direto(css):
    """`form label` limitaria também os aninhados.

    O cartão de Entrega/Retirada é um item de grade: com o teto, ele parava
    antes da própria célula numa tela larga e descolava da grade.
    """
    assert "form > label" in css
    assert "\nform label," not in css


# --------------------------------------------------------------------------- #
# Painel de menu
# --------------------------------------------------------------------------- #


def test_o_botao_do_menu_fica_na_barra_de_comando(client, two_tenants):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/admin/", base_url=BASE).get_data(as_text=True)
    barra = corpo.split('class="v17-commandbar-title"', 1)[1].split("</div>", 1)[0]

    assert 'id="v17-nav-toggle"' in barra
    assert 'aria-controls="menu-painel"' in barra


def test_o_painel_do_restaurante_nao_tem_mais_lateral(client, two_tenants):
    """As duas juntas seriam o mesmo menu em dois lugares — e o que diverge, some
    de um deles sem ninguém perceber."""
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/admin/", base_url=BASE).get_data(as_text=True)

    assert 'class="v17-sidebar"' not in corpo
    assert 'id="menu-painel"' in corpo
    # E o shell não reserva a coluna de 246px.
    assert "com-lateral" not in corpo


def test_o_menu_tem_busca_favoritos_e_historico(client, two_tenants):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/admin/", base_url=BASE).get_data(as_text=True)
    painel = corpo.split('id="menu-painel"', 1)[1].split("</header>", 1)[0]

    assert 'id="menu-painel-busca"' in painel
    assert 'id="menu-favoritos"' in painel
    assert 'id="menu-historico"' in painel


def test_cada_item_do_menu_e_buscavel_por_nome_e_categoria(client, two_tenants):
    """A busca filtra por `data-busca`. Sem a categoria ali, procurar por
    "cardápio" não acharia "Produtos"."""
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/admin/", base_url=BASE).get_data(as_text=True)

    assert 'data-busca="produtos cardápio e campanhas"' in corpo


def test_o_item_da_tela_aberta_aparece_marcado(client, two_tenants):
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/admin/categorias", base_url=BASE).get_data(as_text=True)
    painel = corpo.split('id="menu-painel"', 1)[1].split("</header>", 1)[0]

    marcada = [l for l in painel.splitlines() if "menu-linha ativo" in l]
    assert len(marcada) == 1, "exatamente uma linha marcada"


def test_o_painel_tem_teto_de_largura_e_de_altura(css):
    """Ancorado no botão, e não em tela cheia: um painel que cobre tudo obriga a
    decidir "para onde vou" antes de poder olhar o que se estava fazendo."""
    regra = _regra(css, ".menu-painel")

    assert "width: min(" in regra
    assert "max-height: min(" in regra
    assert "overflow: auto" in regra


# --------------------------------------------------------------------------- #
# Cozinha
# --------------------------------------------------------------------------- #


def test_a_lista_de_itens_nao_e_bloco_pre_formatado(css):
    """`white-space: pre-wrap` num <ul> com <li> preserva a INDENTAÇÃO DO
    TEMPLATE — o espaço entre as tags vira conteúdo.

    Media 170px de caixa, com barra de rolagem, para exibir um item só; o card
    inteiro ia a 346px. Sem isso: 31px de caixa e 207px de card, 139px a menos
    por pedido.
    """
    regra = _regra(css, ".order-items")

    assert "white-space: pre-wrap" not in regra
    assert "list-style: none" in regra


def test_as_seis_fases_cabem_sem_largura_minima_fixa(css):
    """`minmax(240px, 1fr)` somava 1440px de colunas numa tela de 1366 e
    empurrava as duas últimas fases para fora.

    A camada de tema chegou a sobrescrever isto com faixas empilhadas; o bloco
    foi removido na integração porque o custo era alto — quadro de 1381px numa
    tela de 768, com a última fase começando em 1657px. Esta regra voltou a ser
    a que a tela renderiza.
    """
    regra = _regra(css, ".v17-kanban")

    assert "repeat(6, minmax(0, 1fr))" in regra


def test_os_botoes_da_cozinha_sao_chapados(css):
    """Um degradê por botão × quatro botões × seis colunas é ruído demais numa
    tela que se olha de longe."""
    regra = _regra(css, ".order-actions button")

    assert "background-image: none" in regra
    assert "line-height: 1.15" in regra


def test_as_seis_fases_sobrevivem_ate_1240px(css):
    """Monitor de 19" é 1280x1024 ou 1440x900 na maioria dos balcões.

    A quebra estava em 1350px, o que jogava justamente o 1280 para três colunas
    em duas fileiras — a cozinha perdia metade do quadro de vista.
    """
    assert "@media (max-width: 1240px)" in css
    assert "@media (max-width: 1350px)" not in css


def test_a_plataforma_usa_o_mesmo_painel_de_menu(client, platform_admin):
    """O botão existia na barra da plataforma e não fazia nada: o painel só era
    incluído no contexto do restaurante.

    O menu agora é dado em `navegacao.py` para as duas áreas, e o template do
    painel é um só — um arquivo por área seria a mesma tela mantida em dois
    lugares.
    """
    client.post(
        "/plataforma/login",
        data={"username": "admin", "password": "senha-super-admin-123"},
        base_url="http://app.localhost",
        follow_redirects=True,
    )

    corpo = client.get("/plataforma/planos", base_url="http://app.localhost").get_data(as_text=True)

    assert 'id="menu-painel"' in corpo
    assert 'class="v17-sidebar"' not in corpo

    # Escopado ao painel: a tela de planos tem um limite chamado "Produtos no
    # cardápio", e procurar no corpo inteiro acusaria isso como item de menu.
    painel = corpo.split('id="menu-painel"', 1)[1].split("</header>", 1)[0]
    assert 'data-busca="tenants clientes"' in painel
    assert 'data-busca="cobranças assinatura"' in painel
    assert "data-busca=\"produtos" not in painel


def test_o_contador_do_menu_e_resolvido_pelo_nome(app):
    """Um if/elif por contador obrigaria a mexer no template a cada um que
    surgisse — o da plataforma já seria o terceiro."""
    with app.test_request_context():
        resolver = app.jinja_env.globals["contador_do_menu"]

        # Nome desconhecido devolve 0 em vez de estourar: menu que derruba a
        # página por causa de um número ao lado de um item é troca ruim.
        assert resolver("nao_existe_este_contador") == 0
        assert isinstance(resolver("insumos_para_repor"), int)


def test_toda_gaveta_do_sistema_tem_fundo_opaco(css):
    """`color-scheme: dark` na raiz não bastou, e a razão é específica.

    Ele manda o navegador desenhar controles nativos com moldura escura — e
    resolveria isto sozinho se o `<select>` não tivesse fundo definido pelo
    autor. Tem: a regra base pinta todo campo com rgba(255,255,255,.04). A
    partir do momento em que o autor toca no fundo de um select, o Chrome para
    de usar a moldura nativa e desenha a lista com o padrão dele, que é BRANCO —
    e o texto, que herda --text, é quase branco no escuro.

    Valia para o carrinho do CLIENTE também: escolher bairro e forma de
    pagamento no escuro abria uma lista ilegível.
    """
    assert "--gaveta-fundo:" in css

    regra = _regra(css, "select")
    assert "background-color: var(--gaveta-fundo)" in regra

    lista = _regra(css, "select option, select optgroup")
    assert "background-color: var(--gaveta-fundo)" in lista
    assert "color: var(--text)" in lista


def test_a_pagina_do_produto_declara_o_proprio_esquema():
    """Ela é escura e nunca disse isso ao navegador: barra de rolagem e
    controles nativos saíam com moldura clara sobre fundo quase preto."""
    from pathlib import Path

    landing = Path("app/static/css/landing.css").read_text(encoding="utf-8")

    assert "color-scheme: dark" in landing



# --------------------------------------------------------------------------- #
# Camada de tema (nocturne)
# --------------------------------------------------------------------------- #

NOCTURNE = Path("app/static/css/nocturne.css")


@pytest.fixture(scope="module")
def nocturne() -> str:
    return NOCTURNE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css_nocturne() -> str:
    return NOCTURNE.read_text(encoding="utf-8")


def test_a_camada_de_tema_entra_entre_a_base_e_a_marca_do_tenant(client, two_tenants):
    """A ordem é a regra inteira.

    O nocturne reescreve tokens do comanda, então precisa vir depois dele; e a
    cor da marca do restaurante é injetada num <style> que precisa vir depois
    dos dois, senão o tema apaga a identidade do cliente.
    """
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/admin/", base_url=BASE).get_data(as_text=True)

    i_base = corpo.index("css/comanda.css")
    i_tema = corpo.index("css/nocturne.css")
    i_marca = corpo.index("--brand:")

    assert i_base < i_tema < i_marca


def test_o_acento_do_sistema_nao_alcanca_a_vitrine(nocturne):
    """A falha que o pacote trazia: sem escopo, o acento pintava os botões do
    CLIENTE.

    Medido num tenant de marca dourada, "Adicionar" e "Continuar pedido" saíam
    roxos. A própria página de vendas promete "o cliente vê o seu restaurante,
    não o nosso" — o painel é nosso, a vitrine é dele.
    """
    linhas = [
        linha for linha in nocturne.splitlines()
        if "var(--acento" in linha and linha.strip().endswith("{")
    ]
    sem_escopo = [
        linha.strip() for linha in linhas
        if "body.painel" not in linha and not linha.strip().startswith(("/*", "*", ":root"))
    ]

    assert not sem_escopo, f"regra de acento sem escopo de painel: {sem_escopo}"


def test_a_camada_nao_busca_fonte_em_terceiro(nocturne):
    """O cardápio é página de cliente final: cada visitante passaria o IP para
    um terceiro só para escolher um lanche. E `@import` no topo de uma folha
    bloqueia a renderização e ainda serializa o pedido."""
    import re

    # A regra de verdade, não a palavra: o comentário que explica a remoção cita
    # `@import`, e procurar o texto solto acusaria a própria explicação.
    regra_import = re.compile("^" + chr(92) + "s*@import" + chr(92) + "b", re.M)

    assert not regra_import.search(nocturne)
    assert "fonts.googleapis.com" not in nocturne


def test_a_camada_redefine_o_fundo_da_gaveta(nocturne):
    """Ela troca --panel-2, e a lista do <select> se apoia nesse token. Sem
    redefinir, a lista abriria no tom do tema anterior."""
    assert nocturne.count("--gaveta-fundo:") == 2, "um por tema"


def test_a_borda_do_cartao_e_visivel_nos_dois_temas(nocturne):
    """O designer tirou gradiente e sombra de propósito — a aresta virou a
    ÚNICA separação, e o cartão difere do fundo em só 1,16:1 no escuro.

    Os valores entregues davam 1,07:1 (escuro) e 1,37:1 (claro), abaixo do
    limiar em que o olho registra uma linha.
    """
    assert "--border: #3b3e4a" in nocturne, "escuro: 1,43:1 contra o cartão"
    assert "--border: #cfd4e0" in nocturne, "claro: 1,48:1 contra o cartão"


def test_a_camada_de_tema_nao_mexe_no_quadro_da_cozinha(nocturne):
    """As seis fases numa tela de 19" são requisito, não preferência.

    A camada trazia cartões grandes em faixas empilhadas: bonito de perto, e
    medido em 1366x768 com TRÊS pedidos dava um quadro de 1381px de altura, com
    a fase "Saiu para entrega" começando em 1657px. Para saber se havia algo
    pronto, a cozinha rolava a página.

    O tema continua chegando lá pelos tokens — fundo, borda, cor e tipografia
    são globais. O que não pode voltar é o override de LAYOUT.
    """
    for seletor in (".v17-kanban {", ".v17-kanban-column", ".order-card {", ".order-actions button"):
        assert seletor not in nocturne, f"o tema voltou a mexer no layout da cozinha: {seletor}"


# --------------------------------------------------------------------------- #
# Etapa 2 do redesenho: telas com painel lateral
# --------------------------------------------------------------------------- #


def test_o_cadastro_de_produto_sai_de_cima_da_lista(client, two_tenants):
    """Antes o formulário empilhava ACIMA da tabela e os dois disputavam a mesma
    coluna: para conferir um preço enquanto cadastrava, a pessoa rolava a página
    inteira e perdia o formulário de vista."""
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/admin/produtos", base_url=BASE).get_data(as_text=True)

    assert "tela-com-painel" in corpo
    assert "painel-lateral" in corpo
    # A lista vem antes do painel na marcação: quem lê com leitor de tela chega
    # ao conteúdo antes do formulário de cadastro.
    assert corpo.index("tabela-produtos") < corpo.index("painel-lateral")


def test_o_cupom_aparece_uma_vez_so(client, two_tenants):
    """A tela renderizava cada cupom DUAS vezes — uma na tabela e outra numa
    lista de formulários embaixo. A edição virou dobra da própria linha."""
    from app.extensions import db
    from app.models.cupom import Cupom

    db.session.add(Cupom(tenant_id=two_tenants["tenant_a"], codigo="TESTE10",
                         tipo="percentual", valor=10.0, limite_usos=5, ativo=True))
    db.session.commit()

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/admin/cupons", base_url=BASE).get_data(as_text=True)
    tabela = corpo.split('class="tabela-cupons"', 1)[1].split("</table>", 1)[0]

    assert tabela.count(">TESTE10<") == 1
    assert "edicao-cupom" in tabela


def test_a_coluna_que_decide_e_a_ultima(client, two_tenants):
    """"Disponíveis" é o que diz se o cupom ainda vale alguma coisa, e o olho
    termina a linha nela."""
    from app.extensions import db
    from app.models.cupom import Cupom

    db.session.add(Cupom(tenant_id=two_tenants["tenant_a"], codigo="ORDEM",
                         tipo="percentual", valor=5.0, limite_usos=3, ativo=True))
    db.session.commit()

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/admin/cupons", base_url=BASE).get_data(as_text=True)
    cabecalho = corpo.split("<thead>", 1)[1].split("</thead>", 1)[0]
    colunas = re.findall(r"<th[^>]*>([^<]+)</th>", cabecalho)

    assert colunas[-1].strip() == "Disponíveis"


def test_o_menu_das_configuracoes_nunca_aponta_para_secao_inexistente(client, two_tenants):
    """O item do menu e o `libera()` do cartão leem a MESMA lista.

    Escritos separados, divergiriam: um plano sem PIX mostraria o item no menu
    e levaria a uma âncora que não existe naquela página.
    """
    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/admin/configuracoes", base_url=BASE).get_data(as_text=True)

    destinos = set(re.findall(r'<a href="#cfg-(\w+)"', corpo))
    secoes = set(re.findall(r'class="card-admin cfg-secao" id="cfg-(\w+)"', corpo))

    assert destinos, "a navegação lateral não foi montada"
    assert destinos == secoes, f"menu e seções divergem: {destinos ^ secoes}"


def test_a_linha_fora_do_ar_recua_por_cor_e_nao_por_opacidade(css_nocturne):
    """`opacity: .55` derrubava o texto para 2,31:1 no tema claro.

    Opacidade apaga tudo por igual, inclusive o que precisa continuar legível.
    `--muted` é calibrado e passa; a diferença para o texto normal é justamente
    a de-ênfase que se queria.
    """
    regra = css_nocturne.split(".linha-oculta > td {", 1)[1].split("}", 1)[0]

    assert "color: var(--muted)" in regra
    assert "opacity" not in regra
