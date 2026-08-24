"""Layout importado do sistema original: shell por contexto e marca por tenant.

O que realmente pode dar errado aqui:

1. A cor escolhida pelo restaurante é interpolada dentro de um bloco `<style>`.
   Se qualquer texto passasse, seria injeção de CSS — e com CSS dá para redesenhar
   a tela inteira, inclusive imitar um formulário de senha.
2. A identidade é por tenant. A cor ou a logo de um restaurante aparecendo no
   painel de outro seria vazamento visual de cliente para cliente.
3. O shell é escolhido pelo contexto, não declarado pela tela. Um erro aqui
   mostraria a sidebar do painel para o cliente que só quer pedir um lanche.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app import create_app
from app.extensions import db
from app.layout import (
    CONTRASTE_MINIMO,
    COR_PADRAO,
    FUNDO_CLARO,
    FUNDO_ESCURO,
    _contraste,
    contraste_da_marca,
    cor_valida,
    marca_para_texto,
)
from app.models.tenant import Tenant
from tests.conftest import TestConfig, login_tenant


@pytest.fixture()
def app(tmp_path):
    """Uploads em pasta própria: este teste grava logo de verdade."""

    class LayoutConfig(TestConfig):
        UPLOAD_FOLDER = str(tmp_path / "uploads")

    application = create_app(LayoutConfig)
    with application.app_context():
        db.create_all()
        yield application
        db.drop_all()


def _png(largura: int = 200, altura: int = 200) -> io.BytesIO:
    buffer = io.BytesIO()
    Image.new("RGB", (largura, altura), (10, 120, 200)).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _logar_a(client):
    return login_tenant(client, "tenant-a", "admin", "senha-a-123")


def _logar_b(client):
    return login_tenant(client, "tenant-b", "admin", "senha-b-123")


def _tenant(tenant_id: int) -> Tenant:
    return db.session.get(Tenant, tenant_id)


# --------------------------------------------------------------------------- #
# Validação da cor: a única porta para dentro do CSS
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("valor", ["#c8102e", "#C8102E", "#abc", " #123456 "])
def test_cor_valida_aceita_hex(valor):
    assert cor_valida(valor) == valor.strip().lower()


@pytest.mark.parametrize(
    "valor",
    [
        None,
        "",
        "vermelho",
        "c8102e",                      # sem #
        "#12345",                      # tamanho inválido
        "#12g456",                     # dígito não-hex
        "red; } body { display:none",  # fecharia o seletor
        "#fff; background:url(http://evil/x)",
        "javascript:alert(1)",
    ],
)
def test_cor_valida_recusa_o_resto(valor):
    assert cor_valida(valor) is None


def test_cor_invalida_no_banco_nao_chega_ao_html(app, two_tenants):
    """Contraprova de ponta a ponta: mesmo gravada direto no banco, não passa."""
    _tenant(two_tenants["tenant_a"]).cor_marca = "red;} body{display:none"
    db.session.commit()

    client = app.test_client()
    _logar_a(client)
    html = client.get("/admin/", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert "display:none" not in html
    assert f"--brand: {COR_PADRAO}" in html, "deveria cair no padrão"


# --------------------------------------------------------------------------- #
# Legibilidade da cor escolhida
#
# O restaurante escolhe a cor; não escolhe se ela dá para ler. Uma marca amarela
# com texto branco em cima deixa o botão ilegível, e foi exatamente o que
# aconteceu quando a Borba's Pizzaria escolheu #c6ae10.
# --------------------------------------------------------------------------- #

# Uma volta inteira do seletor de cor, incluindo os extremos.
CORES = [
    "#c8102e", "#c6ae10", "#ffffff", "#000000", "#1e88e5",
    "#f1c40f", "#0b1d3a", "#2ecc71", "#ff00ff", "#00ff00", "#7f7f7f",
]


@pytest.mark.parametrize("cor", CORES)
def test_texto_sobre_a_marca_sempre_legivel(cor):
    """O que fica POR CIMA de um preenchimento com a cor da marca."""
    assert _contraste(contraste_da_marca(cor), cor) >= CONTRASTE_MINIMO


@pytest.mark.parametrize("cor", CORES)
def test_marca_como_texto_legivel_nos_dois_temas(cor):
    """A marca usada COMO texto, sobre o painel claro e sobre o escuro."""
    assert _contraste(marca_para_texto(cor, escuro=False), FUNDO_CLARO) >= CONTRASTE_MINIMO
    assert _contraste(marca_para_texto(cor, escuro=True), FUNDO_ESCURO) >= CONTRASTE_MINIMO


@pytest.mark.parametrize("cor", ["#c8102e", "#0b1d3a"])
def test_cor_escura_que_ja_contrasta_fica_intacta(cor):
    """Ajustar quem não precisa descaracterizaria a marca à toa."""
    assert marca_para_texto(cor, escuro=False) == cor


@pytest.mark.parametrize("cor", ["#c6ae10", "#f1c40f", "#2ecc71"])
def test_cor_clara_que_ja_contrasta_no_escuro_fica_intacta(cor):
    assert marca_para_texto(cor, escuro=True) == cor


def test_ajuste_preserva_o_matiz():
    """Escurecer um amarelo tem que continuar amarelo, não virar cinza."""
    import colorsys

    from app.layout import _hex_para_rgb

    def matiz(cor):
        return colorsys.rgb_to_hls(*[v / 255 for v in _hex_para_rgb(cor)])[0]

    original = "#c6ae10"
    assert matiz(marca_para_texto(original, escuro=False)) == pytest.approx(matiz(original), abs=0.01)


def test_html_traz_as_variantes_de_contraste(app, two_tenants):
    """Contraprova no HTML: uma marca clara não gera botão de texto branco."""
    _tenant(two_tenants["tenant_a"]).cor_marca = "#c6ae10"
    db.session.commit()

    client = app.test_client()
    _logar_a(client)
    html = client.get("/admin/", base_url="http://tenant-a.localhost").get_data(as_text=True)

    # Compara com o que as funções devolvem, e não com um hex fixo: o valor
    # exato é resultado do ajuste e pode mudar sem que o comportamento mude.
    assert "--brand: #c6ae10" in html
    assert f"--brand-contraste: {contraste_da_marca('#c6ae10')}" in html
    assert f"--brand-texto: {marca_para_texto('#c6ae10', escuro=False)}" in html
    assert marca_para_texto("#c6ae10", escuro=False) != "#c6ae10", "amarelo devia escurecer"
    assert contraste_da_marca("#c6ae10") != "#ffffff", "botão amarelo não leva texto branco"


# --------------------------------------------------------------------------- #
# Shell escolhido pelo contexto
# --------------------------------------------------------------------------- #


def test_vitrine_nao_tem_sidebar_do_painel(app, two_tenants):
    """Quem só está pedindo comida não vê a navegação de gestão."""
    client = app.test_client()
    html = client.get("/", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert 'v17-app-shell' not in html
    assert "Configurações" not in html
    assert 'class="topbar"' in html


def test_painel_do_restaurante_navega_pelo_painel_de_menu(app, two_tenants):
    """A lateral saiu: quem navega agora é o painel que abre no botão da barra.

    Manter as duas seria o mesmo menu em dois lugares — e o que diverge some de
    um deles sem ninguém perceber.
    """
    client = app.test_client()
    _logar_a(client)
    html = client.get("/admin/", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert "v17-app-shell" in html
    assert 'id="menu-painel"' in html
    assert 'class="v17-sidebar"' not in html
    assert "Restaurante A" in html


def test_painel_da_plataforma_usa_a_marca_do_produto(app, platform_admin):
    client = app.test_client()
    client.post(
        "/plataforma/login",
        data={"username": "admin", "password": "senha-super-admin-123"},
        base_url="http://app.localhost",
    )
    html = client.get("/plataforma/", base_url="http://app.localhost").get_data(as_text=True)

    assert 'v17-app-shell' in html
    assert f"--brand: {COR_PADRAO}" in html
    assert "Comanda ai" in html


def test_login_do_tenant_ainda_e_vitrine(app, two_tenants):
    """Sem sessão não há painel: o login não pode expor a navegação interna."""
    client = app.test_client()
    html = client.get("/login", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert 'v17-app-shell' not in html
    assert 'class="pagina-centrada"' in html


# --------------------------------------------------------------------------- #
# Identidade por tenant, sem vazar entre eles
# --------------------------------------------------------------------------- #


def test_salvar_cor_pinta_so_o_proprio_painel(app, two_tenants):
    client = app.test_client()
    _logar_a(client)
    resposta = client.post(
        "/admin/configuracoes/identidade",
        data={"cor_marca": "#1e88e5"},
        base_url="http://tenant-a.localhost",
    )
    assert resposta.status_code == 302

    html_a = client.get("/admin/", base_url="http://tenant-a.localhost").get_data(as_text=True)
    assert "--brand: #1e88e5" in html_a

    outro = app.test_client()
    _logar_b(outro)
    html_b = outro.get("/admin/", base_url="http://tenant-b.localhost").get_data(as_text=True)
    assert "#1e88e5" not in html_b
    assert f"--brand: {COR_PADRAO}" in html_b


def test_cor_invalida_enviada_no_formulario_volta_ao_padrao(app, two_tenants):
    client = app.test_client()
    _logar_a(client)
    client.post(
        "/admin/configuracoes/identidade",
        data={"cor_marca": "azul-bebe"},
        base_url="http://tenant-a.localhost",
    )

    assert _tenant(two_tenants["tenant_a"]).cor_marca is None


def test_enviar_so_a_logo_nao_apaga_a_cor_escolhida(app, two_tenants):
    client = app.test_client()
    _logar_a(client)
    client.post(
        "/admin/configuracoes/identidade",
        data={"cor_marca": "#1e88e5"},
        base_url="http://tenant-a.localhost",
    )
    client.post(
        "/admin/configuracoes/identidade",
        data={"logo": (_png(), "marca.png")},
        content_type="multipart/form-data",
        base_url="http://tenant-a.localhost",
    )

    assert _tenant(two_tenants["tenant_a"]).cor_marca == "#1e88e5"


def test_logo_fica_na_pasta_do_tenant_e_aparece_na_barra(app, two_tenants):
    client = app.test_client()
    _logar_a(client)
    client.post(
        "/admin/configuracoes/identidade",
        data={"cor_marca": COR_PADRAO, "logo": (_png(), "marca.png")},
        content_type="multipart/form-data",
        base_url="http://tenant-a.localhost",
    )

    caminho = _tenant(two_tenants["tenant_a"]).logo
    assert caminho and caminho.startswith("tenant-a/"), caminho
    assert caminho.endswith(".webp"), "deveria ter sido convertida"
    assert (Path(app.config["UPLOAD_FOLDER"]) / caminho).exists()

    html = client.get("/admin/", base_url="http://tenant-a.localhost").get_data(as_text=True)
    assert caminho in html
    assert 'v17-brand-inicial' not in html, "com logo não mostra a inicial"


def test_sem_logo_a_barra_mostra_a_inicial_do_nome(app, two_tenants):
    client = app.test_client()
    _logar_a(client)
    html = client.get("/admin/", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert 'v17-brand-inicial' in html
    assert ">R<" in html, "Restaurante A -> R"


def test_logo_de_um_tenant_nao_aparece_no_painel_do_outro(app, two_tenants):
    client = app.test_client()
    _logar_a(client)
    client.post(
        "/admin/configuracoes/identidade",
        data={"logo": (_png(), "marca.png")},
        content_type="multipart/form-data",
        base_url="http://tenant-a.localhost",
    )
    caminho = _tenant(two_tenants["tenant_a"]).logo

    outro = app.test_client()
    _logar_b(outro)
    html_b = outro.get("/admin/", base_url="http://tenant-b.localhost").get_data(as_text=True)

    assert caminho not in html_b
    assert _tenant(two_tenants["tenant_b"]).logo is None


def test_trocar_a_logo_apaga_o_arquivo_antigo(app, two_tenants):
    """Sem isso cada troca deixaria um arquivo órfão na pasta do tenant."""
    client = app.test_client()
    _logar_a(client)
    dados = {"logo": (_png(), "primeira.png")}
    client.post(
        "/admin/configuracoes/identidade", data=dados,
        content_type="multipart/form-data", base_url="http://tenant-a.localhost",
    )
    primeira = _tenant(two_tenants["tenant_a"]).logo

    client.post(
        "/admin/configuracoes/identidade", data={"logo": (_png(240, 240), "segunda.png")},
        content_type="multipart/form-data", base_url="http://tenant-a.localhost",
    )
    segunda = _tenant(two_tenants["tenant_a"]).logo

    uploads = Path(app.config["UPLOAD_FOLDER"])
    assert segunda != primeira
    assert not (uploads / primeira).exists(), "arquivo antigo ficou órfão"
    assert (uploads / segunda).exists()


def test_remover_logo_volta_para_a_inicial(app, two_tenants):
    client = app.test_client()
    _logar_a(client)
    client.post(
        "/admin/configuracoes/identidade", data={"logo": (_png(), "marca.png")},
        content_type="multipart/form-data", base_url="http://tenant-a.localhost",
    )
    caminho = _tenant(two_tenants["tenant_a"]).logo

    client.post(
        "/admin/configuracoes/identidade",
        data={"remover_logo": "on"},
        base_url="http://tenant-a.localhost",
    )

    assert _tenant(two_tenants["tenant_a"]).logo is None
    assert not (Path(app.config["UPLOAD_FOLDER"]) / caminho).exists()


def test_arquivo_que_nao_e_imagem_nao_quebra_a_pagina(app, two_tenants):
    client = app.test_client()
    _logar_a(client)
    resposta = client.post(
        "/admin/configuracoes/identidade",
        data={"logo": (io.BytesIO(b"nao sou imagem"), "virus.png")},
        content_type="multipart/form-data",
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )

    assert resposta.status_code == 200
    assert "não é uma imagem válida" in resposta.get_data(as_text=True)
    assert _tenant(two_tenants["tenant_a"]).logo is None


def test_identidade_exige_login(app, two_tenants):
    """A rota é POST simples; sem sessão não pode repintar o painel de ninguém."""
    client = app.test_client()
    resposta = client.post(
        "/admin/configuracoes/identidade",
        data={"cor_marca": "#000000"},
        base_url="http://tenant-a.localhost",
    )

    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]
    assert _tenant(two_tenants["tenant_a"]).cor_marca is None


def test_vitrine_usa_a_logo_do_tenant_no_cabecalho(app, two_tenants):
    """A logo é do restaurante, então também vale para o cliente final."""
    client = app.test_client()
    _logar_a(client)
    client.post(
        "/admin/configuracoes/identidade", data={"logo": (_png(), "marca.png")},
        content_type="multipart/form-data", base_url="http://tenant-a.localhost",
    )
    caminho = _tenant(two_tenants["tenant_a"]).logo

    anonimo = app.test_client()
    html = anonimo.get("/", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert caminho in html


def test_a_marca_como_texto_passa_nas_DUAS_superficies_de_cada_tema():
    """A calibragem tem de valer onde o texto está, não só onde é conveniente.

    O `eyebrow` da introdução usa a marca como texto e fica sobre `--bg`, não
    sobre o branco dos cartões. Calibrar contra `#ffffff` dava 4,67:1 no alvo e
    4,29:1 no lugar real — reprovando por pouco, e sem ninguém notar, porque a
    conta batia no papel.

    No escuro a lógica se inverte: texto claro tem MAIS contraste quanto mais
    escuro o fundo, então o pior caso é a superfície mais clara.
    """
    # Claro: --bg é o pior caso; o branco do cartão é o folgado.
    CLARO_PIOR, CLARO_FOLGADO = "#f4f5f8", "#ffffff"
    # Escuro: --panel-2 é o pior caso; o --bg quase preto é o folgado.
    ESCURO_PIOR, ESCURO_FOLGADO = "#1a1d26", "#0a0b0e"

    for cor in ("#e0243f", "#f6a723", "#2563eb", "#16a34a", "#111111", "#fefefe"):
        claro = marca_para_texto(cor, escuro=False)
        escuro = marca_para_texto(cor, escuro=True)

        assert _contraste(claro, CLARO_PIOR) >= CONTRASTE_MINIMO, cor
        assert _contraste(claro, CLARO_FOLGADO) >= CONTRASTE_MINIMO, cor
        assert _contraste(escuro, ESCURO_PIOR) >= CONTRASTE_MINIMO, cor
        assert _contraste(escuro, ESCURO_FOLGADO) >= CONTRASTE_MINIMO, cor
