"""O que faz um formulário do painel empilhar de verdade.

Estes testes olham o CSS como texto. É uma forma pobre de testar aparência, e
existe por um motivo específico: as regras aqui não são gosto, são a diferença
entre um formulário que empilha e um que se acomoda por acidente. Cada uma
custou uma medição no navegador para ser encontrada, e nenhuma é óbvia lendo o
arquivo — quem editar depois não tem como adivinhar.
"""

from __future__ import annotations

from pathlib import Path

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
