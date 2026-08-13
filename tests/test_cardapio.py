"""Fase 1 — cardápio: categorias, adicionais, upload de foto e vitrine.

O foco é o que pode dar errado especificamente no multi-tenant: nomes iguais em
tenants diferentes, e ligações cruzadas entre entidades de tenants distintos —
sobretudo em produto_adicional, que não carrega tenant_id.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app import create_app
from app.extensions import db
from app.models.adicional import Adicional
from app.models.categoria import Categoria
from app.models.produto import Produto
from app.routes.admin import _to_float
from tests.conftest import TestConfig, login_tenant


@pytest.fixture()
def app(tmp_path):
    """App com pasta de uploads própria por teste, para os arquivos gravados
    não vazarem de um teste para outro nem para o projeto."""

    class CardapioConfig(TestConfig):
        UPLOAD_FOLDER = str(tmp_path / "uploads")

    application = create_app(CardapioConfig)
    with application.app_context():
        db.create_all()
        yield application
        db.drop_all()


@pytest.fixture()
def uploads(app) -> Path:
    return Path(app.config["UPLOAD_FOLDER"])


def _png(largura: int = 300, altura: int = 300) -> io.BytesIO:
    buffer = io.BytesIO()
    Image.new("RGB", (largura, altura), (200, 30, 40)).save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


def _logar_a(client):
    return login_tenant(client, "tenant-a", "admin", "senha-a-123")


def _logar_b(client):
    return login_tenant(client, "tenant-b", "admin", "senha-b-123")


def _criar_categoria(tenant_id: int, nome: str, ordem: int = 0, ativa: bool = True) -> Categoria:
    categoria = Categoria(tenant_id=tenant_id, nome=nome, ordem=ordem, ativa=ativa)
    db.session.add(categoria)
    db.session.commit()
    return categoria


def _criar_adicional(tenant_id: int, nome: str, preco: float = 3.0) -> Adicional:
    adicional = Adicional(tenant_id=tenant_id, nome=nome, preco=preco)
    db.session.add(adicional)
    db.session.commit()
    return adicional


# --------------------------------------------------------------------------- #
# Preço: leitura e exibição no padrão brasileiro
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("digitado", "esperado"),
    [
        ("45,90", 45.90),
        ("45.90", 45.90),
        ("1.234,56", 1234.56),  # ponto como separador de milhar
        ("1234.56", 1234.56),
        ("0", 0.0),
        ("", 0.0),
        ("abc", 0.0),
    ],
)
def test_leitura_de_preco_digitado(digitado, esperado):
    assert _to_float(digitado) == esperado


def test_preco_com_milhar_nao_vira_zero(client, two_tenants):
    """Antes, "1.234,56" virava "1.234.56", falhava no float e salvava 0.0."""
    _logar_a(client)
    client.post(
        "/admin/produtos",
        data={"nome": "Festa Completa", "preco": "1.234,56"},
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )
    assert Produto.query.filter_by(nome="Festa Completa").one().preco == 1234.56


def test_precos_aparecem_no_formato_brasileiro(client, two_tenants):
    db.session.add(Produto(tenant_id=two_tenants["tenant_a"], nome="Combo Família", preco=1234.5))
    db.session.commit()

    corpo = client.get("/", base_url="http://tenant-a.localhost").get_data(as_text=True)
    assert "R$ 1.234,50" in corpo
    assert "1234.50" not in corpo


# --------------------------------------------------------------------------- #
# Escopo por tenant
# --------------------------------------------------------------------------- #


def test_mesma_categoria_em_dois_tenants_nao_colide(client, two_tenants):
    """A unicidade é (tenant_id, nome): "Bebidas" pode existir nos dois."""
    a = _criar_categoria(two_tenants["tenant_a"], "Bebidas")
    b = _criar_categoria(two_tenants["tenant_b"], "Bebidas")
    assert a.id != b.id
    assert Categoria.query.filter_by(nome="Bebidas").count() == 2


def test_categoria_duplicada_no_mesmo_tenant_e_recusada(client, two_tenants):
    _criar_categoria(two_tenants["tenant_a"], "Bebidas")
    _logar_a(client)
    resposta = client.post(
        "/admin/categorias",
        data={"nome": "Bebidas", "ordem": "0"},
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )
    assert "já tem uma categoria chamada" in resposta.get_data(as_text=True)
    assert Categoria.query.filter_by(tenant_id=two_tenants["tenant_a"]).count() == 1


def test_admin_de_um_tenant_nao_ve_categorias_do_outro(client, two_tenants):
    _criar_categoria(two_tenants["tenant_a"], "Burgers Exclusivos A")
    _criar_categoria(two_tenants["tenant_b"], "Pizzas Exclusivas B")

    _logar_a(client)
    corpo = client.get("/admin/categorias", base_url="http://tenant-a.localhost").get_data(as_text=True)
    assert "Burgers Exclusivos A" in corpo
    assert "Pizzas Exclusivas B" not in corpo


def test_admin_de_um_tenant_nao_ve_adicionais_do_outro(client, two_tenants):
    _criar_adicional(two_tenants["tenant_a"], "Bacon Exclusivo A")
    _criar_adicional(two_tenants["tenant_b"], "Catupiry Exclusivo B")

    _logar_a(client)
    corpo = client.get("/admin/adicionais", base_url="http://tenant-a.localhost").get_data(as_text=True)
    assert "Bacon Exclusivo A" in corpo
    assert "Catupiry Exclusivo B" not in corpo


def test_nao_da_para_editar_produto_de_outro_tenant(client, two_tenants):
    produto_b = Produto(tenant_id=two_tenants["tenant_b"], nome="Segredo do B", preco=10.0)
    db.session.add(produto_b)
    db.session.commit()

    _logar_a(client)
    resposta = client.get(
        f"/admin/produtos/{produto_b.id}/editar",
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )
    assert "Segredo do B" not in resposta.get_data(as_text=True)
    assert "Produto não encontrado" in resposta.get_data(as_text=True)


def test_telas_do_admin_renderizam(client, two_tenants):
    """Garante que todo template do admin é de fato renderizado por algum teste.

    Sem isto, um erro de Jinja no formulário de edição ou no painel só apareceria
    em produção — nenhum outro teste chega a renderizar essas duas telas.
    """
    tenant_a = two_tenants["tenant_a"]
    categoria = _criar_categoria(tenant_a, "Burgers")
    _criar_adicional(tenant_a, "Bacon")
    produto = Produto(tenant_id=tenant_a, nome="X-Tudo", preco=30.0, categoria_id=categoria.id)
    db.session.add(produto)
    db.session.commit()

    _logar_a(client)
    base = "http://tenant-a.localhost"

    painel = client.get("/admin/", base_url=base)
    assert painel.status_code == 200
    assert "X-Tudo" not in painel.get_data(as_text=True)  # o painel mostra contagens

    for url in ("/admin/produtos", "/admin/categorias", "/admin/adicionais",
                f"/admin/produtos/{produto.id}/editar"):
        resposta = client.get(url, base_url=base)
        assert resposta.status_code == 200, f"{url} devolveu {resposta.status_code}"

    formulario = client.get(f"/admin/produtos/{produto.id}/editar", base_url=base).get_data(as_text=True)
    assert "X-Tudo" in formulario
    assert "Bacon" in formulario  # checkbox do adicional aparece no formulário


# --------------------------------------------------------------------------- #
# Ligações cruzadas entre tenants
# --------------------------------------------------------------------------- #


def test_adicional_de_outro_tenant_nao_pode_ser_vinculado(client, two_tenants):
    """produto_adicional não tem tenant_id — este é o teste que garante que a
    barreira em Produto.definir_adicionais() está de fato no caminho."""
    adicional_b = _criar_adicional(two_tenants["tenant_b"], "Catupiry do B")

    _logar_a(client)
    client.post(
        "/admin/produtos",
        data={"nome": "X-Salada", "preco": "25,00", "adicionais": [str(adicional_b.id)], "disponivel": "on"},
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )

    produto = Produto.query.filter_by(tenant_id=two_tenants["tenant_a"], nome="X-Salada").one()
    assert produto.adicionais == [], "adicional de outro tenant não pode ser vinculado"


def test_adicional_do_proprio_tenant_e_vinculado(client, two_tenants):
    """Contraprova do teste anterior: o caminho normal precisa funcionar."""
    adicional_a = _criar_adicional(two_tenants["tenant_a"], "Bacon do A")

    _logar_a(client)
    client.post(
        "/admin/produtos",
        data={"nome": "X-Bacon", "preco": "30,00", "adicionais": [str(adicional_a.id)], "disponivel": "on"},
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )

    produto = Produto.query.filter_by(tenant_id=two_tenants["tenant_a"], nome="X-Bacon").one()
    assert [adicional.nome for adicional in produto.adicionais] == ["Bacon do A"]


def test_categoria_de_outro_tenant_e_ignorada(client, two_tenants):
    categoria_b = _criar_categoria(two_tenants["tenant_b"], "Categoria do B")

    _logar_a(client)
    client.post(
        "/admin/produtos",
        data={"nome": "Produto do A", "preco": "10,00", "categoria_id": str(categoria_b.id)},
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )

    produto = Produto.query.filter_by(tenant_id=two_tenants["tenant_a"], nome="Produto do A").one()
    assert produto.categoria_id is None, "produto não pode apontar para categoria de outro tenant"


def test_categoria_do_proprio_tenant_e_gravada(client, two_tenants):
    """Contraprova: sem isto, o teste acima passaria mesmo se o campo categoria
    do formulário estivesse quebrado e nunca gravasse nada."""
    categoria_a = _criar_categoria(two_tenants["tenant_a"], "Categoria do A")

    _logar_a(client)
    client.post(
        "/admin/produtos",
        data={"nome": "Bem Categorizado", "preco": "10,00", "categoria_id": str(categoria_a.id)},
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )

    produto = Produto.query.filter_by(nome="Bem Categorizado").one()
    assert produto.categoria_id == categoria_a.id


# --------------------------------------------------------------------------- #
# Upload de imagem
# --------------------------------------------------------------------------- #


def test_upload_grava_na_pasta_do_tenant_e_converte_para_webp(client, two_tenants, uploads):
    _logar_a(client)
    client.post(
        "/admin/produtos",
        data={"nome": "Com Foto", "preco": "20,00", "imagem": (_png(), "foto.png")},
        base_url="http://tenant-a.localhost",
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    produto = Produto.query.filter_by(nome="Com Foto").one()
    assert produto.imagem is not None
    # O caminho gravado inclui a pasta do tenant, e o arquivo virou webp.
    assert produto.imagem.startswith("tenant-a/")
    assert produto.imagem.endswith(".webp")
    arquivo = uploads / produto.imagem
    assert arquivo.exists()
    with Image.open(arquivo) as imagem:
        assert imagem.format == "WEBP"


def test_upload_de_arquivo_que_nao_e_imagem_e_recusado(client, two_tenants, uploads):
    _logar_a(client)
    resposta = client.post(
        "/admin/produtos",
        data={
            "nome": "Malicioso",
            "preco": "10,00",
            "imagem": (io.BytesIO(b"<?php echo 1; ?>"), "shell.png"),
        },
        base_url="http://tenant-a.localhost",
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    # A extensão dizia .png; o conteúdo não era imagem. Vale o conteúdo.
    assert "não é uma imagem válida" in resposta.get_data(as_text=True)
    assert Produto.query.filter_by(nome="Malicioso").first() is None, "produto não devia ser criado"
    assert list(uploads.rglob("*.webp")) == []


def test_imagem_pequena_demais_e_recusada(client, two_tenants):
    _logar_a(client)
    resposta = client.post(
        "/admin/produtos",
        data={"nome": "Minúscula", "preco": "10,00", "imagem": (_png(50, 50), "foto.png")},
        base_url="http://tenant-a.localhost",
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert "pequena demais" in resposta.get_data(as_text=True)
    assert Produto.query.filter_by(nome="Minúscula").first() is None


def test_dois_tenants_com_o_mesmo_nome_de_arquivo_nao_se_sobrescrevem(client, two_tenants, uploads):
    _logar_a(client)
    client.post(
        "/admin/produtos",
        data={"nome": "Foto A", "preco": "10,00", "imagem": (_png(), "foto.png")},
        base_url="http://tenant-a.localhost",
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    client.get("/logout", base_url="http://tenant-a.localhost")

    _logar_b(client)
    client.post(
        "/admin/produtos",
        data={"nome": "Foto B", "preco": "10,00", "imagem": (_png(), "foto.png")},
        base_url="http://tenant-b.localhost",
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    imagem_a = Produto.query.filter_by(nome="Foto A").one().imagem
    imagem_b = Produto.query.filter_by(nome="Foto B").one().imagem
    assert imagem_a.startswith("tenant-a/") and imagem_b.startswith("tenant-b/")
    assert (uploads / imagem_a).exists() and (uploads / imagem_b).exists()


def test_excluir_produto_apaga_o_arquivo_da_imagem(client, two_tenants, uploads):
    _logar_a(client)
    client.post(
        "/admin/produtos",
        data={"nome": "Vai Sair", "preco": "10,00", "imagem": (_png(), "foto.png")},
        base_url="http://tenant-a.localhost",
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    produto = Produto.query.filter_by(nome="Vai Sair").one()
    arquivo = uploads / produto.imagem
    assert arquivo.exists()

    client.post(
        f"/admin/produtos/{produto.id}/excluir",
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )
    assert not arquivo.exists(), "imagem órfã ficou no disco"


def test_trocar_a_imagem_apaga_a_antiga(client, two_tenants, uploads):
    _logar_a(client)
    client.post(
        "/admin/produtos",
        data={"nome": "Troca", "preco": "10,00", "imagem": (_png(), "antiga.png")},
        base_url="http://tenant-a.localhost",
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    produto = Produto.query.filter_by(nome="Troca").one()
    antiga = uploads / produto.imagem

    client.post(
        f"/admin/produtos/{produto.id}/editar",
        data={"nome": "Troca", "preco": "10,00", "imagem": (_png(400, 400), "nova.png"), "disponivel": "on"},
        base_url="http://tenant-a.localhost",
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    produto = Produto.query.filter_by(nome="Troca").one()
    nova = uploads / produto.imagem
    assert nova.exists()
    assert not antiga.exists(), "imagem antiga ficou órfã no disco"
    assert list(uploads.rglob("*.webp")) == [nova]


# --------------------------------------------------------------------------- #
# Vitrine pública
# --------------------------------------------------------------------------- #


def test_vitrine_respeita_a_ordem_das_categorias_do_tenant(client, two_tenants):
    tenant_a = two_tenants["tenant_a"]
    bebidas = _criar_categoria(tenant_a, "Bebidas", ordem=2)
    burgers = _criar_categoria(tenant_a, "Burgers", ordem=1)
    db.session.add_all(
        [
            Produto(tenant_id=tenant_a, nome="Refrigerante", preco=6.0, categoria_id=bebidas.id),
            Produto(tenant_id=tenant_a, nome="X-Tudo", preco=30.0, categoria_id=burgers.id),
        ]
    )
    db.session.commit()

    corpo = client.get("/", base_url="http://tenant-a.localhost").get_data(as_text=True)
    # Burgers tem ordem menor, então precisa vir antes — mesmo sendo depois no alfabeto.
    assert corpo.index("Burgers") < corpo.index("Bebidas")


def test_categoria_desativada_esconde_seus_produtos_da_vitrine(client, two_tenants):
    tenant_a = two_tenants["tenant_a"]
    oculta = _criar_categoria(tenant_a, "Fora do Ar", ativa=False)
    db.session.add(Produto(tenant_id=tenant_a, nome="Produto Escondido", preco=9.0, categoria_id=oculta.id))
    db.session.commit()

    corpo = client.get("/", base_url="http://tenant-a.localhost").get_data(as_text=True)
    assert "Produto Escondido" not in corpo


def test_produto_sem_categoria_aparece_em_outros(client, two_tenants):
    db.session.add(Produto(tenant_id=two_tenants["tenant_a"], nome="Solto", preco=9.0))
    db.session.commit()

    corpo = client.get("/", base_url="http://tenant-a.localhost").get_data(as_text=True)
    assert "Outros" in corpo
    assert "Solto" in corpo


def test_produto_indisponivel_nao_aparece_na_vitrine(client, two_tenants):
    db.session.add(
        Produto(tenant_id=two_tenants["tenant_a"], nome="Esgotado", preco=9.0, disponivel=False)
    )
    db.session.commit()

    corpo = client.get("/", base_url="http://tenant-a.localhost").get_data(as_text=True)
    assert "Esgotado" not in corpo


def test_vitrine_de_um_tenant_nunca_mostra_cardapio_do_outro(client, two_tenants):
    categoria_b = _criar_categoria(two_tenants["tenant_b"], "Exclusivo do B")
    db.session.add_all(
        [
            Produto(tenant_id=two_tenants["tenant_a"], nome="Item do A", preco=1.0),
            Produto(tenant_id=two_tenants["tenant_b"], nome="Item do B", preco=1.0, categoria_id=categoria_b.id),
        ]
    )
    db.session.commit()

    corpo_a = client.get("/", base_url="http://tenant-a.localhost").get_data(as_text=True)
    assert "Item do A" in corpo_a
    assert "Item do B" not in corpo_a
    assert "Exclusivo do B" not in corpo_a


def test_excluir_categoria_preserva_os_produtos(client, two_tenants):
    tenant_a = two_tenants["tenant_a"]
    categoria = _criar_categoria(tenant_a, "Temporária")
    db.session.add(Produto(tenant_id=tenant_a, nome="Sobrevivente", preco=12.0, categoria_id=categoria.id))
    db.session.commit()

    _logar_a(client)
    client.post(
        f"/admin/categorias/{categoria.id}/excluir",
        base_url="http://tenant-a.localhost",
        follow_redirects=True,
    )

    produto = Produto.query.filter_by(nome="Sobrevivente").one()
    assert produto.categoria_id is None
    corpo = client.get("/", base_url="http://tenant-a.localhost").get_data(as_text=True)
    assert "Sobrevivente" in corpo
