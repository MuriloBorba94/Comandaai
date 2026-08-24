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

CSS = Path("app/static/css/comanda.css")


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
# Gaveta lateral recolhível
# --------------------------------------------------------------------------- #


def test_o_botao_da_gaveta_fica_na_barra_de_comando(client, two_tenants):
    """Substitui o botão flutuante do canto, que só existia no celular e ficava
    por cima do conteúdo. Um controle só, em qualquer largura."""
    from tests.conftest import login_tenant

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/admin/", base_url="http://tenant-a.localhost").get_data(as_text=True)
    barra = corpo.split('class="v17-commandbar-title"', 1)[1].split("</div>", 1)[0]

    assert 'id="v17-nav-toggle"' in barra
    assert 'aria-controls="v17-sidebar"' in barra


def test_o_estado_da_gaveta_e_aplicado_antes_de_pintar(client, two_tenants):
    """No <head>, como o tema. Aplicado depois, a lateral apareceria e sumiria
    na frente de quem está olhando."""
    from tests.conftest import login_tenant

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/admin/", base_url="http://tenant-a.localhost").get_data(as_text=True)
    cabeca = corpo.split("</head>", 1)[0]

    assert "comandaai_nav" in cabeca
    assert "nav-recolhida" in cabeca
    # E o `catch` também recolhe: sem localStorage (navegação privada travada),
    # o padrão continua valendo em vez de a tela nascer num terceiro estado.
    assert cabeca.count("nav-recolhida") >= 2


def test_ha_caminho_de_volta_para_fixar_a_gaveta(client, two_tenants):
    """O botão da barra recolhe e chama a gaveta, mas nunca a prenderia de novo.
    Sem este, recolher seria mão única."""
    from tests.conftest import login_tenant

    login_tenant(client, "tenant-a", "admin", "senha-a-123")
    corpo = client.get("/admin/", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert 'id="v17-fixar-nav"' in corpo


def test_a_classe_da_gaveta_vive_na_raiz(css):
    """No <html>, não no <body>: é aplicada no <head>, quando o <body> ainda não
    existe. Mesma razão do data-theme."""
    assert ":root.nav-recolhida" in css
    assert "body.nav-recolhida" not in css
