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
from app.layout import COR_PADRAO, cor_valida
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
# Shell escolhido pelo contexto
# --------------------------------------------------------------------------- #


def test_vitrine_nao_tem_sidebar_do_painel(app, two_tenants):
    """Quem só está pedindo comida não vê a navegação de gestão."""
    client = app.test_client()
    html = client.get("/", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert 'class="painel-shell"' not in html
    assert "Configurações" not in html
    assert 'class="topbar"' in html


def test_painel_do_restaurante_tem_sidebar(app, two_tenants):
    client = app.test_client()
    _logar_a(client)
    html = client.get("/admin/", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert 'class="painel-shell"' in html
    assert 'class="nav"' in html
    assert "Restaurante A" in html


def test_painel_da_plataforma_usa_a_marca_do_produto(app, platform_admin):
    client = app.test_client()
    client.post(
        "/plataforma/login",
        data={"username": "admin", "password": "senha-super-admin-123"},
        base_url="http://app.localhost",
    )
    html = client.get("/plataforma/", base_url="http://app.localhost").get_data(as_text=True)

    assert 'class="painel-shell"' in html
    assert f"--brand: {COR_PADRAO}" in html
    assert "Comanda ai" in html


def test_login_do_tenant_ainda_e_vitrine(app, two_tenants):
    """Sem sessão não há painel: o login não pode expor a navegação interna."""
    client = app.test_client()
    html = client.get("/login", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert 'class="painel-shell"' not in html
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


def test_logo_fica_na_pasta_do_tenant_e_aparece_na_sidebar(app, two_tenants):
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
    assert 'class="marca-inicial"' not in html, "com logo não mostra a inicial"


def test_sem_logo_a_sidebar_mostra_a_inicial_do_nome(app, two_tenants):
    client = app.test_client()
    _logar_a(client)
    html = client.get("/admin/", base_url="http://tenant-a.localhost").get_data(as_text=True)

    assert 'class="marca-inicial"' in html
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
