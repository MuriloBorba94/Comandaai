"""Layout importado do sistema original: shell por contexto e logo por tenant.

O que realmente pode dar errado aqui:

1. A logo é por tenant. A de um restaurante aparecendo no painel de outro seria
   vazamento visual de cliente para cliente.
2. O shell é escolhido pelo contexto, não declarado pela tela. Um erro aqui
   mostraria a sidebar do painel para o cliente que só quer pedir um lanche.

Havia um terceiro item, e era o mais perigoso: a cor escolhida pelo restaurante
era interpolada dentro de um bloco `<style>`, e texto arbitrário ali seria
injeção de CSS. Ele saiu da lista porque saiu do sistema — não há mais cor por
tenant, nem bloco `<style>`, nem coluna. A defesa que sobra é não haver o que
defender.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app import create_app
from app.extensions import db
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
    assert "Comanda ai" in html
    # A cor vem do CSS e nao mais de um <style> por requisicao. O que o HTML
    # tem de provar e que a folha do tema esta ligada.
    assert "css/tema-industry.css" in html


def test_login_do_tenant_ainda_e_vitrine(app, two_tenants):
    """Sem sessão não há painel: o login não pode expor a navegação interna."""
    client = app.test_client()
    html = client.get("/login", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert 'v17-app-shell' not in html
    assert 'class="pagina-centrada"' in html


# --------------------------------------------------------------------------- #
# Identidade por tenant, sem vazar entre eles
# --------------------------------------------------------------------------- #


def test_o_formulario_nao_oferece_mais_a_cor(app, two_tenants):
    """O campo saiu da tela quando o tema virou padrão.

    Com um tema fixo, a cor escolhida nunca chegava a pintar nada: o
    tema-industry.css sobrepõe --brand. Um seletor que promete e não cumpre é
    pior do que não ter seletor.
    """
    client = app.test_client()
    _logar_a(client)
    html = client.get(
        "/admin/configuracoes", base_url="http://tenant-a.localhost"
    ).get_data(as_text=True)

    assert 'type="color"' not in html
    assert 'name="cor_marca"' not in html
    # A logo, que é do restaurante de verdade, continua onde estava.
    assert 'name="logo"' in html


def test_cor_enviada_por_fora_do_formulario_e_ignorada(app, two_tenants):
    """Formulário velho em cache, ou POST feito na mão, não pode quebrar a rota.

    A coluna não existe mais. O que este teste guarda é que a ausência dela
    seja silenciosa: um campo que sobrou de um formulário antigo tem de ser
    ignorado, e não virar erro 500 na cara de quem só queria trocar a logo.
    """
    client = app.test_client()
    _logar_a(client)
    resposta = client.post(
        "/admin/configuracoes/identidade",
        data={"cor_marca": "#1e88e5"},
        base_url="http://tenant-a.localhost",
    )

    assert resposta.status_code == 302
    assert not hasattr(_tenant(two_tenants["tenant_a"]), "cor_marca")

    html = client.get("/admin/", base_url="http://tenant-a.localhost").get_data(as_text=True)
    assert "#1e88e5" not in html


def test_logo_fica_na_pasta_do_tenant_e_aparece_na_barra(app, two_tenants):
    client = app.test_client()
    _logar_a(client)
    client.post(
        "/admin/configuracoes/identidade",
        data={"logo": (_png(), "marca.png")},
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
        data={"logo": (_png(), "marca.png")},
        content_type="multipart/form-data",
        base_url="http://tenant-a.localhost",
    )

    assert resposta.status_code == 302
    assert "/login" in resposta.headers["Location"]
    assert _tenant(two_tenants["tenant_a"]).logo is None


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


def test_o_icone_da_aba_num_restaurante_e_do_restaurante(app, two_tenants):
    """Vazamento fácil de não notar: a marca do produto na aba de quem está
    pedindo um lanche.

    Quem abre o cardápio está comprando daquele restaurante, e a plataforma não
    tem por que aparecer ali. Sem logo enviada fica sem ícone — melhor nenhum do
    que o de outra empresa.
    """
    client = app.test_client()
    _logar_a(client)
    client.post(
        "/admin/configuracoes/identidade",
        data={"logo": (_png(), "marca.png")},
        content_type="multipart/form-data",
        base_url="http://tenant-a.localhost",
    )

    html = client.get("/", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert "comandaai-marca.svg" not in html
    assert 'rel="icon" href="/static/uploads/tenant-a/' in html


def test_sem_logo_o_restaurante_fica_sem_icone_e_nao_com_o_nosso(app, two_tenants):
    client = app.test_client()
    html = client.get("/", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert "comandaai-marca.svg" not in html
    assert 'rel="icon"' not in html


def test_a_area_da_plataforma_leva_a_marca_do_produto(app, platform_admin):
    client = app.test_client()

    html = client.get("/plataforma/login", base_url="http://app.localhost").get_data(as_text=True)

    assert "comandaai-marca.svg" in html
